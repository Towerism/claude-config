"""cfg capture: turn live changes into files on a feature branch (ADR-0004).

Capture copies what changed live into a working tree of the repo that is on
its own branch, so the change goes through review like any other. It reads
the Claude home, the data directory and the deployed release, and writes only
inside that working tree. It never commits.

Three kinds of change are captured:
- an edit made through a cfg link, which sits in the deployed release as a
  dirty file;
- a plain file or directory where a link should be, mirrored onto its source;
- owned settings items, written back into config/settings.fragment.json with
  the {claude_home} placeholder put back.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.cfg import eval_records, link, release, render, settings_merge, state
from tools.cfg.errors import CfgError

DEFAULT_BRANCH = "main"


@dataclass(frozen=True)
class Change:
    """One repo path to write (bytes or a link text) or delete (neither)."""

    path: str
    data: bytes | None = None
    link: str | None = None


@dataclass
class CaptureResult:
    into: str
    written: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    suites: list[str] = field(default_factory=list)


# ---- where capture may write -------------------------------------------------------


def check_branch(into: Path) -> str:
    """The branch `into` is on; refuse a detached HEAD and the default branch."""
    try:
        branch = release.git(into, "symbolic-ref", "-q", "--short", "HEAD").strip()
    except CfgError as e:
        raise CfgError(f"{into} is on a detached HEAD; capture needs a feature branch") from e
    default = {DEFAULT_BRANCH}
    try:
        head = release.git(into, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
        default.add(head.strip().removeprefix("origin/"))
    except CfgError:
        pass
    if branch in default:
        raise CfgError(f"{into} is on the default branch {branch}; capture needs a feature branch")
    return branch


def check_clean(into: Path, paths: list[str]) -> None:
    if not paths:
        return
    dirty = release.git(into, "status", "--porcelain", "--", *paths).strip()
    if dirty:
        raise CfgError(
            f"{into} has uncommitted changes where capture would write:\n{dirty}\n"
            "commit or stash them first"
        )


# ---- what to capture --------------------------------------------------------------


def _entry(path: Path, rel: str) -> Change:
    if path.is_symlink():
        return Change(rel, link=os.readlink(path))
    return Change(rel, data=path.read_bytes())


def drift_changes(rel: Path, source: str) -> list[Change]:
    """The release's dirty files under `source`, as they are now."""
    out = []
    for dirty in release.dirty_paths(rel):
        if dirty != source and not dirty.startswith(source + "/"):
            continue
        path = rel / dirty
        out.append(_entry(path, dirty) if os.path.lexists(path) else Change(dirty))
    return out


def mirror_changes(live: Path, source: Path, rel_source: str) -> list[Change]:
    """What makes `source` in the repo equal the plain `live` file or directory."""
    if live.is_dir() != source.is_dir():
        raise CfgError(
            f"{live} and the repo's {rel_source} differ in type; capture can't change "
            "a component's type, so change the repo by hand"
        )
    if live.is_file():
        return [Change(rel_source, data=live.read_bytes())]
    ours, theirs = link.tree_entries(live), link.tree_entries(source)
    out = []
    for rel in sorted(set(ours) | set(theirs)):
        if ours.get(rel) == theirs.get(rel):
            continue
        repo_path = f"{rel_source}/{rel}"
        out.append(_entry(live / rel, repo_path) if rel in ours else Change(repo_path))
    return out


def _unrender(value: Any, home: str) -> Any:
    if isinstance(value, str):
        return value.replace(home, render.PLACEHOLDER)
    if isinstance(value, list):
        return [_unrender(v, home) for v in value]
    if isinstance(value, dict):
        return {k: _unrender(v, home) for k, v in value.items()}
    return value


def _set_raw(raw: dict[str, Any], key: str, value: Any, home: str) -> None:
    section, _, rest = key.partition("/")
    if section != "hooks":
        if value is settings_merge.MISSING:
            raw[section].pop(rest, None)
        else:
            raw[section][rest] = value
        return
    event, _, rest = rest.partition("/")
    matcher, _, command = rest.partition("/")
    groups = raw["hooks"][event]
    for group in groups:
        if settings_merge._matcher(group) != matcher:
            continue
        for i, handler in enumerate(group["hooks"]):
            if handler["command"].replace(render.PLACEHOLDER, home) != command:
                continue
            if value is settings_merge.MISSING:
                del group["hooks"][i]
            else:
                group["hooks"][i] = value
            break
    groups[:] = [g for g in groups if g["hooks"]]
    if not groups:
        del raw["hooks"][event]


def settings_change(into: Path, home: Path) -> Change | None:
    """The fragment with the live owned values written back, or None when they match."""
    path = into / render.FRAGMENT
    raw = json.loads(path.read_text())
    rendered = render.render_fragment(into, home)
    live, _ = settings_merge.preflight(home / "settings.json")
    changed = False
    for key, wanted in settings_merge.owned_items(rendered).items():
        current = settings_merge.live_value(live, key)
        if current == wanted:
            continue
        unrendered = current if current is settings_merge.MISSING else _unrender(current, str(home))
        _set_raw(raw, key, unrendered, str(home))
        changed = True
    if not changed:
        return None
    return Change(render.FRAGMENT, data=(json.dumps(raw, indent=2) + "\n").encode())


# ---- the command ------------------------------------------------------------------


def _suites(into: Path, changed: list[str]) -> list[str]:
    gates = into / eval_records.GATES_PATH
    if not changed or not gates.is_file():
        return []
    out = []
    for name, suite in eval_records.parse_gates(gates.read_text()).items():
        by_path = any(eval_records._matches(p, suite.paths + suite.harness) for p in changed)
        by_selector = any(s["file"] in changed for s in suite.selectors)
        if by_path or by_selector:
            out.append(name)
    return sorted(out)


def capture(
    clone: Path,
    home: Path,
    data: Path,
    paths: list[str],
    into: Path,
    host: str | None = None,
) -> CaptureResult:
    st = state.load(data)
    if st is not None and Path(st.claude_home) != home:
        raise CfgError(f"this data directory was deployed to {st.claude_home}, not {home}")
    host = host or (st.host if st else None)
    if host is None:
        raise CfgError("nothing is deployed yet, so name the host profile with --host")
    check_branch(into)
    ds = render.render(into, host, home, data)
    known = {p.live for p in ds.paths} | {"settings.json"}
    unknown = sorted(set(paths) - known)
    if unknown:
        raise CfgError(
            f"{', '.join(unknown)} isn't a managed path; use one of: {', '.join(sorted(known))}"
        )
    wanted = set(paths) or known
    rel = release.releases_dir(data) / st.release if st else None
    changes: list[Change] = []
    for p in ds.paths:
        if p.live not in wanted:
            continue
        kind = link.classify(home / p.live, p.target, into / p.source)
        if kind == "linked" and rel is not None and rel.is_dir():
            changes += drift_changes(rel, p.source)
        elif kind == "differs":
            changes += mirror_changes(home / p.live, into / p.source, p.source)
    if "settings.json" in wanted:
        change = settings_change(into, home)
        if change is not None:
            changes.append(change)
    check_clean(into, [c.path for c in changes])
    result = CaptureResult(str(into))
    for c in changes:
        target = into / c.path
        if c.data is None and c.link is None:
            target.unlink(missing_ok=True)
            result.deleted.append(c.path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(target) and (target.is_symlink() or c.link is not None):
            target.unlink()
        if c.link is not None:
            os.symlink(c.link, target)
        else:
            target.write_bytes(c.data or b"")
        result.written.append(c.path)
    result.suites = _suites(into, result.written + result.deleted)
    return result
