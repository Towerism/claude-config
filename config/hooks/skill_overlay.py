"""Inject the user's per-skill policy ("overlay") when a skill loads.

Events: PostToolUse (matcher Skill) and UserPromptExpansion / UserPromptSubmit
(user-typed /skill). Reads ~/.claude/skill-overlays/<skill>.md and rewrites
`references/…`, `scripts/…` style paths to absolute paths under the skill's
active base directory. Always exits 0; never blocks.
"""
import json
import os
import re
import sys
import traceback
from pathlib import Path

NAME = re.compile(r"^[a-z0-9-]+(:[a-z0-9-]+)?$")
REL_PATH = re.compile(r"`((?:references|scripts|templates|assets|evals)/[^`\s]+)`")
SLASH = re.compile(r"^\s*/([a-z0-9:-]+)(?:\s|$)")


class OverlayError(Exception):
    pass


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_HOME") or Path.home() / ".claude")


def skill_from_payload(d: dict):
    event = d.get("hook_event_name")
    if event == "PostToolUse":
        return event, (d.get("tool_input") or {}).get("skill")
    if event == "UserPromptExpansion":
        return event, d.get("command_name")
    if event == "UserPromptSubmit":
        m = SLASH.match(d.get("prompt") or "")
        return event, m.group(1) if m else None
    return event, None


def find_overlay(home: Path, skill: str):
    """Exact overlay, else the unique overlay whose name ends with :<skill>."""
    overlays = home / "skill-overlays"
    exact = overlays / f"{skill}.md"
    if exact.is_file():
        return skill, exact
    if ":" not in skill and overlays.is_dir():
        hits = [p for p in overlays.glob(f"*:{skill}.md")]
        if len(hits) == 1:
            return hits[0].name[:-3], hits[0]
    return None, None


def skill_dir(home: Path, skill: str) -> Path:
    if ":" not in skill:
        d = home / "skills" / skill
        if (d / "SKILL.md").is_file():
            return d
        raise OverlayError(f"user skill directory not found for {skill}")
    bundle, name = skill.split(":", 1)
    try:
        installed = json.loads((home / "plugins" / "installed_plugins.json").read_text())
    except Exception as e:
        raise OverlayError(f"cannot read installed_plugins.json: {e}") from e
    for key, installs in (installed.get("plugins") or {}).items():
        if key.split("@", 1)[0] != bundle:
            continue
        for inst in installs:
            d = Path(inst.get("installPath", "")) / "skills" / name
            if (d / "SKILL.md").is_file():
                return d
    raise OverlayError(f"no active install of {skill}")


def render(skill: str, body: str, base: Path) -> str:
    missing = []

    def absolutise(m):
        rel = m.group(1)
        target = base / rel
        if not target.exists():
            missing.append(rel)
        return f"`{target}`"

    text = REL_PATH.sub(absolutise, body)
    if missing:
        text += "\n" + "\n".join(f"missing: {rel}" for rel in missing)
    return f"Skill overlay for {skill}:\n{text}"


def emit(event: str, context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}))


def main() -> None:
    try:
        d = json.loads(sys.stdin.read())
        if not isinstance(d, dict):
            return
    except Exception:
        return
    event, skill = skill_from_payload(d)
    if not isinstance(skill, str) or not NAME.match(skill):
        return
    home = claude_home()
    resolved, overlay = find_overlay(home, skill)
    if overlay is None:
        return
    try:
        body = overlay.read_text()
        emit(event, render(resolved, body, skill_dir(home, resolved)))
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        reason = str(e).splitlines()[0] if str(e) else type(e).__name__
        emit(event, f"Skill overlay for {resolved} could not be loaded ({reason}); "
                    f"read ~/.claude/skill-overlays/{resolved}.md manually.")


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
