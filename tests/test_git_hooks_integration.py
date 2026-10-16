"""End-to-end checks: real git commits and pushes through the .githooks wrappers."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import Repo, fake_email, fake_token


def test_noreply_identity_commits_and_others_are_refused(repo: Repo) -> None:
    repo.write("ok.txt", "hello\n")
    assert repo.commit("ok.txt").returncode == 0

    repo.write("two.txt", "x\n")
    result = repo.commit("two.txt", extra=("--author", "X <" + fake_email() + ">"))
    assert result.returncode != 0
    assert "pre-commit: identity: author" in result.stderr

    result = repo.commit("two.txt", extra_env={"GIT_COMMITTER_EMAIL": fake_email()})
    assert result.returncode != 0
    assert "pre-commit: identity: committer" in result.stderr


def test_user_email_that_is_not_noreply_is_refused(make_repo: Callable[..., Repo]) -> None:
    repo = make_repo(email=fake_email())
    repo.write("ok.txt", "hello\n")
    result = repo.commit("ok.txt")
    assert result.returncode != 0
    assert "pre-commit: identity:" in result.stderr


@pytest.mark.parametrize("kind", ["ghp", "gho", "github_pat", "sk", "xoxb"])
def test_planted_tokens_are_refused_without_echo(repo: Repo, kind: str) -> None:
    token = fake_token(kind)
    repo.write("config.txt", "line one\nvalue = " + token + "\n")
    result = repo.commit("config.txt")
    assert result.returncode != 0
    assert "pre-commit: token: config.txt:2" in result.stderr
    assert token not in result.stderr + result.stdout


def test_symlink_replaced_by_a_file_with_a_token_is_refused(repo: Repo) -> None:
    (repo.path / "link").symlink_to("target")
    assert repo.commit("link").returncode == 0
    (repo.path / "link").unlink()
    repo.write("link", fake_token() + "\n")
    result = repo.commit("link")
    assert result.returncode != 0
    assert "pre-commit: token: link:1" in result.stderr


def test_emails_are_refused_unless_allow_listed(repo: Repo) -> None:
    repo.write("people.md", "Write to " + fake_email() + "\n")
    result = repo.commit("people.md")
    assert result.returncode != 0
    assert "pre-commit: email: people.md:1" in result.stderr

    repo.write(
        "people.md",
        "noreply@anthropic.com\n2057426+Towerism@users.noreply.github.com\ntest" + "@example.com\n",
    )
    assert repo.commit("people.md").returncode == 0


def test_missing_gitleaks_warns_and_a_failing_one_refuses(
    repo: Repo, fake_gitleaks: Callable[[int, str], None]
) -> None:
    repo.write("a.txt", "a\n")
    result = repo.commit("a.txt")
    assert result.returncode == 0
    assert "WARNING: gitleaks not found; only the built-in regex scan ran" in result.stderr

    fake_gitleaks(1, "[]")
    repo.write("b.txt", "b\n")
    result = repo.commit("b.txt")
    assert result.returncode != 0
    assert "pre-commit: gitleaks: exit 1" in result.stderr


@pytest.mark.parametrize(
    "path",
    [
        ".credentials.json",
        "logs/x.jsonl",
        "projects/a.md",
        "config/sessions/b",
        "eval/out/r.txt",
        "history.jsonl",
        "config/settings.json",
    ],
)
def test_never_versioned_paths_are_refused(repo: Repo, path: str) -> None:
    repo.write(path, "{}\n")
    result = repo.git("add", "-f", "--", path)
    assert result.returncode == 0
    result = repo.commit()
    assert result.returncode != 0
    assert f"pre-commit: deny-list: {path}" in result.stderr


def test_rename_into_a_denied_directory_is_refused(repo: Repo) -> None:
    repo.write("a.md", "a\n")
    assert repo.commit("a.md").returncode == 0
    (repo.path / "projects").mkdir()
    repo.git("mv", "a.md", "projects/a.md")
    result = repo.commit()
    assert result.returncode != 0
    assert "pre-commit: deny-list: projects/a.md" in result.stderr


@pytest.mark.parametrize(
    "path",
    ["docs/history/projects/a.md", "tools/cache.py", "config/settings.fragment.json"],
)
def test_lookalike_paths_are_allowed(repo: Repo, path: str) -> None:
    repo.write(path, "{}\n")
    repo.git("add", "-f", "--", path)
    assert repo.commit().returncode == 0


def test_excluded_terms_are_refused_and_never_printed(repo: Repo) -> None:
    terms = Path(repo.env["CLAUDE_CONFIG_DENY_TERMS"])
    terms.write_bytes(b"# private list\r\n\r\nZephyrine\r\n")

    repo.write("notes.md", "first\nabout zephyrine\n")
    result = repo.commit("notes.md")
    assert result.returncode != 0
    assert "pre-commit: deny-term: notes.md:2" in result.stderr
    assert "zephyrine" not in result.stderr.lower()

    repo.git("rm", "-q", "--cached", "notes.md")
    repo.write("notes/zephyrine.md", "clean\n")
    result = repo.commit("notes/zephyrine.md")
    assert result.returncode != 0
    assert "pre-commit: deny-term: <redacted path #1>" in result.stderr
    assert "zephyrine" not in result.stderr.lower()

    repo.git("rm", "-q", "--cached", "notes/zephyrine.md")
    repo.write("ok.md", "clean\n")
    result = repo.commit("ok.md", message="feat: mention Zephyrine (#1)")
    assert result.returncode != 0
    assert "commit-msg: deny-term: message:1" in result.stderr
    assert "zephyrine" not in result.stderr.lower()


def test_missing_terms_file_warns_with_its_path(repo: Repo) -> None:
    repo.write("ok.md", "clean\n")
    result = repo.commit("ok.md")
    assert result.returncode == 0
    assert repo.env["CLAUDE_CONFIG_DENY_TERMS"] in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_unreadable_terms_file_warns(repo: Repo) -> None:
    terms = Path(repo.env["CLAUDE_CONFIG_DENY_TERMS"])
    terms.write_text("zephyrine\n")
    terms.chmod(0)
    repo.write("ok.md", "zephyrine\n")
    result = repo.commit("ok.md")
    assert result.returncode == 0
    assert str(terms) in result.stderr


def test_commit_messages_are_scanned_for_tokens_but_trailers_pass(repo: Repo) -> None:
    repo.write("a.md", "a\n")
    result = repo.commit("a.md", message="fix: rotate " + fake_token() + " (#1)")
    assert result.returncode != 0
    assert "commit-msg: token: message:1" in result.stderr
    assert fake_token() not in result.stderr

    message = "fix: tidy (#1)\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    assert repo.commit(message=message).returncode == 0


def test_merge_commits_run_the_pre_commit_checks(repo: Repo) -> None:
    repo.write("base.md", "base\n")
    assert repo.commit("base.md").returncode == 0
    repo.git("checkout", "-q", "-b", "side")
    repo.write("side.md", fake_token() + "\n")
    assert repo.commit("side.md", extra=("--no-verify",)).returncode == 0
    repo.git("checkout", "-q", "main")
    repo.write("main.md", "main\n")
    assert repo.commit("main.md").returncode == 0
    result = repo.git("merge", "--no-ff", "-m", "chore: merge (#1)", "side", check=False)
    assert result.returncode != 0
    assert "pre-merge-commit: token: side.md:1" in result.stderr


def test_push_to_main_is_refused_and_branches_pass(
    make_repo: Callable[..., Repo], tmp_path: Path
) -> None:
    repo = make_repo()
    remote = tmp_path / "remote.git"
    repo.git("init", "-q", "--bare", str(remote))
    repo.git("remote", "add", "origin", str(remote))
    repo.write("a.md", "a\n")
    assert repo.commit("a.md").returncode == 0

    result = repo.git("push", "-q", "origin", "HEAD:main", check=False)
    assert result.returncode != 0
    assert "pre-push: main: refs/heads/main" in result.stderr

    assert (
        repo.git("push", "-q", "origin", "HEAD:refs/heads/chore/1-x", check=False).returncode == 0
    )

    repo.write("b.md", "b\n")
    assert (
        repo.commit(
            "b.md", extra=("--no-verify", "--author", "X <" + fake_email() + ">")
        ).returncode
        == 0
    )
    result = repo.git("push", "-q", "origin", "HEAD:refs/heads/chore/1-x", check=False)
    assert result.returncode != 0
    assert "pre-push: identity:" in result.stderr


def test_check_range_reports_a_clean_range(repo: Repo) -> None:
    repo.write("a.md", "a\n")
    assert repo.commit("a.md").returncode == 0
    base = repo.git("rev-parse", "HEAD").stdout.strip()
    repo.write("b.md", "b\n")
    assert repo.commit("b.md").returncode == 0
    result = repo.git(
        "-c", "alias.cr=!python3 -I tools/git_hooks.py check-range", "cr", base, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "terms checked: 0" in result.stderr
