"""The checker's offline mode, which resolves plugin skills from the plugin lock only."""

import json

import pytest

from tests.checker_helpers import REPO_ROOT, make_lock, make_overlay, make_skill, run_tool

SCRIPT = REPO_ROOT / "tools" / "check_config.py"
QA = "engineering-skills:senior-qa"
GOOD_MD = (
    "# Global rules\n"
    "- Test plans: `engineering-skills:senior-qa`, or just `senior-qa`.\n"
    "- Start work with `my-lifecycle`.\n"
    "- IMPORTANT: merge only with the user's authorization.\n"
)
QA_FILES = {"senior-qa": ["SKILL.md", "references/testing_strategies.md"]}


@pytest.fixture
def offline(claude_home, tmp_path):
    """A home with no plugin data at all, a repo skill root, and a matching lock."""
    (claude_home / "plugins" / "installed_plugins.json").unlink()
    staging = tmp_path / "staging"
    (staging / "my-lifecycle").mkdir(parents=True)
    (staging / "my-lifecycle" / "SKILL.md").write_text("---\nname: my-lifecycle\n---\n")
    md = tmp_path / "CLAUDE.md"
    md.write_text(GOOD_MD)
    make_overlay(claude_home, QA, "Read `references/testing_strategies.md`.")
    lock = make_lock(
        tmp_path / "plugins.lock.json",
        {"engineering-skills@mkt": {"skills": ["senior-qa", "tdd-guide"], "files": QA_FILES}},
    )
    return claude_home, staging, md, lock


def check(home, staging, md, lock, *extra):
    args = [
        "--claude-md", str(md),
        "--overlays", str(home / "skill-overlays"),
        "--claude-home", str(home),
        "--skill-root", str(staging),
        "--plugin-index", str(lock),
        "--json",
        *extra,
    ]  # fmt: skip
    r = run_tool(SCRIPT, args)
    return r, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else None)


def test_full_and_short_names_resolve_from_the_lock(offline):
    r, rep = check(*offline)
    assert r.returncode == 0, r.stdout + r.stderr
    assert rep["unresolvedSkills"] == [] and rep["missingPaths"] == []


def test_plugin_cache_and_synced_skills_on_disk_are_ignored(offline, tmp_path):
    home, staging, md, lock = offline
    # Installed on disk but absent from the lock: offline mode must not see them.
    (home / "plugins" / "installed_plugins.json").write_text('{"version": 2, "plugins": {}}')
    make_skill(home, "engineering-skills", "2.9.0", "decoy")
    synced = home / "skills" / "synced" / "uuid-1" / "pdf"
    synced.mkdir(parents=True)
    (synced / "SKILL.md").write_text("x")
    md.write_text(GOOD_MD + "- `engineering-skills:decoy` and `anthropic-skills:pdf`.\n")
    r, rep = check(home, staging, md, lock)
    assert r.returncode == 1
    assert rep["unresolvedSkills"] == ["engineering-skills:decoy"]
    assert rep["syncedSkills"] == ["anthropic-skills:pdf"]


def test_unknown_and_ambiguous_names_are_unresolved_and_synced_names_reported(offline, tmp_path):
    home, staging, md, _ = offline
    lock = make_lock(
        tmp_path / "two.lock.json",
        {
            "engineering-skills@mkt": {"skills": ["senior-qa"], "files": QA_FILES},
            "other-bundle@mkt": {"skills": ["senior-qa"]},
        },
    )
    md.write_text(
        "- `engineering-skills:senior-qa`, `senior-qa`, `engineering-skills:nope`,\n"
        "  `anthropic-skills:docx`, `other-bundle:senior-qa`\n"
        "- IMPORTANT: merge only with the user's authorization.\n"
    )
    r, rep = check(home, staging, md, lock)
    assert r.returncode == 1
    assert rep["unresolvedSkills"] == ["engineering-skills:nope", "senior-qa"]
    assert rep["syncedSkills"] == ["anthropic-skills:docx"]


def test_overlay_path_not_in_the_lock_is_missing(offline):
    home, staging, md, lock = offline
    make_overlay(home, QA, "Read `references/testing_strategies.md` and `references/nope.md`.")
    r, rep = check(home, staging, md, lock)
    assert r.returncode == 1
    assert rep["missingPaths"] == [{"skill": QA, "path": "references/nope.md"}]


def test_overlay_for_a_skill_without_recorded_files_reports_every_path(offline):
    home, staging, md, lock = offline
    make_overlay(home, "engineering-skills:tdd-guide", "See `references/a.md`.")
    r, rep = check(home, staging, md, lock)
    assert r.returncode == 1
    assert rep["missingPaths"] == [
        {"skill": "engineering-skills:tdd-guide", "path": "references/a.md"}
    ]


def test_overlay_for_a_repo_skill_is_checked_on_disk(offline):
    home, staging, md, lock = offline
    (staging / "my-lifecycle" / "references").mkdir()
    (staging / "my-lifecycle" / "references" / "here.md").write_text("x")
    make_overlay(home, "my-lifecycle", "Read `references/here.md` and `references/gone.md`.")
    r, rep = check(home, staging, md, lock)
    assert r.returncode == 1
    assert rep["missingPaths"] == [{"skill": "my-lifecycle", "path": "references/gone.md"}]


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{not json",
        json.dumps({"lockVersion": 1, "plugins": {}, "synced": []}),
        json.dumps({"lockVersion": 2}),
        json.dumps({"lockVersion": 2, "plugins": []}),
        json.dumps({"lockVersion": 2, "plugins": {"a@m": {"skills": "x"}}}),
        json.dumps({"lockVersion": 2, "plugins": {"a@m": {"skills": [], "files": []}}}),
        json.dumps([1]),
    ],
    ids=["missing", "invalid-json", "old-version", "no-plugins", "plugins-not-object",
         "skills-not-list", "files-not-object", "not-an-object"],
)  # fmt: skip
def test_unusable_lock_is_a_usage_error(offline, tmp_path, content):
    home, staging, md, _ = offline
    lock = tmp_path / "bad.lock.json"
    if content is not None:
        lock.write_text(content)
    r, rep = check(home, staging, md, lock)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "error" in rep


def test_a_user_skill_in_the_home_still_resolves_offline(offline):
    home, staging, md, lock = offline
    make_skill(home, None, "", "home-skill")
    md.write_text(GOOD_MD + "- And `home-skill`.\n")
    r, rep = check(home, staging, md, lock)
    assert r.returncode == 0, rep


def test_a_version_1_lock_is_refused_by_its_version(offline, tmp_path):
    home, staging, md, _ = offline
    lock = tmp_path / "old.lock.json"
    lock.write_text(json.dumps({"lockVersion": 1, "plugins": {}, "synced": ["pdf"]}))
    r, rep = check(home, staging, md, lock)
    assert r.returncode == 2
    assert "lockVersion 1" in rep["error"] and "version 2" in rep["error"], rep["error"]
