"""Factories for the cfg tests: small payload trees, git repos and Claude homes.

Every test builds what it needs under tmp_path. Nothing here reads the real
~/.claude or ~/.local/share.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools.cfg import link, release, render, settings_merge, state

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

EMPTY_LOCK: dict[str, Any] = {"lockVersion": 1, "plugins": {}, "synced": []}

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
        "plugins.lock.json": json.dumps(EMPTY_LOCK) + "\n",
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


@pytest.fixture
def cfg_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point HOME, XDG_DATA_HOME and git's config at tmp_path, so no test sees the real ones."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "isolated-xdg"))
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)


def make_home(root: Path) -> Path:
    """An empty Claude home with an install record that matches EMPTY_LOCK."""
    home = root / ".claude"
    write_tree(home, {"plugins/installed_plugins.json": json.dumps({"plugins": {}})})
    return home


def deploy_with_library(
    repo: Path, home: Path, data: Path, host: str = "hosts/test.json"
) -> state.State:
    """Deploy HEAD with this issue's modules only, standing in for #8's cfg deploy."""
    name = release.create(repo, "HEAD", data)
    release.swap_current(data, name)
    ds = render.render(data / "releases" / name, host, home, data)
    for path in ds.paths:
        link.make_link(home / path.live, path.target)
    live, original = settings_merge.preflight(home / "settings.json")
    merged = settings_merge.merge(live, ds.fragment, {})
    settings_merge.write(home / "settings.json", merged.settings, original, data)
    st = state.State(
        host=host,
        claude_home=str(home),
        sha=release.release_sha(data / "releases" / name),
        release=name,
        deployed_at="2026-10-19T12:00:00Z",
        paths=ds.paths,
        settings=settings_merge.owned_items(ds.fragment),
    )
    state.save(data, st)
    return st


# ---- deploy fixtures (#8) -----------------------------------------------------------

# Hooks that read their event and print nothing, which the smoke test accepts.
QUIET_HOOK = "import sys\nsys.stdin.read()\n"
# A stand-in for the release's tools/check_config.py; the real one needs the real payload.
CHECKER_OK = "import sys\nsys.exit(0)\n"


def deploy_files(**changes: Any) -> dict[str, str]:
    files = payload_files(**changes)
    files["config/hooks/overlay.py"] = QUIET_HOOK
    files["config/hooks/guard.py"] = QUIET_HOOK
    files["tools/check_config.py"] = CHECKER_OK
    return files


def make_deploy_repo(root: Path) -> Path:
    """A repo cfg deploy can install: quiet hooks and a checker that passes."""
    write_tree(root, deploy_files())
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "payload")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def push(repo: Path) -> str:
    """Move origin/main to HEAD, as a merge on GitHub and a fetch would."""
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return git(repo, "rev-parse", "HEAD")


def make_live_install(home: Path, repo: Path, host: str = "hosts/test.json") -> None:
    """Plain copies of the payload in the Claude home, the way the v2 installer left them."""
    profile = json.loads((repo / host).read_text())
    copies = {"CLAUDE.md": "config/CLAUDE.global.md", "skill-overlays": "config/skill-overlays"}
    copies |= {f"hooks/{h}": f"config/hooks/{h}" for h in profile["hooks"]}
    copies |= {f"skills/{s}": f"config/skills/{s}" for s in profile["skills"]}
    for live, source in copies.items():
        src, dst = repo / source, home / live
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copyfile(src, dst)
    fragment = render.render_fragment(repo, home)
    settings = {"theme": "dark", **fragment}
    (home / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    write_tree(home, {"skills/other/SKILL.md": "mine\n", "hooks/extra.py": "pass\n"})


def inventory(*roots: Path, skip: tuple[str, ...] = ()) -> list[tuple[str, str, str, int]]:
    """Path, type, sha256 and mtime of everything under the roots, minus names in `skip`."""
    rows = []
    for root in roots:
        for path in sorted([root, *root.rglob("*")]) if root.exists() else []:
            if path.name in skip:
                continue
            if path.is_symlink():
                rows.append((str(path), "link", os.readlink(path), path.lstat().st_mtime_ns))
            elif path.is_file():
                rows.append((str(path), "file", state.sha256_file(path), path.stat().st_mtime_ns))
            else:
                rows.append((str(path), "dir", "", path.stat().st_mtime_ns))
    return rows
