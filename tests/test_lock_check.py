"""lock.py --check: compare the committed lock and vendored files with this machine."""

import hashlib
import json

import pytest

from tests.checker_helpers import GOOD_GLOBAL_MD, REPO_ROOT, make_repo, make_skill, run_tool

LOCK_SCRIPT = REPO_ROOT / "tools" / "cfg" / "lock.py"
VENDORED_REL = "skills/senior-qa/scripts/v.py"
VENDORED_PATH = "tools/vendor/claude-skills/v.py"  # as lock.py reports it, from the repo root


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
    assert rep == {"ok": True, "lock": [], "vendored": [], "upstream": []}
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


def test_added_and_removed_plugins_are_reported(machine):
    home, repo = machine
    make_skill(home, "new-bundle", "1.0.0", "fresh", [])
    lock = json.loads((repo / "plugins.lock.json").read_text())
    lock["plugins"]["gone@mkt"] = lock["plugins"]["engineering-skills@mkt"]
    (repo / "plugins.lock.json").write_text(json.dumps(lock))
    r, rep = run_check(home, repo)
    assert r.returncode == 1
    assert rep["lock"] == [
        "gone@mkt: in the lock but not installed",
        "new-bundle@mkt: installed but not in the lock",
    ]


def test_the_skills_this_account_syncs_are_not_compared_with_the_lock(machine):
    home, repo = machine
    synced = home / "skills" / "synced" / "uuid-1" / "pdf"
    synced.mkdir(parents=True)
    (synced / "SKILL.md").write_text("x")
    r, rep = run_check(home, repo)
    assert r.returncode == 0, r.stdout
    assert rep["lock"] == []


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
        {"path": VENDORED_PATH, "problem": "the installed copy has a different sha256"}
    ]


def test_a_missing_upstream_copy_or_plugin_is_reported(machine):
    home, repo = machine
    (install_dir(home) / VENDORED_REL).unlink()
    r, rep = run_check(home, repo)
    assert r.returncode == 1
    assert rep["vendored"] == [{"path": VENDORED_PATH, "problem": "the installed copy is missing"}]
    record = json.loads((repo / "tools/vendor/claude-skills/VENDORED.json").read_text())
    record[0]["plugin"] = "absent@mkt"
    (repo / "tools/vendor/claude-skills/VENDORED.json").write_text(json.dumps(record))
    r, rep = run_check(home, repo)
    assert rep["vendored"] == [
        {"path": VENDORED_PATH, "problem": "plugin absent@mkt isn't installed"}
    ]


def test_a_repo_without_vendored_files_checks_only_the_lock(machine):
    home, repo = machine
    (repo / "tools/vendor/claude-skills/VENDORED.json").unlink()
    r, rep = run_check(home, repo)
    assert r.returncode == 0 and rep["vendored"] == []


@pytest.mark.parametrize("content", ["[]", '{"lockVersion": 2, "plugins": {"a@m": 3}}'])
def test_a_committed_lock_of_the_wrong_shape_is_reported_not_a_crash(machine, content):
    home, repo = machine
    (repo / "plugins.lock.json").write_text(content)
    r, rep = run_check(home, repo)
    assert r.returncode == 1 and rep["lock"][0].startswith("plugins.lock.json: ")


def test_a_vendored_record_missing_a_field_is_a_usage_error(machine):
    home, repo = machine
    record_path = repo / "tools/vendor/claude-skills/VENDORED.json"
    record = json.loads(record_path.read_text())
    del record[0]["sha256"]
    record_path.write_text(json.dumps(record))
    r = run_lock(home, repo, "--check")
    assert r.returncode == 2 and "VENDORED.json" in r.stdout


MARKET_REL = "marketing-skill/skills/copy-editing/scripts/s.py"


def add_skill_vendoring(home, repo, files, tree="config/skills", name="prose"):
    """A skill whose VENDORED.json points at files in a marketplace clone."""
    skill = repo / tree / name
    clone = home / "plugins" / "marketplaces" / "claude-code-skills"
    record = []
    for name, content in files.items():
        (skill / "vendored").mkdir(parents=True, exist_ok=True)
        (skill / "vendored" / name).write_text(content)
        upstream = clone / MARKET_REL.replace("s.py", name)
        upstream.parent.mkdir(parents=True, exist_ok=True)
        upstream.write_text(content)
        record.append({
            "path": f"vendored/{name}",
            "marketplace": "claude-code-skills",
            "marketplacePath": MARKET_REL.replace("s.py", name),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        })  # fmt: skip
    (skill / "VENDORED.json").write_text(json.dumps(record))
    return clone


def test_marketplace_changes_are_listed_under_upstream_without_failing(machine):
    home, repo = machine
    clone = add_skill_vendoring(home, repo, {"a.py": "a", "b.py": "b", "c.py": "c"})
    (clone / MARKET_REL.replace("s.py", "a.py")).write_text("changed upstream")
    (clone / MARKET_REL.replace("s.py", "b.py")).unlink()
    r, rep = run_check(home, repo)
    assert r.returncode == 0, r.stdout
    assert rep["ok"] is True and rep["vendored"] == []
    assert rep["upstream"] == [
        {"path": "config/skills/prose/vendored/a.py",
         "problem": "the marketplace copy has a different sha256"},
        {"path": "config/skills/prose/vendored/b.py", "problem": "the marketplace copy is missing"},
    ]  # fmt: skip


def test_records_in_the_vendored_skill_tree_are_checked_too(machine):
    home, repo = machine
    clone = add_skill_vendoring(home, repo, {"a.py": "a"}, "config/vendored-skills", "copied")
    (clone / MARKET_REL.replace("s.py", "a.py")).write_text("changed upstream")
    r, rep = run_check(home, repo)
    assert r.returncode == 0, r.stdout
    assert rep["upstream"] == [
        {"path": "config/vendored-skills/copied/vendored/a.py",
         "problem": "the marketplace copy has a different sha256"},
    ]  # fmt: skip


def test_a_missing_marketplace_clone_reports_every_entry_as_missing(machine):
    home, repo = machine
    clone = add_skill_vendoring(home, repo, {"a.py": "a"})
    (clone / MARKET_REL.replace("s.py", "a.py")).unlink()
    r, rep = run_check(home, repo)
    assert r.returncode == 0
    assert rep["upstream"] == [
        {"path": "config/skills/prose/vendored/a.py", "problem": "the marketplace copy is missing"}
    ]


def test_plugin_entries_still_fail_while_marketplace_entries_only_report(machine):
    home, repo = machine
    add_skill_vendoring(home, repo, {"a.py": "a"})
    (install_dir(home) / VENDORED_REL).write_text("changed upstream")
    r, rep = run_check(home, repo)
    assert r.returncode == 1
    assert [v["path"] for v in rep["vendored"]] == [VENDORED_PATH]
    assert rep["upstream"] == []


def test_an_entry_with_neither_source_is_a_usage_error(machine):
    home, repo = machine
    add_skill_vendoring(home, repo, {"a.py": "a"})
    record_path = repo / "config/skills/prose/VENDORED.json"
    record = json.loads(record_path.read_text())
    del record[0]["marketplace"]
    record_path.write_text(json.dumps(record))
    r = run_lock(home, repo, "--check")
    assert r.returncode == 2 and "VENDORED.json" in r.stdout
