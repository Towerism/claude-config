"""cfg status, plan and diff against real temp repos, homes and releases."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tests.cfg_helpers import (
    EMPTY_LOCK,
    commit_change,
    deploy_with_library,
    git,
    make_home,
    make_repo,
    write_tree,
)
from tools.cfg import drift, state
from tools.cfg.errors import CfgError

pytestmark = pytest.mark.usefixtures("cfg_isolation")

GUARD_KEY_SUFFIX = "/hooks/guard.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "clone")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return make_home(tmp_path / "home")


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def deployed(repo: Path, home: Path, data: Path) -> state.State:
    return deploy_with_library(repo, home, data)


def kinds(result: drift.StatusResult) -> list[tuple[str, str | None]]:
    return [(f.kind, f.path) for f in result.findings]


def release_of(data: Path, st: state.State) -> Path:
    return data / "releases" / st.release


def guard_key(home: Path) -> str:
    return f"hooks/PreToolUse/Bash/python3 -I {home}{GUARD_KEY_SUFFIX}"


# ---- status ---------------------------------------------------------------------


def test_a_fresh_deployment_is_clean(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    result = drift.status(repo, data, home)
    assert result.result == "clean"
    assert result.findings == [] and result.warnings == []
    assert (result.sha, result.release) == (deployed.sha, deployed.release)


def test_no_state_file_means_not_deployed(repo: Path, home: Path, data: Path) -> None:
    result = drift.status(repo, data, home)
    assert result.result == "not-deployed"
    assert kinds(result) == [("not-deployed", None)]


def test_an_edit_in_the_release_is_drift(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    (home / "skills" / "alpha" / "SKILL.md").write_text("edited through the link\n")
    result = drift.status(repo, data, home)
    assert result.result == "not-clean"
    assert kinds(result) == [("drift", "config/skills/alpha/SKILL.md")]


def test_a_link_replaced_by_a_plain_file_is_unlinked(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    (home / "CLAUDE.md").unlink()
    (home / "CLAUDE.md").write_text("# saved by an editor\n")
    assert kinds(drift.status(repo, data, home)) == [("unlinked", "CLAUDE.md")]


@pytest.mark.parametrize(
    "breakage",
    [
        lambda home: (home / "hooks" / "guard.py").unlink(),
        lambda home: (
            (home / "hooks" / "guard.py").unlink(),
            (home / "hooks" / "guard.py").symlink_to("/old/guard.py"),
        ),
    ],
    ids=["removed", "points-elsewhere"],
)
def test_a_missing_or_foreign_link_is_broken(
    repo: Path, home: Path, data: Path, deployed: state.State, breakage: object
) -> None:
    breakage(home)  # type: ignore[operator]
    assert kinds(drift.status(repo, data, home)) == [("broken", "hooks/guard.py")]


def test_a_changed_owned_setting_is_settings_drift(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    settings = json.loads((home / "settings.json").read_text())
    settings["enabledPlugins"]["tools@market"] = False
    settings["hooks"]["PreToolUse"] = []
    (home / "settings.json").write_text(json.dumps(settings))
    result = drift.status(repo, data, home)
    assert kinds(result) == [
        ("settings-drift", "enabledPlugins/tools@market"),
        ("settings-drift", guard_key(home)),
    ]
    assert "gone" in result.findings[1].detail


def test_main_moving_ahead_is_behind(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    commit_change(repo, "config/CLAUDE.global.md", "# v2\n")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert kinds(drift.status(repo, data, home)) == [("behind", None)]


def test_a_missing_current_or_release_is_broken(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    (data / "current").unlink()
    result = drift.status(repo, data, home)
    assert ("broken", "current") in kinds(result)
    (data / "current").mkdir()
    assert ("broken", "current") in kinds(drift.status(repo, data, home))
    (data / "current").rmdir()
    (data / "current").symlink_to("releases/other")
    assert "doesn't point at" in drift.status(repo, data, home).findings[0].detail


def test_a_release_directory_that_is_gone_is_broken(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    git(repo, "worktree", "remove", "--force", str(release_of(data, deployed)))
    result = drift.status(repo, data, home)
    assert result.result == "not-clean"
    assert ("broken", "current") in kinds(result)
    assert any("is gone" in f.detail for f in result.findings)


def test_warnings_never_make_status_unclean(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    write_tree(home, {"skills/synced/abc/alpha/SKILL.md": "synced alpha\n"})
    installed = {"plugins": {}}
    write_tree(home, {"plugins/installed_plugins.json": json.dumps(installed)})
    lock = {**EMPTY_LOCK, "plugins": {"gone@m": {"version": "1"}}}
    (release_of(data, deployed) / "plugins.lock.json").write_text(json.dumps(lock))
    git(release_of(data, deployed), "update-index", "--assume-unchanged", "plugins.lock.json")
    git(repo, "checkout", "-q", "--orphan", "other")
    git(repo, "commit", "-q", "--allow-empty", "-m", "unrelated")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    result = drift.status(repo, data, home)
    assert result.result == "clean"
    assert [w.kind for w in result.warnings] == ["off-tip", "plugin-lock", "synced-collision"]
    assert "gone@m" in result.warnings[1].detail
    assert "alpha" in result.warnings[2].detail


def test_a_missing_origin_main_and_unreadable_lock_are_warnings(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    (home / "plugins" / "installed_plugins.json").write_text("{not json")
    result = drift.status(repo, data, home)
    assert result.result == "clean"
    assert [w.kind for w in result.warnings] == ["off-tip", "plugin-lock"]
    assert "could not compare" in result.warnings[1].detail


def test_status_refuses_another_claude_home(
    repo: Path, home: Path, data: Path, deployed: state.State, tmp_path: Path
) -> None:
    with pytest.raises(CfgError, match="was deployed to"):
        drift.status(repo, data, tmp_path / "other" / ".claude")


# ---- plan -----------------------------------------------------------------------


def make_plan_home(home: Path, data: Path) -> None:
    """One managed path of each kind, and settings with one owned hook missing."""
    write_tree(
        home,
        {
            "CLAUDE.md": "# global\n",
            "skills/alpha/SKILL.md": "---\nname: alpha\n---\n",
            "skills/alpha/scripts/run.py": "print('edited')\n",
            "skills/alpha/extra.md": "mine\n",
        },
    )
    (home / "hooks").mkdir()
    (home / "hooks" / "overlay.py").symlink_to(f"{data}/current/config/hooks/overlay.py")
    (home / "hooks" / "guard.py").symlink_to("/old/guard.py")
    settings = {
        "theme": "dark",
        "enabledPlugins": {"tools@market": True},
        "extraKnownMarketplaces": {"market": {"source": {"source": "github", "repo": "o/r"}}},
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Skill",
                    "hooks": [
                        {"type": "command", "command": f"python3 -I {home}/hooks/overlay.py"}
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 -I {home}/hooks/guard.py",
                            "timeout": 5,
                        }
                    ],
                }
            ],
        },
    }
    (home / "settings.json").write_text(json.dumps(settings))


def test_plan_reports_each_kind_and_would_stop_on_differences(
    repo: Path, home: Path, data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_plan_home(home, data)
    own_tmp = tmp_path / "tmp"
    own_tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(own_tmp))
    monkeypatch.setattr(tempfile, "tempdir", None)
    result = drift.plan(repo, None, "hosts/test.json", home, data)

    assert result.ref == git(repo, "rev-parse", "HEAD")
    assert {p.live: p.state for p in result.paths} == {
        "CLAUDE.md": "match",
        "skill-overlays": "missing",
        "hooks/overlay.py": "linked",
        "hooks/guard.py": "foreign-link",
        "skills/alpha": "differs",
        "skills/beta": "missing",
    }
    assert {i.key: i.state for i in result.settings} == {
        "enabledPlugins/tools@market": "same",
        "extraKnownMarketplaces/market": "same",
        f"hooks/PostToolUse/Skill/python3 -I {home}/hooks/overlay.py": "same",
        guard_key(home): "differs",
    }
    assert result.would_back_up == ["CLAUDE.md", "hooks/guard.py", "skills/alpha"]
    assert result.would_stop is True
    texts = dict(result.diffs)
    assert "-print('alpha')" in texts["skills/alpha"]
    assert "+print('edited')" in texts["skills/alpha"]
    assert "only in the live copy: extra.md" in texts["skills/alpha"]
    assert '"timeout": 5' in texts[guard_key(home)]
    assert os.listdir(own_tmp) == []


def test_plan_on_a_matching_home_would_not_stop(repo: Path, home: Path, data: Path) -> None:
    deploy_with_library(repo, home, data)
    result = drift.plan(repo, "HEAD", "hosts/test.json", home, data)
    assert {p.state for p in result.paths} == {"linked"}
    assert {i.state for i in result.settings} == {"same"}
    assert (result.would_back_up, result.would_stop, result.diffs) == ([], False, [])


def test_plan_lists_owned_items_left_behind_and_missing_live(
    repo: Path, home: Path, data: Path
) -> None:
    st = deploy_with_library(repo, home, data)
    leftover = "enabledPlugins/old@market"
    state.save(data, state.State(**{**vars(st), "settings": {**st.settings, leftover: True}}))
    settings = json.loads((home / "settings.json").read_text())
    settings["enabledPlugins"] = {"old@market": True}
    (home / "settings.json").write_text(json.dumps(settings))
    result = drift.plan(repo, None, "hosts/test.json", home, data)
    states = {i.key: i.state for i in result.settings}
    assert states["enabledPlugins/tools@market"] == "missing-live"
    assert states[leftover] == "extra-live"
    assert result.would_stop is False


def test_plan_shows_type_and_binary_differences(repo: Path, home: Path, data: Path) -> None:
    (home / "CLAUDE.md").mkdir()
    write_tree(home, {"skills/alpha/SKILL.md": "---\nname: alpha\n---\n"})
    (home / "skills" / "alpha" / "scripts").mkdir()
    (home / "skills" / "alpha" / "scripts" / "run.py").write_bytes(b"\xff\xfe binary")
    (home / "skills" / "beta").write_text("a file where a directory belongs\n")
    result = drift.plan(repo, None, "hosts/test.json", home, data)
    texts = dict(result.diffs)
    assert "type differs" in texts["CLAUDE.md"]
    assert "type differs" in texts["skills/beta"]
    assert "binary files differ: scripts/run.py" in texts["skills/alpha"]


def test_plan_shows_link_text_and_missing_files_in_a_directory(
    repo: Path, home: Path, data: Path
) -> None:
    write_tree(home, {"skills/alpha/SKILL.md": "---\nname: alpha\n---\n"})
    (home / "skills" / "alpha" / "scripts").mkdir()
    (home / "skills" / "alpha" / "scripts" / "run.py").symlink_to("other.py")
    text = dict(drift.plan(repo, None, "hosts/test.json", home, data).diffs)["skills/alpha"]
    assert "scripts/run.py: link to other.py in the live copy" in text


def test_plan_without_origin_main_asks_for_a_ref(repo: Path, home: Path, data: Path) -> None:
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    with pytest.raises(CfgError, match="--ref"):
        drift.plan(repo, None, "hosts/test.json", home, data)


def test_plan_refuses_a_temp_directory_inside_the_claude_home(
    repo: Path, home: Path, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TMPDIR", str(home))
    monkeypatch.setattr(tempfile, "tempdir", None)
    with pytest.raises(CfgError, match="TMPDIR"):
        drift.plan(repo, None, "hosts/test.json", home, data)
    assert not [p for p in home.iterdir() if p.name.startswith("cfg-plan-")]


# ---- diff -----------------------------------------------------------------------


def make_drift(home: Path) -> None:
    (home / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    (home / "skills" / "alpha" / "notes.md").write_text("new file\n")
    (home / "skills" / "alpha" / "scripts" / "run.py").unlink()
    (home / "hooks" / "guard.py").unlink()
    (home / "hooks" / "guard.py").write_text("print('mine')\n")


def test_diff_shows_drift_unlinked_and_settings(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    make_drift(home)
    settings = json.loads((home / "settings.json").read_text())
    settings["enabledPlugins"]["tools@market"] = False
    (home / "settings.json").write_text(json.dumps(settings))
    texts = dict(drift.diff(repo, data, home))
    assert "+edited" in texts["config/skills/alpha/SKILL.md"]
    assert "+new file" in texts["config/skills/alpha/notes.md"]
    assert "-print('alpha')" in texts["config/skills/alpha/scripts/run.py"]
    assert "+print('mine')" in texts["hooks/guard.py"]
    assert "-true" in texts["enabledPlugins/tools@market"]


def test_diff_limited_to_one_managed_path(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    make_drift(home)
    assert [p for p, _ in drift.diff(repo, data, home, "hooks/guard.py")] == ["hooks/guard.py"]
    assert [p for p, _ in drift.diff(repo, data, home, "skills/alpha")] == [
        "config/skills/alpha/SKILL.md",
        "config/skills/alpha/notes.md",
        "config/skills/alpha/scripts/run.py",
    ]
    assert drift.diff(repo, data, home, "settings.json") == []


def test_diff_refuses_an_unknown_path_or_no_state(
    repo: Path, home: Path, data: Path, deployed: state.State, tmp_path: Path
) -> None:
    with pytest.raises(CfgError, match="isn't a managed path"):
        drift.diff(repo, data, home, "nope")
    with pytest.raises(CfgError, match="cfg plan"):
        drift.diff(repo, tmp_path / "empty", home)


def test_plan_names_files_missing_from_a_live_directory(repo: Path, home: Path, data: Path) -> None:
    (home / "skills" / "beta").mkdir(parents=True)
    (home / "skills" / "beta" / "notes.md").write_text("mine\n")
    text = dict(drift.plan(repo, None, "hosts/test.json", home, data).diffs)["skills/beta"]
    assert "missing from the live copy: SKILL.md" in text
    assert "only in the live copy: notes.md" in text


def test_plan_cleans_up_when_the_commit_cannot_be_read(
    repo: Path, home: Path, data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repo, "rm", "-q", "-r", "hosts")
    git(repo, "commit", "-q", "-m", "no hosts")
    own_tmp = tmp_path / "tmp"
    own_tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(own_tmp))
    monkeypatch.setattr(tempfile, "tempdir", None)
    with pytest.raises(CfgError, match="git archive"):
        drift.plan(repo, "HEAD", "hosts/test.json", home, data)
    assert os.listdir(own_tmp) == []


def test_plan_refuses_a_ref_that_is_not_a_commit(repo: Path, home: Path, data: Path) -> None:
    with pytest.raises(CfgError, match="isn't a commit"):
        drift.plan(repo, "nope", "hosts/test.json", home, data)


def test_diff_shows_a_binary_edit_in_the_release(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    (home / "skills" / "alpha" / "SKILL.md").write_bytes(b"\xff\xfe")
    texts = dict(drift.diff(repo, data, home))
    assert (
        texts["config/skills/alpha/SKILL.md"]
        == "binary files differ: config/skills/alpha/SKILL.md\n"
    )


def test_diff_after_the_release_is_gone_still_runs(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    git(repo, "worktree", "remove", "--force", str(release_of(data, deployed)))
    assert drift.diff(repo, data, home) == []


def test_diff_shows_drift_outside_the_managed_paths_that_status_reports(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    (release_of(data, deployed) / "README-local.md").write_text("note\n")
    assert ("drift", "README-local.md") in kinds(drift.status(repo, data, home))
    assert "+note" in dict(drift.diff(repo, data, home))["README-local.md"]


def test_an_edited_profile_in_the_release_is_drift_not_a_crash(
    repo: Path, home: Path, data: Path, deployed: state.State
) -> None:
    (release_of(data, deployed) / "hosts" / "test.json").write_text("{broken")
    result = drift.status(repo, data, home)
    assert kinds(result) == [("drift", "hosts/test.json")]
    assert "hosts/test.json" in dict(drift.diff(repo, data, home))
