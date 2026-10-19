"""Factories for the cfg tests: small payload trees, git repos and Claude homes.

Every test builds what it needs under tmp_path. Nothing here reads the real
~/.claude or ~/.local/share.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

FRAGMENT: dict[str, Any] = {
    "enabledPlugins": {"tools@market": True},
    "extraKnownMarketplaces": {"market": {"source": {"source": "github", "repo": "o/r"}}},
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Skill",
                "hooks": [
                    {"type": "command", "command": "python3 -I {claude_home}/hooks/overlay.py"}
                ],
            }
        ],
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": "python3 -I {claude_home}/hooks/guard.py"}
                ],
            }
        ],
    },
}

PROFILE: dict[str, Any] = {
    "name": "test",
    "claudeMd": "config/CLAUDE.global.md",
    "overlays": "all",
    "hooks": ["overlay.py", "guard.py"],
    "skills": ["alpha", "beta"],
    "plugins": ["tools@market"],
    "defaultLifecycle": "alpha",
}

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@users.noreply.github.com",
    "GIT_AUTHOR_DATE": "2026-10-19T12:00:00Z",
    "GIT_COMMITTER_DATE": "2026-10-19T12:00:00Z",
}


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def payload_files(
    profile: dict[str, Any] | None = None, fragment: dict[str, Any] | None = None
) -> dict[str, str]:
    """A small payload: a CLAUDE.md, one overlay, two hooks and two skills."""
    return {
        "config/CLAUDE.global.md": "# global\n",
        "config/skill-overlays/alpha.md": "overlay\n",
        "config/hooks/overlay.py": "print('overlay')\n",
        "config/hooks/guard.py": "print('guard')\n",
        "config/skills/alpha/SKILL.md": "---\nname: alpha\n---\n",
        "config/skills/alpha/scripts/run.py": "print('alpha')\n",
        "config/skills/beta/SKILL.md": "---\nname: beta\n---\n",
        "config/settings.fragment.json": json.dumps(fragment or FRAGMENT, indent=2) + "\n",
        "hosts/test.json": json.dumps(profile or PROFILE, indent=2) + "\n",
    }


def make_tree(root: Path, **changes: Any) -> Path:
    write_tree(root, payload_files(**changes))
    return root


def git(repo: Path, *args: str) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(repo), **GIT_ENV}
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout.strip()


def make_repo(root: Path) -> Path:
    """A git repo holding the small payload in one commit, with origin/main pointing at it."""
    make_tree(root)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "payload")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def commit_change(repo: Path, rel: str, text: str, message: str = "change") -> str:
    write_tree(repo, {rel: text})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")
