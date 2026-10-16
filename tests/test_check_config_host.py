"""The checker's --host mode: inputs and allowed skills come from a host profile."""

import json

import pytest

from tests.checker_helpers import (
    GOOD_GLOBAL_MD,
    REPO_ROOT,
    make_lock,
    make_repo,
    make_skill,
    run_tool,
)

SCRIPT = REPO_ROOT / "tools" / "check_config.py"
QA_FILES = {"senior-qa": ["SKILL.md", "references/testing_strategies.md"]}


@pytest.fixture
def host(claude_home, tmp_path):
    """A repo with profile hosts/a.json, a home with the profile's plugin, and a lock."""
    repo = tmp_path / "repo"
    profile = make_repo(repo)
    make_skill(
        claude_home,
        "engineering-skills",
        "2.9.0",
        "senior-qa",
        ["references/testing_strategies.md"],
    )
    make_skill(claude_home, "other-bundle", "1.0.0", "other-skill", [])
    lock = make_lock(
        tmp_path / "plugins.lock.json",
        {
            "engineering-skills@mkt": {"skills": ["senior-qa"], "files": QA_FILES},
            "other-bundle@mkt": {"skills": ["other-skill"]},
        },
    )
    return claude_home, repo, profile, lock


def check(home, profile, *extra, lock=None):
    args = ["--host", str(profile), "--claude-home", str(home), "--json", *extra]
    if lock is not None:
        args += ["--plugin-index", str(lock)]
    r = run_tool(SCRIPT, args)
    return r, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else None)


@pytest.fixture(params=["live", "offline"])
def mode(request, host):
    """Run a host test in live mode and again in offline mode."""
    home, repo, profile, lock = host
    return home, repo, profile, (lock if request.param == "offline" else None)


def test_a_complete_profile_passes(mode):
    home, _, profile, lock = mode
    r, rep = check(home, profile, lock=lock)
    assert r.returncode == 0, r.stdout + r.stderr
    assert rep["profileProblems"] == [] and rep["unresolvedSkills"] == []


def test_only_profile_skills_and_profile_plugins_resolve(mode):
    home, repo, profile, lock = mode
    make_skill(home, None, "", "home-only")
    (repo / "config" / "skills" / "un-listed").mkdir()
    (repo / "config" / "skills" / "un-listed" / "SKILL.md").write_text("x")
    (repo / "config" / "CLAUDE.global.md").write_text(
        GOOD_GLOBAL_MD
        + "- Also `home-only`, `un-listed`, `other-bundle:other-skill` and `other-skill`.\n"
    )
    r, rep = check(home, profile, lock=lock)
    assert r.returncode == 1
    assert rep["unresolvedSkills"] == [
        "home-only",
        "other-bundle:other-skill",
        "other-skill",
        "un-listed",
    ]


def test_a_short_name_needs_to_be_unique_only_among_profile_plugins(host, tmp_path):
    home, repo, profile, _ = host
    lock = make_lock(
        tmp_path / "dup.lock.json",
        {
            "engineering-skills@mkt": {"skills": ["senior-qa", "dup-skill"], "files": QA_FILES},
            "other-bundle@mkt": {"skills": ["dup-skill"]},
        },
    )
    (repo / "config" / "CLAUDE.global.md").write_text(GOOD_GLOBAL_MD + "- And `dup-skill`.\n")
    r, rep = check(home, profile, lock=lock)
    assert r.returncode == 0, rep


def test_listed_overlays_select_only_those_files(mode):
    home, repo, profile, lock = mode
    overlays = repo / "config" / "skill-overlays"
    (overlays / "engineering-skills:senior-qa.md").write_text(
        "Read `references/testing_strategies.md`."
    )
    (overlays / "engineering-skills:ghost.md").write_text("Read `references/nope.md`.")
    profile.write_text(
        json.dumps(
            {**json.loads(profile.read_text()), "overlays": ["engineering-skills:senior-qa"]}
        )
    )
    r, rep = check(home, profile, lock=lock)
    assert r.returncode == 0, rep
    assert [t["file"] for t in rep["tokenCounts"][2:]] == ["engineering-skills:senior-qa.md"]


def test_every_kind_of_profile_problem_is_reported(mode):
    home, repo, profile, lock = mode
    data = json.loads(profile.read_text())
    data.update(
        skills=["s1", "missing-skill"],
        hooks=["h.py", "missing.py"],
        overlays=["engineering-skills:not-there"],
        plugins=["engineering-skills@mkt", "absent@mkt"],
        defaultLifecycle="elsewhere",
    )
    profile.write_text(json.dumps(data))
    r, rep = check(home, profile, lock=lock)
    assert r.returncode == 1
    assert rep["profileProblems"] == [
        {"kind": "skill", "item": "missing-skill"},
        {"kind": "hook", "item": "missing.py"},
        {"kind": "overlay", "item": "engineering-skills:not-there"},
        {"kind": "default-lifecycle", "item": "elsewhere"},
        {"kind": "plugin", "item": "absent@mkt"},
    ]


def test_clause_check_reads_the_default_lifecycle_skill(mode, tmp_path):
    home, _, profile, lock = mode
    clauses = tmp_path / "clauses.json"
    row = {"id": "C-01", "text": "t", "disposition": "lifecycle-skill", "mustAppear": "issue body"}
    clauses.write_text(json.dumps([row]))
    r, rep = check(home, profile, "--clauses", str(clauses), lock=lock)
    assert r.returncode == 0, rep


@pytest.mark.parametrize("flag", ["--claude-md", "--overlays", "--skill-root", "--lifecycle-skill"])
def test_host_with_an_explicit_input_flag_is_a_usage_error(host, flag):
    home, _, profile, lock = host
    r, rep = check(home, profile, flag, "x", lock=lock)
    assert r.returncode == 2 and "error" in rep


def test_without_host_claude_md_and_overlays_are_required(claude_home):
    r = run_tool(SCRIPT, ["--claude-home", str(claude_home), "--json"])
    assert r.returncode == 2 and "error" in json.loads(r.stdout)


PROFILE_KEYS = ["claudeMd", "skills", "plugins", "hooks", "overlays", "defaultLifecycle"]


def break_profile(path, how, key=None, value=None):
    if how == "delete":
        path.unlink()
    elif how == "text":
        path.write_text(value)
    else:
        data = json.loads(path.read_text())
        if how == "drop":
            del data[key]
        else:
            data[key] = value
        path.write_text(json.dumps(data))


@pytest.mark.parametrize(
    "how,key,value",
    [
        ("delete", None, None),
        ("text", None, "{not json"),
        ("text", None, "[]"),
        *[("drop", key, None) for key in PROFILE_KEYS],
        ("set", "skills", "s1"),
        ("set", "overlays", 3),
        ("set", "claudeMd", "config/absent.md"),
    ],
)
def test_unusable_profile_is_a_usage_error(host, how, key, value):
    home, _, profile, lock = host
    break_profile(profile, how, key, value)
    r, rep = check(home, profile, lock=lock)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "error" in rep


def test_without_host_there_are_no_profile_problems(claude_home, tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text("- IMPORTANT: merge only with the user's authorization.\n")
    args = ["--claude-md", str(md), "--overlays", str(tmp_path / "none"), "--json"]
    r = run_tool(SCRIPT, ["--claude-home", str(claude_home), *args])
    assert r.returncode == 0 and json.loads(r.stdout)["profileProblems"] == []


def test_synced_skills_resolve_in_host_mode(mode, tmp_path):
    home, repo, profile, lock = mode
    synced = home / "skills" / "synced" / "uuid-1" / "skill-creator"
    synced.mkdir(parents=True)
    (synced / "SKILL.md").write_text("x")
    if lock is not None:
        lock = make_lock(
            tmp_path / "synced.lock.json",
            {"engineering-skills@mkt": {"skills": ["senior-qa"], "files": QA_FILES}},
            synced=["skill-creator"],
        )
    (repo / "config" / "CLAUDE.global.md").write_text(
        GOOD_GLOBAL_MD + "- `anthropic-skills:skill-creator`, not `anthropic-skills:pdf`.\n"
    )
    r, rep = check(home, profile, lock=lock)
    assert rep["unresolvedSkills"] == ["anthropic-skills:pdf"]
