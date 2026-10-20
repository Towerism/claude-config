"""SessionStart and SubagentStart hook: tell the model which lifecycle skill this machine uses.

The global CLAUDE.md names no tracker. It says to start with the lifecycle
skill the repo's CLAUDE.md names, else the machine default stated at session
start. This hook states that default (ADR-0011), on SessionStart for the main
session and on SubagentStart for each subagent, which doesn't inherit
SessionStart context.

The default comes from, in order:
1. `defaultLifecycle` in the machine-local file, $XDG_CONFIG_HOME/claude-config/
   local.json or ~/.config/claude-config/local.json, which `cfg` never writes;
2. `defaultLifecycle` in the host profile of the deployment this file belongs
   to. The hook finds that deployment from its own resolved path:
   <data>/releases/<release>/config/hooks/lifecycle_default.py, then reads
   <data>/state.json for the profile's path inside the release.

It reads only those three files, opens each without blocking and only when it
is a regular file, never prints anything it read except a checked skill name
or profile path, and always exits 0 with nothing on stderr (ADR-0007, H1-H4).
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

EVENTS = ("SessionStart", "SubagentStart")
NAME = re.compile(r"[a-z0-9-]+(:[a-z0-9-]+)?")
NAME_MAX = 128
HOST = re.compile(r"hosts/[a-z0-9][a-z0-9-]*\.json")
STATE_CAP = 1024 * 1024
FILE_CAP = 64 * 1024
MISSING = "missing"
LOCAL = "claude-config/local.json"
HOOK = Path(__file__)
TAIL = (
    "Use it to start and close out code changes unless the repo's CLAUDE.md names its "
    "tracker; the repo's choice wins."
)


def load(path: Path, cap: int) -> tuple[dict[str, Any] | None, str]:
    """The JSON object in `path`, or None and why it can't be used ("missing" if absent).

    O_NONBLOCK makes opening a FIFO return at once instead of waiting for a
    writer; anything that isn't a regular file is then refused.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None, MISSING
    except OSError:
        return None, "it couldn't be read"
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None, "it couldn't be read"
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(cap + 1)
    if len(data) > cap:
        return None, f"it's larger than {cap // 1024} KiB"
    try:
        value = json.loads(data.decode("utf-8"))
    except ValueError:
        return None, "it isn't valid JSON"
    if not isinstance(value, dict):
        return None, "it isn't a JSON object"
    return value, ""


def skill_name(value: object) -> str | None:
    if isinstance(value, str) and len(value) <= NAME_MAX and NAME.fullmatch(value):
        return value
    return None


def local_file(env: Mapping[str, str]) -> tuple[Path, str]:
    """The local override's path, and how to name it without printing the home path."""
    xdg = env.get("XDG_CONFIG_HOME", "")
    if xdg and os.path.isabs(xdg):
        return Path(xdg) / LOCAL, f"$XDG_CONFIG_HOME/{LOCAL}"
    return Path.home() / ".config" / LOCAL, f"~/.config/{LOCAL}"


def host_default(hook: Path) -> tuple[str, str] | None:
    """(skill, profile path) from the host profile of the deployment `hook` belongs to."""
    parents = hook.resolve().parents
    if len(parents) < 5:
        return None
    release, data = parents[2], parents[4]
    state, _ = load(data / "state.json", STATE_CAP)
    host = state.get("host") if state else None
    if not (isinstance(host, str) and HOST.fullmatch(host)):
        return None
    profile, _ = load(release / host, FILE_CAP)
    name = skill_name(profile.get("defaultLifecycle")) if profile else None
    return (name, host) if name else None


def line(name: str, source: str) -> str:
    return f"Default lifecycle skill on this machine: `{name}` (set in {source}). {TAIL}"


def default_line(hook: Path, env: Mapping[str, str]) -> str | None:
    path, label = local_file(env)
    local, why = load(path, FILE_CAP)
    if local is not None and "defaultLifecycle" in local:
        name = skill_name(local["defaultLifecycle"])
        if name:
            return line(name, f"`{label}`, which overrides the host profile")
        why = "its defaultLifecycle isn't a skill name"
    found = host_default(hook)
    if found is None:
        return None
    text = line(found[0], f"`{found[1]}`")
    if why and why != MISSING:
        text += f" `{label}` was ignored: {why}."
    return text


def emit(event: str, context: str) -> None:
    print(
        json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}})
    )


def main() -> None:
    try:
        d = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return
    if not isinstance(d, dict):
        return
    event = next((e for e in EVENTS if d.get("hook_event_name") == e), None)
    if event is None:
        return
    try:
        text = default_line(HOOK, os.environ)
    except Exception:
        return
    if text:
        emit(event, text)


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
