"""Release worktrees under <data_dir>/releases/, and the current link (ADR-0001).

A release is a git worktree of the working clone, detached at one commit. Its
name is the commit id, or `<commit>-<n>` for a later release of the same
commit, so `cfg deploy --discard-live` can deploy the same commit again and
leave the edited release on disk (ADR-0004). Every git call here passes
--no-optional-locks, so reading a release never rewrites its index.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tools.cfg.errors import CfgError, GitMissing

IGNORED_PARTS = frozenset({"__pycache__", ".pytest_cache"})


def git(repo: Path, *args: str) -> str:
    """Run git in `repo` and return its stdout; any failure becomes a CfgError."""
    try:
        done = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise GitMissing("git not found on PATH") from e
    if done.returncode != 0:
        raise CfgError(f"git {' '.join(args)} failed in {repo}: {done.stderr.strip()}")
    return done.stdout


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *args], capture_output=True, check=False
        )
    except FileNotFoundError as e:
        raise GitMissing("git not found on PATH") from e


def is_ancestor(clone: Path, older: str, newer: str) -> bool:
    """Whether commit `older` is an ancestor of (or equal to) `newer`."""
    done = _run(clone, "merge-base", "--is-ancestor", older, newer)
    if done.returncode not in (0, 1):
        raise CfgError(f"git merge-base failed in {clone}: {done.stderr.decode().strip()}")
    return done.returncode == 0


def archive(clone: Path, sha: str, *paths: str) -> bytes:
    """A tar of `paths` at commit `sha`, read without touching any worktree."""
    done = _run(clone, "archive", "--format=tar", sha, "--", *paths)
    if done.returncode != 0:
        raise CfgError(f"git archive failed in {clone}: {done.stderr.decode().strip()}")
    return done.stdout


def resolve(clone: Path, ref: str) -> str:
    """The full commit id `ref` names in `clone`."""
    try:
        return git(clone, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()
    except GitMissing:
        raise
    except CfgError as e:
        raise CfgError(f"{ref!r} isn't a commit in {clone}") from e


def releases_dir(data_dir: Path) -> Path:
    return data_dir / "releases"


def release_sha(release: Path) -> str:
    return git(release, "rev-parse", "HEAD").strip()


def _common_dir(repo: Path) -> Path:
    raw = git(repo, "rev-parse", "--git-common-dir").strip()
    return (repo / raw).resolve()


def _is_release_of(path: Path, clone: Path, sha: str) -> bool:
    try:
        return release_sha(path) == sha and _common_dir(path) == _common_dir(clone)
    except CfgError:
        return False


def is_release(path: Path, clone: Path) -> bool:
    """Whether `path` is a worktree of `clone`, so cfg may treat it as a release."""
    if not path.is_dir():
        return False
    try:
        return _common_dir(path) == _common_dir(clone)
    except CfgError:
        return False


def _ignored(path: str) -> bool:
    parts = path.split("/")
    return path.endswith(".pyc") or any(p in IGNORED_PARTS for p in parts)


def dirty_paths(release: Path) -> list[str]:
    """Paths `git status` reports as changed in a release, leaving out caches."""
    out = git(release, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    tokens = out.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(tokens) and tokens[i]:
        status, path = tokens[i][:2], tokens[i][3:]
        paths.append(path)
        if "R" in status or "C" in status:
            i += 1
            paths.append(tokens[i])
        i += 1
    return sorted(p for p in paths if not _ignored(p))


def create(clone: Path, sha: str, data_dir: Path, fresh: bool = False) -> str:
    """The name of a release of `sha`: an existing clean one, or a new worktree."""
    full = resolve(clone, sha)
    root = releases_dir(data_dir)
    n = 1
    while True:
        name = full if n == 1 else f"{full}-{n}"
        path = root / name
        if not path.exists():
            root.mkdir(parents=True, exist_ok=True)
            git(clone, "worktree", "add", "--quiet", "--detach", str(path), full)
            return name
        if not _is_release_of(path, clone, full):
            raise CfgError(f"{path} exists but isn't a release of {full}; move it aside")
        if not fresh and not dirty_paths(path):
            return name
        n += 1


def swap_current(data_dir: Path, name: str) -> None:
    """Point <data_dir>/current at releases/<name> in one rename."""
    if not (releases_dir(data_dir) / name).is_dir():
        raise CfgError(f"there is no release {name} under {releases_dir(data_dir)}")
    current = data_dir / "current"
    if current.exists() and not current.is_symlink():
        raise CfgError(f"{current} is not a symlink; move it aside")
    tmp = data_dir / f".current.cfg-{os.getpid()}"
    if tmp.is_symlink():
        tmp.unlink()
    os.symlink(f"releases/{name}", tmp)
    os.replace(tmp, current)


def current_release(data_dir: Path) -> str | None:
    """The release name `current` points at, or None when there is no current link."""
    current = data_dir / "current"
    if not current.is_symlink():
        if current.exists():
            raise CfgError(f"{current} is not a symlink")
        return None
    text = os.readlink(current)
    head, _, name = text.partition("/")
    if head != "releases" or not name or "/" in name:
        raise CfgError(f"{current} points at {text!r}, not at releases/<name>")
    return name


def remove(clone: Path, data_dir: Path, name: str) -> None:
    """Remove release `name` with `git worktree remove`, which refuses one with changes."""
    git(clone, "worktree", "remove", str(releases_dir(data_dir) / name))
