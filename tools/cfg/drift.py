"""cfg status, plan and diff: compare the live config with the repo (ADR-0004).

None of these write anything under the Claude home, the data directory or the
clone. `plan` reads a commit through `git archive` into a temporary directory
outside both and removes it afterwards; every git call passes
--no-optional-locks.
"""

from __future__ import annotations

import difflib
import io
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from tools.cfg import link, lock, release, render, settings_merge, state
from tools.cfg.errors import CfgError

ItemState = Literal["same", "differs", "missing-live", "extra-live"]
DEFAULT_REF = "origin/main"


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str | None
    detail: str


@dataclass
class StatusResult:
    result: Literal["clean", "not-clean", "not-deployed"]
    sha: str | None = None
    release: str | None = None
    findings: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)


@dataclass(frozen=True)
class PathPlan:
    live: str
    state: link.PathState


@dataclass(frozen=True)
class ItemPlan:
    key: str
    state: ItemState


@dataclass
class PlanResult:
    ref: str
    paths: list[PathPlan]
    settings: list[ItemPlan]
    would_back_up: list[str]
    would_stop: bool
    diffs: list[tuple[str, str]]


# ---- diffs ------------------------------------------------------------------------


def _bytes_diff(old: bytes | None, new: bytes | None, label: str) -> str:
    """A unified diff from `old` (the repo's) to `new` (the live one); None means absent."""
    try:
        a = old.decode().splitlines(keepends=True) if old is not None else []
        b = new.decode().splitlines(keepends=True) if new is not None else []
    except UnicodeDecodeError:
        return f"binary files differ: {label}\n"
    lines = difflib.unified_diff(a, b, f"repo/{label}", f"live/{label}")
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def _file_diff(old: Path, new: Path, label: str) -> str:
    return _bytes_diff(old.read_bytes(), new.read_bytes(), label)


def _describe(entry: str) -> str:
    return f"link to {entry[5:]}" if entry.startswith("link:") else "a file"


def tree_diff(live: Path, source: Path) -> str:
    """How a plain live file or directory differs from the repo's copy."""
    if not (live.is_dir() and source.is_dir()):
        if live.is_file() and source.is_file():
            return _file_diff(source, live, live.name)
        live_kind = "directory" if live.is_dir() else "file"
        repo_kind = "directory" if source.is_dir() else "file"
        return f"type differs: the live copy is a {live_kind}, the repo has a {repo_kind}\n"
    ours, theirs = link.tree_entries(live), link.tree_entries(source)
    parts = []
    for rel in sorted(set(ours) | set(theirs)):
        mine, repo = ours.get(rel), theirs.get(rel)
        if mine == repo:
            continue
        if repo is None:
            parts.append(f"only in the live copy: {rel}\n")
        elif mine is None:
            parts.append(f"missing from the live copy: {rel}\n")
        elif mine.startswith("sha:") and repo.startswith("sha:"):
            parts.append(_file_diff(source / rel, live / rel, rel))
        else:
            parts.append(
                f"{rel}: {_describe(mine)} in the live copy, {_describe(repo)} in the repo\n"
            )
    return "".join(parts)


def _value_diff(key: str, deployed: Any, live: Any) -> str:
    def lines(value: Any) -> list[str]:
        if value is settings_merge.MISSING:
            return []
        return (json.dumps(value, indent=2, sort_keys=True) + "\n").splitlines(keepends=True)

    return "".join(difflib.unified_diff(lines(deployed), lines(live), f"repo {key}", f"live {key}"))


# ---- status -----------------------------------------------------------------------


def _check_current(data_dir: Path, st: state.State) -> list[Finding]:
    try:
        name = release.current_release(data_dir)
    except CfgError as e:
        return [Finding("broken", "current", str(e))]
    if name is None:
        return [Finding("broken", "current", f"{data_dir / 'current'} is missing")]
    if name != st.release:
        return [
            Finding("broken", "current", f"current doesn't point at release {st.release}: {name}")
        ]
    if not (release.releases_dir(data_dir) / name).is_dir():
        return [Finding("broken", "current", f"release {name} is gone from {data_dir}")]
    return []


def _deployed_paths(
    rel: Path, st: state.State, home: Path, data_dir: Path
) -> tuple[state.ManagedPath, ...]:
    """The deployed profile, rendered from its release; the state's copy if that can't render.

    A release whose own host profile was edited (drift) or that is gone still
    has its paths recorded in the state file.
    """
    if rel.is_dir():
        try:
            return render.render(rel, st.host, home, data_dir).paths
        except CfgError:
            pass
    return st.paths


def _check_paths(paths: tuple[state.ManagedPath, ...], home: Path, rel: Path) -> list[Finding]:
    found = []
    for p in paths:
        kind = link.classify(home / p.live, p.target, rel / p.source)
        if kind in ("missing", "foreign-link"):
            what = "is missing" if kind == "missing" else f"points at {os.readlink(home / p.live)}"
            found.append(Finding("broken", p.live, f"{home / p.live} {what}"))
        elif kind in ("match", "differs"):
            found.append(
                Finding("unlinked", p.live, f"{home / p.live} is a plain file or directory")
            )
    return found


def _check_settings(home: Path, st: state.State) -> list[Finding]:
    live, _ = settings_merge.preflight(home / "settings.json")
    found = []
    for key, value in settings_merge.live_values(live, st.settings).items():
        if value is settings_merge.MISSING:
            found.append(Finding("settings-drift", key, "gone from settings.json"))
        elif value != st.settings[key]:
            found.append(Finding("settings-drift", key, "changed since the last deploy"))
    return found


def _check_tip(clone: Path, st: state.State) -> tuple[list[Finding], list[Finding]]:
    try:
        tip = release.resolve(clone, DEFAULT_REF)
    except CfgError:
        return [], [Finding("off-tip", None, f"{DEFAULT_REF} is missing in {clone}")]
    if tip == st.sha:
        return [], []
    if release.is_ancestor(clone, st.sha, tip):
        return [Finding("behind", None, f"{DEFAULT_REF} is at {tip[:12]}, ahead of the deploy")], []
    return [], [Finding("off-tip", None, f"the deployed commit isn't on {DEFAULT_REF}")]


def _check_lock(home: Path, rel: Path, skills: list[str]) -> list[Finding]:
    warnings = []
    try:
        committed = json.loads((rel / "plugins.lock.json").read_text())
        diffs = lock.lock_differences(committed, lock.build_lock(home, rel))
    except (OSError, ValueError, lock.LockInputError) as e:
        warnings.append(Finding("plugin-lock", None, f"could not compare the plugin lock: {e}"))
    else:
        if diffs:
            warnings.append(Finding("plugin-lock", None, "; ".join(diffs)))
    clash = sorted(set(lock.synced_names(home)) & set(skills))
    if clash:
        warnings.append(
            Finding("synced-collision", None, f"synced skills share a name: {', '.join(clash)}")
        )
    return warnings


def _require_state(data_dir: Path, home: Path) -> state.State | None:
    st = state.load(data_dir)
    if st is not None and Path(st.claude_home) != home:
        raise CfgError(f"this data directory was deployed to {st.claude_home}, not {home}")
    return st


def status(clone: Path, data_dir: Path, claude_home: Path) -> StatusResult:
    """Every way the live config differs from the last deploy."""
    st = _require_state(data_dir, claude_home)
    if st is None:
        finding = Finding("not-deployed", None, f"no deploy state in {data_dir}")
        return StatusResult("not-deployed", findings=[finding])
    rel = release.releases_dir(data_dir) / st.release
    paths = _deployed_paths(rel, st, claude_home, data_dir)
    findings = _check_current(data_dir, st)
    findings += _check_paths(paths, claude_home, rel)
    if rel.is_dir():
        findings += [
            Finding("drift", p, "changed in the release") for p in release.dirty_paths(rel)
        ]
    findings += _check_settings(claude_home, st)
    behind, warnings = _check_tip(clone, st)
    findings += behind
    if rel.is_dir():
        skills = [p.live.split("/", 1)[1] for p in paths if p.live.startswith("skills/")]
        warnings += _check_lock(claude_home, rel, skills)
    return StatusResult(
        "not-clean" if findings else "clean", st.sha, st.release, findings, warnings
    )


# ---- plan -------------------------------------------------------------------------


def _extract(clone: Path, sha: str, home: Path) -> Path:
    base = Path(tempfile.gettempdir()).resolve()
    for forbidden in (home, clone):
        if base.is_relative_to(Path(forbidden).resolve()):
            raise CfgError(f"TMPDIR {base} is inside {forbidden}; point TMPDIR elsewhere")
    tmp = Path(tempfile.mkdtemp(prefix="cfg-plan-"))
    try:
        data = release.archive(clone, sha, "config", "hosts")
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(tmp, filter="data")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp


def _settings_plan(
    home: Path, fragment: dict[str, Any], st: state.State | None
) -> tuple[list[ItemPlan], list[tuple[str, str]]]:
    live, _ = settings_merge.preflight(home / "settings.json")
    wanted = settings_merge.owned_items(fragment)
    items, diffs = [], []
    for key, value in wanted.items():
        current = settings_merge.live_value(live, key)
        kind: ItemState = (
            "missing-live"
            if current is settings_merge.MISSING
            else "same"
            if current == value
            else "differs"
        )
        items.append(ItemPlan(key, kind))
        if kind != "same":
            diffs.append((key, _value_diff(key, value, current)))
    for key in st.settings if st else {}:
        current = settings_merge.live_value(live, key)
        if key not in wanted and current is not settings_merge.MISSING:
            items.append(ItemPlan(key, "extra-live"))
            diffs.append((key, _value_diff(key, settings_merge.MISSING, current)))
    return items, diffs


def plan(clone: Path, ref: str | None, host: str, claude_home: Path, data_dir: Path) -> PlanResult:
    """What a deploy of `ref` would do to the live config, read-only."""
    try:
        sha = release.resolve(clone, ref or DEFAULT_REF)
    except CfgError as e:
        if ref is None:
            raise CfgError(f"{clone} has no {DEFAULT_REF}; pass --ref <commit>") from e
        raise
    st = _require_state(data_dir, claude_home)
    tree = _extract(clone, sha, claude_home)
    try:
        ds = render.render(tree, host, claude_home, data_dir)
        paths, diffs = [], []
        for p in ds.paths:
            kind = link.classify(claude_home / p.live, p.target, tree / p.source)
            paths.append(PathPlan(p.live, kind))
            if kind == "differs":
                diffs.append((p.live, tree_diff(claude_home / p.live, tree / p.source)))
        items, item_diffs = _settings_plan(claude_home, ds.fragment, st)
    finally:
        shutil.rmtree(tree, ignore_errors=True)
    return PlanResult(
        ref=sha,
        paths=paths,
        settings=items,
        would_back_up=[p.live for p in paths if p.state in ("match", "differs", "foreign-link")],
        would_stop=any(p.state == "differs" for p in paths)
        or any(i.state == "differs" for i in items),
        diffs=diffs + item_diffs,
    )


# ---- diff -------------------------------------------------------------------------


def _drift_diff(rel: Path, path: str) -> str:
    """How one changed path in a release differs from the release's commit."""
    old = None
    if release.git(rel, "ls-files", "--", path).strip():
        with tarfile.open(fileobj=io.BytesIO(release.archive(rel, "HEAD", path))) as tar:
            member = tar.extractfile(path)
            old = member.read() if member is not None else b""
    new = (rel / path).read_bytes() if (rel / path).is_file() else None
    return _bytes_diff(old, new, path)


def diff(
    clone: Path, data_dir: Path, claude_home: Path, path: str | None = None
) -> list[tuple[str, str]]:
    """Unified diffs of the live config against the deployed release."""
    st = _require_state(data_dir, claude_home)
    if st is None:
        raise CfgError("nothing is deployed yet; cfg plan shows the differences")
    rel = release.releases_dir(data_dir) / st.release
    paths = _deployed_paths(rel, st, claude_home, data_dir)
    known = {p.live for p in paths} | {"settings.json"}
    if path is not None and path not in known:
        raise CfgError(f"{path} isn't a managed path; use one of: {', '.join(sorted(known))}")
    chosen = [p for p in paths if path in (None, p.live)]
    out: list[tuple[str, str]] = []
    if rel.is_dir():
        for dirty in release.dirty_paths(rel):
            under = any(dirty == p.source or dirty.startswith(p.source + "/") for p in chosen)
            if path is None or under:
                out.append((dirty, _drift_diff(rel, dirty)))
    for p in chosen:
        if link.classify(claude_home / p.live, p.target, rel / p.source) in ("match", "differs"):
            out.append((p.live, tree_diff(claude_home / p.live, rel / p.source)))
    if path in (None, "settings.json"):
        live, _ = settings_merge.preflight(claude_home / "settings.json")
        for key, value in settings_merge.live_values(live, st.settings).items():
            if value != st.settings[key]:
                out.append((key, _value_diff(key, st.settings[key], value)))
    return out
