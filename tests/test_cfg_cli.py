"""The cfg command line: plan, status, diff, and the forwarded check, lock and eval."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
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


def data_dir() -> Path:
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
    st = deploy_with_library(repo, home, data_dir())
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
    deploy_with_library(repo, home, data_dir())
    (home / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert run(repo, home, "status") == 1
    out = capsys.readouterr().out
    assert "drift  config/skills/alpha/SKILL.md: changed in the release" in out
    assert "warning off-tip:" in out


def test_status_refuses_another_claude_home(
    repo: Path, home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deploy_with_library(repo, home, data_dir())
    assert run(repo, tmp_path / "other", "status") == 2
    assert "cfg: this data directory was deployed to" in capsys.readouterr().err


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
    deploy_with_library(repo, home, data_dir())
    assert run(repo, home, "plan", "--host", "hosts/test.json", "--ref", "HEAD") == 0
    assert "nothing to change" in capsys.readouterr().out


def test_diff_exits_1_with_diffs_0_without_and_2_without_state(
    repo: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(repo, home, "diff") == 2
    assert "cfg plan" in capsys.readouterr().err
    deploy_with_library(repo, home, data_dir())
    assert run(repo, home, "diff") == 0
    (home / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    assert run(repo, home, "diff", "skills/alpha") == 1
    assert "+edited" in capsys.readouterr().out
    assert run(repo, home, "diff", "--json") == 1
    out = json.loads(capsys.readouterr().out)
    assert out["diffs"][0]["path"] == "config/skills/alpha/SKILL.md"


def test_the_commands_write_nothing(repo: Path, home: Path) -> None:
    deploy_with_library(repo, home, data_dir())
    (home / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    roots = (home, data_dir(), repo / ".git")
    before = inventory(*roots)
    run(repo, home, "status")
    run(repo, home, "plan", "--host", "hosts/test.json")
    run(repo, home, "diff")
    assert inventory(*roots) == before


def test_a_relative_xdg_data_home_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert cli.data_dir() == Path(os.environ["HOME"]) / ".local" / "share" / "claude-config"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert cli.data_dir() == Path(os.environ["HOME"]) / ".local" / "share" / "claude-config"


def test_paths_are_made_absolute_without_resolving_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "alias"))
    assert cli.data_dir() == tmp_path / "alias" / "claude-config"


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
