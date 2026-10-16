"""This repo's own payload passes the checker offline, as CI will run it."""

import json

import pytest

from tests.checker_helpers import REPO_ROOT, run_script

SCRIPT = REPO_ROOT / "tools" / "check_config.py"
# The lifecycle skill's spec and behaviour evals still name scratch and home
# paths; #4 removes them and then switches the documented command to fail mode.
PENDING = {
    "config/skills/github-issue-lifecycle/SPEC.md",
    "config/skills/github-issue-lifecycle/evals/run_behaviour_evals.py",
}


@pytest.fixture
def empty_home(tmp_path):
    home = tmp_path / ".claude"
    (home / "plugins").mkdir(parents=True)
    return home


@pytest.mark.parametrize(
    "profile", sorted((REPO_ROOT / "hosts").glob("*.json")), ids=lambda p: p.name
)
@pytest.mark.parametrize("mode,code", [("report", 0), ("fail", 1)])
def test_each_host_profile_passes_offline_apart_from_pending_paths(empty_home, profile, mode, code):
    args = [
        "--host", str(profile),
        "--plugin-index", str(REPO_ROOT / "plugins.lock.json"),
        "--claude-home", str(empty_home),
        "--path-rule", mode,
        "--json",
    ]  # fmt: skip
    r = run_script(SCRIPT, "", empty_home, args)
    rep = json.loads(r.stdout)
    assert r.returncode == code, r.stdout
    assert rep["pathProblems"] and {p["file"] for p in rep["pathProblems"]} <= PENDING
    rep["pathProblems"] = []
    assert rep | {"ok": True, "tokenCounts": []} == {
        "ok": True,
        "unresolvedSkills": [],
        "missingPaths": [],
        "clauseProblems": [],
        "forbiddenContent": [],
        "tokenCounts": [],
        "profileProblems": [],
        "pathProblems": [],
    }
