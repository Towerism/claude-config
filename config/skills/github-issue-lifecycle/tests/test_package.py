"""Package shape and the claim block run against a fake gh."""

import re
from pathlib import Path

import pytest
from conftest import SKILL_DIR

SKILL = (SKILL_DIR / "SKILL.md").read_text()
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


def frontmatter(key):
    match = re.search(rf"^{key}: (.+)$", FRONT, re.M)
    return match.group(1) if match else None


def test_frontmatter_name_and_description():
    assert frontmatter("name") == "github-issue-lifecycle"
    desc = frontmatter("description")
    assert len(desc) <= 1024
    assert "issue" in desc and "PR" in desc
    assert "does not write spec content" in desc


def test_skill_md_is_short():
    assert len(SKILL.splitlines()) <= 300


@pytest.mark.parametrize(
    "rel",
    [
        "SKILL.md",
        "references/git-workflow.md",
        "scripts/issue_lifecycle.py",
        "evals/evals.json",
        "evals/trigger-evals.json",
        "evals/run_behaviour_evals.py",
        "evals/run_trigger_evals.py",
        "expected_outputs",
        "README.md",
        "assets/pr-body-template.md",
        "examples",
        "references/gh-commands.md",
    ],
)
def test_bundle_files_exist(rel):
    assert (SKILL_DIR / rel).exists()


def test_every_reference_is_linked_from_skill_md():
    for ref in (SKILL_DIR / "references").iterdir():
        assert f"references/{ref.name}" in SKILL


def test_git_reference_credits_source_and_drops_rejected_parts():
    text = (SKILL_DIR / "references/git-workflow.md").read_text()
    assert (
        text.startswith("> Adapted from")
        and "MIT License, Copyright (c) 2025 Alireza Rezvani" in text
    )
    for banned in ("--squash", "git tag v", "reset --hard", "push --force"):
        assert banned not in text
    assert len(text.splitlines()) < 300


def claim_block():
    blocks = re.findall(r"```bash\n(.*?)```", SKILL, re.S)
    return next(b for b in blocks if 'gh label create "in progress"' in b)


def test_claim_is_idempotent_and_assigns(gh):
    gh.labels_exist()
    for _ in range(2):
        result = gh.run_bash("set -e\n" + claim_block())
        assert result.returncode == 0, result.stderr
    calls = gh.calls()
    requested = {c[2] for c in calls if c[:2] == ["label", "create"]}
    assert requested == set(LABELS)
    assert (
        calls.count(["issue", "edit", "42", "--add-assignee", "@me", "--add-label", "in progress"])
        == 2
    )


def test_claim_on_a_fresh_repo(gh):
    result = gh.run_bash("set -e\n" + claim_block())
    assert result.returncode == 0
    assert sum(1 for c in gh.calls() if c[:2] == ["label", "create"]) == len(LABELS)


def test_every_eval_expectation_is_a_valid_pattern():
    import json

    cases = json.loads((SKILL_DIR / "evals/evals.json").read_text())["evals"]
    for case in cases:
        for exp in case["expectations"] + case.get("turn1_expectations", []):
            kind, _, arg = exp.partition(": ")
            parts = arg.split(" ||| ") if kind == "gh-order" else arg.split(" ::: ")
            for part in parts:
                re.compile(part.split(" ", 1)[1] if kind == "gh-count" else part)


def test_eval_harness_refuses_a_temp_root_under_home(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "harness", SKILL_DIR / "evals/run_behaviour_evals.py"
    )
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    monkeypatch.setattr(harness.tempfile, "gettempdir", lambda: str(Path.home() / "tmp"))
    with pytest.raises(SystemExit):
        harness.eval_tmp_root()
    monkeypatch.setattr(harness.tempfile, "gettempdir", lambda: "/tmp")
    assert harness.eval_tmp_root() == "/tmp"


def test_one_match_search_case_states_issue_intent():
    import json

    cases = {c["id"]: c for c in json.loads((SKILL_DIR / "evals/evals.json").read_text())["evals"]}
    assert cases[2]["turns"] == ["pick up the issue about the export bug"]


def _harness():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "harness", SKILL_DIR / "evals/run_behaviour_evals.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_routed_bare_request_case_uses_the_global_config():
    import json

    cases = {c["id"]: c for c in json.loads((SKILL_DIR / "evals/evals.json").read_text())["evals"]}
    case = cases[23]
    assert case["turns"] == ["fix the export bug"] and case["global_config"] is True
    kinds = {e.split(": ", 1)[0] for e in case["expectations"]}
    assert {"skill-loaded", "gh-called", "tool-not-used"} <= kinds


def test_grader_checks_skills_loaded_and_tools_used():
    h = _harness()
    graded = h.grade(
        ["skill-loaded: github-issue-lifecycle", "tool-not-used: ^(Write|Edit)$"],
        [],
        [],
        "",
        [],
        loaded=["github-issue-lifecycle"],
        tools=["Bash", "Skill"],
    )
    assert all(r["passed"] for r in graded)
    graded = h.grade(
        ["skill-loaded: github-issue-lifecycle", "tool-not-used: ^(Write|Edit)$"],
        [],
        [],
        "",
        [],
        loaded=[],
        tools=["Edit"],
    )
    assert not any(r["passed"] for r in graded)
