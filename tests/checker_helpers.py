"""Factories and fixtures for the config checker tests.

Each test builds its own Claude home under tmp_path and runs the checker as a
subprocess with HOME and CLAUDE_HOME inside it, so no test reads the real
~/.claude.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "config" / "hooks"


def run_script(
    script: Path, payload: Any, env_home: Path, args: Iterable[str] = ()
) -> subprocess.CompletedProcess[str]:
    """Run a hook or checker script as a subprocess, the way the harness does."""
    env = {"PATH": os.environ["PATH"], "CLAUDE_HOME": str(env_home), "HOME": str(env_home.parent)}
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, "-I", str(script), *args],
        input=data,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    home = tmp_path / ".claude"
    (home / "plugins").mkdir(parents=True)
    (home / "skill-overlays").mkdir()
    (home / "skills").mkdir()
    (home / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {}})
    )
    return home


def make_skill(
    home: Path,
    bundle: str | None,
    version: str,
    name: str,
    files: Iterable[str] = (),
    active: bool = True,
    marketplace: str = "mkt",
) -> Path:
    """Create a plugin skill (bundle given) or a user skill (bundle=None)."""
    if bundle is None:
        root = home / "skills" / name
    else:
        install = home / "plugins" / "cache" / marketplace / bundle / version
        root = install / "skills" / name
        if active:
            ip = home / "plugins" / "installed_plugins.json"
            d = json.loads(ip.read_text())
            d["plugins"][f"{bundle}@{marketplace}"] = [
                {"scope": "user", "installPath": str(install), "version": version}
            ]
            ip.write_text(json.dumps(d))
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return root


def make_overlay(home: Path, skill: str, body: str) -> Path:
    p = home / "skill-overlays" / f"{skill}.md"
    p.write_text(body)
    return p


def make_lock(
    path: Path,
    plugins: dict[str, dict[str, Any]] | None = None,
    synced: Iterable[str] = (),
    version: int = 1,
) -> Path:
    """Write a plugin lock. Each plugin needs only "skills" and, optionally, "files"."""
    entries = {}
    for pid, p in (plugins or {}).items():
        marketplace = pid.split("@", 1)[1]
        entries[pid] = {
            "marketplace": marketplace,
            "marketplaceRepo": f"owner/{marketplace}",
            "marketplaceSource": "github",
            "version": "1.0.0",
            "gitCommitSha": "0" * 40,
            "skills": sorted(p.get("skills", [])),
            "files": p.get("files", {}),
        }
    lock = {"lockVersion": version, "plugins": entries, "synced": sorted(synced)}
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return path
