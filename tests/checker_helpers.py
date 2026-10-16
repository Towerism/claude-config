import json, os, subprocess, sys
from pathlib import Path
import pytest

IMPL = Path(__file__).resolve().parent.parent
HOOKS = IMPL / "deliverable" / "hooks"


def run_script(script: Path, payload, env_home: Path, args=()):
    """Run a hook/checker script as a subprocess, the way the harness does."""
    env = {"PATH": os.environ["PATH"], "CLAUDE_HOME": str(env_home), "HOME": str(env_home.parent)}
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, "-I", str(script), *args], input=data,
                          capture_output=True, text=True, env=env, timeout=30)


@pytest.fixture
def claude_home(tmp_path):
    home = tmp_path / ".claude"
    (home / "plugins").mkdir(parents=True)
    (home / "skill-overlays").mkdir()
    (home / "skills").mkdir()
    (home / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    return home


def make_skill(home: Path, bundle, version, name, files=(), active=True, marketplace="mkt"):
    """Create a plugin skill (bundle given) or a user skill (bundle=None)."""
    if bundle is None:
        root = home / "skills" / name
    else:
        install = home / "plugins" / "cache" / marketplace / bundle / version
        root = install / "skills" / name
        if active:
            ip = home / "plugins" / "installed_plugins.json"
            d = json.loads(ip.read_text())
            d["plugins"][f"{bundle}@{marketplace}"] = [{"scope": "user", "installPath": str(install), "version": version}]
            ip.write_text(json.dumps(d))
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return root


def make_overlay(home: Path, skill, body):
    p = home / "skill-overlays" / f"{skill}.md"
    p.write_text(body)
    return p


def hook_payload(kind, **kw):
    if kind == "tool":
        return {"hook_event_name": "PostToolUse", "tool_name": "Skill",
                "tool_input": {"skill": kw["skill"]}, "tool_response": {"success": True}}
    if kind == "expansion":
        return {"hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command",
                "command_name": kw["skill"], "command_args": kw.get("args", ""),
                "prompt": f"/{kw['skill']} {kw.get('args', '')}"}
    if kind == "submit":
        return {"hook_event_name": "UserPromptSubmit", "prompt": kw["prompt"]}
    if kind == "bash":
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": kw["command"]}}
    raise ValueError(kind)
