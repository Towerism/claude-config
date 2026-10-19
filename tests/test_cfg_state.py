"""The deploy state file, backup manifests, atomic writes and the cfg lock."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.cfg import state
from tools.cfg.errors import CfgError, LockBusy


def sample_state(**changes: object) -> state.State:
    base: dict[str, object] = {
        "host": "hosts/personal.json",
        "claude_home": "/home/u/.claude",
        "sha": "a" * 40,
        "release": "a" * 40,
        "deployed_at": "2026-10-19T12:00:00Z",
        "paths": (
            state.ManagedPath("CLAUDE.md", "config/CLAUDE.global.md", "file", "/d/current/x"),
            state.ManagedPath("skills/s", "config/skills/s", "dir", "/d/current/config/skills/s"),
        ),
        "settings": {"enabledPlugins/p@m": True},
        "backups": ("/home/u/.claude/backups/claude-config/20261019T120000Z",),
    }
    base.update(changes)
    return state.State(**base)  # type: ignore[arg-type]


def test_saved_state_loads_back_equal(tmp_path: Path) -> None:
    st = sample_state()
    state.save(tmp_path, st)
    assert state.load(tmp_path) == st


def test_missing_state_file_means_not_deployed(tmp_path: Path) -> None:
    assert state.load(tmp_path) is None


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[]",
        json.dumps({"schema": 99}),
        json.dumps({**state.to_json(sample_state()), "paths": "CLAUDE.md"}),
        json.dumps({**state.to_json(sample_state()), "sha": 5}),
        json.dumps({**state.to_json(sample_state()), "settings": []}),
        json.dumps({**state.to_json(sample_state()), "paths": [{"live": "x"}]}),
        json.dumps(
            {
                **state.to_json(sample_state()),
                "paths": [{"live": "x", "source": "y", "kind": "pipe", "target": "z"}],
            }
        ),
        json.dumps({**state.to_json(sample_state()), "backups": [3]}),
    ],
    ids=[
        "unparseable",
        "not-object",
        "schema",
        "paths-type",
        "sha-type",
        "settings-type",
        "path-fields",
        "path-kind",
        "backups-type",
    ],
)
def test_unusable_state_file_is_refused_naming_it(tmp_path: Path, text: str) -> None:
    (tmp_path / "state.json").write_text(text)
    with pytest.raises(CfgError, match="state.json"):
        state.load(tmp_path)


def test_state_is_written_atomically_with_no_temp_left(tmp_path: Path) -> None:
    state.save(tmp_path, sample_state())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_atomic_write_keeps_the_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_text("old")
    target.chmod(0o600)
    state.write_atomic(target, b"new")
    assert target.read_bytes() == b"new"
    assert target.stat().st_mode & 0o777 == 0o600


def test_atomic_write_gives_a_new_file_mode_644(tmp_path: Path) -> None:
    old = os.umask(0o077)
    try:
        state.write_atomic(tmp_path / "f", b"x")
    finally:
        os.umask(old)
    assert (tmp_path / "f").stat().st_mode & 0o777 == 0o644


def test_atomic_write_removes_its_temp_file_when_the_callback_refuses(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_text("old")

    def refuse() -> None:
        raise CfgError("changed underneath")

    with pytest.raises(CfgError):
        state.write_atomic(target, b"new", before_replace=refuse)
    assert target.read_text() == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f"]


def test_manifest_round_trips_files_and_links(tmp_path: Path) -> None:
    entries = [
        state.ManifestEntry("CLAUDE.md", sha256="b" * 64),
        state.ManifestEntry("skills/s/SKILL.md", sha256="c" * 64),
        state.ManifestEntry("hooks/x.py", link="/elsewhere/x.py"),
    ]
    path = tmp_path / "manifest.sha256"
    state.write_manifest(path, entries)
    assert path.read_text().splitlines() == [
        f"{'b' * 64}  CLAUDE.md",
        f"{'c' * 64}  skills/s/SKILL.md",
        "link  hooks/x.py -> /elsewhere/x.py",
    ]
    assert state.read_manifest(path) == entries


@pytest.mark.parametrize("line", ["nonsense", "link  no-arrow", f"{'z' * 64}  x"])
def test_malformed_manifest_line_is_refused(tmp_path: Path, line: str) -> None:
    path = tmp_path / "manifest.sha256"
    path.write_text(line + "\n")
    with pytest.raises(CfgError, match="manifest"):
        state.read_manifest(path)


def test_second_cfg_lock_fails_at_once(tmp_path: Path) -> None:
    with state.cfg_lock(tmp_path):
        with pytest.raises(LockBusy, match="another cfg is running"):
            with state.cfg_lock(tmp_path):
                pass  # pragma: no cover
    with state.cfg_lock(tmp_path):
        pass


def test_sha256_of_a_file(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"abc")
    assert state.sha256_file(tmp_path / "f") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
