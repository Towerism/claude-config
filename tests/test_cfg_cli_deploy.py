"""The cfg command line for deploy, rollback, capture and restore, plus the whole loop."""

from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.cfg_helpers import (
    commit_change,
    git,
    make_deploy_repo,
    make_home,
    make_live_install,
    push,
)
from tools.cfg import capture, cli, deploy, drift, release, state
from tools.cfg.errors import CfgError

pytestmark = pytest.mark.usefixtures("cfg_isolation")
REPO_ROOT = Path(__file__).resolve().parent.parent
HOST = "hosts/test.json"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_deploy_repo(tmp_path / "clone")


@pytest.fixture
def target(tmp_path: Path, repo: Path) -> Path:
    home = make_home(tmp_path / "t")
    make_live_install(home, repo)
    return home


def cfg(repo: Path, target: Path, *args: str) -> int:
    return cli.main([*args, "--repo", str(repo), "--target", str(target)])


def files(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if rel.startswith("backups"):
            continue
        if path.is_symlink():
            out[rel] = os.readlink(path)
        elif path.is_file():
            out[rel] = path.read_bytes()
    return out


def test_deploy_prints_what_it_did_and_where_the_undo_is(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cfg(repo, target, "deploy", "--host", HOST, "--yes") == 0
    out = capsys.readouterr().out
    assert "deployed" in out and "back up and link CLAUDE.md (match)" in out
    assert "/restore.sh" in out and "undo: cfg rollback" not in out
    assert (target / "CLAUDE.md").is_symlink()
    assert os.readlink(target / "CLAUDE.md").startswith(f"{target}-cfg-data/current/")


def test_deploy_without_yes_off_a_terminal_lists_the_changes_and_stops(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = files(target)
    assert cfg(repo, target, "deploy", "--host", HOST) == 1
    out = capsys.readouterr().out
    assert "back up and link CLAUDE.md (match)" in out
    assert "rerun with --yes" in out
    assert files(target) == before


def test_a_stopped_deploy_shows_each_conflict_and_how_to_resolve_it(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (target / "CLAUDE.md").write_text("# live\n")
    (target / "skills" / "alpha" / "x").write_text("x\n")
    assert cfg(repo, target, "deploy", "--host", HOST, "--yes") == 1
    out = capsys.readouterr().out
    assert "== conflict: CLAUDE.md" in out and "+# live" in out
    assert "--discard-live skills/alpha" in out
    cfg(
        repo,
        target,
        "deploy",
        "--host",
        HOST,
        "--yes",
        "--discard-live",
        "CLAUDE.md",
        "--discard-live",
        "skills/alpha",
    )
    out = capsys.readouterr().out
    assert "discarded (kept in the backup): CLAUDE.md, skills/alpha" in out


def test_a_drift_conflict_names_the_file_inside_the_release(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg(repo, target, "deploy", "--host", HOST, "--yes")
    (target / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    capsys.readouterr()
    assert cfg(repo, target, "deploy", "--host", HOST, "--yes") == 1
    assert "conflict: skills/alpha (config/skills/alpha/SKILL.md)" in capsys.readouterr().out


def test_deploy_json_has_the_documented_shape(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (target / "CLAUDE.md").write_text("# live\n")
    assert cfg(repo, target, "deploy", "--host", HOST, "--json") == 1
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "stopped"
    assert out["conflicts"][0]["path"] == "CLAUDE.md"
    assert sorted(out) == sorted(
        [
            "result", "sha", "release", "conflicts", "changes", "linked", "matched",
            "discarded", "removed", "settingsChanges", "backup", "warnings",
        ]
    )  # fmt: skip


def test_deploying_again_is_already_deployed_and_exits_0(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg(repo, target, "deploy", "--host", HOST, "--yes")
    assert cfg(repo, target, "deploy", "--host", HOST) == 0
    assert "already-deployed" in capsys.readouterr().out


def test_a_failure_after_the_first_change_exits_3(
    repo: Path, target: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(*args: Any, **kwargs: Any) -> deploy.DeployResult:
        raise deploy.DeployFailed("deploy failed after changing the live config: x\nundo with r")

    monkeypatch.setattr(deploy, "deploy", failing)
    assert cfg(repo, target, "deploy", "--host", HOST, "--yes") == 3
    assert "undo with r" in capsys.readouterr().err


def test_target_and_claude_home_together_is_a_usage_error(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["status", "--repo", str(repo), "--target", str(target), "--claude-home", "x"])
    assert code == 2
    assert "can't be given together" in capsys.readouterr().err


def test_a_target_whose_data_directory_cannot_be_made_is_refused(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "file").write_text("")
    assert cfg(repo, tmp_path / "file" / "home", "deploy", "--host", HOST, "--yes") == 2
    assert "cfg:" in capsys.readouterr().err


def test_ask_accepts_only_yes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["y", "YES", "", "no"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert [cli.ask(["link CLAUDE.md"]) for _ in range(4)] == [True, True, False, False]
    assert "link CLAUDE.md" in capsys.readouterr().out

    def eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.ask(["x"]) is False


@pytest.mark.parametrize(("answer", "deployed"), [(b"n\n", False), (b"y\n", True)])
def test_deploy_asks_on_a_terminal(repo: Path, target: Path, answer: bytes, deployed: bool) -> None:
    primary, secondary = pty.openpty()
    os.write(primary, answer)
    done = subprocess.run(
        [
            sys.executable, "-I", str(REPO_ROOT / "tools" / "cfg" / "cli.py"),
            "deploy", "--host", HOST, "--repo", str(repo), "--target", str(target),
        ],
        stdin=secondary, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    os.close(primary)
    os.close(secondary)
    assert "continue? [y/N]" in done.stdout
    assert done.returncode == (0 if deployed else 1), done.stdout + done.stderr
    assert (target / "CLAUDE.md").is_symlink() is deployed


def test_rollback_prints_where_it_went_and_how_to_return(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg(repo, target, "deploy", "--host", HOST, "--yes")
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    fragment["enabledPlugins"]["new@market"] = True
    commit_change(repo, "config/settings.fragment.json", json.dumps(fragment, indent=2) + "\n")
    commit_change(
        repo, HOST, json.dumps({**json.loads((repo / HOST).read_text()), "skills": ["alpha"]})
    )
    push(repo)
    cfg(repo, target, "deploy", "--host", HOST, "--yes")
    (target / "skills" / "alpha").unlink()
    (target / "skills" / "alpha").mkdir()
    capsys.readouterr()
    assert cfg(repo, target, "rollback") == 0
    out = capsys.readouterr().out
    assert "rolled back from" in out and "to return: cfg deploy" in out
    assert "relinked skills/beta" in out
    assert "left alone (not cfg's link): skills/alpha" in out
    assert "cfg deploy --host hosts/test.json --ref" in out
    assert cfg(repo, target, "rollback", "--json") == 2


def test_rollback_json_and_its_warnings(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg(repo, target, "deploy", "--host", HOST, "--yes")
    commit_change(
        repo,
        HOST,
        json.dumps({**json.loads((repo / HOST).read_text()), "skills": ["alpha", "beta", "gamma"]}),
    )
    commit_change(repo, "config/skills/gamma/SKILL.md", "g\n")
    push(repo)
    cfg(repo, target, "deploy", "--host", HOST, "--yes")
    (target / "skills" / "alpha" / "SKILL.md").write_text("edited\n")
    capsys.readouterr()
    assert cfg(repo, target, "rollback", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["removed"] == ["skills/gamma"]
    assert out["warnings"]
    assert sorted(out) == sorted(
        ["from", "to", "relinked", "removed", "leftAlone", "settingsDiffer", "command", "warnings"]
    )


def test_capture_prints_what_it_wrote_and_the_next_steps(
    repo: Path, target: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    into = tmp_path / "feature"
    git(repo, "worktree", "add", "-q", "-b", "feature/9-x", str(into), "HEAD")
    (target / "CLAUDE.md").write_text("# live\n")
    assert cfg(repo, target, "capture", "--into", str(into), "--host", HOST) == 0
    out = capsys.readouterr().out
    assert "wrote   config/CLAUDE.global.md" in out
    assert "--discard-live <path>" in out
    git(into, "commit", "-q", "-am", "capture")
    assert cfg(repo, target, "capture", "--into", str(into), "--host", HOST, "--json") == 0
    assert json.loads(capsys.readouterr().out)["written"] == []
    assert cfg(repo, target, "capture", "--into", str(into), "--host", HOST) == 0
    assert "nothing to capture" in capsys.readouterr().out


def test_capture_names_the_suites_a_change_needs(
    repo: Path, target: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {
        "suites": {
            "rubric": {"runner": "rubric", "paths": ["config/CLAUDE.global.md"], "harness": []}
        }
    }
    commit_change(repo, "eval/gates.json", json.dumps(gates))
    into = tmp_path / "feature"
    git(repo, "worktree", "add", "-q", "-b", "feature/9-x", str(into), "HEAD")
    (target / "CLAUDE.md").write_text("# live\n")
    cfg(repo, target, "capture", "--into", str(into), "--host", HOST)
    assert "eval suites this change needs: rubric" in capsys.readouterr().out


def test_restore_runs_the_newest_backup_or_the_one_named(
    repo: Path, target: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cfg(repo, target, "restore") == 2
    assert "name the backup directory" in capsys.readouterr().err
    assert cfg(repo, target, "restore", str(tmp_path)) == 2
    cfg(repo, target, "deploy", "--host", HOST, "--yes")
    st = state.load(Path(f"{target}-cfg-data"))
    assert st is not None
    assert cfg(repo, target, "restore") == 0
    assert not (target / "CLAUDE.md").is_symlink()
    assert cfg(repo, target, "restore", st.backups[0]) == 0


def test_the_whole_loop_end_to_end(repo: Path, target: Path, tmp_path: Path) -> None:
    """Deploy, edit through a link, status, refused deploy, capture, merge, deploy,
    rollback, restore: the plan's temp-home run, through the CLI as a subprocess."""
    script = str(REPO_ROOT / "tools" / "cfg" / "cli.py")
    original = files(target)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", script, *args, "--repo", str(repo), "--target", str(target)],
            capture_output=True, text=True, timeout=120, check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip

    assert run("deploy", "--host", HOST, "--yes").returncode == 0
    (target / "CLAUDE.md").write_text("# edited through the link\n")
    status = run("status")
    assert status.returncode == 1 and "drift" in status.stdout
    assert run("deploy", "--host", HOST, "--yes").returncode == 1

    into = tmp_path / "feature"
    git(repo, "worktree", "add", "-q", "-b", "feature/9-x", str(into), "origin/main")
    captured = run("capture", "--into", str(into))
    assert captured.returncode == 0, captured.stderr
    git(into, "commit", "-q", "-am", "capture the edit")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feature/9-x")
    push(repo)

    deployed = run("deploy", "--host", HOST, "--yes")
    assert deployed.returncode == 0, deployed.stdout + deployed.stderr
    assert run("status").returncode == 0
    assert run("rollback").returncode == 0
    first_backup = sorted((target / "backups" / "claude-config").iterdir())[0]
    restored = subprocess.run(
        [str(first_backup / "restore.sh")], capture_output=True, text=True, check=False
    )
    assert restored.returncode == 0, restored.stdout
    assert files(target) == original


def test_a_deploy_that_only_switches_releases_names_no_backup(
    repo: Path, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg(repo, target, "deploy", "--host", HOST, "--yes")
    commit_change(repo, "config/skill-overlays/alpha.md", "v2\n")
    push(repo)
    capsys.readouterr()
    assert cfg(repo, target, "deploy", "--host", HOST, "--yes") == 0
    out = capsys.readouterr().out
    assert "switch current" in out and "backup:" not in out
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    fragment["enabledPlugins"]["new@market"] = True
    commit_change(repo, "config/settings.fragment.json", json.dumps(fragment, indent=2) + "\n")
    push(repo)
    assert cfg(repo, target, "deploy", "--host", HOST, "--yes") == 0
    assert "undo: cfg rollback, or" in capsys.readouterr().out


def test_deploy_yes_refuses_a_home_named_only_by_the_environment(
    repo: Path, target: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = files(target)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(target))
    deploy_yes = ["deploy", "--host", HOST, "--yes", "--repo", str(repo)]
    assert cli.main(deploy_yes) == 2
    err = capsys.readouterr().err
    assert err.splitlines()[0] == f"cfg: Claude home {target} (source: CLAUDE_CONFIG_DIR)"
    assert "--claude-home" in err.splitlines()[1] and "--target" in err.splitlines()[1]
    assert files(target) == before
    assert not Path(os.environ["XDG_DATA_HOME"]).exists()


def test_deploy_without_yes_on_a_home_from_the_environment_lists_the_changes(
    repo: Path, target: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(target))
    assert cli.main(["deploy", "--host", HOST, "--repo", str(repo)]) == 1
    assert "back up and link CLAUDE.md (match)" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--claude-home", "--target"])
def test_deploy_yes_goes_ahead_when_the_home_is_named_on_the_command_line(
    repo: Path, target: Path, flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(target))
    argv = ["deploy", "--host", HOST, "--yes", "--repo", str(repo), flag, str(target)]
    assert cli.main(argv) == 0
    assert (target / "CLAUDE.md").is_symlink()


# More than one Claude home


def test_two_homes_deploy_from_one_clone_at_different_commits(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    side_sha = commit_change(repo, "config/CLAUDE.global.md", "# side\n")
    git(repo, "checkout", "-q", "main")
    a, b = make_home(tmp_path / "a"), make_home(tmp_path / "b")

    def run(home: Path, *args: str) -> int:
        return cli.main([*args, "--repo", str(repo), "--claude-home", str(home)])

    assert run(a, "deploy", "--host", HOST, "--yes") == 0
    assert run(b, "deploy", "--host", HOST, "--ref", side_sha, "--yes") == 0
    data_a, data_b = cli.data_dir(a), cli.data_dir(b)
    assert data_a != data_b
    for home, data, sha in [(a, data_a, main_sha), (b, data_b, side_sha)]:
        st = state.load(data)
        assert st is not None and st.sha == sha and st.claude_home == str(home)
        capsys.readouterr()
        assert run(home, "status") == 0, capsys.readouterr().out
    assert run(a, "deploy", "--host", HOST, "--ref", side_sha, "--yes") == 0
    current_b = os.readlink(data_b / "current")
    assert run(a, "rollback") == 0
    st_a = state.load(data_a)
    assert st_a is not None and st_a.sha == main_sha
    assert os.readlink(data_b / "current") == current_b
    assert run(b, "status") == 0


def test_a_colliding_state_directory_is_refused_for_the_other_home(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "home_key", lambda home: "0" * 12)
    a, b = make_home(tmp_path / "a"), make_home(tmp_path / "b")
    data = cli.data_dir(a)
    assert data == cli.data_dir(b)
    fabricated = state.State(
        host=HOST,
        claude_home=str(a),
        sha="0" * 40,
        release="0" * 40,
        deployed_at="2026-10-24T12:00:00Z",
        paths=(),
        settings={},
    )
    state.save(data, fabricated)
    refusal = f"this data directory was deployed to {a}, not {b}"
    with pytest.raises(CfgError, match=refusal):
        drift._require_state(data, b)
    with pytest.raises(CfgError, match=refusal):
        deploy.deploy(repo, b, data, HOST, yes=True)
    with pytest.raises(CfgError, match=refusal):
        capture.capture(repo, b, data, [], repo, HOST)
    for args in (["status"], ["deploy", "--host", HOST, "--yes"], ["capture", "--host", HOST]):
        assert cli.main([*args, "--repo", str(repo), "--claude-home", str(b)]) == 2
        assert f"cfg: {refusal}" in capsys.readouterr().err
    assert cli.main(["restore", "--repo", str(repo), "--claude-home", str(b)]) == 2
    assert "no deploy backups are recorded" in capsys.readouterr().err


def test_a_deploy_for_a_second_home_fails_while_the_clone_lock_is_held(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = make_home(tmp_path / "b")
    argv = ["deploy", "--host", HOST, "--yes", "--repo", str(repo), "--claude-home", str(home)]
    with release.clone_lock(repo):
        assert cli.main(argv) == 2
    assert "another cfg is adding or removing release worktrees" in capsys.readouterr().err
    assert not (home / "CLAUDE.md").exists()
    assert cli.main(argv) == 0
