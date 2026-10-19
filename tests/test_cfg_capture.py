"""cfg capture: live changes written into a feature-branch worktree, never committed."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.cfg_helpers import (
    commit_change,
    git,
    inventory,
    make_deploy_repo,
    make_home,
    make_live_install,
    write_tree,
)
from tools.cfg import capture, deploy, release
from tools.cfg.errors import CfgError

pytestmark = pytest.mark.usefixtures("cfg_isolation")

HOST = "hosts/test.json"


def clock() -> datetime:
    return datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_deploy_repo(tmp_path / "clone")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return make_home(tmp_path / "home")


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def branch(repo: Path, tmp_path: Path) -> Path:
    path = tmp_path / "feature"
    git(repo, "worktree", "add", "-q", "-b", "feature/9-capture", str(path), "HEAD")
    return path


@pytest.fixture
def deployed(repo: Path, home: Path, data: Path) -> Path:
    make_live_install(home, repo)
    deploy.deploy(repo, home, data, HOST, yes=True, clock=clock)
    return home


def fragment(tree: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((tree / "config" / "settings.fragment.json").read_text())
    return loaded


def set_live_plugin(home: Path, value: Any) -> None:
    s = json.loads((home / "settings.json").read_text())
    s["enabledPlugins"]["tools@market"] = value
    (home / "settings.json").write_text(json.dumps(s, indent=2) + "\n")


def test_capture_writes_every_kind_of_live_change_and_commits_nothing(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    home = deployed
    current = release.releases_dir(data) / (release.current_release(data) or "")
    (current / "config" / "CLAUDE.global.md").write_text("# edited through the link\n")
    (home / "skills" / "alpha").unlink()
    write_tree(
        home,
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\n---\nchanged\n",
            "skills/alpha/new.md": "added\n",
            "skills/alpha/__pycache__/x.pyc": "cache",
        },
    )
    os.symlink("SKILL.md", home / "skills" / "alpha" / "alias")
    set_live_plugin(home, False)
    head = git(branch, "rev-parse", "HEAD")
    before = inventory(home, data, skip=("cfg.lock",))

    result = capture.capture(repo, home, data, [], branch)

    assert (branch / "config" / "CLAUDE.global.md").read_text() == "# edited through the link\n"
    skill = branch / "config" / "skills" / "alpha"
    assert (skill / "SKILL.md").read_text().endswith("changed\n")
    assert (skill / "new.md").read_text() == "added\n"
    assert os.readlink(skill / "alias") == "SKILL.md"
    assert not (skill / "scripts" / "run.py").exists()
    assert not (skill / "__pycache__").exists()
    frag = fragment(branch)
    assert frag["enabledPlugins"]["tools@market"] is False
    commands = [h["command"] for g in frag["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert commands == ["python3 -I {claude_home}/hooks/guard.py"]
    assert git(branch, "rev-parse", "HEAD") == head
    assert inventory(home, data, skip=("cfg.lock",)) == before
    assert "config/skills/alpha/scripts/run.py" in result.deleted
    assert "config/settings.fragment.json" in result.written


def test_capture_takes_only_the_paths_named(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    current = release.releases_dir(data) / (release.current_release(data) or "")
    (current / "config" / "CLAUDE.global.md").write_text("# edited\n")
    set_live_plugin(deployed, False)
    result = capture.capture(repo, deployed, data, ["settings.json"], branch)
    assert result.written == ["config/settings.fragment.json"]
    assert (branch / "config" / "CLAUDE.global.md").read_text() == "# global\n"


def test_capture_of_a_first_deploys_differing_file_needs_a_host(
    repo: Path, home: Path, data: Path, branch: Path
) -> None:
    make_live_install(home, repo)
    (home / "CLAUDE.md").write_text("# live wording\n")
    with pytest.raises(CfgError, match="--host"):
        capture.capture(repo, home, data, ["CLAUDE.md"], branch)
    result = capture.capture(repo, home, data, ["CLAUDE.md"], branch, host=HOST)
    assert result.written == ["config/CLAUDE.global.md"]
    assert (branch / "config" / "CLAUDE.global.md").read_text() == "# live wording\n"


def test_a_deleted_file_in_the_release_is_deleted_on_the_branch(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    current = release.releases_dir(data) / (release.current_release(data) or "")
    (current / "config" / "skills" / "alpha" / "scripts" / "run.py").unlink()
    os.symlink("SKILL.md", current / "config" / "skills" / "alpha" / "link")
    (current / "config" / "skills" / "alpha" / "SKILL.md").unlink()
    os.symlink("link", current / "config" / "skills" / "alpha" / "SKILL.md")
    result = capture.capture(repo, deployed, data, ["skills/alpha"], branch)
    assert result.deleted == ["config/skills/alpha/scripts/run.py"]
    assert result.written == ["config/skills/alpha/SKILL.md", "config/skills/alpha/link"]
    assert os.readlink(branch / "config" / "skills" / "alpha" / "SKILL.md") == "link"
    assert not (branch / "config" / "skills" / "alpha" / "scripts" / "run.py").exists()


def test_a_removed_owned_setting_leaves_the_fragment(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    s = json.loads((deployed / "settings.json").read_text())
    del s["enabledPlugins"]["tools@market"]
    s["hooks"]["PreToolUse"] = []
    s["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"] = 9
    s["hooks"]["PostToolUse"][0]["hooks"][0]["args"] = [f"{deployed}/hooks/x"]
    (deployed / "settings.json").write_text(json.dumps(s, indent=2) + "\n")
    capture.capture(repo, deployed, data, ["settings.json"], branch)
    frag = fragment(branch)
    assert "tools@market" not in frag["enabledPlugins"]
    assert "PreToolUse" not in frag["hooks"]
    handler = frag["hooks"]["PostToolUse"][0]["hooks"][0]
    assert handler == {
        "type": "command",
        "command": "python3 -I {claude_home}/hooks/overlay.py",
        "timeout": 9,
        "args": ["{claude_home}/hooks/x"],
    }


def test_a_hook_group_keeps_its_other_handlers(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    frag = fragment(branch)
    frag["hooks"]["PreToolUse"][0]["hooks"].insert(0, {"type": "command", "command": "echo hi"})
    frag["hooks"]["PreToolUse"].insert(
        0, {"matcher": "Read", "hooks": [{"type": "command", "command": "r"}]}
    )
    frag["hooks"]["PreToolUse"].insert(
        1, {"matcher": "Bash", "hooks": [{"type": "command", "command": "first"}]}
    )
    write_tree(branch, {"config/settings.fragment.json": json.dumps(frag, indent=2) + "\n"})
    git(branch, "commit", "-q", "-am", "two handlers")
    s = json.loads((deployed / "settings.json").read_text())
    s["hooks"]["PreToolUse"][0]["hooks"] = [
        h for h in s["hooks"]["PreToolUse"][0]["hooks"] if "guard" not in h["command"]
    ]
    s["hooks"]["PreToolUse"][0]["hooks"].append({"type": "command", "command": "echo hi"})
    s["hooks"]["PreToolUse"].append(
        {"matcher": "Read", "hooks": [{"type": "command", "command": "r"}]}
    )
    s["hooks"]["PreToolUse"].append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "first"}]}
    )
    (deployed / "settings.json").write_text(json.dumps(s, indent=2) + "\n")
    capture.capture(repo, deployed, data, ["settings.json"], branch)
    assert fragment(branch)["hooks"]["PreToolUse"] == [
        {"matcher": "Read", "hooks": [{"type": "command", "command": "r"}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "first"}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]},
    ]


def test_nothing_to_capture_writes_nothing(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    result = capture.capture(repo, deployed, data, [], branch)
    assert result.written == [] and result.deleted == [] and result.suites == []


@pytest.mark.parametrize("where", ["main", "detached", "origin-head"])
def test_capture_refuses_the_default_branch_or_a_detached_head(
    repo: Path, deployed: Path, data: Path, branch: Path, where: str
) -> None:
    set_live_plugin(deployed, False)
    if where == "main":
        into = repo
    elif where == "detached":
        git(branch, "checkout", "-q", "--detach")
        into = branch
    else:
        git(
            repo,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/feature/9-capture",
        )
        into = branch
    with pytest.raises(CfgError, match="default branch|detached"):
        capture.capture(repo, deployed, data, [], into)
    assert fragment(into)["enabledPlugins"]["tools@market"] is True


def test_capture_refuses_to_overwrite_uncommitted_changes(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    set_live_plugin(deployed, False)
    frag = fragment(branch)
    frag["enabledPlugins"]["other@m"] = True
    write_tree(branch, {"config/settings.fragment.json": json.dumps(frag) + "\n"})
    with pytest.raises(CfgError, match="uncommitted"):
        capture.capture(repo, deployed, data, [], branch)
    assert "other@m" in fragment(branch)["enabledPlugins"]


def test_an_unknown_path_is_a_usage_error(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    with pytest.raises(CfgError, match="isn't a managed path"):
        capture.capture(repo, deployed, data, ["plugins"], branch)


def test_a_state_for_another_home_is_refused(
    repo: Path, deployed: Path, data: Path, branch: Path, tmp_path: Path
) -> None:
    with pytest.raises(CfgError, match="deployed to"):
        capture.capture(repo, tmp_path / "x", data, [], branch)


def test_capture_lists_the_eval_suites_the_change_needs(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    gates = {
        "suites": {
            "rubric": {"runner": "rubric", "paths": ["config/CLAUDE.global.md"], "harness": []},
            "hook-merge": {
                "runner": "hook-merge",
                "paths": ["config/hooks/guard.py"],
                "harness": [],
                "selectors": [
                    {"file": "config/settings.fragment.json", "pointer": "/hooks/PreToolUse"}
                ],
            },
        }
    }
    write_tree(branch, {"eval/gates.json": json.dumps(gates)})
    git(branch, "add", "-A")
    git(branch, "commit", "-q", "-m", "gates")
    current = release.releases_dir(data) / (release.current_release(data) or "")
    (current / "config" / "CLAUDE.global.md").write_text("# edited\n")
    assert capture.capture(repo, deployed, data, ["CLAUDE.md"], branch).suites == ["rubric"]
    git(branch, "checkout", "-q", "--", ".")
    set_live_plugin(deployed, False)
    result = capture.capture(repo, deployed, data, ["settings.json"], branch)
    assert result.suites == ["hook-merge"]


def test_a_missing_plain_path_or_a_foreign_link_has_nothing_to_capture(
    repo: Path, home: Path, data: Path, branch: Path
) -> None:
    make_live_install(home, repo)
    shutil.rmtree(home / "skills" / "beta")
    (home / "CLAUDE.md").unlink()
    os.symlink("/elsewhere", home / "CLAUDE.md")
    result = capture.capture(repo, home, data, [], branch, host=HOST)
    assert result.written == [] and result.deleted == []


def test_a_type_change_is_captured_as_the_live_type(
    repo: Path, home: Path, data: Path, branch: Path
) -> None:
    make_live_install(home, repo)
    (home / "hooks" / "guard.py").unlink()
    (home / "hooks" / "guard.py").mkdir()
    with pytest.raises(CfgError, match="type"):
        capture.capture(repo, home, data, ["hooks/guard.py"], branch, host=HOST)


def test_capture_of_a_newer_branch_still_finds_the_release_drift(
    repo: Path, deployed: Path, data: Path, branch: Path
) -> None:
    commit_change(branch, "config/skill-overlays/alpha.md", "newer\n")
    current = release.releases_dir(data) / (release.current_release(data) or "")
    (current / "config" / "skill-overlays" / "alpha.md").write_text("live edit\n")
    result = capture.capture(repo, deployed, data, ["skill-overlays"], branch)
    assert result.written == ["config/skill-overlays/alpha.md"]
    assert (branch / "config" / "skill-overlays" / "alpha.md").read_text() == "live edit\n"


def test_unchanged_files_in_a_plain_directory_are_not_rewritten(
    repo: Path, home: Path, data: Path, branch: Path
) -> None:
    make_live_install(home, repo)
    (home / "skills" / "alpha" / "SKILL.md").write_text("changed\n")
    result = capture.capture(repo, home, data, ["skills/alpha"], branch, host=HOST)
    assert result.written == ["config/skills/alpha/SKILL.md"]
