"""Regression tests for the bypasses found by the PR-diff review of #1.

Each test reproduces a way a token, email or excluded term used to get past the hooks.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import NOREPLY, Repo, fake_email, fake_token, run_main
from tools import git_hooks as gh

ZERO = "0" * 40
SCISSORS = "# ------------------------ >8 ------------------------"


def head(repo: Repo) -> str:
    return repo.git("rev-parse", "HEAD").stdout.strip()


def push_line(
    local_sha: str, remote_ref: str, remote_sha: str = ZERO, local_ref: str = "refs/heads/x"
) -> str:
    return f"{local_ref} {local_sha} {remote_ref} {remote_sha}\n"


# ---------------------------------------------------------------- staged content


def test_file_named_like_a_stage_spec_is_scanned_itself(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("x", "clean\n")
    inproc.write("0:x", fake_token() + "\n")
    inproc.git("add", "x", "0:x")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert "pre-commit: token: 0:x:1" in err


def test_utf16_file_with_a_token_is_refused(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("notes.txt", ("﻿" + fake_token() + "\n").encode("utf-16-le"))
    inproc.git("add", "notes.txt")
    assert run_main(["pre-commit"], capsys)[0] == 1


def test_submodule_named_after_a_term_is_refused_without_a_traceback(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    Path(inproc.env["CLAUDE_CONFIG_DENY_TERMS"]).write_text("zephyrine\n")
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    inproc.git("update-index", "--add", "--cacheinfo", f"160000,{head(inproc)},zephyrine-lib")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert "pre-commit: deny-term: <redacted path #1>" in err
    assert "Traceback" not in err
    assert "zephyrine" not in err.lower()


def test_a_failing_git_command_prints_no_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    for key, value in isolated_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    code, err = run_main(["pre-commit"], capsys)
    assert code == 2
    assert "pre-commit: git failed" in err
    assert "Traceback" not in err


def test_reading_a_missing_blob_is_a_git_failure(inproc: Repo) -> None:
    with gh.BlobReader() as reader, pytest.raises(gh.GitError):
        reader.read("1" * 40)


def test_duplicate_gitleaks_findings_print_once(
    inproc: Repo,
    fake_gitleaks: Callable[[int, str], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_gitleaks(1, '[{"File": "g.txt", "StartLine": 2}, {"File": "g.txt", "StartLine": 2}]')
    inproc.write("g.txt", "fine\n")
    inproc.git("add", "g.txt")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert err.count("pre-commit: gitleaks: g.txt:2") == 1


# ---------------------------------------------------------------- commit messages


def test_m_message_with_a_comment_line_secret_is_refused(repo: Repo) -> None:
    repo.write("a.md", "a\n")
    result = repo.commit("a.md", message="fix: x (#1)\n\n# " + fake_token())
    assert result.returncode != 0
    assert "commit-msg: token: message:3" in result.stderr


def test_verbose_commit_with_a_removed_secret_in_its_diff_passes(
    repo: Repo, tmp_path: Path
) -> None:
    repo.write("old.txt", fake_token() + "\n")
    assert repo.commit("old.txt", extra=("--no-verify",)).returncode == 0
    repo.git("rm", "-q", "old.txt")
    editor = tmp_path / "editor.sh"
    editor.write_text(
        "#!/bin/sh\nexec python3 -I -c 'import sys; p = sys.argv[1]; s = open(p).read(); "
        'open(p, "w").write("fix: drop the old file (#1)\\n" + s)\' "$1"\n'
    )
    editor.chmod(0o755)
    result = repo.git(
        "commit", "-q", "-v", "--no-status", check=False, extra_env={"GIT_EDITOR": str(editor)}
    )
    assert result.returncode == 0, result.stderr


def test_fake_scissors_message_passes_commit_msg_but_not_push(
    make_repo: Callable[..., Repo], tmp_path: Path
) -> None:
    repo = make_repo()
    remote = tmp_path / "remote.git"
    repo.git("init", "-q", "--bare", str(remote))
    repo.git("remote", "add", "origin", str(remote))
    msg = tmp_path / "MSG"
    msg.write_text("fix: x (#1)\n" + SCISSORS + "\ndiff --git a/x b/x\n" + fake_token() + "\n")
    repo.write("a.md", "a\n")
    repo.git("add", "a.md")
    assert repo.git("commit", "-q", "-F", str(msg), check=False).returncode == 0
    result = repo.git("push", "-q", "origin", "HEAD:refs/heads/feat", check=False)
    assert result.returncode != 0
    assert "pre-push: token: " in result.stderr
    assert fake_token() not in result.stderr


# ---------------------------------------------------------------- check-range


def test_check_range_finds_a_secret_added_then_removed(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    base = head(inproc)
    inproc.write("leak.txt", fake_token() + "\n")
    assert inproc.commit("leak.txt", extra=("--no-verify",)).returncode == 0
    added = inproc.git("rev-parse", "--short", "HEAD").stdout.strip()
    inproc.git("rm", "-q", "leak.txt")
    assert inproc.commit(extra=("--no-verify",)).returncode == 0
    code, err = run_main(["check-range", base], capsys)
    assert code == 1
    assert f"check-range: token: {added}:leak.txt:1" in err


def test_check_range_finds_a_secret_added_by_a_merge_resolution(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    base = head(inproc)
    inproc.git("checkout", "-q", "-b", "side")
    inproc.write("s.txt", "s\n")
    assert inproc.commit("s.txt").returncode == 0
    inproc.git("checkout", "-q", "main")
    inproc.write("m.txt", "m\n")
    assert inproc.commit("m.txt").returncode == 0
    inproc.git("merge", "-q", "--no-commit", "--no-ff", "side")
    inproc.write("evil.txt", fake_token() + "\n")
    inproc.git("add", "evil.txt")
    assert inproc.commit(extra=("--no-verify",), message="chore: merge (#1)").returncode == 0
    inproc.git("rm", "-q", "evil.txt")
    assert inproc.commit(extra=("--no-verify",)).returncode == 0
    code, err = run_main(["check-range", base], capsys)
    assert code == 1
    assert "evil.txt:1" in err


def test_check_range_works_from_a_subdirectory(
    inproc: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("root.txt", fake_token() + "\n")
    inproc.write("sub/s.txt", "s\n")
    assert inproc.commit("root.txt", "sub/s.txt", extra=("--no-verify",)).returncode == 0
    inproc.write("sub/t.txt", "t\n")
    assert inproc.commit("sub/t.txt").returncode == 0
    base = head(inproc)
    monkeypatch.chdir(inproc.path / "sub")
    code, err = run_main(["check-range", base], capsys)
    assert code == 1
    assert "check-range: token: root.txt:1" in err


def test_check_range_skips_submodules(inproc: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    base = head(inproc)
    inproc.git("update-index", "--add", "--cacheinfo", f"160000,{base},lib")
    assert inproc.commit(extra=("--no-verify",)).returncode == 0
    assert run_main(["check-range", base], capsys)[0] == 0


# ---------------------------------------------------------------- pre-push


def test_pre_push_checks_content_of_every_pushed_commit(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    base = head(inproc)
    inproc.write("leak.txt", fake_email() + "\n")
    assert inproc.commit("leak.txt", extra=("--no-verify",)).returncode == 0
    inproc.git("rm", "-q", "leak.txt")
    assert (
        inproc.commit(
            extra=("--no-verify",),
            message="fix: " + fake_token() + " (#1)",
        ).returncode
        == 0
    )
    code, err = run_main(
        ["pre-push", "origin", "url"],
        capsys,
        stdin=push_line(head(inproc), "refs/heads/feat", base),
    )
    assert code == 1
    assert ":leak.txt:1" in err
    assert "pre-push: email: " in err
    assert " message:1" in err


def test_pre_push_checks_annotated_tags(inproc: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    inproc.git("-c", "user.email=" + fake_email(), "tag", "-a", "v1", "-m", "release")
    inproc.git("tag", "-a", "v2", "-m", "release " + fake_token())
    inproc.git("tag", "-a", "v3", "-m", "fine")
    for tag, check in (("v1", "identity: refs/tags/v1 tagger"), ("v2", "token: refs/tags/v2")):
        sha = inproc.git("rev-parse", tag).stdout.strip()
        code, err = run_main(
            ["pre-push", "origin", "url"],
            capsys,
            stdin=push_line(sha, f"refs/tags/{tag}", local_ref=f"refs/tags/{tag}"),
        )
        assert code == 1, tag
        assert f"pre-push: {check}" in err
    sha = inproc.git("rev-parse", "v3").stdout.strip()
    assert (
        run_main(
            ["pre-push", "origin", "url"],
            capsys,
            stdin=push_line(sha, "refs/tags/v3", local_ref="refs/tags/v3"),
        )[0]
        == 0
    )


def test_branch_deletion_and_rebased_push_pass(
    make_repo: Callable[..., Repo], tmp_path: Path
) -> None:
    repo = make_repo()
    remote = tmp_path / "remote.git"
    repo.git("init", "-q", "--bare", str(remote))
    repo.git("remote", "add", "origin", str(remote))
    repo.write("a.md", "a\n")
    assert repo.commit("a.md").returncode == 0
    repo.git("config", "core.hooksPath", "/dev/null")
    repo.git("push", "-q", "origin", "HEAD:refs/heads/main")
    repo.git("config", "core.hooksPath", ".githooks")

    repo.git("checkout", "-q", "-b", "feat")
    repo.write("f.md", "f\n")
    assert repo.commit("f.md").returncode == 0
    assert repo.git("push", "-q", "-u", "origin", "feat", check=False).returncode == 0

    # main gains a commit like GitHub's merge commits, committed by GitHub's own address.
    repo.git("checkout", "-q", "main")
    repo.write("m.md", "m\n")
    repo.git("add", "m.md")
    github_committer = fake_email("noreply", "github.com")
    gh_env = {"GIT_COMMITTER_EMAIL": github_committer, "GIT_COMMITTER_NAME": "GitHub"}
    repo.git("commit", "-q", "--no-verify", "-m", "Merge pull request (#2)", extra_env=gh_env)
    repo.git("config", "core.hooksPath", "/dev/null")
    repo.git("push", "-q", "origin", "main")
    repo.git("config", "core.hooksPath", ".githooks")
    repo.git("fetch", "-q", "origin")

    repo.git("checkout", "-q", "feat")
    repo.git("rebase", "-q", "origin/main")
    result = repo.git("push", "-q", "-f", "origin", "feat", check=False)
    assert result.returncode == 0, result.stderr
    result = repo.git("push", "-q", "origin", "--delete", "feat", check=False)
    assert result.returncode == 0, result.stderr


def test_pre_push_with_no_input_passes(inproc: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_main(["pre-push", "origin", "url"], capsys, stdin="")[0] == 0


def test_missing_terms_warning_covers_pre_push(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    code, err = run_main(["pre-push", "origin", "url"], capsys, stdin="")
    assert code == 0
    assert inproc.env["CLAUDE_CONFIG_DENY_TERMS"] in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_noreply_identity_still_pushes(repo: Repo, tmp_path: Path) -> None:
    remote = tmp_path / "r.git"
    repo.git("init", "-q", "--bare", str(remote))
    repo.git("remote", "add", "origin", str(remote))
    repo.write("a.md", "a\n")
    assert repo.commit("a.md").returncode == 0
    assert repo.git("log", "-1", "--format=%ae").stdout.strip() == NOREPLY
    assert repo.git("push", "-q", "origin", "HEAD:refs/heads/x", check=False).returncode == 0


def test_raw_entries_with_a_zero_id_are_skipped() -> None:
    blob = "a" * 40
    out = (
        f":000000 100644 {ZERO} {blob} A\0new.txt\0"
        f":100644 000000 {blob} {ZERO} D\0gone.txt\0"
        f"::100644 100644 100644 {blob} {blob} {blob} MM\0merged.txt\0"
    )
    assert [e.path for e in gh.parse_raw(out)] == ["new.txt", "merged.txt"]


def test_a_secret_seen_by_both_decodings_is_reported_once(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("mixed.bin", (fake_token() + "\n").encode() + b"\0\0")
    inproc.git("add", "mixed.bin")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert err.count("pre-commit: token: mixed.bin:1") == 1
