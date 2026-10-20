"""This repo's own payload passes the checker offline, as CI will run it."""

import json

import pytest

from tests.checker_helpers import REPO_ROOT, run_script

SCRIPT = REPO_ROOT / "tools" / "check_config.py"


@pytest.fixture
def empty_home(tmp_path):
    home = tmp_path / ".claude"
    (home / "plugins").mkdir(parents=True)
    return home


@pytest.mark.parametrize(
    "profile", sorted((REPO_ROOT / "hosts").glob("*.json")), ids=lambda p: p.name
)
@pytest.mark.parametrize("mode", ["report", "fail"])
def test_each_host_profile_passes_offline_with_no_home_or_scratch_paths(empty_home, profile, mode):
    args = [
        "--host", str(profile),
        "--plugin-index", str(REPO_ROOT / "plugins.lock.json"),
        "--claude-home", str(empty_home),
        "--path-rule", mode,
        "--json",
    ]  # fmt: skip
    r = run_script(SCRIPT, "", empty_home, args)
    rep = json.loads(r.stdout)
    assert r.returncode == 0, r.stdout
    assert rep["pathProblems"] == []
    rep["pathProblems"] = []
    assert rep | {"ok": True, "tokenCounts": []} == {
        "ok": True,
        "unresolvedSkills": [],
        "syncedSkills": [],
        "missingPaths": [],
        "clauseProblems": [],
        "forbiddenContent": [],
        "tokenCounts": [],
        "profileProblems": [],
        "pathProblems": [],
    }
