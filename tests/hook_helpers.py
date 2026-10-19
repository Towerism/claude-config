"""Load the Claude Code hooks and build the config directories they read.

The hooks run as `python3 -I <path>` with a JSON payload on standard input.
Tests load them fresh by path, so coverage counts them and no state leaks
between tests, and also run them as real processes to check exit codes and
streams.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from tests.checker_helpers import REPO_ROOT

HOOKS = REPO_ROOT / "config" / "hooks"


def load_hook(name: str) -> ModuleType:
    """A fresh copy of config/hooks/<name>.py, imported by path."""
    spec = importlib.util.spec_from_file_location(f"hook_{name}", HOOKS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_main(module: ModuleType, stdin: bytes, monkeypatch, capsys) -> tuple[str, str]:
    """Run the hook's main() in-process on raw stdin bytes; return (stdout, stderr)."""
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8"))
    assert module.main() is None
    out = capsys.readouterr()
    return out.out, out.err


def run_script(name: str, stdin: bytes, home: Path, **env: str) -> subprocess.CompletedProcess:
    """Run the hook as Claude Code does, with an environment built from scratch."""
    base = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home.parent),
        "LC_ALL": "C.UTF-8",
        "CLAUDE_HOME": str(home),
    }
    return subprocess.run(
        [sys.executable, "-I", str(HOOKS / f"{name}.py")],
        input=stdin,
        capture_output=True,
        env={**base, **env},
        timeout=10,
        check=False,
    )


def payload(**fields: Any) -> bytes:
    return json.dumps(fields).encode()


def make_home(
    root: Path,
    overlays: dict[str, str] | None = None,
    skills: dict[str, list[str]] | None = None,
    plugins: dict[str, dict[str, list[str]]] | None = None,
) -> Path:
    """A config directory like ~/.claude.

    overlays maps an overlay name to its text. skills maps a user skill name
    to the files under its directory (SKILL.md is always added). plugins maps
    "bundle@marketplace" to {skill name: files} for one install of that bundle.
    """
    home = root / "claude-home"
    (home / "skills").mkdir(parents=True)
    if overlays is not None:
        (home / "skill-overlays").mkdir()
        for name, text in overlays.items():
            (home / "skill-overlays" / f"{name}.md").write_text(text, encoding="utf-8")
    for name, files in (skills or {}).items():
        _skill_dir(home / "skills" / name, files)
    if plugins is not None:
        installed: dict[str, list[dict[str, str]]] = {}
        for key, bundle_skills in plugins.items():
            install = root / "plugin-cache" / key.replace("@", "-")
            for name, files in bundle_skills.items():
                _skill_dir(install / "skills" / name, files)
            installed[key] = [{"installPath": str(install)}]
        (home / "plugins").mkdir()
        (home / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": installed}), encoding="utf-8"
        )
    return home


def _skill_dir(path: Path, files: list[str]) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    for rel in files:
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text("x", encoding="utf-8")
