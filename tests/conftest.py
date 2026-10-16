"""Shared fixtures for the git hook tests.

Fake secrets are assembled at runtime so that no committed file contains one.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tools import git_hooks

REPO_ROOT = Path(__file__).resolve().parent.parent
NOREPLY = "2057426+Towerism@users.noreply.github.com"

_TOKEN_PREFIXES = {
    "ghp": ("ghp", "_", "A1b2C3d4" * 4 + "E5f6"),
    "gho": ("gho", "_", "Z9y8X7w6" * 4 + "V5u4"),
    "github_pat": ("github", "_pat_", "11ABCDEFG0123456789_abcdefghijklmnop"),
    "sk": ("sk", "-", "ant-api03-" + "Q" * 24),
    "xoxb": ("xox", "b-", "1234567890-abcdefghij"),
}


def fake_token(kind: str = "ghp") -> str:
    head, sep, tail = _TOKEN_PREFIXES[kind]
    return head + sep + tail


def fake_email(local: str = "jane.doe", domain: str = "gmail" + ".com") -> str:
    return local + "@" + domain


@pytest.fixture
def isolated_env(tmp_path: Path) -> dict[str, str]:
    """A minimal environment: no global git config, no real HOME, no gitleaks."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("git", "python3", "sh"):
        found = shutil.which(tool)
        assert found, tool
        (bin_dir / tool).symlink_to(found)
    return {
        "HOME": str(home),
        "PATH": str(bin_dir),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "CLAUDE_CONFIG_DENY_TERMS": str(tmp_path / "deny-terms"),
        "LANG": "C.UTF-8",
    }


class Repo:
    def __init__(self, path: Path, env: dict[str, str]) -> None:
        self.path = path
        self.env = env

    def git(
        self,
        *args: str,
        check: bool = True,
        input: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=env,
            text=True,
            capture_output=True,
            check=check,
            input=input,
        )

    def write(self, rel: str, content: str | bytes) -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
        return target

    def commit(
        self,
        *paths: str,
        message: str = "chore: test (#1)",
        extra: tuple[str, ...] = (),
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if paths:
            self.git("add", "--", *paths)
        return self.git("commit", "-q", "-m", message, *extra, check=False, extra_env=extra_env)


@pytest.fixture
def make_repo(tmp_path: Path, isolated_env: dict[str, str]) -> Callable[..., Repo]:
    """Build a repo with this checkout's hooks copied in, untracked, and enabled."""

    def factory(name: str = "repo", email: str = NOREPLY) -> Repo:
        path = tmp_path / name
        path.mkdir()
        repo = Repo(path, isolated_env)
        repo.git("init", "-q", "-b", "main")
        repo.git("config", "user.name", "Test Owner")
        repo.git("config", "user.email", email)
        shutil.copytree(REPO_ROOT / ".githooks", path / ".githooks")
        (path / "tools").mkdir()
        shutil.copy2(REPO_ROOT / "tools" / "git_hooks.py", path / "tools" / "git_hooks.py")
        (path / ".git" / "info" / "exclude").write_text(".githooks/\ntools/\n")
        repo.git("config", "core.hooksPath", ".githooks")
        return repo

    return factory


@pytest.fixture
def repo(make_repo: Callable[..., Repo]) -> Repo:
    return make_repo()


@pytest.fixture
def fake_gitleaks(isolated_env: dict[str, str]) -> Callable[[int, str], None]:
    """Put a stand-in gitleaks on PATH that exits with a given code and report."""

    def install(code: int, report_json: str = "[]") -> None:
        script = Path(isolated_env["PATH"]) / "gitleaks"
        script.write_text(
            "#!/bin/sh\n"
            "while [ $# -gt 0 ]; do\n"
            '  if [ "$1" = "--report-path" ]; then shift; report="$1"; fi\n'
            "  shift\n"
            "done\n"
            f"printf '%s' '{report_json}' > \"$report\"\n"
            "echo 'gitleaks own output'\n"
            f"exit {code}\n"
        )
        script.chmod(0o755)

    return install


@pytest.fixture
def inproc(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> Repo:
    """Run the hook module in this process, inside the test repo, for coverage."""
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    for key, value in repo.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(repo.path)
    return repo


def run_main(
    argv: list[str], capsys: pytest.CaptureFixture[str], stdin: str = ""
) -> tuple[int, str]:
    code = git_hooks.main(argv, stdin=io.StringIO(stdin))
    captured = capsys.readouterr()
    assert captured.out == ""
    return code, captured.err
