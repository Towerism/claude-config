"""spec-status reads a skill spec's approval from its Status line."""

import json

import pytest
from conftest import run, write


def status(tmp_path, text, *extra):
    return run("spec-status", "--spec", write(tmp_path, "SPEC.md", text), *extra)


@pytest.mark.parametrize(
    "line,word",
    [
        ('**Status:** Approved — "approve", 2026-10-18', "approved"),
        ("**Status:** approved by the owner", "approved"),
        ("**Status:** Draft — waiting for review", "draft"),
        ("**Status:** draft again (2026-10-18)", "draft"),
    ],
)
def test_approved_and_draft_lines_are_read(tmp_path, line, word):
    r = status(tmp_path, f"# Spec: foo\n\n**Author:** x\n{line}\n")
    assert (r.returncode, r.stdout.strip()) == (0, word)


def test_only_the_first_status_line_counts(tmp_path):
    text = "# Spec\n**Status:** Draft\n\n## History\n**Status:** Approved once\n"
    assert status(tmp_path, text).stdout.strip() == "draft"


@pytest.mark.parametrize(
    "text,code",
    [
        ("# Spec\n**Status:** In review\n", "unknown-status"),
        ("# Spec\nNo status here.\n", "no-status-line"),
        ("# Spec\n**Status:**\n", "unknown-status"),
    ],
)
def test_other_states_fail_the_check(tmp_path, text, code):
    r = status(tmp_path, text, "--json")
    data = json.loads(r.stdout)
    assert r.returncode == 1
    assert data["ok"] is False and data["status"] is None
    assert [p["code"] for p in data["problems"]] == [code]


def test_json_for_an_approved_spec(tmp_path):
    r = status(tmp_path, "**Status:** Approved\n", "--json")
    assert json.loads(r.stdout) == {"ok": True, "status": "approved", "problems": []}


def test_a_missing_file_is_a_usage_error(tmp_path):
    assert run("spec-status", "--spec", tmp_path / "nope.md").returncode == 2
