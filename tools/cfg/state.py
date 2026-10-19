"""Deploy state, backup manifests, atomic writes and the cfg lock.

The state file, <data_dir>/state.json, records what the last deploy put in
place, so `cfg status` can tell what changed since. Backup manifests record
what a deploy moved aside, so a restore can check hashes before moving
anything back. Both formats are read and written only here.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from tools.cfg.errors import CfgError, LockBusy

SCHEMA = 1
NEW_FILE_MODE = 0o644
_SHA_RE = re.compile(r"[0-9a-f]{64}")

Kind = Literal["file", "dir"]


@dataclass(frozen=True)
class ManagedPath:
    """One component the repo puts into the Claude home.

    `live` is relative to the Claude home, `source` relative to the release,
    and `target` is the absolute text the live symlink holds.
    """

    live: str
    source: str
    kind: Kind
    target: str


@dataclass(frozen=True)
class State:
    host: str
    claude_home: str
    sha: str
    release: str
    deployed_at: str
    paths: tuple[ManagedPath, ...]
    settings: dict[str, Any]
    backups: tuple[str, ...] = field(default=())
    history: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class ManifestEntry:
    """A file moved aside (with its sha256) or a symlink moved aside (with its text)."""

    path: str
    sha256: str | None = None
    link: str | None = None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic(path: Path, data: bytes, before_replace: Callable[[], None] | None = None) -> None:
    """Write through a temp file in the same directory, fsync, then os.replace.

    The live file's mode is kept; a new file gets NEW_FILE_MODE. When
    `before_replace` raises, the temp file is removed and `path` is untouched.
    """
    mode = path.stat().st_mode & 0o777 if path.exists() else NEW_FILE_MODE
    tmp = path.parent / f".{path.name}.cfg-{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        tmp.chmod(mode)
        if before_replace is not None:
            before_replace()
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def state_path(data_dir: Path) -> Path:
    return data_dir / "state.json"


def to_json(st: State) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "host": st.host,
        "claudeHome": st.claude_home,
        "sha": st.sha,
        "release": st.release,
        "deployedAt": st.deployed_at,
        "paths": [vars(p) for p in st.paths],
        "settings": st.settings,
        "backups": list(st.backups),
        "history": list(st.history),
    }


def save(data_dir: Path, st: State) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(to_json(st), indent=2, ensure_ascii=False) + "\n"
    write_atomic(state_path(data_dir), text.encode())


def _bad(path: Path, why: str) -> CfgError:
    return CfgError(f"{path} can't be used: {why}. Move it aside and deploy again.")


def _path_from(path: Path, raw: Any) -> ManagedPath:
    keys = ("live", "source", "kind", "target")
    if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) for k in keys):
        raise _bad(path, "each entry in paths needs live, source, kind and target strings")
    if raw["kind"] not in ("file", "dir"):
        raise _bad(path, f"path kind {raw['kind']!r} is neither file nor dir")
    return ManagedPath(raw["live"], raw["source"], raw["kind"], raw["target"])


def load(data_dir: Path) -> State | None:
    """The deploy state, or None when nothing has been deployed."""
    path = state_path(data_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except ValueError as e:
        raise _bad(path, f"it doesn't parse ({e})") from e
    if not isinstance(raw, dict):
        raise _bad(path, "it isn't a JSON object")
    if raw.get("schema") != SCHEMA:
        raise _bad(path, f"schema {raw.get('schema')!r} isn't {SCHEMA}")
    for key in ("host", "claudeHome", "sha", "release", "deployedAt"):
        if not isinstance(raw.get(key), str):
            raise _bad(path, f"{key} isn't a string")
    if not isinstance(raw.get("paths"), list):
        raise _bad(path, "paths isn't a list")
    if not isinstance(raw.get("settings"), dict):
        raise _bad(path, "settings isn't an object")
    backups = raw.get("backups", [])
    if not isinstance(backups, list) or not all(isinstance(b, str) for b in backups):
        raise _bad(path, "backups isn't a list of strings")
    history = raw.get("history", [])
    if not isinstance(history, list) or not all(isinstance(h, str) for h in history):
        raise _bad(path, "history isn't a list of strings")
    return State(
        host=raw["host"],
        claude_home=raw["claudeHome"],
        sha=raw["sha"],
        release=raw["release"],
        deployed_at=raw["deployedAt"],
        paths=tuple(_path_from(path, p) for p in raw["paths"]),
        settings=raw["settings"],
        backups=tuple(backups),
        history=tuple(history),
    )


def write_manifest(path: Path, entries: list[ManifestEntry]) -> None:
    lines = [
        f"link  {e.path} -> {e.link}" if e.link is not None else f"{e.sha256}  {e.path}"
        for e in entries
    ]
    write_atomic(path, ("".join(line + "\n" for line in lines)).encode())


def read_manifest(path: Path) -> list[ManifestEntry]:
    entries = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        head, sep, rest = line.partition("  ")
        if sep and head == "link" and " -> " in rest:
            rel, _, text = rest.partition(" -> ")
            entries.append(ManifestEntry(rel, link=text))
        elif sep and _SHA_RE.fullmatch(head):
            entries.append(ManifestEntry(rest, sha256=head))
        else:
            raise CfgError(f"{path}, line {number}: not a manifest line")
    return entries


@contextmanager
def cfg_lock(data_dir: Path) -> Iterator[None]:
    """Hold <data_dir>/cfg.lock so two cfg runs never write at the same time."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / "cfg.lock", "a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise LockBusy(
                f"another cfg is running (it holds {data_dir / 'cfg.lock'}); wait for it to finish"
            ) from e
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
