"""cfg deploy and cfg rollback: move the live config from one release to another.

Deploy installs a commit (origin/main as last fetched, or --ref) as a release
worktree and links the live config through it (ADR-0001). It changes nothing
until it has found every conflict (ADR-0004), checked the release with its own
config checker and smoke-tested the hooks, and been told to continue. From the
first change on, every step is recorded in a backup directory whose restore.sh
undoes it. Rollback points `current` back at the previous release.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.cfg import drift, link, release, render, settings_merge, state
from tools.cfg.errors import CfgError

KEEP_RELEASES = 5
SMOKE_TIMEOUT = 5.0
RESTORE_SH = '#!/bin/sh\nexec python3 -I "$(dirname "$0")/restore.py" "$@"\n'
RESTORE_PY = Path(__file__).with_name("restore.py")

Clock = Callable[[], datetime]
Step = Callable[[str], None]
Confirm = Callable[[list[str]], bool]


class DeployFailed(CfgError):
    """A failure after the live config started changing; the message names the undo."""


@dataclass(frozen=True)
class Conflict:
    kind: str  # "path", "drift" or "settings"
    path: str  # the managed path, or settings.json
    detail: str  # the file inside the release, or the settings key
    diff: str


@dataclass
class DeployResult:
    result: str
    sha: str = ""
    release: str = ""
    conflicts: list[Conflict] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    linked: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    settings_changes: list[str] = field(default_factory=list)
    backup: str | None = None
    warnings: list[str] = field(default_factory=list)
    first: bool = False


@dataclass
class _Plan:
    """What a deploy will do, worked out before anything changes."""

    sha: str
    ds: render.DeploySet
    kinds: dict[str, link.PathState]
    move: list[state.ManagedPath]
    link: list[state.ManagedPath]
    unlink: list[state.ManagedPath]
    merge: settings_merge.MergeResult
    live_settings: dict[str, Any]
    original_settings: bytes | None
    discarded: list[str]
    overlay: str | None


def utc_now() -> datetime:
    return datetime.now(UTC)


def _stamp(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


# ---- finding conflicts ------------------------------------------------------------


def _owner(dirty: str, paths: tuple[state.ManagedPath, ...]) -> state.ManagedPath | None:
    for p in paths:
        if dirty == p.source or dirty.startswith(p.source + "/"):
            return p
    return None


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() and not path.is_symlink() else None


def drift_conflicts(
    st: state.State | None, data: Path, tree: Path
) -> tuple[list[Conflict], list[str]]:
    """Edits in the deployed release that the target doesn't already have."""
    if st is None:
        return [], []
    rel = release.releases_dir(data) / st.release
    if not rel.is_dir():
        return [], []
    conflicts, warnings = [], []
    for dirty in release.dirty_paths(rel):
        owner = _owner(dirty, st.paths)
        if owner is None:
            warnings.append(f"{dirty} changed in release {st.release}, outside the managed paths")
            continue
        mine, theirs = _read(rel / dirty), _read(tree / dirty)
        if mine != theirs:
            diff = drift.bytes_diff(theirs, mine, dirty)
            conflicts.append(Conflict("drift", owner.live, dirty, diff))
    return conflicts, warnings


def settings_conflicts(
    live: dict[str, Any], fragment: dict[str, Any], st: state.State | None
) -> list[Conflict]:
    """Owned items someone else changed, which the target would overwrite."""
    out = []
    for key, wanted in settings_merge.owned_items(fragment).items():
        current = settings_merge.live_value(live, key)
        if current is settings_merge.MISSING or current == wanted:
            continue
        if st is not None and current == st.settings.get(key, settings_merge.MISSING):
            continue
        out.append(
            Conflict("settings", "settings.json", key, drift.value_diff(key, wanted, current))
        )
    return out


def _check_discards(discard: tuple[str, ...], conflicts: list[Conflict]) -> None:
    named = {c.path for c in conflicts}
    extra = sorted(set(discard) - named)
    if extra:
        raise CfgError(
            f"--discard-live {', '.join(extra)}: no conflict there in this deploy; "
            "nothing to discard"
        )


# ---- the plan ---------------------------------------------------------------------


def _first_overlay(tree: Path) -> str | None:
    names = sorted(p.stem for p in (tree / "config" / "skill-overlays").glob("*.md"))
    return names[0] if names else None


def _target(clone: Path, ref: str | None) -> tuple[str, list[str]]:
    try:
        sha = release.resolve(clone, ref or drift.DEFAULT_REF)
    except CfgError as e:
        if ref is None:
            raise CfgError(f"{clone} has no {drift.DEFAULT_REF}; pass --ref <commit>") from e
        raise
    try:
        tip = release.resolve(clone, drift.DEFAULT_REF)
    except CfgError:
        return sha, [f"{drift.DEFAULT_REF} is missing; deploying {sha[:12]} as asked"]
    if release.is_ancestor(clone, sha, tip):
        return sha, []
    return sha, [f"{sha[:12]} isn't on {drift.DEFAULT_REF}; cfg status will flag it"]


def plan(
    clone: Path,
    home: Path,
    data: Path,
    host: str,
    ref: str | None,
    discard: tuple[str, ...],
    st: state.State | None,
    result: DeployResult,
) -> _Plan | None:
    """Work out the deploy, or fill `result` with the conflicts and return None."""
    live, original = settings_merge.preflight(home / "settings.json")
    sha, warnings = _target(clone, ref)
    result.sha, result.warnings = sha, warnings
    tree = drift.extract_tree(clone, sha, home)
    try:
        ds = render.render(tree, host, home, data)
        kinds = {p.live: link.classify(home / p.live, p.target, tree / p.source) for p in ds.paths}
        conflicts = [
            Conflict("path", p.live, p.live, drift.tree_diff(home / p.live, tree / p.source))
            for p in ds.paths
            if kinds[p.live] == "differs"
        ]
        dirty, dirty_warnings = drift_conflicts(st, data, tree)
        overlay = _first_overlay(tree)
    finally:
        shutil.rmtree(tree, ignore_errors=True)
    conflicts += dirty + settings_conflicts(live, ds.fragment, st)
    result.warnings += dirty_warnings
    _check_discards(discard, conflicts)
    left = [c for c in conflicts if c.path not in discard]
    if left:
        result.result, result.conflicts = "stopped", left
        return None
    wanted = {p.live for p in ds.paths}
    unlink = []
    for old in st.paths if st else ():
        if old.live in wanted:
            continue
        path = home / old.live
        if path.is_symlink() and os.readlink(path) == old.target:
            unlink.append(old)
        elif os.path.lexists(path):
            result.warnings.append(f"{old.live} left the profile but isn't cfg's link; left alone")
    move = [p for p in ds.paths if kinds[p.live] in ("match", "differs", "foreign-link")]
    return _Plan(
        sha=sha,
        ds=ds,
        kinds=kinds,
        move=move,
        link=[p for p in ds.paths if kinds[p.live] != "linked"],
        unlink=unlink,
        merge=settings_merge.merge(live, ds.fragment, st.settings if st else {}),
        live_settings=live,
        original_settings=original,
        discarded=[p.live for p in ds.paths if kinds[p.live] == "differs"],
        overlay=overlay,
    )


def change_list(pl: _Plan, current: str | None, name: str) -> list[str]:
    lines = [f"back up and link {p.live} ({pl.kinds[p.live]})" for p in pl.move]
    lines += [f"link {p.live}" for p in pl.link if p not in pl.move]
    lines += [f"remove cfg's link {p.live} (left the profile)" for p in pl.unlink]
    lines += [f"settings.json: {c}" for c in pl.merge.changes]
    if current != name:
        lines.append(f"switch current from {current or 'nothing'} to {name}")
    return lines


# ---- checks on the release --------------------------------------------------------


def run_checker(rel: Path, host: str, home: Path) -> None:
    """The release's own config checker, offline against its plugin lock."""
    argv = [
        "python3",
        "-I",
        str(rel / "tools" / "check_config.py"),
        "--host",
        str(rel / host),
        "--plugin-index",
        str(rel / "plugins.lock.json"),
        "--claude-home",
        str(home),
    ]
    done = subprocess.run(argv, cwd=rel, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise CfgError(
            f"the release's config checker failed (exit {done.returncode}):\n"
            f"{done.stdout}{done.stderr}"
        )


def _handlers(fragment: dict[str, Any]) -> list[tuple[str, str, str]]:
    out = []
    for event, groups in fragment.get("hooks", {}).items():
        for group in groups:
            matcher = settings_merge._matcher(group)
            for handler in group["hooks"]:
                out.append((event, matcher, handler["command"]))
    return out


def _expect_ask(out: dict[str, Any] | None) -> str | None:
    decision = ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision")
    return None if decision == "ask" else "didn't ask before a merge"


def _expect_nothing(out: dict[str, Any] | None) -> str | None:
    return None if out is None else "printed output for a harmless command"


def _expect_context(out: dict[str, Any] | None) -> str | None:
    context = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext")
    if not isinstance(context, str) or not context:
        return "printed no overlay"
    if "could not be loaded" in context:
        return "couldn't load the overlay"
    return None


LIFECYCLE_START = "Default lifecycle skill on this machine:"


def _states_default(out: dict[str, Any] | None) -> bool:
    context = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext")
    return isinstance(context, str) and context.startswith(LIFECYCLE_START)


def _expect_default(out: dict[str, Any] | None) -> str | None:
    return None if _states_default(out) else "stated no default lifecycle"


def _expect_default_or_nothing(out: dict[str, Any] | None) -> str | None:
    """Before the switch a first deploy has no state file, so the hook may stay quiet."""
    return None if out is None else _expect_default(out)


Check = Callable[[dict[str, Any] | None], str | None]


def smoke_cases(
    event: str, matcher: str, script: str, overlay: str | None, after: bool = False
) -> list[tuple[Any, Check | None]]:
    """The sample events for one handler, each with what it must print.

    `after` is true once current points at the new release and its state file
    is written, so the lifecycle hook can find its host profile.
    """
    if script == "lifecycle_default.py" and event in ("SessionStart", "SubagentStart"):
        check = _expect_default if after else _expect_default_or_nothing
        return [({"hook_event_name": event}, check)]
    if script == "merge_confirm.py" and event == "PreToolUse":
        tool = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
        return [
            ({**tool, "tool_input": {"command": "gh pr merge 1"}}, _expect_ask),
            ({**tool, "tool_input": {"command": "ls"}}, _expect_nothing),
        ]
    if script == "skill_overlay.py" and event == "PostToolUse" and overlay:
        payload = {"hook_event_name": event, "tool_name": "Skill", "tool_input": {"skill": overlay}}
        return [(payload, _expect_context)]
    if script == "skill_overlay.py" and event == "UserPromptExpansion" and overlay:
        return [({"hook_event_name": event, "command_name": overlay}, _expect_context)]
    return [({}, None)]


def _one_case(
    argv: list[str], payload: Any, check: Check | None, env: dict[str, str], timeout: float
) -> str | None:
    try:
        done = subprocess.run(
            argv,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"took longer than {timeout:g} s"
    except OSError as e:
        return f"couldn't run: {e}"
    if done.returncode != 0:
        return f"exited {done.returncode}"
    if done.stderr:
        return f"wrote to stderr: {done.stderr.strip()[:200]}"
    out: dict[str, Any] | None = None
    if done.stdout.strip():
        try:
            parsed = json.loads(done.stdout)
        except ValueError:
            return "printed something that isn't JSON"
        if not isinstance(parsed, dict):
            return "printed JSON that isn't an object"
        out = parsed
    return check(out) if check else None


def smoke_test(
    fragment: dict[str, Any],
    home: Path,
    data: Path,
    overlay: str | None,
    rel: Path | None = None,
    timeout: float = SMOKE_TIMEOUT,
) -> None:
    """Run every owned hook handler on sample events; raise CfgError listing what failed.

    With `rel`, the scripts run from that release against a temporary Claude
    home holding the release's overlays and the live plugins, before any live
    change. Without it, the rendered commands run as Claude Code would run them.
    """
    problems = []
    env = dict(os.environ)
    tmp: Path | None = None
    if rel is not None:
        tmp = data / f".smoke-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        os.symlink(rel / "config" / "skill-overlays", tmp / "skill-overlays")
        os.symlink(rel / "config" / "skills", tmp / "skills")
        if (home / "plugins").exists():
            os.symlink(home / "plugins", tmp / "plugins")
    env["CLAUDE_HOME"] = str(tmp if tmp is not None else home)
    try:
        for event, matcher, command in _handlers(fragment):
            argv = shlex.split(command)
            if rel is not None:
                prefix = f"{home}/hooks/"
                argv = [
                    str(rel / "config" / "hooks" / a[len(prefix) :]) if a.startswith(prefix) else a
                    for a in argv
                ]
            script = Path(argv[-1]).name if argv else ""
            for payload, check in smoke_cases(event, matcher, script, overlay, rel is None):
                why = _one_case(argv, payload, check, env, timeout)
                if why:
                    problems.append(f"{event}/{matcher or '-'} {script}: {why}")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    if problems:
        when = "before" if rel is not None else "after"
        raise CfgError(f"the hook smoke test failed {when} the switch:\n  " + "\n  ".join(problems))


# ---- changing the live config -----------------------------------------------------


def _new_backup_dir(home: Path, now: datetime) -> Path:
    base = home / "backups" / "claude-config"
    base.mkdir(parents=True, exist_ok=True)
    name, n = _stamp(now), 1
    while (base / name).exists():
        n += 1
        name = f"{_stamp(now)}-{n}"
    path = base / name
    path.mkdir()
    return path


def _settings_entries(pl: _Plan) -> list[dict[str, Any]]:
    out = []
    for change in pl.merge.changes:
        key = change.split(" ", 1)[1]
        entry: dict[str, Any] = {"key": key}
        before = settings_merge.live_value(pl.live_settings, key)
        after = settings_merge.live_value(pl.merge.settings, key)
        if before is not settings_merge.MISSING:
            entry["before"] = before
        if after is not settings_merge.MISSING:
            entry["after"] = after
        out.append(entry)
    return out


def write_backup_files(backup: Path, pl: _Plan, home: Path, data: Path, first: bool) -> None:
    shutil.copyfile(RESTORE_PY, backup / "restore.py")
    (backup / "restore.sh").write_text(RESTORE_SH)
    (backup / "restore.sh").chmod(0o755)
    meta = {
        "schema": 1,
        "claudeHome": str(home),
        "dataDir": str(data),
        "firstDeploy": first,
        "moved": [p.live for p in pl.move],
        "links": [{"live": p.live, "target": p.target} for p in pl.link],
        "removedLinks": [{"live": p.live, "target": p.target} for p in pl.unlink],
        "settings": _settings_entries(pl),
    }
    state.write_atomic(backup / "restore.json", (json.dumps(meta, indent=2) + "\n").encode())
    if pl.merge.changes and pl.original_settings is not None:
        (backup / "settings.json").write_bytes(pl.original_settings)


def prune(clone: Path, data: Path, history: list[str], current: str) -> tuple[list[str], list[str]]:
    """Remove old clean releases; return the history left and any warnings."""
    keep = set(history[-KEEP_RELEASES:]) | {current}
    left, warnings = [], []
    for name in history:
        path = release.releases_dir(data) / name
        try:
            if name in keep or not path.is_dir() or release.dirty_paths(path):
                left.append(name)
                continue
            release.remove(clone, data, name)
        except CfgError as e:
            warnings.append(f"couldn't prune release {name}: {e}")
            left.append(name)
    return left, warnings


def _undo_hint(backup: Path, st: state.State | None) -> str:
    hint = f"undo with {backup / 'restore.sh'}"
    if st is not None:
        hint += ", or switch back with cfg rollback"
    return hint


def deploy(
    clone: Path,
    home: Path,
    data: Path,
    host: str,
    ref: str | None = None,
    discard: tuple[str, ...] = (),
    yes: bool = False,
    confirm: Confirm | None = None,
    clock: Clock = utc_now,
    step: Step | None = None,
    before_replace: Callable[[], None] | None = None,
    smoke_timeout: float = SMOKE_TIMEOUT,
) -> DeployResult:
    result = DeployResult("deployed")
    with state.cfg_lock(data):
        st = state.load(data)
        result.first = st is None
        if st is not None and Path(st.claude_home) != home:
            raise CfgError(f"this data directory was deployed to {st.claude_home}, not {home}")
        pl = plan(clone, home, data, host, ref, discard, st, result)
        if pl is None:
            return result
        dirty_same = (
            st is not None
            and (release.releases_dir(data) / st.release).is_dir()
            and release.release_sha(release.releases_dir(data) / st.release) == pl.sha
            and bool(release.dirty_paths(release.releases_dir(data) / st.release))
        )
        name = release.create(clone, pl.sha, data, fresh=dirty_same)
        rel = release.releases_dir(data) / name
        result.release = name
        run_checker(rel, host, home)
        smoke_test(pl.ds.fragment, home, data, pl.overlay, rel=rel, timeout=smoke_timeout)
        current = release.current_release(data)
        result.changes = change_list(pl, current, name)
        write_settings = pl.merge.settings != pl.live_settings
        if not result.changes and st is not None and st.release == name:
            result.result = "already-deployed"
            return result
        if result.changes and not yes and not (confirm is not None and confirm(result.changes)):
            result.result = "needs-confirmation"
            return result
        needs_backup = bool(pl.link or pl.unlink or write_settings)
        backup: Path | None = None
        if needs_backup:
            backup = _new_backup_dir(home, clock())
            write_backup_files(backup, pl, home, data, st is None)
            result.backup = str(backup)
        _apply(
            clone,
            home,
            data,
            pl,
            st,
            name,
            backup,
            write_settings,
            result,
            clock,
            step,
            before_replace,
            smoke_timeout,
        )
    return result


def _apply(
    clone: Path,
    home: Path,
    data: Path,
    pl: _Plan,
    st: state.State | None,
    name: str,
    backup: Path | None,
    write_settings: bool,
    result: DeployResult,
    clock: Clock,
    step: Step | None,
    before_replace: Callable[[], None] | None,
    smoke_timeout: float,
) -> None:
    def done(what: str) -> None:
        if step is not None:
            step(what)

    try:
        if backup is not None:
            done("backup-files")
            link.backup([home / p.live for p in pl.move], home, backup)
            done("backup")
        release.swap_current(data, name)
        done("swap")
        for p in pl.link:
            link.make_link(home / p.live, p.target)
        done("link")
        for p in pl.unlink:
            (home / p.live).unlink()
        done("unlink")
        if write_settings:
            settings_merge.write(
                home / "settings.json",
                pl.merge.settings,
                pl.original_settings,
                data,
                before_replace=before_replace,
                locked=True,
            )
        done("settings")
        history = [h for h in (st.history if st else ()) if h != name] + [name]
        backups = [*(st.backups if st else ()), *([str(backup)] if backup else [])]
        new_state = state.State(
            host=pl.ds.host,
            claude_home=str(home),
            sha=pl.sha,
            release=name,
            deployed_at=clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            paths=pl.ds.paths,
            settings=settings_merge.owned_items(pl.ds.fragment),
            backups=tuple(backups),
            history=tuple(history),
        )
        state.save(data, new_state)
        done("state")
        smoke_test(pl.ds.fragment, home, data, pl.overlay, timeout=smoke_timeout)
        done("smoke")
    except Exception as e:
        hint = _undo_hint(backup, st) if backup else "run cfg rollback" if st else "nothing to undo"
        raise DeployFailed(f"deploy failed after changing the live config: {e}\n{hint}") from e
    kept, warnings = prune(clone, data, list(new_state.history), name)
    if kept != list(new_state.history):
        state.save(data, state.State(**{**vars(new_state), "history": tuple(kept)}))
    result.warnings += warnings
    result.linked = [p.live for p in pl.link]
    result.matched = [p.live for p in pl.move if pl.kinds[p.live] == "match"]
    result.discarded = [p for p in pl.discarded]
    result.removed = [p.live for p in pl.unlink]
    result.settings_changes = list(pl.merge.changes)


# ---- rollback ---------------------------------------------------------------------


@dataclass
class RollbackResult:
    left: str
    to: str
    relinked: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    left_alone: list[str] = field(default_factory=list)
    settings_differ: bool = False
    command: str | None = None
    warnings: list[str] = field(default_factory=list)


def _earlier(clone: Path, data: Path, st: state.State) -> str:
    history = list(st.history)
    end = history.index(st.release) if st.release in history else len(history)
    for name in reversed(history[:end]):
        if name != st.release and release.is_release(release.releases_dir(data) / name, clone):
            return name
    raise CfgError("there is no earlier release to roll back to")


def rollback(clone: Path, home: Path, data: Path) -> RollbackResult:
    """Point current at the release before the deployed one, and fix cfg's own links."""
    with state.cfg_lock(data):
        st = state.load(data)
        if st is None:
            raise CfgError("nothing is deployed yet, so there is nothing to roll back")
        if Path(st.claude_home) != home:
            raise CfgError(f"this data directory was deployed to {st.claude_home}, not {home}")
        name = _earlier(clone, data, st)
        rel = release.releases_dir(data) / name
        ds = render.render(rel, st.host, home, data)
        live, _ = settings_merge.preflight(home / "settings.json")
        result = RollbackResult(left=st.release, to=name)
        release.swap_current(data, name)
        try:
            _after_swap(home, data, st, name, rel, ds, live, result)
        except Exception as e:
            raise DeployFailed(
                f"rollback failed after switching current to {name}: {e}\n"
                "run cfg status to see what is linked, then cfg deploy to go forward again"
            ) from e
        return result


def _after_swap(
    home: Path,
    data: Path,
    st: state.State,
    name: str,
    rel: Path,
    ds: render.DeploySet,
    live: dict[str, Any],
    result: RollbackResult,
) -> None:
    """Rollback's work once current points at the earlier release."""
    for p in ds.paths:
        path = home / p.live
        if not os.path.lexists(path):
            link.make_link(path, p.target)
            result.relinked.append(p.live)
        elif not (path.is_symlink() and os.readlink(path) == p.target):
            result.left_alone.append(p.live)
    wanted = {p.live for p in ds.paths}
    for old in st.paths:
        path = home / old.live
        if old.live in wanted or not os.path.lexists(path):
            continue
        if path.is_symlink() and os.readlink(path) == old.target:
            path.unlink()
            result.removed.append(old.live)
        else:
            result.left_alone.append(old.live)
    sha = release.release_sha(rel)
    state.save(data, state.State(**{**vars(st), "release": name, "sha": sha, "paths": ds.paths}))
    owned = settings_merge.owned_items(ds.fragment)
    result.settings_differ = any(
        settings_merge.live_value(live, k) != v for k, v in owned.items()
    ) or any(
        k not in owned and settings_merge.live_value(live, k) is not settings_merge.MISSING
        for k in st.settings
    )
    if result.settings_differ:
        result.command = f"cfg deploy --host {st.host} --ref {sha}"
    left = release.releases_dir(data) / st.release
    if left.is_dir() and release.dirty_paths(left):
        result.warnings.append(f"the edits in release {st.release} stay there on disk")
