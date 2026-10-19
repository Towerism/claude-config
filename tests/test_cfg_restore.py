"""restore.py, the standalone undo written into every deploy backup.

These tests build a backup directory by hand, the way a deploy leaves one,
so each restore rule is checked on its own. tests/test_cfg_deploy.py checks
restore against real deploys, and runs restore.sh with the repo moved away.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tools.cfg import restore, state

pytestmark = pytest.mark.usefixtures("cfg_isolation")

GUARD = {"type": "command", "command": "python3 -I /h/hooks/guard.py"}
GUARD_KEY = "hooks/PreToolUse/Bash/python3 -I /h/hooks/guard.py"


def clock() -> datetime:
    return datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)


def snapshot(backup: Path) -> Path:
    return backup / "restore-20261019T120000Z"


class Install:
    """A Claude home after a first deploy, with its backup and data directory."""

    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.data = root / "data"
        self.backup = self.home / "backups" / "claude-config" / "20261019T110000Z"
        self.home.mkdir(parents=True)
        self.data.mkdir()
        self.backup.mkdir(parents=True)
        self.originals = {
            "CLAUDE.md": b"# original\n",
            "skills/alpha/SKILL.md": b"alpha\n",
            "skills/alpha/scripts/run.py": b"print(1)\n",
        }
        for rel, data in self.originals.items():
            path = self.backup / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        os.symlink("../elsewhere", self.backup / "skills" / "alpha" / "old-link")
        entries = [
            state.ManifestEntry(rel, sha256=state.sha256_file(self.backup / rel))
            for rel in self.originals
        ]
        entries.append(state.ManifestEntry("skills/alpha/old-link", link="../elsewhere"))
        state.write_manifest(self.backup / "manifest.sha256", entries)
        self.links = {
            "CLAUDE.md": f"{self.data}/current/config/CLAUDE.global.md",
            "skills/alpha": f"{self.data}/current/config/skills/alpha",
            "hooks/guard.py": f"{self.data}/current/config/hooks/guard.py",
        }
        (self.home / "skills").mkdir()
        (self.home / "hooks").mkdir()
        for live, target in self.links.items():
            os.symlink(target, self.home / live)
        self.settings_before: dict[str, Any] = {
            "theme": "dark",
            "enabledPlugins": {"tools@market": False},
        }
        self.settings_after: dict[str, Any] = {
            "theme": "dark",
            "enabledPlugins": {"tools@market": True},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [GUARD]}]},
        }
        self.write_settings(self.settings_after)
        (self.data / "state.json").write_text("{}\n")
        self.meta: dict[str, Any] = {
            "schema": 1,
            "claudeHome": str(self.home),
            "dataDir": str(self.data),
            "firstDeploy": True,
            "moved": ["CLAUDE.md", "skills/alpha"],
            "links": [{"live": k, "target": v} for k, v in self.links.items()],
            "removedLinks": [],
            "settings": [
                {"key": "enabledPlugins/tools@market", "before": False, "after": True},
                {"key": GUARD_KEY, "after": GUARD},
            ],
        }
        self.save_meta()

    def save_meta(self) -> None:
        (self.backup / "restore.json").write_text(json.dumps(self.meta, indent=2))

    def write_settings(self, value: dict[str, Any]) -> None:
        (self.home / "settings.json").write_text(json.dumps(value, indent=2) + "\n")

    def settings(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads((self.home / "settings.json").read_text())
        return loaded

    def run(self, **kwargs: Any) -> restore.Report:
        return restore.run(self.backup, clock=clock, **kwargs)


def tree(root: Path) -> dict[str, str]:
    """Every entry under root: file bytes as hex, links as their text, dirs as 'dir'."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = "link:" + os.readlink(path)
        elif path.is_file():
            out[rel] = path.read_bytes().hex()
        else:
            out[rel] = "dir"
    return out


@pytest.fixture
def install(tmp_path: Path) -> Install:
    return Install(tmp_path)


def test_restore_puts_every_original_back_and_removes_only_its_own_links(
    install: Install,
) -> None:
    report = install.run()
    assert report.exit == 0, report.left_alone
    for rel, data in install.originals.items():
        assert (install.home / rel).read_bytes() == data
    assert os.readlink(install.home / "skills" / "alpha" / "old-link") == "../elsewhere"
    assert not (install.home / "hooks" / "guard.py").exists()
    assert install.settings() == install.settings_before
    assert not (install.data / "state.json").exists()
    assert (snapshot(install.backup) / "state.json").read_text() == "{}\n"


def test_running_restore_again_changes_nothing(install: Install) -> None:
    install.run()
    before = tree(install.home.parent)
    report = install.run()
    assert report.exit == 0, report.left_alone
    assert tree(install.home.parent) == before


def test_restore_reverts_a_replaced_hook_handler_to_the_one_before(install: Install) -> None:
    old = {**GUARD, "timeout": 30}
    install.settings_before["hooks"] = {"PreToolUse": [{"matcher": "Bash", "hooks": [old]}]}
    install.meta["settings"] = [{"key": GUARD_KEY, "before": old, "after": GUARD}]
    install.save_meta()
    assert install.run().exit == 0
    assert install.settings()["hooks"]["PreToolUse"][0]["hooks"] == [old]


def test_restore_puts_back_a_handler_the_deploy_removed(install: Install) -> None:
    install.write_settings({"theme": "dark"})
    install.meta["settings"] = [{"key": GUARD_KEY, "before": GUARD}]
    install.save_meta()
    assert install.run().exit == 0
    assert install.settings()["hooks"] == {"PreToolUse": [{"matcher": "Bash", "hooks": [GUARD]}]}


def test_restore_adds_a_removed_handler_to_an_existing_group(install: Install) -> None:
    other = {"type": "command", "command": "other"}
    install.write_settings({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [other]}]}})
    install.meta["settings"] = [{"key": GUARD_KEY, "before": GUARD}]
    install.save_meta()
    assert install.run().exit == 0
    assert install.settings()["hooks"]["PreToolUse"][0]["hooks"] == [other, GUARD]


def test_restore_removes_a_hook_group_and_event_it_emptied(install: Install) -> None:
    other = {"type": "command", "command": "other"}
    install.write_settings(
        {
            "hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [GUARD]}],
                "PostToolUse": [{"hooks": [other]}],
            }
        }
    )
    install.meta["settings"] = [{"key": GUARD_KEY, "after": GUARD}]
    install.save_meta()
    assert install.run().exit == 0
    assert install.settings() == {"hooks": {"PostToolUse": [{"hooks": [other]}]}}


def test_restore_keeps_a_group_that_still_holds_the_owners_handler(install: Install) -> None:
    other = {"type": "command", "command": "other"}
    install.write_settings(
        {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [GUARD, other]}]}}
    )
    install.meta["settings"] = [{"key": GUARD_KEY, "after": GUARD}]
    install.save_meta()
    assert install.run().exit == 0
    assert install.settings() == {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [other]}]}}


def test_restore_handles_a_handler_in_a_group_without_a_matcher(install: Install) -> None:
    key = "hooks/UserPromptExpansion//python3 -I /h/hooks/overlay.py"
    handler = {"type": "command", "command": "python3 -I /h/hooks/overlay.py"}
    install.write_settings({})
    install.meta["settings"] = [{"key": key, "before": handler}]
    install.save_meta()
    assert install.run().exit == 0
    assert install.settings() == {"hooks": {"UserPromptExpansion": [{"hooks": [handler]}]}}


def test_restore_leaves_later_changes_alone_and_reports_them(install: Install) -> None:
    (install.home / "CLAUDE.md").unlink()
    (install.home / "CLAUDE.md").write_text("# typed by hand\n")
    (install.backup / "skills" / "alpha" / "SKILL.md").write_text("tampered\n")
    settings = install.settings()
    settings["enabledPlugins"]["tools@market"] = "owner's choice"
    install.write_settings(settings)

    report = install.run()

    assert report.exit == 1
    reported = {item["path"] for item in report.left_alone}
    assert {"CLAUDE.md", "skills/alpha", "enabledPlugins/tools@market"} <= reported
    assert (snapshot(install.backup) / "CLAUDE.md").read_text() == "# typed by hand\n"
    assert (install.home / "CLAUDE.md").read_bytes() == install.originals["CLAUDE.md"]
    assert (install.backup / "skills" / "alpha" / "SKILL.md").read_text() == "tampered\n"
    assert not (install.home / "skills" / "alpha").exists()
    assert install.settings()["enabledPlugins"]["tools@market"] == "owner's choice"


def test_a_live_path_that_is_not_empty_keeps_the_backup_copy(install: Install) -> None:
    (install.home / "skills" / "alpha").unlink()
    (install.home / "skills" / "alpha").mkdir()
    report = install.run()
    assert report.exit == 1
    assert (snapshot(install.backup) / "skills" / "alpha").is_dir()
    assert (install.home / "skills" / "alpha" / "SKILL.md").read_bytes() == b"alpha\n"


def test_a_path_the_deploy_never_moved_counts_as_already_in_place(install: Install) -> None:
    (install.home / "CLAUDE.md").unlink()
    os.rename(install.backup / "CLAUDE.md", install.home / "CLAUDE.md")
    assert install.run().exit == 0
    assert (install.home / "CLAUDE.md").read_bytes() == install.originals["CLAUDE.md"]


def test_a_missing_backup_copy_with_nothing_live_is_reported(install: Install) -> None:
    (install.backup / "CLAUDE.md").unlink()
    report = install.run()
    assert report.exit == 1
    assert {"path": "CLAUDE.md", "why": "the backup copy is gone"} in report.left_alone


def test_a_backup_without_a_manifest_means_nothing_was_moved(install: Install) -> None:
    (install.backup / "manifest.sha256").unlink()
    install.meta["moved"] = []
    install.save_meta()
    assert install.run().exit == 0
    assert not (install.home / "CLAUDE.md").exists()


def test_a_link_already_gone_is_fine(install: Install) -> None:
    (install.home / "hooks" / "guard.py").unlink()
    assert install.run().exit == 0


def test_a_settings_change_already_undone_is_fine(install: Install) -> None:
    install.write_settings(install.settings_before)
    assert install.run().exit == 0
    assert install.settings() == install.settings_before


def test_no_settings_changes_means_settings_json_is_not_read(install: Install) -> None:
    install.meta["settings"] = []
    install.save_meta()
    (install.home / "settings.json").write_text("{not json")
    assert install.run().exit == 0


@pytest.mark.parametrize("bad", ["{not json", "[]"])
def test_unusable_settings_are_reported_not_written(install: Install, bad: str) -> None:
    (install.home / "settings.json").write_text(bad)
    report = install.run()
    assert report.exit == 1
    assert (install.home / "settings.json").read_text() == bad
    assert any(item["path"] == "settings.json" for item in report.left_alone)


def test_a_symlinked_settings_file_is_reported_not_written(install: Install) -> None:
    real = install.home / "real-settings.json"
    os.rename(install.home / "settings.json", real)
    os.symlink(real, install.home / "settings.json")
    report = install.run()
    assert report.exit == 1
    assert (install.home / "settings.json").is_symlink()


def test_a_settings_section_of_the_wrong_shape_is_reported(install: Install) -> None:
    install.write_settings({"enabledPlugins": [], "hooks": {"PreToolUse": "x"}})
    report = install.run()
    assert report.exit == 1
    assert install.settings() == {"enabledPlugins": [], "hooks": {"PreToolUse": "x"}}


def test_a_hooks_key_of_the_wrong_shape_is_reported(install: Install) -> None:
    install.write_settings({"hooks": []})
    install.meta["settings"] = [{"key": GUARD_KEY, "before": GUARD}]
    install.save_meta()
    report = install.run()
    assert report.exit == 1
    assert install.settings() == {"hooks": []}


def test_settings_changed_during_the_write_are_left_as_they_are(install: Install) -> None:
    def harness_saves() -> None:
        (install.home / "settings.json").write_text('{"harness": true}\n')

    report = install.run(before_replace=harness_saves)
    assert report.exit == 1
    assert install.settings() == {"harness": True}
    assert not [p for p in install.home.iterdir() if p.name.endswith(".tmp")]


def test_the_live_settings_are_copied_into_the_snapshot_before_writing(install: Install) -> None:
    before = (install.home / "settings.json").read_bytes()
    install.run()
    assert (snapshot(install.backup) / "settings.json").read_bytes() == before


def test_a_later_deploys_backup_leaves_the_state_alone(install: Install) -> None:
    install.meta["firstDeploy"] = False
    install.save_meta()
    assert install.run().exit == 0
    assert (install.data / "state.json").exists()


def test_a_missing_data_directory_is_reported(install: Install) -> None:
    (install.data / "state.json").unlink()
    install.data.rmdir()
    report = install.run()
    assert report.exit == 1
    assert any(item["path"] == "state.json" for item in report.left_alone)


def test_a_state_file_already_moved_is_fine(install: Install) -> None:
    (install.data / "state.json").unlink()
    assert install.run().exit == 0


def test_two_runs_in_the_same_second_get_separate_snapshots(install: Install) -> None:
    (install.home / "CLAUDE.md").unlink()
    (install.home / "CLAUDE.md").write_text("one\n")
    install.run()
    (install.home / "hooks" / "guard.py").write_text("two\n")
    install.run()
    assert (install.backup / "restore-20261019T120000Z-2" / "hooks" / "guard.py").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"moved": ["/etc/passwd"]},
        {"moved": ["../outside"]},
        {"links": [{"live": "../x", "target": "y"}]},
        {"settings": "nope"},
        {"links": [{"live": 3}]},
        {"schema": 2},
        {"claudeHome": "relative/home"},
        {"moved": [""]},
        {"moved": "CLAUDE.md"},
        {"links": "nope"},
    ],
    ids=[
        "absolute",
        "dotdot",
        "link-dotdot",
        "settings-type",
        "link-shape",
        "schema",
        "home",
        "empty-path",
        "moved-type",
        "links-type",
    ],
)
def test_a_damaged_restore_json_changes_nothing(install: Install, change: dict[str, Any]) -> None:
    install.meta.update(change)
    install.save_meta()
    before = tree(install.home.parent)
    report = install.run()
    assert report.exit == 2
    assert tree(install.home.parent) == before


def test_unreadable_restore_json_or_manifest_changes_nothing(install: Install) -> None:
    before = tree(install.home.parent)
    (install.backup / "manifest.sha256").write_text("not a manifest line\n")
    assert install.run().exit == 2
    (install.backup / "restore.json").write_text("{not json")
    assert install.run().exit == 2
    (install.backup / "restore.json").unlink()
    assert install.run().exit == 2
    (install.backup / "manifest.sha256").unlink()
    after = tree(install.home.parent)
    before.pop("home/backups/claude-config/20261019T110000Z/manifest.sha256")
    before.pop("home/backups/claude-config/20261019T110000Z/restore.json")
    assert after == before


def test_a_manifest_path_outside_the_home_changes_nothing(install: Install) -> None:
    with open(install.backup / "manifest.sha256", "a") as f:
        f.write(f"{'0' * 64}  ../escape\n")
    assert install.run().exit == 2


def test_main_restores_the_backup_given_and_prints_a_summary(
    install: Install, capsys: pytest.CaptureFixture[str]
) -> None:
    assert restore.main([str(install.backup)]) == 0
    out = capsys.readouterr().out
    assert "restored" in out
    (install.home / "CLAUDE.md").unlink()
    (install.home / "CLAUDE.md").write_text("changed\n")
    (install.backup / "CLAUDE.md").write_text("x")
    assert restore.main([str(install.backup)]) == 1
    assert "left alone" in capsys.readouterr().out


def test_main_reports_a_damaged_backup_on_stderr(
    install: Install, capsys: pytest.CaptureFixture[str]
) -> None:
    (install.backup / "restore.json").write_text("[]")
    assert restore.main([str(install.backup)]) == 2
    assert "restore:" in capsys.readouterr().err


def test_restore_py_runs_alone_from_its_backup_directory(install: Install, tmp_path: Path) -> None:
    script = Path(restore.__file__).read_bytes()
    (install.backup / "restore.py").write_bytes(script)
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    done = subprocess.run(
        [sys.executable, "-I", str(install.backup / "restore.py")],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (install.home / "CLAUDE.md").read_bytes() == install.originals["CLAUDE.md"]


def test_restore_imports_only_the_standard_library() -> None:
    import ast

    names = set()
    for node in ast.walk(ast.parse(Path(restore.__file__).read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names)


def test_a_backup_copy_whose_inner_link_changed_stays_in_the_backup(install: Install) -> None:
    (install.backup / "skills" / "alpha" / "old-link").unlink()
    os.symlink("../somewhere-else", install.backup / "skills" / "alpha" / "old-link")
    report = install.run()
    assert report.exit == 1
    assert {"path": "skills/alpha", "why": "the backup copy no longer matches its manifest"} in (
        report.left_alone
    )


def test_an_occupied_place_the_deploy_did_not_link_keeps_the_backup_copy(
    install: Install,
) -> None:
    install.meta["links"] = [x for x in install.meta["links"] if x["live"] != "CLAUDE.md"]
    install.save_meta()
    (install.home / "CLAUDE.md").unlink()
    (install.home / "CLAUDE.md").write_text("someone else's\n")
    report = install.run()
    assert report.exit == 1
    assert (install.home / "CLAUDE.md").read_text() == "someone else's\n"
    assert (install.backup / "CLAUDE.md").exists()


def test_a_hook_event_of_the_wrong_shape_is_reported(install: Install) -> None:
    install.write_settings({"hooks": {"PreToolUse": "x"}})
    install.meta["settings"] = [{"key": GUARD_KEY, "after": GUARD}]
    install.save_meta()
    report = install.run()
    assert report.exit == 1
    assert install.settings() == {"hooks": {"PreToolUse": "x"}}


def test_a_plugin_the_deploy_added_is_removed(install: Install) -> None:
    install.write_settings({"enabledPlugins": {"tools@market": True, "mine@m": True}})
    install.meta["settings"] = [{"key": "enabledPlugins/tools@market", "after": True}]
    install.save_meta()
    assert install.run().exit == 0
    assert install.settings() == {"enabledPlugins": {"mine@m": True}}


def test_links_the_deploy_removed_are_reported_not_recreated(install: Install) -> None:
    install.meta["removedLinks"] = [{"live": "skills/gone", "target": "/d/current/x"}]
    install.save_meta()
    report = install.run()
    assert report.exit == 1
    assert not os.path.lexists(install.home / "skills" / "gone")


def test_main_prints_no_snapshot_when_nothing_was_set_aside(
    install: Install, capsys: pytest.CaptureFixture[str]
) -> None:
    install.meta.update(firstDeploy=False, settings=[])
    install.save_meta()
    assert restore.main([str(install.backup)]) == 0
    assert "snapshot" not in capsys.readouterr().out
