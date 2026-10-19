"""Classify managed paths, back them up, and replace them with links (ADR-0001).

Every check looks at the live path itself and never follows it: a symlink is
judged by its text, so a link into an old release is caught even when the
file it reaches happens to match.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from tools.cfg import state
from tools.cfg.errors import CfgError

PathState = Literal["missing", "linked", "foreign-link", "match", "differs"]

IGNORED_DIRS = frozenset({"__pycache__", ".pytest_cache"})
IGNORED_SUFFIX = ".pyc"


def tree_entries(path: Path) -> dict[str, str]:
    """Map each file under `path` (or `path` itself) to 'sha:<hex>', each link to 'link:<text>'.

    Caches (__pycache__/, .pytest_cache/, *.pyc) are left out, so a skill that
    has run its scripts still matches the repo's copy.
    """
    if path.is_file():
        return {"": f"sha:{state.sha256_file(path)}"}
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(path):
        here = Path(dirpath)
        for name in list(dirnames):
            full = here / name
            if name in IGNORED_DIRS:
                dirnames.remove(name)
            elif full.is_symlink():
                entries[str(full.relative_to(path))] = f"link:{os.readlink(full)}"
        for name in filenames:
            full = here / name
            if name.endswith(IGNORED_SUFFIX):
                continue
            rel = str(full.relative_to(path))
            if full.is_symlink():
                entries[rel] = f"link:{os.readlink(full)}"
            elif full.is_file():
                entries[rel] = f"sha:{state.sha256_file(full)}"
    return entries


def _same_kind(live: Path, source: Path) -> bool:
    return (live.is_dir() and source.is_dir()) or (live.is_file() and source.is_file())


def classify(live: Path, target: str, source: Path) -> PathState:
    """Which of the five kinds a managed path is, looking at the path itself."""
    if live.is_symlink():
        return "linked" if os.readlink(live) == target else "foreign-link"
    if not live.exists():
        return "missing"
    if not _same_kind(live, source):
        return "differs"
    return "match" if tree_entries(live) == tree_entries(source) else "differs"


def _same_filesystem(a: Path, b: Path) -> bool:
    return a.stat().st_dev == b.stat().st_dev


def _manifest_entries(path: Path, home: Path) -> list[state.ManifestEntry]:
    rel = path.relative_to(home)
    if path.is_symlink():
        return [state.ManifestEntry(str(rel), link=os.readlink(path))]
    if path.is_file():
        return [state.ManifestEntry(str(rel), sha256=state.sha256_file(path))]
    entries = []
    for dirpath, dirnames, filenames in os.walk(path):
        here = Path(dirpath)
        names = list(filenames) + [d for d in dirnames if (here / d).is_symlink()]
        for name in names:
            full = here / name
            if full.is_symlink():
                entries.append(
                    state.ManifestEntry(str(full.relative_to(home)), link=os.readlink(full))
                )
            elif full.is_file():
                entries.append(
                    state.ManifestEntry(str(full.relative_to(home)), sha256=state.sha256_file(full))
                )
    return sorted(entries, key=lambda e: e.path)


def backup(paths: list[Path], claude_home: Path, backup_dir: Path) -> list[state.ManifestEntry]:
    """Move each path into backup_dir at its place relative to the Claude home.

    The manifest is written before anything moves, so it lists everything a
    restore may look for even if a move fails part way.
    """
    home = Path(os.path.abspath(claude_home))
    for path in paths:
        if not Path(os.path.abspath(path)).is_relative_to(home):
            raise CfgError(f"{path} is outside {home}; cfg backs up only paths inside it")
    backup_dir.mkdir(parents=True, exist_ok=True)
    if not _same_filesystem(home, backup_dir):
        raise CfgError(
            f"{backup_dir} is on another filesystem from {home}; a backup must be a rename"
        )
    manifest = backup_dir / "manifest.sha256"
    if manifest.exists():
        raise CfgError(f"{manifest} already exists; use a new backup directory")
    entries = [e for p in paths for e in _manifest_entries(Path(os.path.abspath(p)), home)]
    state.write_manifest(manifest, entries)
    for path in paths:
        dest = backup_dir / Path(os.path.abspath(path)).relative_to(home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(path, dest)
    return entries


def make_link(live: Path, target: str, replace_link: bool = False) -> None:
    """Make `live` a symlink holding `target`, atomically, through a temporary link."""
    if live.is_symlink() and not replace_link:
        raise CfgError(f"{live} is already a link; back it up before replacing it")
    if live.exists() and not live.is_symlink():
        raise CfgError(f"{live} is a plain file or directory; back it up before linking")
    live.parent.mkdir(parents=True, exist_ok=True)
    tmp = live.parent / f".{live.name}.cfg-link-{os.getpid()}"
    if tmp.is_symlink():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, live)
