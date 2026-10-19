"""Every `cfg` command in docs/runbook.md must be one the real tool accepts.

The runbook is followed when something is broken, so a flag that no longer
exists costs the most exactly when it's read. These tests parse each
`python3 -I tools/cfg/cli.py ...` line with the parser the command would
reach, without running anything.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import Path

import pytest

from tools import check_config
from tools.cfg import cli, lock
from tools.cfg import eval as cfg_eval

RUNBOOK = Path(__file__).resolve().parent.parent / "docs" / "runbook.md"
PREFIX = ["python3", "-I", "tools/cfg/cli.py"]
PRACTICE_HEADING = "Practice on a trial directory"

# A value the parser accepts in each placeholder's position. A placeholder
# missing here fails the check, so a new one is added here on purpose.
PLACEHOLDERS = {
    "path": "CLAUDE.md",
    "sha": "a" * 40,
    "good-sha": "a" * 40,
    "dir": "/tmp/trial",
    "trial": "/tmp/trial",
    "backup": "/tmp/trial",
    "worktree": "/tmp/feature-worktree",
    "host": "hosts/personal.json",
}
PLACEHOLDER = re.compile(r"<([a-z][a-z-]*)>")

# The forwarded commands hand their arguments to another tool's parser.
FORWARDED: dict[str, Callable[[list[str]], object]] = {
    "check": check_config.parse_args,
    "lock": lock.parse_args,
    "eval": cfg_eval._parse,
}


def code_lines(markdown: str) -> list[str]:
    """The lines inside fenced code blocks, with backslash continuations joined."""
    lines: list[str] = []
    fenced = False
    pending = ""
    for raw in markdown.splitlines():
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            continue
        if raw.rstrip().endswith("\\"):
            pending += raw.rstrip()[:-1] + " "
            continue
        lines.append(pending + raw)
        pending = ""
    return lines


def is_cfg(line: str) -> bool:
    """Whether a code line runs `cfg`; other lines are never split or filled."""
    return line.lstrip().startswith(" ".join(PREFIX))


def cfg_words(line: str) -> list[str] | None:
    """The words after the `cfg` prefix, cut at the first shell operator or comment.

    Returns None when the line isn't a `cfg` command. Other lines are never
    split, so an apostrophe in sample output can't read as an open quote.
    """
    if not is_cfg(line):
        return None
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    words: list[str] = []
    for word in lexer:
        if set(word) <= set("();<>|&"):
            break
        words.append(word)
    if words[: len(PREFIX)] != PREFIX:
        return None
    return words[len(PREFIX) :]


def fill(line: str) -> str:
    """Replace each `<placeholder>` with its sample value; an unknown one raises KeyError.

    This runs before the line is split into words, because the shell splitter
    would read a placeholder's angle brackets as redirections.
    """
    return PLACEHOLDER.sub(lambda m: PLACEHOLDERS[m.group(1)], line)


def parse_problem(argv: list[str]) -> str | None:
    """Why the parser behind these words rejects them, or None if it accepts them."""
    parse: Callable[[list[str]], object] = cli._parse
    if argv and argv[0] in FORWARDED:
        parse, argv = FORWARDED[argv[0]], argv[1:]
    try:
        parse(argv)
    except SystemExit as e:
        if e.code not in (0, None):
            return f"the parser exited with {e.code}"
    return None


def problems(markdown: str) -> list[str]:
    """Every `cfg` line the real parsers reject, plus an empty runbook."""
    found: list[str] = []
    seen = 0
    for line in code_lines(markdown):
        if not is_cfg(line):
            continue
        problem: str | None
        try:
            words = cfg_words(fill(line))
        except KeyError as e:
            words, problem = [], f"unknown placeholder <{e.args[0]}>"
        else:
            problem = None
        if words is None:
            continue
        seen += 1
        problem = problem or parse_problem(words)
        if problem:
            found.append(f"{problem}: {line.strip()}")
    if seen == 0:
        found.append("no cfg command lines found")
    return found


def section(markdown: str, heading: str) -> str | None:
    """The text under the heading with this title, up to the next heading of its level or higher."""
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"(#+) (.*)", line)
        if match and match.group(2).strip() == heading:
            level = len(match.group(1))
            body = []
            for rest in lines[i + 1 :]:
                deeper = re.match(r"(#+) ", rest)
                if deeper and len(deeper.group(1)) <= level:
                    break
                body.append(rest)
            return "\n".join(body)
    return None


def practice_problems(markdown: str) -> list[str]:
    """`cfg` lines in the practice section that could act on the live config."""
    body = section(markdown, PRACTICE_HEADING)
    if body is None:
        return [f"no '{PRACTICE_HEADING}' section"]
    found = []
    for line in code_lines(body):
        words = cfg_words(PLACEHOLDER.sub("x", line))
        if words is not None and "--target" not in words:
            found.append(f"no --target: {line.strip()}")
    return found


def fence(*lines: str) -> str:
    return "\n".join(["```", *lines, "```"])


# The committed runbook


def test_every_cfg_command_in_the_runbook_is_accepted() -> None:
    assert problems(RUNBOOK.read_text(encoding="utf-8")) == []


def test_every_practice_command_works_on_a_trial_directory() -> None:
    assert practice_problems(RUNBOOK.read_text(encoding="utf-8")) == []


# The checks themselves


def test_a_flag_cfg_does_not_have_is_reported_with_its_line() -> None:
    found = problems(fence("python3 -I tools/cfg/cli.py deploy --host <host> --force"))
    assert len(found) == 1
    assert "deploy --host <host> --force" in found[0]


def test_a_runbook_without_cfg_commands_fails() -> None:
    assert problems("# Runbook\n\n" + fence("git pull --ff-only")) == ["no cfg command lines found"]


def test_commands_outside_code_blocks_are_not_read() -> None:
    text = "python3 -I tools/cfg/cli.py deploy --force\n\n" + fence(
        "python3 -I tools/cfg/cli.py status"
    )
    assert problems(text) == []


def test_a_continued_line_is_read_as_one_command() -> None:
    text = fence(
        "python3 -I tools/cfg/cli.py deploy --host <host> \\",
        "  --discard-live <path> --yes",
    )
    assert problems(text) == []
    assert code_lines(text) == [
        "python3 -I tools/cfg/cli.py deploy --host <host>    --discard-live <path> --yes"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "python3 -I tools/cfg/cli.py status # then read the state",
        "python3 -I tools/cfg/cli.py plan --host <host> | less",
        "python3 -I tools/cfg/cli.py status && echo clean",
        "python3 -I tools/cfg/cli.py status; echo done",
    ],
)
def test_only_the_cfg_part_of_a_shell_line_is_parsed(line: str) -> None:
    assert problems(fence(line)) == []


def test_an_unknown_placeholder_is_reported() -> None:
    found = problems(fence("python3 -I tools/cfg/cli.py diff <file>"))
    assert found == ["unknown placeholder <file>: python3 -I tools/cfg/cli.py diff <file>"]


def test_a_placeholder_inside_a_word_is_filled() -> None:
    assert problems(fence("python3 -I tools/cfg/cli.py status --target <trial>/home")) == []


@pytest.mark.parametrize(
    ("line", "accepted"),
    [
        ("python3 -I tools/cfg/cli.py check --host <host> --path-rule fail", True),
        ("python3 -I tools/cfg/cli.py check --path-rule sometimes", False),
        ("python3 -I tools/cfg/cli.py lock --check", True),
        ("python3 -I tools/cfg/cli.py lock --bogus", False),
        ("python3 -I tools/cfg/cli.py eval --suite scenario", True),
    ],
)
def test_forwarded_commands_are_checked_by_their_own_tool(
    line: str, accepted: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (problems(fence(line)) == []) is accepted
    capsys.readouterr()


def test_help_is_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    assert problems(fence("python3 -I tools/cfg/cli.py deploy --help")) == []
    capsys.readouterr()


def test_other_commands_in_a_block_are_ignored() -> None:
    text = fence("git pull --ff-only", "python3 -I tools/cfg/cli.py status", "")
    assert problems(text) == []


def test_a_practice_command_without_target_is_reported() -> None:
    text = "\n".join(
        [
            "# Runbook",
            f"## {PRACTICE_HEADING}",
            fence(
                'python3 -I tools/cfg/cli.py status --target "$TRIAL/home"',
                "python3 -I tools/cfg/cli.py deploy --host <host> --discard-live <path> --yes",
            ),
            "## Next section",
            fence("python3 -I tools/cfg/cli.py status"),
        ]
    )
    assert practice_problems(text) == [
        "no --target: python3 -I tools/cfg/cli.py deploy --host <host> --discard-live <path> --yes"
    ]


def test_a_runbook_without_the_practice_section_fails() -> None:
    assert practice_problems("# Runbook\n## Deploying\n") == [f"no '{PRACTICE_HEADING}' section"]


def test_a_practice_subsection_belongs_to_the_practice_section() -> None:
    text = "\n".join(
        [
            f"## {PRACTICE_HEADING}",
            "### Roll back",
            fence("python3 -I tools/cfg/cli.py rollback"),
        ]
    )
    assert practice_problems(text) == ["no --target: python3 -I tools/cfg/cli.py rollback"]


def test_an_apostrophe_in_other_code_lines_is_not_read_as_a_quote() -> None:
    text = fence(
        "left alone  CLAUDE.md: it was no longer cfg's link", "python3 -I tools/cfg/cli.py status"
    )
    assert problems(text) == []


def test_placeholders_in_other_code_lines_are_ignored() -> None:
    text = fence(
        "backup: /home/<owner>/.claude/backups",
        "git worktree add -b <branch> <worktree> origin/main",
    )
    assert problems(text + "\n" + fence("python3 -I tools/cfg/cli.py status")) == []


def test_a_different_script_with_the_same_prefix_is_not_cfg() -> None:
    text = fence("python3 -I tools/cfg/cli.py.bak --anything", "python3 -I tools/cfg/cli.py status")
    assert problems(text) == []
