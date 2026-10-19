"""Unit tests for tools/git_hooks.py, run in-process so they count for coverage."""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import NOREPLY, Repo, fake_email, fake_token, run_main
from tools import git_hooks as gh

ZERO = "0" * 40

# ---------------------------------------------------------------- identity


@pytest.mark.parametrize(
    "email",
    [NOREPLY, "Towerism@users.noreply.github.com", "a-b@users.noreply.github.com"],
)
def test_noreply_addresses_are_accepted(email: str) -> None:
    assert gh.is_noreply(email)


@pytest.mark.parametrize(
    "email",
    [
        fake_email(),
        "x" + NOREPLY,
        NOREPLY + ".evil.example",
        "-bad@users.noreply.github.com",
        "@users.noreply.github.com",
        "",
    ],
)
def test_other_addresses_are_not_noreply(email: str) -> None:
    assert not gh.is_noreply(email)


def test_email_is_parsed_from_a_git_ident() -> None:
    assert gh.ident_email("Test Owner <" + NOREPLY + "> 1700000000 +0000") == NOREPLY
    assert gh.ident_email("no brackets here") == ""


# ---------------------------------------------------------------- content scan


@pytest.mark.parametrize("kind", ["ghp", "gho", "github_pat", "sk", "xoxb"])
def test_each_token_kind_is_found_with_its_line(kind: str) -> None:
    text = "first line\nkey = " + fake_token(kind) + "\n"
    assert gh.scan_text(text) == [("token", 2)]


@pytest.mark.parametrize(
    "word",
    [
        "task-management-workflow-design",
        "risk-assessment-for-hooks-v2",
        "ask-the-owner-before-merging-it",
        "my_ghp_" + "a" * 36,
    ],
)
def test_words_that_merely_contain_a_prefix_pass(word: str) -> None:
    assert gh.scan_text(word) == []


def test_a_real_email_is_found() -> None:
    assert gh.scan_text("contact " + fake_email() + " today") == [("email", 1)]


@pytest.mark.parametrize(
    "allowed",
    [
        "noreply@anthropic.com",
        NOREPLY,
        "test" + "@example.com",
        "a" + "@example.org",
        "b" + "@example.net",
    ],
)
def test_allow_listed_emails_pass(allowed: str) -> None:
    assert gh.scan_text("Co-Authored-By: X <" + allowed + ">") == []


def test_lookalike_allow_list_domains_are_still_found() -> None:
    assert gh.scan_text("x" + "@example.com.evil.io") == [("email", 1)]
    assert gh.scan_text("x" + "@notexample.com") == [("email", 1)]


# RFC 2606 reserves these top-level domains, so no real mailbox can sit under them.
RESERVED_TLD_ADDRESSES = [
    "test" + "@example.invalid",
    "a" + "@b.test",
    "x" + "@foo.example",
    "y" + "@mail.localhost",
    "Z" + "@EXAMPLE.INVALID",
]
# Each looks like a reserved address but routes to a real domain.
RESERVED_TLD_LOOKALIKES = [
    "x" + "@example.invalid.evil.io",
    "x" + "@invalid.com",
    "x" + "@testing.io",
    "x" + "@mail.notinvalid",
]


@pytest.mark.parametrize("allowed", RESERVED_TLD_ADDRESSES)
def test_addresses_under_reserved_top_level_domains_pass(allowed: str) -> None:
    assert gh.scan_text("GIT_AUTHOR_EMAIL=" + allowed) == []


@pytest.mark.parametrize("lookalike", RESERVED_TLD_LOOKALIKES)
def test_lookalikes_of_reserved_top_level_domains_are_still_found(lookalike: str) -> None:
    assert gh.scan_text("x " + lookalike) == [("email", 1)]


def _gitleaks_email_rule() -> dict[str, object]:
    config = tomllib.loads((Path(__file__).parent.parent / ".gitleaks.toml").read_text())
    rules = [r for r in config["rules"] if r["id"] == "claude-config-email-address"]
    assert len(rules) == 1
    rule: dict[str, object] = rules[0]
    return rule


def _gitleaks_allows(address: str) -> bool:
    allowlists = _gitleaks_email_rule()["allowlists"]
    assert isinstance(allowlists, list)
    return any(
        re.search(pattern, address) for allowlist in allowlists for pattern in allowlist["regexes"]
    )


def test_gitleaks_email_rule_matches_the_hook_pattern() -> None:
    assert _gitleaks_email_rule()["regex"] == gh.EMAIL_RE.pattern


@pytest.mark.parametrize("allowed", RESERVED_TLD_ADDRESSES)
def test_gitleaks_allows_reserved_top_level_domains(allowed: str) -> None:
    assert _gitleaks_allows(allowed)


@pytest.mark.parametrize("lookalike", RESERVED_TLD_LOOKALIKES)
def test_gitleaks_still_flags_reserved_lookalikes(lookalike: str) -> None:
    assert not _gitleaks_allows(lookalike)


def test_one_line_with_a_token_and_an_email_gives_two_findings() -> None:
    assert gh.scan_text(fake_token() + " " + fake_email()) == [("token", 1), ("email", 1)]


# ---------------------------------------------------------------- deny-list


@pytest.mark.parametrize(
    "path",
    [
        ".credentials.json",
        "config/.credentials.json",
        "logs/x.jsonl",
        ".env",
        "a/.env.local",
        "k.pem",
        "deep/k.key",
        "id_rsa",
        "id_ed25519.pub",
        ".claude.json",
        "history.jsonl",
        "history",
        "config/history.txt",
        "daemon.log",
        "daemon.lock",
        "x.log",
        "gh-pr-status-cache.json",
        ".last-cleanup",
        "settings.json",
        "config/settings.json",
        "projects/a.md",
        "config/sessions/b",
        "session-env/x",
        "jobs/1/out",
        "tasks/t",
        "todos/t",
        "shell-snapshots/s",
        "file-history/f",
        "paste-cache/p",
        "cache/c",
        "backups/b",
        "state/s",
        "statsig/s",
        "ide/i",
        "chrome/c",
        "daemon/d",
        "feedback/f",
        "plugins/cache/x",
        "config/plugins/marketplaces/y",
        "skills/synced/z",
        "config/skills/.trash/q",
        "eval/out/r.txt",
    ],
)
def test_never_versioned_paths_are_denied(path: str) -> None:
    assert gh.denied_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "docs/history/projects/a.md",
        "tools/cache.py",
        "config/settings.fragment.json",
        "docs/history/x.md",
        "tools/x.log.py",
        "config/skills/a/state.py",
        "plugins/other/x",
        "skills/mine/SKILL.md",
        "README.md",
        "eval/outline.md",
        "x/eval/out/r.txt",
        "config/hooks/daemon_check.py",
    ],
)
def test_ordinary_paths_are_allowed(path: str) -> None:
    assert not gh.denied_path(path)


@pytest.mark.parametrize("path", [".ENV", "Projects/a.md", "CONFIG/Settings.json", "x/K.PEM"])
def test_deny_list_ignores_case(path: str) -> None:
    assert gh.denied_path(path)


def test_every_deny_list_entry_is_in_gitignore() -> None:
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text().splitlines()
    for entry in gh.gitignore_entries():
        assert entry in gitignore, entry


# ---------------------------------------------------------------- excluded terms


def test_terms_file_is_parsed(tmp_path: Path) -> None:
    f = tmp_path / "terms"
    f.write_bytes(b"# comment\r\n\r\n  Zephyrine  \r\nOther\n")
    assert gh.load_terms(f) == ["zephyrine", "other"]


def test_missing_terms_file_gives_none(tmp_path: Path) -> None:
    assert gh.load_terms(tmp_path / "absent") is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_unreadable_terms_file_gives_none(tmp_path: Path) -> None:
    f = tmp_path / "terms"
    f.write_text("x\n")
    f.chmod(0)
    assert gh.load_terms(f) is None


def test_terms_are_found_case_insensitively_by_line() -> None:
    assert gh.term_lines("a\nthe ZEPHYRINE file\nb", ["zephyrine"]) == [2]
    assert gh.term_lines("nothing", ["zephyrine"]) == []
    assert gh.term_lines("anything", []) == []


def test_terms_path_comes_from_env_then_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DENY_TERMS", str(tmp_path / "t"))
    assert gh.terms_path() == tmp_path / "t"
    monkeypatch.delenv("CLAUDE_CONFIG_DENY_TERMS")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gh.terms_path() == tmp_path / ".config" / "claude-config" / "deny-terms"


# ---------------------------------------------------------------- output


def test_findings_are_formatted_and_paths_with_terms_are_redacted() -> None:
    findings = [
        gh.Finding("pre-commit", "token", "a.txt", 3),
        gh.Finding("pre-commit", "deny-term", "notes/Zephyrine.md"),
        gh.Finding("pre-commit", "email", "notes/Zephyrine.md", 2),
        gh.Finding("pre-commit", "deny-term", "zephyrine2.md", 1),
    ]
    assert gh.format_findings(findings, ["zephyrine"]) == [
        "pre-commit: token: a.txt:3",
        "pre-commit: deny-term: <redacted path #1>",
        "pre-commit: email: <redacted path #1>:2",
        "pre-commit: deny-term: <redacted path #2>:1",
    ]


# ---------------------------------------------------------------- commit messages


SCISSORS = " ------------------------ >8 ------------------------"


def test_comment_lines_are_kept_for_scanning() -> None:
    raw = "feat: x (#1)\n\nbody\n# a comment " + fake_token() + "\n"
    assert gh.message_to_scan(raw) == raw


def test_only_gits_own_verbose_diff_is_skipped() -> None:
    head = "feat: x (#1)\n# Please enter the commit message\n"
    raw = head + "#" + SCISSORS + "\n# Do not modify or remove the line above.\n"
    raw += "diff --git a/x b/x\n-" + fake_token() + "\n"
    assert gh.message_to_scan(raw) == head


def test_verbose_diff_skipping_honours_a_multi_character_comment_prefix() -> None:
    head = "feat: x (#1)\n"
    raw = head + "//" + SCISSORS + "\n// keep out\ndiff --git a/x b/x\n-gone\n"
    assert gh.message_to_scan(raw) == head


def test_a_fake_scissors_line_without_a_diff_is_scanned() -> None:
    raw = "feat: x (#1)\n#" + SCISSORS + "\n" + fake_token() + "\n"
    assert gh.message_to_scan(raw) == raw


def test_lines_split_on_newline_only() -> None:
    assert gh.scan_text("a\x0cb\n" + fake_token()) == [("token", 2)]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\xff\xfe\x00\x00" + "hi ok".encode("utf-32-le"), "hi ok"),
        (b"\x00\x00\xfe\xff" + "hi ok".encode("utf-32-be"), "hi ok"),
        (b"\xff\xfe" + "hi ok".encode("utf-16-le"), "hi ok"),
        (b"\xfe\xff" + "hi ok".encode("utf-16-be"), "hi ok"),
        (b"plain", "plain"),
    ],
)
def test_byte_order_marks_pick_the_codec(data: bytes, expected: str) -> None:
    assert gh.decode_blob(data)[0] == expected


def test_text_with_nul_bytes_is_also_scanned_without_them() -> None:
    texts = gh.decode_blob(fake_token().encode("utf-16-le"))
    assert any(gh.scan_text(t) for t in texts)


def test_broken_utf16_does_not_raise() -> None:
    assert gh.decode_blob(b"\xff\xfe\x00")


# ---------------------------------------------------------------- pre-push parsing


def test_push_lines_are_parsed() -> None:
    line = "refs/heads/x " + "a" * 40 + " refs/heads/main " + ZERO
    assert gh.parse_push_line(line) == gh.PushRef("refs/heads/x", "a" * 40, "refs/heads/main", ZERO)
    assert gh.parse_push_line("garbage") is None
    assert gh.parse_push_line("") is None


@pytest.mark.parametrize("sha", ["0" * 40, "0" * 64])
def test_zero_shas_of_both_lengths_are_recognised(sha: str) -> None:
    assert gh.is_zero(sha)
    assert not gh.is_zero("0" * 39 + "1")


# ---------------------------------------------------------------- main(), in-process


def test_usage_errors_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_main([], capsys)[0] == 2
    assert run_main(["bogus"], capsys)[0] == 2
    assert run_main(["commit-msg"], capsys)[0] == 2
    assert run_main(["check-range"], capsys)[0] == 2


def test_clean_staged_file_passes_with_warnings(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("ok.txt", "hello\n")
    inproc.git("add", "ok.txt")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 0
    assert "WARNING: gitleaks not found; only the built-in regex scan ran" in err
    assert "deny-terms" in err


def test_staged_file_with_a_reserved_domain_address_passes(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("conftest.py", 'EMAIL = "test' + '@example.invalid"\n')
    inproc.git("add", "conftest.py")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 0
    assert "email" not in err


def test_staged_token_is_refused_without_echo(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    token = fake_token()
    inproc.write("leak.txt", "a\n" + token + "\n")
    inproc.git("add", "leak.txt")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert "pre-commit: token: leak.txt:2" in err
    assert token not in err


def test_wrong_identity_is_refused(
    inproc: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", fake_email())
    inproc.write("ok.txt", "hello\n")
    inproc.git("add", "ok.txt")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert "pre-commit: identity: committer" in err
    assert fake_email() not in err


def test_deleted_files_are_not_scanned(inproc: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    inproc.write("x.txt", "fine\n")
    assert inproc.commit("x.txt").returncode == 0
    inproc.git("rm", "-q", "x.txt")
    assert run_main(["pre-commit"], capsys)[0] == 0


def test_denied_path_and_term_in_content_and_path(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    Path(inproc.env["CLAUDE_CONFIG_DENY_TERMS"]).write_text("Zephyrine\n")
    inproc.write("projects/a.md", "x\n")
    inproc.write("notes/zephyrine.md", "x\n")
    inproc.write("b.txt", "one\nZEPHYRINE two\n")
    inproc.git("add", "projects/a.md", "notes/zephyrine.md", "b.txt")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert "pre-commit: deny-list: projects/a.md" in err
    assert "pre-commit: deny-term: <redacted path #1>" in err
    assert "pre-commit: deny-term: b.txt:2" in err
    assert "zephyrine" not in err.lower()


def test_gitleaks_findings_are_reformatted(
    inproc: Repo, fake_gitleaks: Callable[[int, str], None], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_gitleaks(1, json.dumps([{"File": "g.txt", "StartLine": 4}]))
    inproc.write("g.txt", "fine\n")
    inproc.git("add", "g.txt")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert "pre-commit: gitleaks: g.txt:4" in err
    assert "gitleaks own output" not in err


def test_gitleaks_exit_1_without_report_still_refuses(
    inproc: Repo, fake_gitleaks: Callable[[int, str], None], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_gitleaks(1, "not json")
    inproc.write("g.txt", "fine\n")
    inproc.git("add", "g.txt")
    code, err = run_main(["pre-commit"], capsys)
    assert code == 1
    assert "pre-commit: gitleaks: exit 1" in err


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (3, 1)])
def test_gitleaks_exit_codes(
    inproc: Repo,
    fake_gitleaks: Callable[[int, str], None],
    capsys: pytest.CaptureFixture[str],
    code: int,
    expected: int,
) -> None:
    fake_gitleaks(code)
    inproc.write("g.txt", "fine\n")
    inproc.git("add", "g.txt")
    result, err = run_main(["pre-merge-commit"], capsys)
    assert result == expected
    assert "gitleaks not found" not in err
    if code == 3:
        assert "pre-merge-commit: gitleaks: exit 3" in err


def test_commit_msg_checks(
    inproc: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Path(inproc.env["CLAUDE_CONFIG_DENY_TERMS"]).write_text("zephyrine\n")
    msg = tmp_path / "MSG"
    msg.write_text("feat: x (#1)\n\nCo-Authored-By: C <noreply@anthropic.com>\n")
    assert run_main(["commit-msg", str(msg)], capsys)[0] == 0
    msg.write_text("feat: x (#1)\n\nsee " + fake_token("xoxb") + "\nZephyrine\n")
    code, err = run_main(["commit-msg", str(msg)], capsys)
    assert code == 1
    assert "commit-msg: token: message:3" in err
    assert "commit-msg: deny-term: message:4" in err


def test_commit_msg_refuses_secrets_on_comment_lines(
    inproc: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    msg = tmp_path / "MSG"
    msg.write_text("feat: x (#1)\n# " + fake_token() + "\n")
    code, err = run_main(["commit-msg", str(msg)], capsys)
    assert code == 1
    assert "commit-msg: token: message:2" in err


def test_commit_msg_with_unreadable_file_is_a_usage_error(
    inproc: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_main(["commit-msg", str(tmp_path / "nope")], capsys)[0] == 2


def _push_line(repo: Repo, remote_ref: str, remote_sha: str = ZERO) -> str:
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    return f"refs/heads/x {head} {remote_ref} {remote_sha}\n"


def test_pre_push_refuses_main_and_passes_branches(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    code, err = run_main(
        ["pre-push", "origin", "url"], capsys, stdin=_push_line(inproc, "refs/heads/main")
    )
    assert code == 1
    assert "pre-push: main: refs/heads/main" in err
    deletion = f"(delete) {ZERO} refs/heads/main {'b' * 40}\n"
    assert run_main(["pre-push", "origin", "url"], capsys, stdin=deletion)[0] == 1
    assert (
        run_main(
            ["pre-push", "origin", "url"], capsys, stdin=_push_line(inproc, "refs/heads/chore/1-x")
        )[0]
        == 0
    )
    other_deletion = f"(delete) {ZERO} refs/heads/old {'b' * 40}\n"
    assert run_main(["pre-push", "origin", "url"], capsys, stdin=other_deletion)[0] == 0
    assert run_main(["pre-push", "origin", "url"], capsys, stdin="\n")[0] == 0
    code, err = run_main(["pre-push", "origin", "url"], capsys, stdin="\njunk\n")
    assert code == 1
    assert "pre-push: input: 2" in err
    unknown_local = f"refs/heads/x {'d' * 40} refs/heads/y {ZERO}\n"
    code, err = run_main(["pre-push", "origin", "url"], capsys, stdin=unknown_local)
    assert code == 1
    assert "pre-push: input: 1" in err


def test_pre_push_refuses_commits_with_a_foreign_identity(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    base = inproc.git("rev-parse", "HEAD").stdout.strip()
    inproc.write("b.txt", "b\n")
    bad = inproc.commit("b.txt", extra=("--no-verify", "--author", "X <" + fake_email() + ">"))
    assert bad.returncode == 0
    code, err = run_main(
        ["pre-push", "origin", "url"], capsys, stdin=_push_line(inproc, "refs/heads/feature", base)
    )
    assert code == 1
    assert "pre-push: identity: " in err
    assert fake_email() not in err
    unknown_remote_sha = "c" * 40
    assert (
        run_main(
            ["pre-push", "origin", "url"],
            capsys,
            stdin=_push_line(inproc, "refs/heads/feature", unknown_remote_sha),
        )[0]
        == 1
    )


def test_check_range_covers_tree_and_commits(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    base = inproc.git("rev-parse", "HEAD").stdout.strip()
    assert run_main(["check-range", base], capsys)[0] == 0
    inproc.write("b.txt", fake_token() + "\n")
    inproc.write("history.jsonl", "{}\n")
    bad = inproc.commit(
        "b.txt",
        "history.jsonl",
        message="feat: " + fake_email() + " (#1)",
        extra=("--no-verify", "--author", "X <" + fake_email() + ">"),
    )
    assert bad.returncode == 0
    short = inproc.git("rev-parse", "--short", "HEAD").stdout.strip()
    code, err = run_main(["check-range", base], capsys)
    assert code == 1
    assert "check-range: token: b.txt:1" in err
    assert "check-range: deny-list: history.jsonl" in err
    assert f"check-range: identity: {short} author" in err
    assert f"check-range: email: {short} message:1" in err
    assert "terms checked: 0" in err
    assert run_main(["check-range", "no-such-ref"], capsys)[0] == 2


def _base_commit(inproc: Repo) -> str:
    inproc.write("base.txt", "base\n")
    assert inproc.commit("base.txt").returncode == 0
    return inproc.git("rev-parse", "HEAD").stdout.strip()


def _github_merge(inproc: Repo, name: str, author: str, committer: str) -> str:
    inproc.write(name, name + "\n")
    env = {"GIT_COMMITTER_EMAIL": committer, "GIT_COMMITTER_NAME": "GitHub"}
    extra = ("--no-verify", "--author", f"Owner <{author}>")
    result = inproc.commit(name, message="chore: merge (#1)", extra=extra, extra_env=env)
    assert result.returncode == 0
    return inproc.git("rev-parse", "--short", "HEAD").stdout.strip()


def test_check_range_accepts_merges_github_commits_for_a_noreply_author(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base_commit(inproc)
    _github_merge(inproc, "m.txt", NOREPLY, fake_email("noreply", "github.com"))
    code, err = run_main(["check-range", base], capsys)
    assert code == 0
    assert "identity" not in err


def test_check_range_still_refuses_githubs_address_as_author_or_other_committers(
    inproc: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base_commit(inproc)
    github = fake_email("noreply", "github.com")
    as_author = _github_merge(inproc, "a.txt", github, github)
    other = _github_merge(inproc, "b.txt", NOREPLY, fake_email("someone", "example.invalid"))
    lookalike = _github_merge(inproc, "c.txt", NOREPLY, fake_email("noreply", "github.com.evil.io"))
    code, err = run_main(["check-range", base], capsys)
    assert code == 1
    assert f"check-range: identity: {as_author} author" in err
    assert f"check-range: identity: {as_author} committer" in err
    assert f"check-range: identity: {other} committer" in err
    assert f"check-range: identity: {lookalike} committer" in err


def test_check_range_reports_term_count(inproc: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    Path(inproc.env["CLAUDE_CONFIG_DENY_TERMS"]).write_text("one\ntwo\n# c\n")
    inproc.write("a.txt", "a\n")
    assert inproc.commit("a.txt").returncode == 0
    base = inproc.git("rev-parse", "HEAD").stdout.strip()
    code, err = run_main(["check-range", base], capsys)
    assert code == 0
    assert "terms checked: 2" in err
