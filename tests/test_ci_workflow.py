"""Security and shape rules for .github/workflows/ci.yml (issue #10, ADR-0005, ADR-0007).

`problems` lists every rule the workflow breaks. The tests run it on the real file, which
must have none, and on broken copies, which must each be caught, so a rule can't silently
stop checking anything.
"""

from __future__ import annotations

import copy
import io
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The jobs the main ruleset will require; a job's id is its check name.
REQUIRED_CHECKS = ("changes", "hygiene", "commits", "lint", "test", "config", "eval-record")
ALLOWED_ACTIONS = {
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "actions/setup-python": ("5fda3b95a4ea91299a34e894583c3862153e4b97", "v7.0.0"),
}
HEAD_REF = "${{ github.event.pull_request.head.sha || github.sha }}"
# Only the jobs that install requirements-dev.txt cache pip, keyed on that hash-pinned file.
PIP_CACHED = {"lint", "test"}
PIP_CACHE = {"cache": "pip", "cache-dependency-path": "requirements-dev.txt"}
# A PR that changes only docs/ skips these; a failed changes job makes them "skipped",
# which a required check accepts, so changes is itself a required check.
CODE_GATED = {"lint", "test", "config"}
CODE_CONDITION = "needs.changes.outputs.code == 'true'"
PR_ONLY = {"commits": "github.event_name == 'pull_request'"}


def load(text: str) -> dict[str, Any]:
    yaml = YAML(typ="safe")
    data = yaml.load(io.StringIO(text))
    assert isinstance(data, dict)
    return data


def steps(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(job_id, step) for job_id, job in workflow["jobs"].items() for step in job["steps"]]


def problems(workflow: dict[str, Any], text: str) -> list[str]:
    found: list[str] = []
    main_only = {"branches": ["main"]}
    if workflow.get("on") != {"pull_request": main_only, "push": main_only}:
        found.append("triggers")
    if "pull_request_target" in text:
        found.append("pull_request_target")
    if workflow.get("permissions") != {"contents": "read"}:
        found.append("top-level permissions")
    if workflow.get("concurrency") != {
        "group": "ci-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }:
        found.append("concurrency")
    if workflow.get("defaults") != {"run": {"shell": "bash"}}:
        found.append("default shell")
    if tuple(workflow["jobs"]) != REQUIRED_CHECKS:
        found.append("job list")
    outputs = workflow["jobs"].get("changes", {}).get("outputs", {})
    if set(outputs) != {"base", "code"}:
        found.append("changes: outputs")
    for job_id, job in workflow["jobs"].items():
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        expected_if = CODE_CONDITION if job_id in CODE_GATED else PR_ONLY.get(job_id)
        if job.get("if") != expected_if:
            found.append(f"{job_id}: condition")
        if job_id != "changes" and "changes" not in needs:
            found.append(f"{job_id}: needs changes")
        if job.get("permissions", {"contents": "read"}) != {"contents": "read"}:
            found.append(f"{job_id}: permissions")
        if job.get("name", job_id) != job_id:
            found.append(f"{job_id}: name")
        if job.get("runs-on") != "ubuntu-latest":
            found.append(f"{job_id}: runs-on")
        if job.get("timeout-minutes") != 10:
            found.append(f"{job_id}: timeout")
    for job_id, step in steps(workflow):
        if "${{" in str(step.get("run", "")):
            found.append(f"{job_id}: expression in run")
        # bash -e ignores a failing command inside `echo "$(...)"`, so the step would pass.
        if re.search(r"\becho\b[^\n]*\$\(", str(step.get("run", ""))):
            found.append(f"{job_id}: command substitution inside echo")
        uses = step.get("uses")
        if uses is None:
            continue
        action, _, ref = uses.partition("@")
        if action not in ALLOWED_ACTIONS or ref != ALLOWED_ACTIONS[action][0]:
            found.append(f"{job_id}: uses {uses}")
            continue
        with_ = step.get("with", {})
        if action == "actions/checkout" and with_ != {
            "ref": HEAD_REF,
            "fetch-depth": 0,
            "persist-credentials": False,
        }:
            found.append(f"{job_id}: checkout options")
        version_file = with_.get("python-version-file")
        if action == "actions/setup-python" and version_file != ".python-version":
            found.append(f"{job_id}: python version")
        if action == "actions/setup-python":
            cache = {k: v for k, v in with_.items() if k.startswith("cache")}
            if cache != (PIP_CACHE if job_id in PIP_CACHED else {}):
                found.append(f"{job_id}: pip cache")
    for action, (sha, tag) in ALLOWED_ACTIONS.items():
        for line in text.splitlines():
            if f"uses: {action}@" in line and not line.rstrip().endswith(f"@{sha} # {tag}"):
                found.append(f"tag comment: {line.strip()}")
    return found


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_workflow_breaks_no_rule(text: str) -> None:
    assert problems(load(text), text) == []


def test_every_job_checks_out_the_pr_head_and_sets_up_python(text: str) -> None:
    workflow = load(text)
    for job_id, job in workflow["jobs"].items():
        actions = [step.get("uses", "").partition("@")[0] for step in job["steps"]]
        assert actions[:2] == ["actions/checkout", "actions/setup-python"], job_id


def _set(path: tuple[str, ...], value: object) -> Callable[[dict[str, Any]], None]:
    def change(workflow: dict[str, Any]) -> None:
        target = workflow
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return change


def _first_step(job: str, key: str, value: object) -> Callable[[dict[str, Any]], None]:
    def change(workflow: dict[str, Any]) -> None:
        workflow["jobs"][job]["steps"][0][key] = value

    return change


def _drop_checkout_option(option: str) -> Callable[[dict[str, Any]], None]:
    def change(workflow: dict[str, Any]) -> None:
        del workflow["jobs"]["test"]["steps"][0]["with"][option]

    return change


def _setup_python(workflow: dict[str, Any], job: str) -> dict[str, Any]:
    step = workflow["jobs"][job]["steps"][1]
    assert step["uses"].startswith("actions/setup-python@")
    with_: dict[str, Any] = step["with"]
    return with_


def _drop_setup_python_option(job: str, option: str) -> Callable[[dict[str, Any]], None]:
    def change(workflow: dict[str, Any]) -> None:
        _setup_python(workflow, job).pop(option, None)

    return change


def _set_setup_python_option(job: str, option: str, value: str) -> Callable[[dict[str, Any]], None]:
    def change(workflow: dict[str, Any]) -> None:
        _setup_python(workflow, job)[option] = value

    return change


def _add_job(workflow: dict[str, Any]) -> None:
    workflow["jobs"]["extra"] = copy.deepcopy(workflow["jobs"]["lint"])


def _run_with_expression(workflow: dict[str, Any]) -> None:
    workflow["jobs"]["commits"]["steps"].append(
        {"run": 'echo "${{ github.event.pull_request.title }}"'}
    )


BREAKAGES: dict[str, Callable[[dict[str, Any]], None]] = {
    "pull_request_target trigger": _set(("on",), {"pull_request_target": {}}),
    "a push to any branch": _set(("on", "push"), {}),
    "write-all permissions": _set(("permissions",), "write-all"),
    "a job that widens permissions": _set(("jobs", "lint", "permissions"), {"contents": "write"}),
    "no concurrency": _set(("concurrency",), None),
    "the default shell without pipefail": _set(("defaults",), None),
    "a job renamed": _set(("jobs", "test", "name"), "Tests"),
    "another runner": _set(("jobs", "test", "runs-on"), "self-hosted"),
    "no timeout": _set(("jobs", "test", "timeout-minutes"), None),
    "an action by tag": _first_step("test", "uses", "actions/checkout@v7"),
    "a third action": _first_step("test", "uses", "some/action@" + "a" * 40),
    "checkout keeping the token": _drop_checkout_option("persist-credentials"),
    "checkout of the merge commit": _drop_checkout_option("ref"),
    "a shallow checkout": _drop_checkout_option("fetch-depth"),
    "lint without the pip cache": _drop_setup_python_option("lint", "cache"),
    "a cache keyed on the wrong file": _set_setup_python_option(
        "test", "cache-dependency-path", "pyproject.toml"
    ),
    "a cache on a job that installs nothing": _set_setup_python_option("hygiene", "cache", "pip"),
    "test runs without the code condition": _set(("jobs", "test", "if"), None),
    "config needs only test": _set(("jobs", "config", "needs"), "test"),
    "hygiene gated on code": _set(("jobs", "hygiene", "if"), CODE_CONDITION),
    "eval-record gated on code": _set(("jobs", "eval-record", "if"), CODE_CONDITION),
    "commits on pushes too": _set(("jobs", "commits", "if"), None),
    "changes without the code output": _set(("jobs", "changes", "outputs"), {"base": "x"}),
    "a failure hidden inside echo": _first_step(
        "changes", "run", 'echo "base=$(python3 -I tools/ci.py base)" >> "$GITHUB_OUTPUT"'
    ),
    "an extra job": _add_job,
    "an expression inside run": _run_with_expression,
}


@pytest.mark.parametrize("breakage", sorted(BREAKAGES))
def test_each_rule_catches_a_broken_copy(text: str, breakage: str) -> None:
    workflow = copy.deepcopy(load(text))
    BREAKAGES[breakage](workflow)
    assert problems(workflow, text) != []


def test_a_missing_tag_comment_is_caught(text: str) -> None:
    sha = ALLOWED_ACTIONS["actions/checkout"][0]
    stripped = re.sub(rf"(@{sha}) # v\S+", r"\1", text)
    assert any(p.startswith("tag comment") for p in problems(load(stripped), stripped))


def test_the_workflow_matches_githubs_workflow_schema() -> None:
    tool = shutil.which("check-jsonschema")
    if tool is None:
        venv_tool = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "check-jsonschema"
        tool = str(venv_tool) if venv_tool.exists() else None
    assert tool, "check-jsonschema is missing; install requirements-dev.txt"
    result = subprocess.run(
        [tool, "--builtin-schema", "vendor.github-workflows", str(WORKFLOW)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
