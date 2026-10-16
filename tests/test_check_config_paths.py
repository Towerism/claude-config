"""The path rule: no home-directory or scratch paths in tools/, config/ or eval/."""

import json

import pytest

from tests.checker_helpers import REPO_ROOT, make_lock, make_repo, make_skill, run_tool
from tools import check_config

SCRIPT = REPO_ROOT / "tools" / "check_config.py"
# Built from parts so that this file reads the same as the files it plants.
HOME_PATH = "/" + "home" + "/x/notes"
SCRATCH = "dev" + "/" + "scratch"


@pytest.fixture
def repo(claude_home, tmp_path):
    """A repo whose profile passes, with offending lines planted in and out of scope."""
    root = tmp_path / "repo"
    profile = make_repo(root)
    make_skill(claude_home, "engineering-skills", "2.9.0", "senior-qa", [])
    lock = make_lock(
        tmp_path / "plugins.lock.json", {"engineering-skills@mkt": {"skills": ["senior-qa"]}}
    )
    (root / "tools").mkdir()
    (root / "tools" / "a.py").write_text(f"ok = 1\npath = '{HOME_PATH}'\n")
    (root / "eval").mkdir()
    (root / "eval" / "b.json").write_text(f'["{SCRATCH}"]\n')
    # Out of scope: tests/, a virtualenv, caches and symlinks.
    for skipped in [
        "tests/c.py",
        "tools/.venv/d.py",
        "tools/__pycache__/e.pyc",
        "config/.pytest_cache/f",
    ]:
        (root / skipped).parent.mkdir(parents=True, exist_ok=True)
        (root / skipped).write_text(f"{HOME_PATH} {SCRATCH}\n")
    outside = tmp_path / "outside.txt"
    outside.write_text(f"{HOME_PATH}\n")
    (root / "config" / "link.txt").symlink_to(outside)
    (root / "config" / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    return claude_home, profile, lock


def check(home, profile, lock, *extra):
    args = [
        "--host",
        str(profile),
        "--plugin-index",
        str(lock),
        "--claude-home",
        str(home),
        "--json",
    ]
    r = run_tool(SCRIPT, [*args, *extra])
    return r, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else None)


EXPECTED = [
    {"file": "eval/b.json", "line": 1, "match": SCRATCH},
    {"file": "tools/a.py", "line": 2, "match": "/" + "home" + "/"},
]


def test_fail_mode_reports_in_scope_lines_and_fails(repo):
    r, rep = check(*repo, "--path-rule", "fail")
    assert r.returncode == 1
    assert rep["pathProblems"] == EXPECTED and rep["ok"] is False


def test_report_mode_lists_the_same_lines_but_passes(repo):
    r, rep = check(*repo, "--path-rule", "report")
    assert r.returncode == 0, r.stdout + r.stderr
    assert rep["pathProblems"] == EXPECTED and rep["ok"] is True


def test_without_the_rule_nothing_is_scanned(repo):
    r, rep = check(*repo)
    assert r.returncode == 0 and rep["pathProblems"] == []


def test_a_binary_file_is_scanned_by_line(repo):
    home, profile, lock = repo
    (profile.parent.parent / "config" / "blob.bin").write_bytes(
        b"\x00\xff\n\x01\n" + SCRATCH.encode() + b"\x00\n"
    )
    r, rep = check(home, profile, lock, "--path-rule", "fail")
    assert {"file": "config/blob.bin", "line": 3, "match": SCRATCH} in rep["pathProblems"]


@pytest.mark.parametrize("mode", [["--path-rule", "strict"], ["--path-rule"]])
def test_an_unknown_or_missing_mode_is_a_usage_error(repo, mode):
    r, _ = check(*repo, *mode)
    assert r.returncode == 2


def test_the_checker_source_does_not_trip_its_own_rule():
    assert not [p for p in check_config.scan_paths(REPO_ROOT) if p["file"].startswith("tools/")]
