"""Undo one cfg deploy, from the backup directory that deploy wrote.

    python3 -I <backup>/restore.py      (or <backup>/restore.sh)

Every deploy that changes the live config copies this file into its backup
directory, next to restore.json (what the deploy did) and manifest.sha256
(what it moved aside). It uses only the standard library and reads nothing
from the repo, so it still works when the repo is broken or gone.

It never destroys anything. It removes a link only while the link still
holds the text the deploy gave it, moves an original back only into an empty
place and only when its hashes still match, and puts a settings item back
only while the item still has the value the deploy wrote. Anything else it
moves into a restore-<time>/ snapshot or leaves where it is, and reports.

Exit codes: 0 everything is back; 1 something was left alone (listed);
2 restore.json or the manifest is unusable, and nothing was changed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = 1
MISSING: Any = object()


class Damaged(Exception):
    """restore.json or the manifest can't be used; nothing has been changed."""


@dataclass
class Report:
    exit: int = 0
    restored: list[str] = field(default_factory=list)
    left_alone: list[dict[str, str]] = field(default_factory=list)
    snapshot: str | None = None

    def leave(self, path: str, why: str) -> None:
        self.left_alone.append({"path": path, "why": why})


@dataclass(frozen=True)
class Entry:
    path: str
    sha256: str | None = None
    link: str | None = None


# ---- reading the backup -----------------------------------------------------------


def _safe(rel: Any) -> str:
    """A relative path that can't leave the directory it is joined to."""
    if not isinstance(rel, str) or not rel:
        raise Damaged(f"{rel!r} isn't a path")
    pure = PurePosixPath(rel)
    if pure.is_absolute() or ".." in pure.parts:
        raise Damaged(f"{rel!r} would reach outside the Claude home")
    return rel


def _link_list(raw: Any, name: str) -> list[tuple[str, str]]:
    if not isinstance(raw, list):
        raise Damaged(f"{name} isn't a list")
    out = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("target"), str):
            raise Damaged(f"an entry in {name} needs live and target strings")
        out.append((_safe(item.get("live")), item["target"]))
    return out


def read_meta(backup: Path) -> dict[str, Any]:
    try:
        raw = json.loads((backup / "restore.json").read_text())
    except (OSError, ValueError) as e:
        raise Damaged(f"restore.json can't be read: {e}") from e
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise Damaged(f"restore.json isn't a schema {SCHEMA} object")
    for key in ("claudeHome", "dataDir"):
        if not isinstance(raw.get(key), str) or not os.path.isabs(raw[key]):
            raise Damaged(f"restore.json: {key} isn't an absolute path")
    if not isinstance(raw.get("moved"), list):
        raise Damaged("restore.json: moved isn't a list")
    settings = raw.get("settings")
    if not isinstance(settings, list) or not all(
        isinstance(s, dict) and isinstance(s.get("key"), str) for s in settings
    ):
        raise Damaged("restore.json: settings isn't a list of items with keys")
    return {
        "home": Path(raw["claudeHome"]),
        "data": Path(raw["dataDir"]),
        "first": raw.get("firstDeploy") is True,
        "moved": [_safe(m) for m in raw["moved"]],
        "links": _link_list(raw.get("links"), "links"),
        "removed": _link_list(raw.get("removedLinks", []), "removedLinks"),
        "settings": settings,
    }


def read_manifest(backup: Path) -> list[Entry] | None:
    """The manifest's entries, or None when the deploy stopped before writing it."""
    path = backup / "manifest.sha256"
    if not path.exists():
        return None
    entries = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        head, sep, rest = line.partition("  ")
        if sep and head == "link" and " -> " in rest:
            rel, _, text = rest.partition(" -> ")
            entries.append(Entry(_safe(rel), link=text))
        elif sep and len(head) == 64 and all(c in "0123456789abcdef" for c in head):
            entries.append(Entry(_safe(rest), sha256=head))
        else:
            raise Damaged(f"manifest.sha256, line {number}: not a manifest line")
    return entries


# ---- files and links --------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _under(entries: list[Entry] | None, top: str) -> list[Entry]:
    return [e for e in entries or [] if e.path == top or e.path.startswith(top + "/")]


def _matches(root: Path, entries: list[Entry]) -> bool:
    """Whether every entry, read under `root`, still has its recorded hash or link text."""
    for e in entries:
        path = root / e.path
        if e.link is not None:
            if not path.is_symlink() or os.readlink(path) != e.link:
                return False
        elif path.is_symlink() or not path.is_file() or _sha256(path) != e.sha256:
            return False
    return bool(entries)


class Snapshot:
    """restore-<time>/ in the backup, made only when something has to go into it."""

    def __init__(self, backup: Path, now: datetime, report: Report) -> None:
        self.backup, self.report = backup, report
        self.name = "restore-" + now.strftime("%Y%m%dT%H%M%SZ")
        self.dir: Path | None = None

    def path(self) -> Path:
        if self.dir is None:
            n, name = 1, self.name
            while (self.backup / name).exists():
                n += 1
                name = f"{self.name}-{n}"
            self.dir = self.backup / name
            self.dir.mkdir()
            self.report.snapshot = str(self.dir)
        return self.dir

    def keep(self, path: Path, rel: str) -> None:
        dest = self.path() / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(path, dest)


def _in_place(home: Path, backup: Path, top: str, entries: list[Entry] | None) -> bool:
    """A path the deploy never actually moved: no backup copy, the original still live.

    With no manifest at all, the deploy stopped before moving anything, so
    whatever is live is the original.
    """
    if os.path.lexists(backup / top):
        return False
    if entries is None:
        return os.path.lexists(home / top)
    return _matches(home, _under(entries, top))


def undo_links(
    meta: dict[str, Any], backup: Path, entries: list[Entry] | None, snap: Snapshot
) -> None:
    home, report = meta["home"], snap.report
    for live, target in meta["links"]:
        path = home / live
        if path.is_symlink() and os.readlink(path) == target:
            path.unlink()
        elif not os.path.lexists(path):
            continue
        elif live in meta["moved"] and _in_place(home, backup, live, entries):
            continue
        else:
            snap.keep(path, live)
            report.leave(live, "it was no longer cfg's link; moved into the snapshot")


def put_back(
    meta: dict[str, Any], backup: Path, entries: list[Entry] | None, report: Report
) -> None:
    home = meta["home"]
    for top in meta["moved"]:
        mine = _under(entries, top)
        if not os.path.lexists(backup / top):
            if not _in_place(home, backup, top, entries):
                report.leave(top, "the backup copy is gone")
            continue
        if os.path.lexists(home / top):
            report.leave(top, "something is in its place; the backup copy stays")
        elif not _matches(backup, mine):
            report.leave(top, "the backup copy no longer matches its manifest")
        else:
            (home / top).parent.mkdir(parents=True, exist_ok=True)
            os.rename(backup / top, home / top)
            report.restored.append(top)


# ---- settings ---------------------------------------------------------------------


def _split(key: str) -> tuple[str, ...]:
    section, _, rest = key.partition("/")
    if section != "hooks":
        return (section, rest)
    event, _, rest = rest.partition("/")
    matcher, _, command = rest.partition("/")
    return ("hooks", event, matcher, command)


def _matcher(group: dict[str, Any]) -> str:
    value = group.get("matcher")
    return value if isinstance(value, str) else ""


def _object(parent: dict[str, Any], name: str, create: bool) -> dict[str, Any]:
    value = parent.setdefault(name, {}) if create else parent.get(name, {})
    if not isinstance(value, dict):
        raise Damaged(f"settings.json: {name} is not an object")
    return value


def _groups(settings: dict[str, Any], event: str, create: bool) -> list[Any]:
    hooks = _object(settings, "hooks", create)
    groups = hooks.setdefault(event, []) if create else hooks.get(event, [])
    if not isinstance(groups, list):
        raise Damaged(f"settings.json: hooks.{event} is not a list")
    return groups


def _same_matcher(groups: list[Any], matcher: str) -> list[dict[str, Any]]:
    return [
        g
        for g in groups
        if isinstance(g, dict) and _matcher(g) == matcher and isinstance(g.get("hooks"), list)
    ]


def value_of(settings: dict[str, Any], key: str) -> Any:
    parts = _split(key)
    if parts[0] != "hooks":
        return _object(settings, parts[0], False).get(parts[1], MISSING)
    _, event, matcher, command = parts
    for group in _same_matcher(_groups(settings, event, False), matcher):
        for handler in group["hooks"]:
            if isinstance(handler, dict) and handler.get("command") == command:
                return handler
    return MISSING


def set_value(settings: dict[str, Any], key: str, value: Any) -> None:
    """Set one owned item to `value`, or remove it when `value` is MISSING."""
    parts = _split(key)
    if parts[0] != "hooks":
        section = _object(settings, parts[0], value is not MISSING)
        if value is MISSING:
            section.pop(parts[1], None)
        else:
            section[parts[1]] = copy.deepcopy(value)
        return
    _, event, matcher, command = parts
    groups = _groups(settings, event, True)
    same = _same_matcher(groups, matcher)
    for group in same:
        group["hooks"] = [
            h for h in group["hooks"] if not (isinstance(h, dict) and h.get("command") == command)
        ]
    if value is not MISSING:
        if same:
            same[0]["hooks"].append(copy.deepcopy(value))
        else:
            groups.append({**({"matcher": matcher} if matcher else {}), "hooks": [value]})
    groups[:] = [g for g in groups if not (any(g is s for s in same) and not g["hooks"])]
    if not groups:
        del settings["hooks"][event]
    if not settings["hooks"]:
        del settings["hooks"]


def revert_settings(
    meta: dict[str, Any],
    snap: Snapshot,
    before_replace: Callable[[], None] | None,
) -> None:
    report = snap.report
    if not meta["settings"]:
        return
    path = meta["home"] / "settings.json"
    if path.is_symlink() or not path.is_file():
        report.leave("settings.json", "it is a symlink or not a regular file; not written")
        return
    original = path.read_bytes()
    try:
        live = json.loads(original)
    except ValueError:
        report.leave("settings.json", "it doesn't parse; not written")
        return
    if not isinstance(live, dict):
        report.leave("settings.json", "it isn't a JSON object; not written")
        return
    new = copy.deepcopy(live)
    try:
        for item in meta["settings"]:
            before, after = item.get("before", MISSING), item.get("after", MISSING)
            current = value_of(new, item["key"])
            if current == before:
                continue
            if current != after:
                report.leave(item["key"], "changed since the deploy; kept")
                continue
            set_value(new, item["key"], before)
            report.restored.append(item["key"])
    except Damaged as e:
        report.leave("settings.json", f"{e}; not written")
        return
    if new == live:
        return
    shot = snap.path() / "settings.json"
    shot.write_bytes(original)
    tmp = path.parent / f".{path.name}.restore-{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            f.write(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.chmod(path.stat().st_mode & 0o777)
        if before_replace is not None:
            before_replace()
        if path.read_bytes() != original:
            report.leave("settings.json", "another program changed it during the restore")
            return
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# ---- the run ----------------------------------------------------------------------


def forget_state(meta: dict[str, Any], snap: Snapshot) -> None:
    if not meta["first"]:
        return
    data = meta["data"]
    if not data.is_dir():
        snap.report.leave("state.json", f"the data directory {data} is gone")
        return
    if (data / "state.json").exists():
        snap.keep(data / "state.json", "state.json")


def run(
    backup: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    before_replace: Callable[[], None] | None = None,
) -> Report:
    report = Report()
    try:
        meta = read_meta(backup)
        entries = read_manifest(backup)
    except Damaged as e:
        report.exit = 2
        report.leave("restore.json", str(e))
        return report
    snap = Snapshot(backup, clock(), report)
    undo_links(meta, backup, entries, snap)
    put_back(meta, backup, entries, report)
    revert_settings(meta, snap, before_replace)
    forget_state(meta, snap)
    for live, _ in meta["removed"]:
        report.leave(live, "the deploy removed this link; restore doesn't recreate it")
    report.exit = 1 if report.left_alone else 0
    return report


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    backup = Path(args[0]) if args else Path(__file__).resolve().parent
    report = run(backup)
    if report.exit == 2:
        print(f"restore: {report.left_alone[0]['why']}; nothing was changed", file=sys.stderr)
        return 2
    for path in report.restored:
        print(f"restored  {path}")
    for item in report.left_alone:
        print(f"left alone  {item['path']}: {item['why']}")
    if report.snapshot:
        print(f"snapshot: {report.snapshot}")
    return report.exit


if __name__ == "__main__":
    sys.exit(main())
