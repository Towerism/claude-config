"""The gitea-issue-lifecycle package: shape, shared script, access rules, registration.

Every check reads files in the repo. Nothing here runs `tea`, touches a network or reads the clock.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = SKILL_DIR.parent
REPO_ROOT = SKILLS_DIR.parent.parent
SHARED_SCRIPT = SKILLS_DIR / "github-issue-lifecycle" / "scripts" / "issue_lifecycle.py"

SKILL = (SKILL_DIR / "SKILL.md").read_text()
COMMANDS = (SKILL_DIR / "references" / "gitea-commands.md").read_text()
FRONT = SKILL.split("---")[1]
LABELS = [
    "in progress",
    "draft",
    "approved",
    "hotfix",
    "approval: manual",
    "approval: adversarial",
    "approval: no-review",
]
MODES = [
    "Manual review with adversarial pass",
    "Adversarial approval",
    "No review",
]


def frontmatter(key):
    match = re.search(rf"^{key}: (.+)$", FRONT, re.M)
    return match.group(1) if match else None


def fenced_lines(text):
    """Lines inside ``` fences, stripped."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
        elif inside and line.strip():
            lines.append(line.strip())
    return lines


def test_frontmatter_names_the_skill_and_its_neighbours():
    assert frontmatter("name") == "gitea-issue-lifecycle"
    desc = frontmatter("description")
    assert len(desc) <= 1024
    assert "Gitea" in desc
    assert "does not write spec content" in desc
    assert "github-issue-lifecycle" in desc
    assert "jira-ticket-lifecycle" in desc


def test_skill_md_stays_short():
    assert len(SKILL.splitlines()) <= 250


@pytest.mark.parametrize(
    "rel",
    [
        "SKILL.md",
        "SPEC.md",
        "REVIEW-NOTE.md",
        "references/gitea-commands.md",
        "assets/pr-body-template.md",
        "evals/trigger-evals.json",
    ],
)
def test_package_files_exist(rel):
    assert (SKILL_DIR / rel).is_file()


def test_every_reference_is_linked_from_skill_md():
    for ref in (SKILL_DIR / "references").glob("*.md"):
        assert f"references/{ref.name}" in SKILL, ref.name


def test_no_home_or_scratch_path_in_the_package():
    # Built from parts so this file does not contain the paths it looks for.
    bad_paths = ("/" + "home/", "/" + "Users/", "/" + "tmp/")
    for path in SKILL_DIR.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json", ".py"}:
            text = path.read_text()
            for bad in bad_paths:
                assert bad not in text, f"{path.name} contains {bad}"


def test_the_package_has_no_script_of_its_own():
    assert not (SKILL_DIR / "scripts").exists()


def test_shared_script_exists_and_every_named_subcommand_is_real():
    assert SHARED_SCRIPT.is_file()
    helped = subprocess.run(
        [sys.executable, "-I", str(SHARED_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    named = {
        "extract",
        "replace",
        "branch-name",
        "commit-check",
        "pr-body-check",
        "diff-check",
        "spec-status",
    }
    for name in named:
        assert name in SKILL, name
        assert name in helped, name


def test_every_script_path_in_skill_md_points_at_the_github_skills_script():
    for match in re.finditer(r"(\S*)issue_lifecycle\.py", SKILL):
        assert match.group(1).endswith("github-issue-lifecycle/scripts/"), match.group(0)


def test_git_workflow_gh_examples_are_declared_replaced():
    assert "github-issue-lifecycle/references/git-workflow.md" in SKILL
    section = SKILL[SKILL.index("git-workflow.md") :]
    assert re.search(r"gh pr (create|merge)", section)
    assert "replace" in section.lower()


@pytest.mark.parametrize("doc", [SKILL, COMMANDS], ids=["SKILL.md", "gitea-commands.md"])
def test_gitea_is_reached_only_through_tea_api_with_a_login(doc):
    for line in fenced_lines(doc):
        assert not line.startswith(("curl", "wget", "gh ")), line
        if "tea " in line:
            assert re.search(r"tea api\b", line), line
            assert "--login" in line, line


def test_access_rules_are_stated():
    for phrase in [
        "-X GET user",
        "--login",
        "unverified",
        "letters, digits",
        "a file",
    ]:
        assert phrase in SKILL, phrase
    assert "data" in SKILL.lower() and "ignored" in SKILL.lower()


def test_unverified_commands_are_flagged_in_the_reference_too():
    assert "unverified" in COMMANDS.lower()


def test_labels_modes_and_spec_markers_are_named():
    for label in LABELS:
        assert label in SKILL, label
    positions = [SKILL.index(mode) for mode in MODES]
    assert positions == sorted(positions)
    assert "default" in SKILL[positions[0] : positions[0] + 120].lower()
    for phrase in ["timeline", "spec:start", "spec:end", "approved", "mktemp -d"]:
        assert phrase in SKILL, phrase


def test_git_and_pr_rules_are_stated():
    for phrase in [
        "branch-name",
        "commit-check",
        "diff-check",
        "pr-body-check",
        "Closes #",
        "hotfix",
        "writing-docs",
    ]:
        assert phrase in SKILL, phrase


def test_merge_waits_for_a_chat_confirmation_and_never_squashes():
    assert "AskUserQuestion" in SKILL
    assert re.search(r"merge", SKILL) and '"merge"' in SKILL and '"rebase"' in SKILL
    assert re.search(r"never[^.\n]*squash", SKILL, re.I)
    assert re.search(r"declin|no answer", SKILL, re.I)
    assert "no other" in SKILL.lower()


def test_trigger_queries_cover_both_directions():
    queries = json.loads((SKILL_DIR / "evals" / "trigger-evals.json").read_text())
    assert all(set(q) == {"query", "should_trigger"} for q in queries)
    assert sum(q["should_trigger"] for q in queries) >= 6
    assert sum(not q["should_trigger"] for q in queries) >= 6


@pytest.mark.parametrize("host", ["personal", "work"])
def test_host_profiles_list_the_skill_beside_the_script_it_shares(host):
    skills = json.loads((REPO_ROOT / "hosts" / f"{host}.json").read_text())["skills"]
    assert "gitea-issue-lifecycle" in skills
    assert "github-issue-lifecycle" in skills


def test_architecture_page_has_a_row_for_the_skill():
    page = (REPO_ROOT / "docs" / "architecture" / "skills.md").read_text()
    assert "| `gitea-issue-lifecycle` |" in page


def test_ci_runs_this_packages_tests():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "pytest config/skills/gitea-issue-lifecycle/tests" in ci
