"""commit-check and pr-body-check list plain-prose's AI-writing tells without failing on them."""

import json
import shutil
import subprocess
import sys

import pytest
from conftest import SKILL_DIR, run, write

TELLING = (
    "feat(roster): add CSV export (#42)\n\n"
    "This seamless change leverages a robust pipeline to delve into exports.\n"
)
PLAIN = "feat(roster): add CSV export (#42)\n\nWrites the roster as CSV with a header row.\n"
PR_GOOD = "Writes the roster as CSV.\n\nCloses #42\nSpec: #42\n\n## Review\n- W1 fixed\n"
PR_TELLING = PR_GOOD.replace(
    "Writes the roster as CSV.", "This seamless change leverages a robust pipeline."
)


def commit_json(tmp_path, message, script=None):
    args = ["commit-check", "--message", write(tmp_path, "msg", message), "--json"]
    if script is None:
        return run(*args)
    return subprocess.run(
        [sys.executable, "-I", str(script), *map(str, args)], capture_output=True, text=True
    )


def test_tells_are_listed_and_the_check_still_passes(tmp_path):
    r = commit_json(tmp_path, TELLING)
    data = json.loads(r.stdout)
    assert r.returncode == 0 and data["ok"] is True
    kinds = {t["kind"] for t in data["tells"]}
    assert kinds and all(set(t) == {"line", "kind", "text", "fix"} for t in data["tells"])


def test_plain_message_has_no_tells(tmp_path):
    assert json.loads(commit_json(tmp_path, PLAIN).stdout)["tells"] == []


def test_text_output_adds_tell_lines_after_the_verdict(tmp_path):
    r = run("commit-check", "--message", write(tmp_path, "msg", TELLING))
    lines = r.stdout.splitlines()
    assert r.returncode == 0 and lines[0] == "OK"
    assert any(line.startswith("TELL ") for line in lines[1:])


def test_tells_never_turn_a_failing_check_into_a_pass(tmp_path):
    r = commit_json(tmp_path, TELLING.replace(" (#42)", ""))
    data = json.loads(r.stdout)
    assert r.returncode == 1 and data["ok"] is False and data["tells"]


@pytest.mark.parametrize("body,code", [(PR_TELLING, 0), (PR_TELLING.replace("## Review", ""), 1)])
def test_pr_body_check_lists_tells_without_changing_the_result(tmp_path, body, code):
    r = run("pr-body-check", "--body", write(tmp_path, "pr.md", body), "--issue", 42, "--json")
    data = json.loads(r.stdout)
    assert r.returncode == code and data["tells"]


def copy_skills(tmp_path, prose_check_source=None):
    """A skills folder holding this skill, and plain-prose unless prose_check_source is given
    ("" leaves plain-prose out; any other text replaces its prose_check.py)."""
    root = tmp_path / "skills"
    shutil.copytree(SKILL_DIR / "scripts", root / "github-issue-lifecycle" / "scripts")
    prose = SKILL_DIR.parent / "plain-prose"
    if prose_check_source != "":
        shutil.copytree(prose / "scripts", root / "plain-prose" / "scripts")
        shutil.copytree(prose / "references", root / "plain-prose" / "references")
        if prose_check_source is not None:
            (root / "plain-prose" / "scripts" / "prose_check.py").write_text(prose_check_source)
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache)
    return root, root / "github-issue-lifecycle" / "scripts" / "issue_lifecycle.py"


@pytest.mark.parametrize("source", ["", "raise RuntimeError('broken on purpose')\n"])
def test_tells_are_skipped_with_a_note_when_prose_check_is_missing_or_broken(tmp_path, source):
    _, script = copy_skills(tmp_path, source)
    for message, code in ((TELLING, 0), (TELLING.replace(" (#42)", ""), 1)):
        r = commit_json(tmp_path, message, script)
        data = json.loads(r.stdout)
        assert r.returncode == code
        assert data["tells"] is None and "not checked" in data["tellsNote"]


def test_loading_prose_check_writes_no_bytecode_cache(tmp_path):
    root, script = copy_skills(tmp_path)
    r = commit_json(tmp_path, TELLING, script)
    assert json.loads(r.stdout)["tells"]
    assert not list(root.rglob("__pycache__"))


def test_text_output_says_when_tells_were_not_checked(tmp_path):
    _, script = copy_skills(tmp_path, "")
    args = ["commit-check", "--message", write(tmp_path, "msg", TELLING)]
    r = subprocess.run([sys.executable, "-I", str(script), *map(str, args)],
                       capture_output=True, text=True)  # fmt: skip
    assert r.returncode == 0
    assert r.stdout.splitlines()[0] == "OK" and "NOTE tells not checked" in r.stdout
