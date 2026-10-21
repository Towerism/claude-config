"""The checker's --host mode: inputs and allowed skills come from a host profile."""

import json

import pytest

from eval import run_eval
from tests.checker_helpers import (
    GOOD_GLOBAL_MD,
    REPO_ROOT,
    make_lock,
    make_repo,
    make_skill,
    run_tool,
)
from tools.check_config import SKILL_TREES, load_profile

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


def test_a_synced_skill_name_is_a_finding_in_host_mode_even_when_synced_here(mode, tmp_path):
    home, repo, profile, lock = mode
    synced = home / "skills" / "synced" / "uuid-1" / "skill-creator"
    synced.mkdir(parents=True)
    (synced / "SKILL.md").write_text("x")
    if lock is not None:
        lock = make_lock(
            tmp_path / "synced.lock.json",
            {"engineering-skills@mkt": {"skills": ["senior-qa"], "files": QA_FILES}},
        )
    (repo / "config" / "CLAUDE.global.md").write_text(
        GOOD_GLOBAL_MD + "- `anthropic-skills:skill-creator`, not `anthropic-skills:pdf`.\n"
    )
    r, rep = check(home, profile, lock=lock)
    assert r.returncode == 1
    assert rep["unresolvedSkills"] == []
    assert rep["syncedSkills"] == ["anthropic-skills:pdf", "anthropic-skills:skill-creator"]


def vendored_skill(repo, name):
    skill = repo / "config" / "vendored-skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")


def test_a_profile_skill_in_the_vendored_tree_resolves(mode, tmp_path):
    home, _, _, lock = mode
    repo = tmp_path / "with-vendored"
    profile = make_repo(repo, skills=["s1", "v1"])
    vendored_skill(repo, "v1")
    (repo / "config" / "CLAUDE.global.md").write_text(GOOD_GLOBAL_MD + "- Also `v1`.\n")
    r, rep = check(home, profile, lock=lock)
    assert rep["profileProblems"] == [], r.stdout + r.stderr
    assert rep["unresolvedSkills"] == []


def test_a_skill_in_both_trees_is_a_profile_problem(mode):
    home, repo, profile, lock = mode
    vendored_skill(repo, "s1")
    r, rep = check(home, profile, lock=lock)
    assert r.returncode == 1
    both = "s1: config/skills/s1 and config/vendored-skills/s1"
    assert rep["profileProblems"] == [{"kind": "skill-in-both-trees", "item": both}]


def test_a_leftover_directory_without_a_skill_md_is_not_a_second_copy(mode, tmp_path):
    # A pull that moves a skill leaves behind any git-ignored files, such as __pycache__.
    home, _, _, lock = mode
    repo = tmp_path / "leftover"
    profile = make_repo(repo, skills=["s1", "v1"])
    vendored_skill(repo, "v1")
    (repo / "config" / "skills" / "v1" / "__pycache__").mkdir(parents=True)
    (repo / "config" / "CLAUDE.global.md").write_text(GOOD_GLOBAL_MD + "- Also `v1`.\n")
    r, rep = check(home, profile, lock=lock)
    assert rep["profileProblems"] == [], r.stdout + r.stderr
    assert load_profile(profile).skill_path("v1") == repo / "config" / "vendored-skills" / "v1"


def test_the_deploy_and_the_eval_harness_agree_on_the_skill_trees():
    # eval/ keeps its own copy so an extracted eval tree needs nothing from tools/.
    assert run_eval.SKILL_TREES == SKILL_TREES


def test_a_skill_in_neither_tree_is_looked_for_in_the_maintained_tree(tmp_path):
    profile = load_profile(make_repo(tmp_path, skills=["s1", "gone"]))
    assert profile.skill_path("gone") == tmp_path / "config" / "skills" / "gone"
