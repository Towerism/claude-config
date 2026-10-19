"""Tests for tools/ci.py, the helper the CI workflow runs.

They run ci.main in this process, inside a temp git repo, so coverage counts them. The real
git, commit-check, prose_check and spec validator run; only the environment GitHub sets is
faked, through an event file.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import NOREPLY, Repo
from tools import ci

GOOD = "feat(x): add a thing (#1)\n\nThe thing exists now, because the issue asked for it.\n"
BAD = "added stuff\n"
ZERO = "0" * 40
SPEC_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ci"


@pytest.fixture
def ci_repo(tmp_path: Path, isolated_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Repo:
    """An empty repo with one commit on main, the cwd and environment set for ci.main."""
    path = tmp_path / "repo"
    path.mkdir()
    env = {
        **isolated_env,
        "GIT_AUTHOR_DATE": "2026-10-19T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-10-19T12:00:00Z",
    }
    repo = Repo(path, env)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Test Owner")
    repo.git("config", "user.email", NOREPLY)
    repo.write("README.md", "start\n")
    repo.commit("README.md", message=GOOD)
    for key in list(os.environ):
        if key.startswith(("GIT_", "GITHUB_")):
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(path)
    return repo


def head(repo: Repo) -> str:
    return repo.git("rev-parse", "HEAD").stdout.strip()


def add_commit(repo: Repo, path: str, message: str = GOOD) -> str:
    repo.write(path, path + "\n")
    assert repo.commit(path, message=message).returncode == 0
    return head(repo)


@pytest.fixture
def event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Write an event payload and point GITHUB_EVENT_NAME and GITHUB_EVENT_PATH at it."""

    def write(name: str, payload: dict[str, object]) -> Path:
        path = tmp_path / "event.json"
        path.write_text(json.dumps(payload))
        monkeypatch.setenv("GITHUB_EVENT_NAME", name)
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
        return path

    return write


def pr_event(
    base: str, title: str = "feat: a change (#1)", body: str | None = "Closes #1"
) -> dict[str, object]:
    return {"pull_request": {"base": {"sha": base}, "title": title, "body": body}}


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = ci.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------- base


def test_a_pull_request_starts_from_the_merge_base(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fork = head(ci_repo)
    ci_repo.git("checkout", "-q", "-b", "feature")
    add_commit(ci_repo, "a.txt")
    add_commit(ci_repo, "b.txt")
    ci_repo.git("checkout", "-q", "main")
    main_tip = add_commit(ci_repo, "m.txt")
    ci_repo.git("checkout", "-q", "feature")
    event("pull_request", pr_event(main_tip))
    assert run(["base"], capsys) == (0, fork + "\n", "")


def test_a_push_starts_from_before(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    before = head(ci_repo)
    add_commit(ci_repo, "a.txt")
    event("push", {"before": before})
    assert run(["base"], capsys) == (0, before + "\n", "")


@pytest.mark.parametrize(
    ("name", "payload", "message"),
    [
        ("push", {"before": ZERO}, "no earlier commit"),
        ("push", {"before": "1" * 40}, "not a commit in this clone"),
        ("push", {}, "has no before"),
        ("pull_request", {"pull_request": {}}, "has no pull_request.base.sha"),
        ("workflow_dispatch", {}, "unsupported event workflow_dispatch"),
    ],
)
def test_base_refuses_events_it_cannot_use(
    ci_repo: Repo,
    event: Callable[..., Path],
    capsys: pytest.CaptureFixture[str],
    name: str,
    payload: dict[str, object],
    message: str,
) -> None:
    event(name, payload)
    code, out, err = run(["base"], capsys)
    assert (code, out) == (2, "")
    assert message in err


def test_a_pull_request_with_unrelated_history_has_no_base(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    main_tip = head(ci_repo)
    ci_repo.git("checkout", "-q", "--orphan", "unrelated")
    add_commit(ci_repo, "other.txt")
    event("pull_request", pr_event(main_tip))
    code, out, err = run(["base"], capsys)
    assert (code, out) == (2, "")
    assert "git merge-base failed" in err


def test_base_needs_the_event_environment(
    ci_repo: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run(["base"], capsys)
    assert code == 2 and "GITHUB_EVENT_NAME" in err
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    code, _, err = run(["base"], capsys)
    assert code == 2 and "GITHUB_EVENT_PATH" in err
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing.json"))
    code, _, err = run(["base"], capsys)
    assert code == 2 and "cannot read" in err
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "bad.json"))
    code, _, err = run(["base"], capsys)
    assert code == 2 and "cannot read" in err
    (tmp_path / "list.json").write_text("[]")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "list.json"))
    code, _, err = run(["base"], capsys)
    assert code == 2 and "not a JSON object" in err


# ---------------------------------------------------------------- changes


@pytest.mark.parametrize(
    ("paths", "code"),
    [
        (["docs/adr/x.md"], "false"),
        (["docs/adr/x.md", "docs/evidence/y.json"], "false"),
        (["docs/adr/x.md", "docs/runbook.md"], "true"),
        (["docs/adr/x.md", "tools/x.py"], "true"),
        (["README.md"], "true"),
        (["docsish/x.md"], "true"),
    ],
)
def test_a_pull_request_skips_code_jobs_only_for_docs(
    ci_repo: Repo,
    event: Callable[..., Path],
    capsys: pytest.CaptureFixture[str],
    paths: list[str],
    code: str,
) -> None:
    base = head(ci_repo)
    for path in paths:
        ci_repo.write(path, "x\n")
    ci_repo.commit(*paths)
    event("pull_request", pr_event(base))
    assert run(["changes", "--base", base], capsys) == (0, f"code={code}\n", "")


def test_a_rename_out_of_docs_counts_its_new_path(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    ci_repo.write("docs/a.md", "a fairly long line so git sees a rename\n" * 5)
    ci_repo.commit("docs/a.md")
    base = head(ci_repo)
    (ci_repo.path / "tools").mkdir()
    ci_repo.git("mv", "docs/a.md", "tools/a.md")
    ci_repo.commit()
    event("pull_request", pr_event(base))
    assert run(["changes", "--base", base], capsys) == (0, "code=true\n", "")


def test_an_empty_diff_runs_everything(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    base = head(ci_repo)
    event("pull_request", pr_event(base))
    assert run(["changes", "--base", base], capsys) == (0, "code=true\n", "")


def test_a_push_runs_everything_even_for_docs(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    base = head(ci_repo)
    add_commit(ci_repo, "docs/adr/x.md")
    event("push", {"before": base})
    assert run(["changes", "--base", base], capsys) == (0, "code=true\n", "")


def test_changes_refuses_an_unknown_base(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    event("pull_request", pr_event(head(ci_repo)))
    code, out, err = run(["changes", "--base", "no-such-ref"], capsys)
    assert (code, out) == (2, "")
    assert "not a commit" in err


# ---------------------------------------------------------------- commits


def test_every_commit_message_is_checked_and_a_bad_one_fails(
    ci_repo: Repo,
    event: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = head(ci_repo)
    add_commit(ci_repo, "a.txt")
    bad = add_commit(ci_repo, "b.txt", message=BAD)
    event("pull_request", pr_event(base))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, out, _ = run(["commits", "--base", base], capsys)
    assert code == 1
    assert out.splitlines() == [f"{bad[:7]} added stuff"]
    assert "prose_check" in summary.read_text()


def test_prose_findings_never_fail_the_job(
    ci_repo: Repo,
    event: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = head(ci_repo)
    wordy = "feat(x): leverage a robust seamless thing (#1)\n\nIt's worth noting that we delve.\n"
    add_commit(ci_repo, "a.txt", message=wordy)
    event("pull_request", pr_event(base, title="feat: leverage robust synergy (#1)"))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, out, _ = run(["commits", "--base", base], capsys)
    assert (code, out) == (0, "")
    text = summary.read_text()
    assert "leverage" in text
    assert "PR title" in text and "PR body" in text


def test_a_hostile_pr_body_cannot_close_the_summary_fence(
    ci_repo: Repo,
    event: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = head(ci_repo)
    add_commit(ci_repo, "a.txt")
    body = "fine\n```\n# Injected heading\n````` and more\n"
    event("pull_request", pr_event(base, body=body))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert run(["commits", "--base", base], capsys)[0] == 0
    assert ci.fence_for(body) == "``````"
    assert ci.fence_for("no backticks") == "```"
    open_fence = None
    blocks = 0
    for line in summary.read_text().splitlines():
        if open_fence is None:
            if line.startswith("```"):
                assert line.endswith("text")
                open_fence = line.removesuffix("text")
        elif line == open_fence:
            open_fence = None
            blocks += 1
        else:
            assert not line.lstrip().startswith(open_fence)
    assert open_fence is None
    assert blocks == 3


def test_a_null_pr_body_and_no_summary_file_are_fine(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    base = head(ci_repo)
    add_commit(ci_repo, "a.txt")
    event("pull_request", pr_event(base, body=None))
    assert run(["commits", "--base", base], capsys) == (0, "", "")


def test_commits_refuses_an_unknown_base(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    event("pull_request", pr_event(head(ci_repo)))
    code, out, err = run(["commits", "--base", "no-such-ref"], capsys)
    assert (code, out) == (2, "")
    assert "not a commit" in err


def test_commits_needs_a_pull_request_event(
    ci_repo: Repo, event: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    base = head(ci_repo)
    event("push", {"before": base})
    code, _, err = run(["commits", "--base", base], capsys)
    assert code == 2
    assert "pull_request" in err


# ---------------------------------------------------------------- specs


def spec_tree(tmp_path: Path, *specs: tuple[str, str]) -> Path:
    root = tmp_path / "tree"
    for target, fixture in specs:
        dest = root / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SPEC_FIXTURES / fixture, dest)
    root.mkdir(exist_ok=True)
    return root


def test_a_spec_with_only_warnings_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = spec_tree(tmp_path, ("config/SPEC.md", "scores-well.md"))
    code, out, _ = run(["specs", "--root", str(root)], capsys)
    assert code == 0
    assert out.startswith("config/SPEC.md: Spec Completeness Score:")


def test_a_low_scoring_spec_fails_and_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = spec_tree(
        tmp_path,
        ("config/SPEC.md", "scores-well.md"),
        ("config/skills/thin/SPEC.md", "scores-badly.md"),
    )
    code, out, err = run(["specs", "--root", str(root)], capsys)
    assert code == 1
    assert "config/skills/thin/SPEC.md: Spec Completeness Score:" in out
    assert "config/skills/thin/SPEC.md" in err
    assert "config/SPEC.md" not in err


def test_no_spec_at_all_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(["specs", "--root", str(spec_tree(tmp_path))], capsys)
    assert code == 1
    assert "no spec" in err


# ---------------------------------------------------------------- usage


def test_no_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        ci.main([])
    assert exc.value.code == 2
