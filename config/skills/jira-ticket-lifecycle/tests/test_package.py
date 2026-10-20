"""Package shape, and no value from a real Jira project anywhere in the skill."""

import re

import pytest
from conftest import SKILL_DIR

SKILL = (SKILL_DIR / "SKILL.md").read_text()
FRONT = SKILL.split("---")[1]


def frontmatter(key):
    match = re.search(rf"^{key}: (.+)$", FRONT, re.M)
    return match.group(1) if match else None


def test_frontmatter_name_and_description():
    assert frontmatter("name") == "jira-ticket-lifecycle"
    desc = frontmatter("description")
    assert len(desc) <= 1024
    for word in ("Jira", "ticket", "PR", "github-issue-lifecycle", "does not write spec content"):
        assert word in desc


def test_skill_md_is_short():
    assert len(SKILL.splitlines()) <= 300


@pytest.mark.parametrize(
    "rel",
    [
        "SKILL.md",
        "SPEC.md",
        "REVIEW-NOTE.md",
        "README.md",
        "scripts/ticket_lifecycle.py",
        "references/jira-commands.md",
        "references/settings.md",
        "assets/pr-body-template.md",
        "examples/sample-local.json",
        "examples/sample-commit-message.txt",
        "examples/sample-pr-body.md",
        "expected_outputs/commit-check-sample.json",
        "evals/trigger-evals.json",
        "evals/evals.json",
        "evals/run_trigger_evals.py",
        "evals/run_behaviour_evals.py",
        "evals/fake_cli.py",
    ],
)
def test_bundle_files_exist(rel):
    assert (SKILL_DIR / rel).is_file()


def test_every_reference_and_the_template_are_linked_from_skill_md():
    for ref in (SKILL_DIR / "references").iterdir():
        assert f"](references/{ref.name})" in SKILL
    assert "](assets/pr-body-template.md)" in SKILL
    assert "../github-issue-lifecycle/references/git-workflow.md" in SKILL
    assert (
        SKILL_DIR.parent / "github-issue-lifecycle" / "references" / "git-workflow.md"
    ).is_file()


KEY = re.compile(r"\b([A-Z][A-Z0-9_]{1,9})-[0-9]+\b")
URL = re.compile(r"https?://([A-Za-z0-9.-]+)")
EMAIL = re.compile(r"\b[\w.+-]+@([A-Za-z0-9.-]+\.[a-z]{2,})\b")
# Requirement IDs and similar tokens that look like keys but aren't Jira projects.
NOT_PROJECTS = {
    "FR",
    "NFR",
    "AC",
    "EC",
    "OS",
    "ADR",
    "GH",
    "UTF",
    "SHA",
    "ISO",
    "RFC",
    "D",
    "Q",
    "W",
    "C",
    "N",
    "H",
    "S",
    "E",
}
HOSTS = {
    "example.atlassian.net",
    "github.com",
    "x.example",
    "evil.example",
    "other.example.net",
    "127.0.0.1",
    "127.0.0.2",
    "www.w3.org",
    "example.com",
    "claude.com",
    "keepachangelog.com",
    "semver.org",
}


def package_files():
    for path in SKILL_DIR.rglob("*"):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in (".md", ".py", ".json", ".txt")
        ):
            yield path


def test_only_invented_project_values_appear():
    problems = []
    for path in package_files():
        text = re.sub(
            r"\\+\.", ".", path.read_text()
        )  # a regex such as github\.com names a host too
        for project in {m.group(1) for m in KEY.finditer(text)} - NOT_PROJECTS - {"PAY"}:
            problems.append(f"{path.name}: key prefix {project}")
        for host in {m.group(1).rstrip(".") for m in URL.finditer(text)} - HOSTS:
            problems.append(f"{path.name}: host {host}")
        for domain in {m.group(1) for m in EMAIL.finditer(text)} - {
            "example.com",
            "anthropic.com",
            "example.invalid",
        }:
            problems.append(f"{path.name}: email domain {domain}")
    assert problems == []
