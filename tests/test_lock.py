"""tools/cfg/lock.py: writing the plugin lock from the installed plugins."""

import json
import os

import pytest

from tests.checker_helpers import (
    GOOD_GLOBAL_MD,
    REPO_ROOT,
    make_repo,
    make_skill,
    run_script,
    run_tool,
)
from tools.cfg import lock as lock_module

LOCK_SCRIPT = REPO_ROOT / "tools" / "cfg" / "lock.py"
CHECK_SCRIPT = REPO_ROOT / "tools" / "check_config.py"


@pytest.fixture
def installed(claude_home, tmp_path):
    """Two plugins (one with an extra project-scope install), synced skills and a repo."""
    qa = make_skill(
        claude_home,
        "engineering-skills",
        "2.9.0",
        "senior-qa",
        [
            "references/testing_strategies.md",
            "scripts/__pycache__/x.pyc",
            ".pytest_cache/v",
            "a.pyc",
        ],
    )
    make_skill(claude_home, "engineering-skills", "2.9.0", "tdd-guide", ["scripts/run.py"])
    make_skill(
        claude_home, "other-bundle", "1.0.0", "other-skill", ["references/o.md"], marketplace="mkt2"
    )
    (qa.parent / "not-a-skill").mkdir()
    ip = claude_home / "plugins" / "installed_plugins.json"
    data = json.loads(ip.read_text())
    data["plugins"]["engineering-skills@mkt"][0].update(
        gitCommitSha="a" * 40,
        installedAt="2026-10-10T22:00:15.682Z",
        lastUpdated="2026-10-10T22:00:15.682Z",
    )
    data["plugins"]["engineering-skills@mkt"].append(
        {
            "scope": "project",
            "projectPath": str(tmp_path / "proj"),
            "installPath": str(tmp_path / "proj-install"),
            "version": "9.9.9",
        }
    )
    data["plugins"]["project-only@mkt"] = [
        {"scope": "project", "installPath": str(tmp_path / "proj-install"), "version": "1.0.0"}
    ]
    ip.write_text(json.dumps(data))
    (claude_home / "plugins" / "known_marketplaces.json").write_text(
        json.dumps(
            {
                "mkt": {
                    "source": {"source": "github", "repo": "owner/mkt"},
                    "installLocation": str(claude_home / "plugins" / "marketplaces" / "mkt"),
                }
            }
        )
    )
    synced = claude_home / "skills" / "synced" / "uuid-1"
    (synced / "skill-creator").mkdir(parents=True)
    (synced / "skill-creator" / "SKILL.md").write_text("x")
    (synced / "manifest.json").write_text("{}")
    repo = tmp_path / "repo"
    make_repo(repo)
    (repo / "config" / "CLAUDE.global.md").write_text(
        GOOD_GLOBAL_MD + "- Then `tdd-guide`, and maybe `other-skill`.\n"
    )
    (repo / "config" / "skill-overlays" / "engineering-skills:senior-qa.md").write_text(
        "Read `references/testing_strategies.md`."
    )
    return claude_home, repo


def run_lock(home, repo, *extra):
    args = ["--claude-home", str(home), "--repo-root", str(repo), *extra]
    return run_tool(LOCK_SCRIPT, args)


EXPECTED = {
    "lockVersion": 1,
    "plugins": {
        "engineering-skills@mkt": {
            "marketplace": "mkt",
            "marketplaceRepo": "owner/mkt",
            "marketplaceSource": "github",
            "version": "2.9.0",
            "gitCommitSha": "a" * 40,
            "skills": ["senior-qa", "tdd-guide"],
            "files": {
                "senior-qa": ["SKILL.md", "references/testing_strategies.md"],
                "tdd-guide": ["SKILL.md", "scripts/run.py"],
            },
        },
        "other-bundle@mkt2": {
            "marketplace": "mkt2",
            "marketplaceRepo": None,
            "marketplaceSource": None,
            "version": "1.0.0",
            "gitCommitSha": None,
            "skills": ["other-skill"],
            "files": {},
        },
    },
    "synced": ["skill-creator"],
}


def test_the_lock_records_user_installs_skills_synced_names_and_referenced_files(installed):
    home, repo = installed
    r = run_lock(home, repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((repo / "plugins.lock.json").read_text()) == EXPECTED


def test_the_lock_is_byte_identical_across_runs_and_holds_no_paths_or_times(installed, tmp_path):
    home, repo = installed
    run_lock(home, repo)
    first = (repo / "plugins.lock.json").read_bytes()
    run_lock(home, repo, "--out", str(tmp_path / "again.json"))
    assert (tmp_path / "again.json").read_bytes() == first
    text = first.decode()
    assert text == json.dumps(EXPECTED, indent=2, sort_keys=True) + "\n"
    assert str(tmp_path) not in text and "installedAt" not in text and "installPath" not in text


def test_a_lock_written_by_lock_py_lets_the_checker_pass_with_no_plugins_installed(
    installed, tmp_path
):
    home, repo = installed
    # Name only skills this profile can reach; other-skill's plugin isn't in it.
    (repo / "config" / "CLAUDE.global.md").write_text(GOOD_GLOBAL_MD + "- Then `tdd-guide`.\n")
    assert run_lock(home, repo).returncode == 0
    empty = tmp_path / "empty-home" / ".claude"
    (empty / "plugins").mkdir(parents=True)
    args = [
        "--host",
        str(repo / "hosts" / "a.json"),
        "--plugin-index",
        str(repo / "plugins.lock.json"),
        "--claude-home",
        str(empty),
        "--json",
    ]
    r = run_tool(CHECK_SCRIPT, args)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_failed_build_leaves_the_existing_lock_alone(installed):
    home, repo = installed
    (repo / "plugins.lock.json").write_text("previous\n")
    install = home / "plugins" / "cache" / "mkt2" / "other-bundle" / "1.0.0"
    for p in sorted(install.rglob("*"), reverse=True):
        p.rmdir() if p.is_dir() else p.unlink()
    install.rmdir()
    r = run_lock(home, repo)
    assert r.returncode == 2 and "other-bundle@mkt2" in r.stdout
    assert (repo / "plugins.lock.json").read_text() == "previous\n"
    assert sorted(p.name for p in repo.iterdir()) == ["config", "hosts", "plugins.lock.json"]


def test_two_user_installs_of_one_plugin_are_refused(installed):
    home, repo = installed
    ip = home / "plugins" / "installed_plugins.json"
    data = json.loads(ip.read_text())
    entry = data["plugins"]["other-bundle@mkt2"]
    entry.append(dict(entry[0]))
    ip.write_text(json.dumps(data))
    r = run_lock(home, repo)
    assert r.returncode == 2 and "other-bundle@mkt2" in r.stdout


@pytest.mark.parametrize("content", [None, "{not json", "[]"])
def test_unreadable_installed_plugins_is_refused(installed, content):
    home, repo = installed
    ip = home / "plugins" / "installed_plugins.json"
    if content is None:
        ip.unlink()
    else:
        ip.write_text(content)
    r = run_lock(home, repo)
    assert r.returncode == 2 and "installed_plugins.json" in r.stdout
    assert not (repo / "plugins.lock.json").exists()


def test_no_synced_directory_gives_an_empty_synced_list(installed):
    home, repo = installed
    for p in sorted((home / "skills" / "synced").rglob("*"), reverse=True):
        p.rmdir() if p.is_dir() else p.unlink()
    (home / "skills" / "synced").rmdir()
    assert run_lock(home, repo).returncode == 0
    assert json.loads((repo / "plugins.lock.json").read_text())["synced"] == []


def test_a_broken_host_profile_is_refused(installed):
    home, repo = installed
    (repo / "hosts" / "a.json").write_text("{not json")
    r = run_lock(home, repo)
    assert r.returncode == 2 and "a.json" in r.stdout


def test_lock_py_runs_on_its_own_under_python_isolated(installed):
    home, repo = installed
    args = ["--claude-home", str(home), "--repo-root", str(repo)]
    r = run_script(LOCK_SCRIPT, "", home, args)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((repo / "plugins.lock.json").read_text()) == EXPECTED


def test_a_failed_rename_removes_the_temp_file_and_keeps_the_old_lock(tmp_path, monkeypatch):
    target = tmp_path / "plugins.lock.json"
    target.write_text("previous\n")

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        lock_module.write_atomically(target, "new\n")
    assert target.read_text() == "previous\n"
    assert [p.name for p in tmp_path.iterdir()] == ["plugins.lock.json"]


def test_a_plugin_without_a_skills_directory_has_no_skills(installed):
    home, repo = installed
    install = home / "plugins" / "cache" / "mkt2" / "other-bundle" / "1.0.0"
    for p in sorted((install / "skills").rglob("*"), reverse=True):
        p.rmdir() if p.is_dir() else p.unlink()
    (install / "skills").rmdir()
    assert run_lock(home, repo).returncode == 0
    lock = json.loads((repo / "plugins.lock.json").read_text())
    assert lock["plugins"]["other-bundle@mkt2"]["skills"] == []


def test_a_user_install_without_an_install_path_is_refused(installed):
    home, repo = installed
    ip = home / "plugins" / "installed_plugins.json"
    data = json.loads(ip.read_text())
    del data["plugins"]["other-bundle@mkt2"][0]["installPath"]
    ip.write_text(json.dumps(data))
    r = run_lock(home, repo)
    assert r.returncode == 2 and "other-bundle@mkt2" in r.stdout
