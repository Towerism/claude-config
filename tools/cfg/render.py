"""Turn a host profile into the deploy set: what gets linked where, and the settings keys.

`render` reads only the tree it is given, a release worktree or a commit
extracted for `cfg plan`, and never looks at the Claude home or the data
directory. The link targets it returns all go through `<data_dir>/current`,
so they stay the same from one release to the next (ADR-0001).
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools import check_config
from tools.cfg.errors import CfgError
from tools.cfg.state import Kind, ManagedPath

FRAGMENT = "config/settings.fragment.json"
FRAGMENT_KEYS = frozenset({"enabledPlugins", "extraKnownMarketplaces", "hooks"})
PLACEHOLDER = "{claude_home}"
_ANY_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


@dataclass(frozen=True)
class DeploySet:
    host: str
    paths: tuple[ManagedPath, ...]
    fragment: dict[str, Any]
    skills: tuple[str, ...]


def _check_name(kind: str, name: str) -> None:
    if name in ("", ".", "..") or "/" in name or "\\" in name:
        raise CfgError(f"{kind} name {name!r} can't be used as a single path component")


def _managed(source: str, live: str, kind: Kind, data_dir: Path) -> ManagedPath:
    return ManagedPath(live, source, kind, f"{data_dir}/current/{source}")


def _substitute(value: Any, home: str) -> Any:
    if isinstance(value, str):
        out = value.replace(PLACEHOLDER, home)
        other = _ANY_PLACEHOLDER.search(value.replace(PLACEHOLDER, ""))
        if other:
            raise CfgError(f"{FRAGMENT} holds the unknown placeholder {other.group()}")
        return out
    if isinstance(value, list):
        return [_substitute(v, home) for v in value]
    if isinstance(value, dict):
        return {_substitute(k, home): _substitute(v, home) for k, v in value.items()}
    return value


def render_fragment(tree: Path, claude_home: Path) -> dict[str, Any]:
    """The settings fragment with {claude_home} replaced by the absolute Claude home."""
    home = str(claude_home)
    if shlex.quote(home) != home:
        raise CfgError(
            f"Claude home {home!r} needs shell quoting; hook commands run through a shell, "
            "so use a path without spaces or shell characters"
        )
    try:
        raw = json.loads((tree / FRAGMENT).read_text())
    except (OSError, ValueError) as e:
        raise CfgError(f"{FRAGMENT} can't be read: {e}") from e
    if not isinstance(raw, dict):
        raise CfgError(f"{FRAGMENT} isn't a JSON object")
    unknown = sorted(set(raw) - FRAGMENT_KEYS)
    if unknown:
        raise CfgError(f"{FRAGMENT} has keys the repo doesn't own: {', '.join(unknown)}")
    rendered: dict[str, Any] = _substitute(raw, home)
    return rendered


def render(tree: Path, host: str, claude_home: Path, data_dir: Path) -> DeploySet:
    """The deploy set for `tree/host`, a profile path relative to the tree."""
    try:
        profile = check_config.load_profile(tree / host)
    except check_config.UsageError as e:
        raise CfgError(str(e)) from e
    if profile.overlays is not None:
        raise CfgError(
            f'{host}: overlays must be "all"; a directory link can\'t select single overlays'
        )
    for name in profile.skills:
        _check_name("skill", name)
    for name in profile.hooks:
        _check_name("hook", name)
    config = (tree / "config").resolve()
    claude_md = profile.claude_md.resolve()
    if not claude_md.is_relative_to(config):
        raise CfgError(f"{host}: claudeMd must be a file under config/")
    missing = [
        f"skill {s}" for s in profile.skills if not (profile.skill_path(s) / "SKILL.md").is_file()
    ]
    missing += [f"hook {h}" for h in profile.hooks if not (tree / "config" / "hooks" / h).is_file()]
    if not profile.overlay_dir.is_dir():
        missing.append("config/skill-overlays")
    if missing:
        raise CfgError(f"{host} lists what the tree doesn't have: {', '.join(missing)}")

    paths = [
        _managed(str(claude_md.relative_to(tree.resolve())), "CLAUDE.md", "file", data_dir),
        _managed("config/skill-overlays", "skill-overlays", "dir", data_dir),
    ]
    paths += [_managed(f"config/hooks/{h}", f"hooks/{h}", "file", data_dir) for h in profile.hooks]
    paths += [
        _managed(f"config/skills/{s}", f"skills/{s}", "dir", data_dir) for s in profile.skills
    ]
    return DeploySet(
        host=host,
        paths=tuple(paths),
        fragment=render_fragment(tree, claude_home),
        skills=tuple(profile.skills),
    )
