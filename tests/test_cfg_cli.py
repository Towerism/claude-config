"""The cfg command line: plan, status, diff, and the forwarded check, lock and eval."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.cfg_helpers import deploy_with_library, git, make_home, make_repo
from tools.cfg import cli
from tools.cfg.errors import CfgError

pytestmark = pytest.mark.usefixtures("cfg_isolation")
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "clone")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return make_home(tmp_path / "home")


def base_dir() -> Path:
    return Path(os.environ["XDG_DATA_HOME"]) / "claude-config"


def inventory(*roots: Path) -> list[tuple[str, str, str, int]]:
    """Path, type, sha256 and mtime of everything under the roots."""
    rows = []
    for root in roots:
        for path in sorted([root, *root.rglob("*")]) if root.exists() else []:
            if path.is_symlink():
                rows.append((str(path), "link", os.readlink(path), path.lstat().st_mtime_ns))
            elif path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                rows.append((str(path), "file", digest, path.stat().st_mtime_ns))
            else:
                rows.append((str(path), "dir", "", path.stat().st_mtime_ns))
    return rows


def run(repo: Path, home: Path, *args: str) -> int:
    return cli.main([*args, "--repo", str(repo), "--claude-home", str(home)])


def test_status_before_any_deploy_is_not_deployed(
    repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(repo, home, "status") == 1
    assert "not-deployed" in capsys.readouterr().out


def test_status_of_a_clean_deploy_exits_0_and_prints_json(
    repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    st = deploy_with_library(repo, home, cli.data_dir(home))
    assert run(repo, home, "status") == 0
    assert "clean" in capsys.readouterr().out
    assert run(repo, home, "status", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "result": "clean",
        "sha": st.sha,
        "release": st.release,
        "findings": [],
        "warnings": [],
    }


def test_status_lists_findings_and_warnings(
    repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deploy_with_library(repo, home, cli.data_dir(home))
    (home / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert run(repo, home, "status") == 1
    out = capsys.readouterr().out
    assert "drift  config/skills/alpha/SKILL.md: changed in the release" in out
    assert "warning off-tip:" in out


def test_another_claude_home_has_nothing_deployed(
    repo: Path, home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deploy_with_library(repo, home, cli.data_dir(home))
    assert run(repo, tmp_path / "other", "status") == 1
    assert "not-deployed" in capsys.readouterr().out


def test_status_refuses_a_home_whose_key_collides_with_a_deployed_one(
    repo: Path,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "home_key", lambda home: "0" * 12)
    deploy_with_library(repo, home, cli.data_dir(home))
    assert run(repo, tmp_path / "other", "status") == 2
    err = capsys.readouterr().err
    assert f"cfg: this data directory was deployed to {home}, not {tmp_path / 'other'}" in err


def test_plan_prints_kinds_and_diffs_and_exits_1_on_differences(
    repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "CLAUDE.md").write_text("# mine\n")
    assert run(repo, home, "plan", "--host", "hosts/test.json") == 1
    out = capsys.readouterr().out
    assert "CLAUDE.md" in out and "differs" in out
    assert "+# mine" in out
    assert "a deploy would stop" in out


def test_plan_json_has_the_documented_shape(
    repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(repo, home, "plan", "--host", "hosts/test.json", "--json") == 1
    out = json.loads(capsys.readouterr().out)
    assert sorted(out) == ["diffs", "paths", "ref", "settings", "wouldBackUp", "wouldStop"]
    assert out["paths"][0] == {"live": "CLAUDE.md", "state": "missing"}
    assert out["settings"][0] == {"key": "enabledPlugins/tools@market", "state": "missing-live"}


def test_plan_of_a_deployed_home_exits_0(
    repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deploy_with_library(repo, home, cli.data_dir(home))
    assert run(repo, home, "plan", "--host", "hosts/test.json", "--ref", "HEAD") == 0
    assert "nothing to change" in capsys.readouterr().out


def test_diff_exits_1_with_diffs_0_without_and_2_without_state(
    repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(repo, home, "diff") == 2
    assert "cfg plan" in capsys.readouterr().err
    deploy_with_library(repo, home, cli.data_dir(home))
    assert run(repo, home, "diff") == 0
    (home / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    assert run(repo, home, "diff", "skills/alpha") == 1
    assert "+edited" in capsys.readouterr().out
    assert run(repo, home, "diff", "--json") == 1
    out = json.loads(capsys.readouterr().out)
    assert out["diffs"][0]["path"] == "config/skills/alpha/SKILL.md"


def test_the_commands_write_nothing(repo: Path, home: Path) -> None:
    deploy_with_library(repo, home, cli.data_dir(home))
    (home / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    roots = (home, cli.data_dir(home), repo / ".git")
    before = inventory(*roots)
    run(repo, home, "status")
    run(repo, home, "plan", "--host", "hosts/test.json")
    run(repo, home, "diff")
    assert inventory(*roots) == before


def test_a_relative_xdg_data_home_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    default = Path(os.environ["HOME"]) / ".claude"
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert cli.data_dir(default) == Path(os.environ["HOME"]) / ".local" / "share" / "claude-config"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert cli.data_dir(default) == Path(os.environ["HOME"]) / ".local" / "share" / "claude-config"


def test_paths_are_made_absolute_without_resolving_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "alias"))
    default = Path(os.environ["HOME"]) / ".claude"
    assert cli.data_dir(default) == tmp_path / "alias" / "claude-config"


# Where each Claude home keeps its deploy state


@pytest.mark.parametrize("xdg", [True, False], ids=["XDG_DATA_HOME set", "XDG_DATA_HOME unset"])
def test_each_home_gets_its_own_state_directory_and_dot_claude_keeps_todays(
    xdg: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not xdg:
        monkeypatch.delenv("XDG_DATA_HOME")
    base = (
        Path(os.environ["XDG_DATA_HOME"]) if xdg else Path(os.environ["HOME"], ".local", "share")
    ) / "claude-config"
    before = sorted(tmp_path.rglob("*"))
    assert cli.data_dir("~/.claude") == base
    assert cli.data_dir("~/.claude/") == base
    assert cli.data_dir(Path(os.environ["HOME"], ".claude")) == base
    a = cli.data_dir("/a")
    assert a == cli.data_dir("/b/../a") == cli.data_dir("/a/")
    assert a.parent == base / "homes"
    assert re.fullmatch(r"[0-9a-f]{12}", a.name)
    assert a.name == hashlib.sha256(b"/a").hexdigest()[:12]
    assert cli.data_dir("/b") not in (a, base)
    assert sorted(tmp_path.rglob("*")) == before


def test_a_symlinked_home_and_its_target_get_two_state_directories(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    assert cli.data_dir(tmp_path / "alias") != cli.data_dir(tmp_path / "real")


def test_a_bare_command_finds_an_existing_dot_claude_deploy_where_it_always_was(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = make_home(Path(os.environ["HOME"]))
    deploy_with_library(repo, home, base_dir())
    assert cli.main(["status", "--repo", str(repo)]) == 0
    assert "clean" in capsys.readouterr().out


@pytest.mark.parametrize("tool", ["check", "lock", "eval"])
def test_check_lock_and_eval_forward_their_arguments(
    tool: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as done:
        cli.main([tool, "--help"])
    assert done.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_cfg_runs_as_a_script(repo: Path, home: Path) -> None:
    env = {**os.environ}
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            str(REPO_ROOT / "tools" / "cfg" / "cli.py"),
            "status",
            "--repo",
            str(repo),
            "--claude-home",
            str(home),
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout)["result"] == "not-deployed"


def test_no_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as done:
        cli.main([])
    assert done.value.code == 2


# Which Claude home a command works on


def spelled(home: str, cwd: Path) -> Path:
    """An expected home as the tests write it: ~/ is HOME and ./ is the working directory."""
    if home.startswith("~/"):
        return Path(os.environ["HOME"], home[2:])
    if home.startswith("./"):
        return cwd / home[2:]
    return Path(home)


@pytest.mark.parametrize(
    ("target", "claude_home", "env", "home", "source"),
    [
        (None, None, {"CLAUDE_CONFIG_DIR": "/x"}, "/x", "CLAUDE_CONFIG_DIR"),
        (None, "/y", {"CLAUDE_CONFIG_DIR": "/x"}, "/y", "--claude-home"),
        ("/t", None, {"CLAUDE_CONFIG_DIR": "/x"}, "/t", "--target"),
        (None, None, {"CLAUDE_CONFIG_DIR": ""}, "~/.claude", "default"),
        (None, None, {}, "~/.claude", "default"),
        (None, None, {"CLAUDE_CONFIG_DIR": "~/other/"}, "~/other", "CLAUDE_CONFIG_DIR"),
        (None, None, {"CLAUDE_CONFIG_DIR": "rel/../dir"}, "./dir", "CLAUDE_CONFIG_DIR"),
        (None, "rel", {}, "./rel", "--claude-home"),
    ],
    ids=["env", "flag wins", "target wins", "empty", "unset", "tilde", "relative", "flag"],
)
def test_the_home_comes_from_target_then_flag_then_the_environment_then_the_default(
    target: str | None,
    claude_home: str | None,
    env: dict[str, str],
    home: str,
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.resolve_home(target, claude_home, env) == (spelled(home, tmp_path), source)


def test_target_and_claude_home_together_are_refused_by_the_resolver() -> None:
    with pytest.raises(CfgError, match="--target and --claude-home can't be given together"):
        cli.resolve_home("/t", "/y", {})


def test_resolving_the_home_creates_nothing(tmp_path: Path) -> None:
    cli.resolve_home(None, None, {"CLAUDE_CONFIG_DIR": str(tmp_path / "new")})
    assert list(tmp_path.iterdir()) == []


def test_plan_names_the_home_and_its_source_on_stderr_and_leaves_stdout_alone(
    repo: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = ["plan", "--host", "hosts/test.json", "--json", "--repo", str(repo)]
    assert cli.main([*plan, "--claude-home", str(home)]) == 1
    flagged = capsys.readouterr()
    assert flagged.err.splitlines()[0] == f"cfg: Claude home {home} (source: --claude-home)"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    assert cli.main(plan) == 1
    from_env = capsys.readouterr()
    assert from_env.err.splitlines()[0] == f"cfg: Claude home {home} (source: CLAUDE_CONFIG_DIR)"
    assert from_env.out == flagged.out
    json.loads(from_env.out)


def test_plan_with_no_flag_and_no_variable_names_the_default_home(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_home(Path(os.environ["HOME"]))
    cli.main(["plan", "--host", "hosts/test.json", "--repo", str(repo)])
    default = Path(os.environ["HOME"]) / ".claude"
    first = capsys.readouterr().err.splitlines()[0]
    assert first == f"cfg: Claude home {default} (source: default)"


# What check and lock receive


@pytest.fixture
def forwarded(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """The argv check_config.main and lock.main are called with, by tool name."""
    from tools import check_config
    from tools.cfg import lock

    calls: dict[str, list[str]] = {}

    def recorder(name: str) -> Callable[[list[str]], int]:
        def main(argv: list[str]) -> int:
            calls[name] = argv
            return 0

        return main

    monkeypatch.setattr(check_config, "main", recorder("check"))
    monkeypatch.setattr(lock, "main", recorder("lock"))
    return calls


@pytest.mark.parametrize("tool", ["check", "lock"])
def test_check_and_lock_get_the_home_from_claude_config_dir(
    tool: str, forwarded: dict[str, list[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "second"))
    assert cli.main([tool, "--json"]) == 0
    assert forwarded[tool] == ["--claude-home", str(tmp_path / "second"), "--json"]


@pytest.mark.parametrize("tool", ["check", "lock"])
def test_check_and_lock_get_the_default_home_when_nothing_names_one(
    tool: str, forwarded: dict[str, list[str]]
) -> None:
    assert cli.main([tool]) == 0
    assert forwarded[tool] == ["--claude-home", str(Path(os.environ["HOME"]) / ".claude")]


@pytest.mark.parametrize("tool", ["check", "lock"])
@pytest.mark.parametrize(
    "given", [["--claude-home", "/given"], ["--claude-home=/given"]], ids=["two words", "one word"]
)
def test_a_claude_home_already_in_the_arguments_is_left_alone(
    tool: str,
    given: list[str],
    forwarded: dict[str, list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "second"))
    assert cli.main([tool, "--check", *given]) == 0
    assert forwarded[tool] == ["--check", *given]
