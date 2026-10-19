"""Merge the repo's own keys into ~/.claude/settings.json, and write it safely.

settings.json is shared: Claude Code, /plugin and the owner all write it, so
it is never linked (ADR-0001). The repo owns exactly these items, named by
key in plan output and in the state file:

    enabledPlugins/<id>
    extraKnownMarketplaces/<name>
    hooks/<event>/<matcher, empty for none>/<command>

A hook handler's value is the whole handler object.

This grew from the v2 installer, install_v2.py. Its preflight, atomic write
and "keep every other key" rules carry over. Its hook merge appended entries
unconditionally, so a second run duplicated them; `merge` here leaves exactly
one handler per owned identity.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.cfg import state
from tools.cfg.errors import CfgError, SettingsChanged, SettingsUnusable

SECTIONS = ("enabledPlugins", "extraKnownMarketplaces")


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


@dataclass
class MergeResult:
    settings: dict[str, Any]
    changes: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)


def preflight(path: Path) -> tuple[dict[str, Any], bytes | None]:
    """The parsed settings and their exact bytes; ({}, None) when the file is missing."""
    if path.is_symlink():
        raise SettingsUnusable(f"{path} is a symlink; cfg won't rewrite it")
    if not path.exists():
        return {}, None
    if not path.is_file():
        raise SettingsUnusable(f"{path} is not a regular file")
    original = path.read_bytes()
    try:
        parsed = json.loads(original)
    except ValueError as e:
        raise SettingsUnusable(f"{path} doesn't parse as JSON ({e})") from e
    if not isinstance(parsed, dict):
        raise SettingsUnusable(f"{path} is not a JSON object")
    return parsed, original


def _matcher(group: dict[str, Any]) -> str:
    value = group.get("matcher")
    return value if isinstance(value, str) else ""


def _hook_key(event: str, matcher: str, command: str) -> str:
    return f"hooks/{event}/{matcher}/{command}"


def owned_items(fragment: dict[str, Any]) -> dict[str, Any]:
    """Every item a rendered fragment owns, in fragment order."""
    items: dict[str, Any] = {}
    for section in SECTIONS:
        for name, value in fragment.get(section, {}).items():
            items[f"{section}/{name}"] = value
    for event, groups in fragment.get("hooks", {}).items():
        for group in groups:
            matcher = _matcher(group)
            if "/" in matcher:
                raise CfgError(f"settings fragment matcher {matcher!r} can't hold a '/'")
            for handler in group["hooks"]:
                items[_hook_key(event, matcher, handler["command"])] = handler
    return items


def _split(key: str) -> tuple[str, ...]:
    section, _, rest = key.partition("/")
    if section != "hooks":
        return (section, rest)
    event, _, rest = rest.partition("/")
    matcher, _, command = rest.partition("/")
    return ("hooks", event, matcher, command)


def _hooks(settings: dict[str, Any], create: bool) -> dict[str, Any]:
    hooks = settings.setdefault("hooks", {}) if create else settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsUnusable("settings.json: hooks is not an object")
    return hooks


def _groups(settings: dict[str, Any], event: str, create: bool = False) -> list[Any]:
    hooks = _hooks(settings, create)
    groups = hooks.setdefault(event, []) if create else hooks.get(event, [])
    if not isinstance(groups, list):
        raise SettingsUnusable(f"settings.json: hooks.{event} is not a list")
    return groups


def _matching(groups: list[Any], matcher: str) -> list[dict[str, Any]]:
    return [
        g
        for g in groups
        if isinstance(g, dict) and _matcher(g) == matcher and isinstance(g.get("hooks"), list)
    ]


def _is_handler(handler: Any, command: str) -> bool:
    return isinstance(handler, dict) and handler.get("command") == command


def _section(settings: dict[str, Any], name: str, create: bool = False) -> dict[str, Any]:
    section = settings.setdefault(name, {}) if create else settings.get(name, {})
    if not isinstance(section, dict):
        raise SettingsUnusable(f"settings.json: {name} is not an object")
    return section


def live_value(settings: dict[str, Any], key: str) -> Any:
    """The live value of one owned item, or MISSING."""
    parts = _split(key)
    if parts[0] != "hooks":
        return _section(settings, parts[0]).get(parts[1], MISSING)
    _, event, matcher, command = parts
    for group in _matching(_groups(settings, event), matcher):
        for handler in group["hooks"]:
            if _is_handler(handler, command):
                return handler
    return MISSING


def live_values(settings: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: live_value(settings, key) for key in keys}


def _ensure_handler(
    settings: dict[str, Any], event: str, matcher: str, handler: dict[str, Any], spelled: bool
) -> bool:
    """Leave exactly one copy of `handler`, the fragment's; return whether anything changed."""
    groups = _groups(settings, event, create=True)
    found, changed = False, False
    emptied: list[dict[str, Any]] = []
    for group in _matching(groups, matcher):
        kept = []
        for existing in group["hooks"]:
            if not _is_handler(existing, handler["command"]):
                kept.append(existing)
            elif not found:
                found = True
                changed |= existing != handler
                kept.append(copy.deepcopy(handler))
            else:
                changed = True
        if group["hooks"] and not kept:
            emptied.append(group)
        group["hooks"] = kept
    groups[:] = [g for g in groups if not any(g is e for e in emptied)]
    if not found:
        same = _matching(groups, matcher)
        if same:
            same[0]["hooks"].append(copy.deepcopy(handler))
        else:
            head = {"matcher": matcher} if spelled else {}
            groups.append({**head, "hooks": [copy.deepcopy(handler)]})
        changed = True
    return changed


def _remove_handler(settings: dict[str, Any], event: str, matcher: str, command: str) -> None:
    hooks = settings["hooks"]
    groups = hooks[event]
    for group in _matching(groups, matcher):
        before = len(group["hooks"])
        group["hooks"] = [h for h in group["hooks"] if not _is_handler(h, command)]
        if before and not group["hooks"]:
            groups.remove(group)
    if not groups:
        del hooks[event]
    if not hooks:
        del settings["hooks"]


def _spelled_matchers(fragment: dict[str, Any]) -> set[tuple[str, str]]:
    """(event, matcher) pairs whose fragment group writes out a matcher key."""
    return {
        (event, _matcher(group))
        for event, groups in fragment.get("hooks", {}).items()
        for group in groups
        if "matcher" in group
    }


def merge(
    live: dict[str, Any], fragment: dict[str, Any], last_deployed: dict[str, Any]
) -> MergeResult:
    """The live settings with the fragment's items set; `live` itself is left alone.

    An item in `last_deployed` that has left the fragment is removed only when
    its live value still equals what was deployed; otherwise it is kept and
    listed in `kept`.
    """
    new = copy.deepcopy(live)
    result = MergeResult(new)
    wanted = owned_items(fragment)
    spelled = _spelled_matchers(fragment)
    for key, value in wanted.items():
        parts = _split(key)
        if parts[0] != "hooks":
            section = _section(new, parts[0], create=True)
            if section.get(parts[1], MISSING) != value:
                section[parts[1]] = copy.deepcopy(value)
                result.changes.append(f"set {key}")
            continue
        _, event, matcher, _ = parts
        if _ensure_handler(new, event, matcher, value, (event, matcher) in spelled):
            result.changes.append(f"set {key}")
    for key, deployed in last_deployed.items():
        if key in wanted:
            continue
        current = live_value(new, key)
        if current is MISSING:
            continue
        if current != deployed:
            result.kept.append(key)
            continue
        parts = _split(key)
        if parts[0] != "hooks":
            del new[parts[0]][parts[1]]
        else:
            _remove_handler(new, parts[1], parts[2], parts[3])
        result.changes.append(f"remove {key}")
    return result


def render_text(settings: dict[str, Any]) -> str:
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def write(
    path: Path,
    new: dict[str, Any],
    original: bytes | None,
    data_dir: Path,
    before_replace: Callable[[], None] | None = None,
    locked: bool = False,
) -> bool:
    """Write `new` over settings.json unless nothing changed; return whether it wrote.

    `original` is what preflight read. Right before the rename the file is read
    again; if another program changed it in between, nothing is written. A
    caller that already holds the cfg lock, such as a deploy, passes `locked`.
    """
    current = json.loads(original) if original is not None else {}
    if new == current:
        return False

    def unchanged_since_read() -> None:
        if before_replace is not None:
            before_replace()
        now = path.read_bytes() if path.exists() else None
        if now != original:
            raise SettingsChanged(
                f"another program changed {path} while cfg was writing it; "
                "nothing was written, so run the command again"
            )

    if locked:
        state.write_atomic(path, render_text(new).encode(), before_replace=unchanged_since_read)
        return True
    with state.cfg_lock(data_dir):
        state.write_atomic(path, render_text(new).encode(), before_replace=unchanged_since_read)
    return True
