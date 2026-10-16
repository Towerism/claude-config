"""lock.py --check: compare the committed lock and vendored files with this machine."""

import hashlib
import json

import pytest

from tests.checker_helpers import GOOD_GLOBAL_MD, REPO_ROOT, make_repo, make_skill, run_tool

LOCK_SCRIPT = REPO_ROOT / "tools" / "cfg" / "lock.py"
VENDORED_REL = "skills/senior-qa/scripts/v.py"


@pytest.fixture
def machine(claude_home, tmp_path):
    """A repo with a committed lock and a vendored file that match the home."""
    make_skill(claude_home, "engineering-skills", "2.9.0", "senior-qa", ["scripts/v.py"])
    repo = tmp_path / "repo"
    make_repo(repo)
    (repo / "config" / "CLAUDE.global.md").write_text(GOOD_GLOBAL_MD)
    vendor = repo / "tools" / "vendor" / "claude-skills"
    vendor.mkdir(parents=True)
    (vendor / "v.py").write_text("x")
    record = {
        "path": "v.py",
        "plugin": "engineering-skills@mkt",
        "installedPath": VENDORED_REL,
        "sha256": hashlib.sha256(b"x").hexdigest(),
    }
    (vendor / "VENDORED.json").write_text(json.dumps([record]))
    r = run_lock(claude_home, repo)
    assert r.returncode == 0, r.stdout
    return claude_home, repo


def run_lock(home, repo, *extra):
    return run_tool(LOCK_SCRIPT, ["--claude-home", str(home), "--repo-root", str(repo), *extra])


def run_check(home, repo):
    r = run_lock(home, repo, "--check")
    return r, json.loads(r.stdout)


def install_dir(home):
    return home / "plugins" / "cache" / "mkt" / "engineering-skills" / "2.9.0"


def test_a_matching_machine_passes_and_nothing_is_written(machine):
    home, repo = machine
    before = sorted((p.name, p.read_bytes()) for p in repo.iterdir() if p.is_file())
    r, rep = run_check(home, repo)
    assert r.returncode == 0, r.stdout
    assert rep == {"ok": True, "lock": [], "vendored": []}
    assert sorted((p.name, p.read_bytes()) for p in repo.iterdir() if p.is_file()) == before


def test_a_changed_plugin_version_is_reported(machine):
    home, repo = machine
    ip = home / "plugins" / "installed_plugins.json"
    data = json.loads(ip.read_text())
    data["plugins"]["engineering-skills@mkt"][0]["version"] = "3.0.0"
    ip.write_text(json.dumps(data))
    r, rep = run_check(home, repo)
    assert r.returncode == 1
    assert rep["lock"] == ["engineering-skills@mkt: version was '2.9.0', now '3.0.0'"]


def test_added_and_removed_plugins_and_synced_skills_are_reported(machine):
    home, repo = machine
    make_skill(home, "new-bundle", "1.0.0", "fresh", [])
    synced = home / "skills" / "synced" / "uuid-1" / "pdf"
    synced.mkdir(parents=True)
    (synced / "SKILL.md").write_text("x")
    lock = json.loads((repo / "plugins.lock.json").read_text())
    lock["plugins"]["gone@mkt"] = lock["plugins"]["engineering-skills@mkt"]
    (repo / "plugins.lock.json").write_text(json.dumps(lock))
    r, rep = run_check(home, repo)
    assert r.returncode == 1
    assert rep["lock"] == [
        "gone@mkt: in the lock but not installed",
        "new-bundle@mkt: installed but not in the lock",
        "synced: was [], now ['pdf']",
    ]


def test_a_missing_or_unreadable_lock_is_reported(machine):
    home, repo = machine
    (repo / "plugins.lock.json").write_text("{not json")
    r, rep = run_check(home, repo)
    assert r.returncode == 1 and rep["lock"][0].startswith("plugins.lock.json: ")
    (repo / "plugins.lock.json").unlink()
    r, rep = run_check(home, repo)
    assert r.returncode == 1 and rep["lock"][0].startswith("plugins.lock.json: ")


def test_a_changed_upstream_copy_of_a_vendored_file_is_reported(machine):
    home, repo = machine
    (install_dir(home) / VENDORED_REL).write_text("changed upstream")
    r, rep = run_check(home, repo)
    assert r.returncode == 1
    assert rep["vendored"] == [
        {"path": "v.py", "problem": "the installed copy has a different sha256"}
    ]


def test_a_missing_upstream_copy_or_plugin_is_reported(machine):
    home, repo = machine
    (install_dir(home) / VENDORED_REL).unlink()
    r, rep = run_check(home, repo)
    assert r.returncode == 1
    assert rep["vendored"] == [{"path": "v.py", "problem": "the installed copy is missing"}]
    record = json.loads((repo / "tools/vendor/claude-skills/VENDORED.json").read_text())
    record[0]["plugin"] = "absent@mkt"
    (repo / "tools/vendor/claude-skills/VENDORED.json").write_text(json.dumps(record))
    r, rep = run_check(home, repo)
    assert rep["vendored"] == [{"path": "v.py", "problem": "plugin absent@mkt isn't installed"}]


def test_a_repo_without_vendored_files_checks_only_the_lock(machine):
    home, repo = machine
    (repo / "tools/vendor/claude-skills/VENDORED.json").unlink()
    r, rep = run_check(home, repo)
    assert r.returncode == 0 and rep["vendored"] == []
