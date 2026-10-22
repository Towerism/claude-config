"""Skills copied whole from upstream match their provenance records.

A vendored skill is a repo skill whose VENDORED.json lists its SKILL.md. It is
kept as upstream wrote it, apart from files marked adapted, and ruff skips it.
Vendored skills live in config/vendored-skills/ and the skills maintained here
in config/skills/, so the layout shows which ones are edited by hand (#75).
"""

import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path

from tests.checker_helpers import REPO_ROOT

SKILLS = REPO_ROOT / "config" / "vendored-skills"
MAINTAINED = REPO_ROOT / "config" / "skills"
UPSTREAM_REPO = "alirezarezvani/claude-skills"
# The skills maintained in this repo. plain-prose vendors a few files, not its SKILL.md.
OWN_SKILLS = {
    "fresh-session-handoff", "gitea-issue-lifecycle", "github-issue-lifecycle",
    "jira-ticket-lifecycle",
    "linux-troubleshooting",
    "plain-prose", "writing-docs",
}  # fmt: skip
# The skills the owner chose to keep when the claude-code-skills plugins were removed.
EXPECTED = {
    "adversarial-reviewer", "senior-architect", "senior-qa", "tdd-guide",
    "senior-prompt-engineer", "spec-driven-workflow", "api-test-suite-builder",
    "ship-gate", "ci-cd-pipeline-builder", "skill-security-auditor", "skill-tester",
    "dependency-auditor", "migration-architect", "api-design-reviewer",
    "database-schema-designer", "agent-workflow-designer", "runbook-generator",
}  # fmt: skip
OWN_FILES = {"VENDORED.json", "LICENSE-vendored.md"}
FIELDS = {
    "path", "upstream", "marketplace", "marketplacePath", "sha256",
    "adapted", "licence", "licenceFile",
}  # fmt: skip
MAX_TOTAL_BYTES = 3 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024


def record(skill: Path) -> list[dict[str, object]]:
    return json.loads((skill / "VENDORED.json").read_text())


def lists_its_skill_md(skill: Path) -> bool:
    path = skill / "VENDORED.json"
    return path.is_file() and any(e["path"] == "SKILL.md" for e in json.loads(path.read_text()))


def tracked_skills(tree: Path) -> list[Path]:
    """Skill directories git tracks in `tree`, so local synced/ and .trash/ don't count."""
    # :(glob) keeps * inside one directory, so a fixture skill nested deeper,
    # such as skill-tester's sample-skill, doesn't count.
    pathspec = f":(glob){tree.relative_to(REPO_ROOT)}/*/SKILL.md"
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", pathspec],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted({(REPO_ROOT / line).parent for line in out.splitlines()})


def vendored_skills() -> list[Path]:
    return [s for s in tracked_skills(SKILLS) if lists_its_skill_md(s)]


def test_the_vendored_tree_holds_only_vendored_skills():
    assert [s.name for s in tracked_skills(SKILLS) if not lists_its_skill_md(s)] == []


def test_the_maintained_tree_holds_only_the_skills_maintained_here():
    own = tracked_skills(MAINTAINED)
    assert [s.name for s in own if lists_its_skill_md(s)] == []
    assert {s.name for s in own} == OWN_SKILLS


def test_nothing_outside_the_history_names_a_vendored_skills_old_path():
    # Eval records are written by eval.py, and eval_records.py verify reports a
    # stale one, so they are left to that check.
    names = "|".join(sorted(EXPECTED))
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "grep", "-l", "-E", f"config/skills/({names})([^a-z-]|$)",
         "--", ".", ":!docs/history", ":!config/vendored-skills", ":!eval/records"],
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert out.returncode == 1, f"old paths in: {out.stdout}"


def files_on_disk(skill: Path) -> set[str]:
    return {
        p.relative_to(skill).as_posix()
        for p in skill.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_kept_skills_are_the_vendored_skills():
    assert {s.name for s in vendored_skills()} == EXPECTED


def test_every_file_of_a_vendored_skill_is_recorded():
    for skill in vendored_skills():
        listed = {e["path"] for e in record(skill)}
        assert files_on_disk(skill) - OWN_FILES == listed, skill.name


def test_every_entry_has_its_provenance_fields():
    for skill in vendored_skills():
        for entry in record(skill):
            where = f"{skill.name}/{entry['path']}"
            extra = {"note", "localSha256"} if entry["adapted"] else set()
            assert set(entry) == FIELDS | extra, where
            upstream = entry["upstream"]
            assert isinstance(upstream, dict)
            assert set(upstream) == {"repo", "commit", "path"}, where
            assert upstream["repo"] == UPSTREAM_REPO, where
            assert len(upstream["commit"]) == 40, where
            assert entry["marketplace"] == "claude-code-skills", where
            assert entry["marketplacePath"] == upstream["path"], where
            assert entry["licence"] == "MIT", where
            assert entry["licenceFile"] == "LICENSE-vendored.md", where


def test_unadapted_files_are_byte_identical_to_upstream():
    for skill in vendored_skills():
        for entry in record(skill):
            if not entry["adapted"]:
                assert sha256(skill / str(entry["path"])) == entry["sha256"], (
                    f"{skill.name}/{entry['path']} differs from upstream but isn't marked adapted"
                )


def test_adapted_files_say_what_changed_and_match_their_local_hash():
    for skill in vendored_skills():
        for entry in record(skill):
            if entry["adapted"]:
                where = f"{skill.name}/{entry['path']}"
                assert str(entry["note"]).strip(), f"{where} is adapted but has no note"
                assert sha256(skill / str(entry["path"])) == entry["localSha256"], (
                    f"{where} changed since it was adapted; update its note and localSha256"
                )


def test_no_compiled_python_is_copied():
    for skill in vendored_skills():
        compiled = [p for p in skill.rglob("*") if p.suffix == ".pyc" or p.name == "__pycache__"]
        assert not compiled, compiled


def test_the_copies_stay_small():
    sizes = [p.stat().st_size for s in vendored_skills() for p in s.rglob("*") if p.is_file()]
    assert sum(sizes) <= MAX_TOTAL_BYTES
    assert max(sizes) <= MAX_FILE_BYTES


def test_each_licence_file_holds_the_mit_text():
    for skill in vendored_skills():
        text = (skill / "LICENSE-vendored.md").read_text()
        assert "MIT License" in text, skill.name
        assert "Permission is hereby granted, free of charge" in text, skill.name


def test_ruff_skips_the_vendored_tree_with_one_entry():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    excluded = config["tool"]["ruff"]["extend-exclude"]
    assert "./config/vendored-skills" in excluded
    per_skill = [e for e in excluded if re.fullmatch(r"\./config/(vendored-)?skills/[\w-]+", e)]
    assert per_skill == []
