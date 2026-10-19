"""The merge-confirmation hook: every PR merge waits for the owner's click.

The hook runs before every Bash call, so it must decide quickly whatever the
command holds, never fail, and never echo the command back (ADR-0007's hook
review bar).
"""

import itertools
import json
import re

import pytest

from tests.hook_helpers import load_hook, make_home, payload, run_main, run_script

REASON = "Merging needs your confirmation (global rule: merge only with your authorization)"
CANARY = "CANARY-7f3a"
SIZE = 64 * 1024


@pytest.fixture
def hook():
    return load_hook("merge_confirm")


def bash(command):
    return payload(hook_event_name="PreToolUse", tool_name="Bash", tool_input={"command": command})


def decision(hook, command, monkeypatch, capsys):
    out, err = run_main(hook, bash(command), monkeypatch, capsys)
    assert err == ""
    return json.loads(out)["hookSpecificOutput"] if out else None


MUTATION = 'mergePullRequest(input: {pullRequestId: "x"}) { clientMutationId }'
MERGEABLE_QUERY = 'repository(owner: "o", name: "r") { pullRequest(number: 5) { mergeable } }'

ASKS = [
    "gh pr merge 5",
    "gh pr merge",
    "gh -R o/r pr merge 5",
    "gh --repo o/r pr merge --squash 5",
    "cd x && gh pr merge 5",
    'bash -c "gh pr merge 5"',
    "GH_HOST=x gh pr merge 5",
    "gh  pr\tmerge 5",
    "gh api repos/o/r/pulls/5/merge",
    "gh api -X PUT repos/o/r/pulls/5/merge",
    "cat <<'EOF' > notes.md\nsome text\nEOF\ngh pr merge 5",
    "gh pr -R o/r merge 5",
    "gh pr --repo o/r merge 5",
    "gh -R o/r pr --repo o/r merge --squash 5",
    f"gh api graphql -f query='mutation {{ {MUTATION} }}'",
    f"gh api graphql -f query='\nmutation {{\n  {MUTATION}\n}}'",
]

QUIET = [
    "gh pr view 5",
    "gh pr list --search merge",
    "gh pr view --json mergeable 5",
    "git merge main",
    "echo gh pr mergeable",
    "gh api repos/o/r/pulls/5 --jq .mergeable_state",
    "gh pr view 5; echo merge",
    "gh pr -R o/r view 5",
    "gh pr --repo o/r list --search merge",
    f"gh api graphql -f query='query {{ {MERGEABLE_QUERY} }}'",
    "echo mergePullRequest",
    "",
]


@pytest.mark.parametrize("command", ASKS)
def test_a_merge_asks_for_confirmation(hook, monkeypatch, capsys, command):
    assert decision(hook, command, monkeypatch, capsys) == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": REASON,
    }


@pytest.mark.parametrize("command", QUIET)
def test_anything_else_passes_silently(hook, monkeypatch, capsys, command):
    assert decision(hook, command, monkeypatch, capsys) is None


@pytest.mark.parametrize(
    "stdin",
    [
        b"",
        b"not json",
        b"\xff\xfe\x00",
        b"[1, 2]",
        b"null",
        payload(hook_event_name="PreToolUse"),
        payload(tool_input=[1, 2]),
        payload(tool_input={"cmd": "gh pr merge 5"}),
        payload(tool_input={"command": 5}),
        payload(tool_input={"command": ["gh", "pr", "merge"]}),
    ],
    ids=[
        "empty",
        "not json",
        "not utf-8",
        "json array",
        "json null",
        "no tool input",
        "tool input a list",
        "no command",
        "command a number",
        "command a list",
    ],
)
def test_malformed_input_prints_nothing_and_never_raises(hook, monkeypatch, capsys, stdin):
    assert run_main(hook, stdin, monkeypatch, capsys) == ("", "")


def test_the_command_is_never_echoed(hook, monkeypatch, capsys):
    out, err = run_main(hook, bash(f"gh pr merge 5 --body {CANARY}"), monkeypatch, capsys)
    assert out
    assert CANARY not in out + err


# Hostile commands. A pattern that backtracks exponentially or quadratically
# takes seconds to minutes on these; a linear one takes milliseconds. The 1 s
# timeout sits between the two with a wide margin on either side, so this
# needs no wall-clock assertion.
HOSTILE = {
    "dash words after gh": "gh " + " -a" * (SIZE // 3) + " pr mergex",
    "gh -a repeated": "gh -a " * (SIZE // 6),
    "(gh -a repeated": "(gh -a " * (SIZE // 7),
    "gh api repeated": "gh api " * (SIZE // 7),
    "dash words after gh pr": "gh pr " + " -a" * (SIZE // 3) + " mergex",
    "flag and value pairs": "gh " + "-a x " * (SIZE // 5),
}


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_commands_are_decided_quickly(tmp_path, name):
    done = run_script("merge_confirm", bash(HOSTILE[name]), make_home(tmp_path), timeout=1)
    assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")


def test_the_script_exits_0_with_nothing_on_stderr_for_malformed_input(tmp_path):
    done = run_script("merge_confirm", payload(tool_input=[1]), make_home(tmp_path))
    assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")


# The pattern the hook used until #20, which backtracked exponentially on long
# runs of dash words, and the same pattern widened to what #20 adds: flags
# between `pr` and `merge`, and a GraphQL mergePullRequest call anywhere after
# `gh api`. Both are fine as references on commands of a few words.
FLAGS = r"(\s+-\S+(\s+\S+)?)*"
ORIGINAL = re.compile(r"\bgh\b" + FLAGS + r"\s+pr\s+merge\b|\bgh\s+api\b.*/merge\b")
WIDENED = re.compile(
    r"\bgh\b" + FLAGS + r"\s+pr" + FLAGS + r"\s+merge\b"
    r"|\bgh\s+api\b.*/merge\b|\bgh\s+api\b[\s\S]*\bmergePullRequest\b"
)
WORDS = [
    "gh", "pr", "merge", "-R", "x", "-", "merge-x", '"gh', "o/gh-t", "api", "a/merge",
    "mergePullRequest", "\n",
]  # fmt: skip
COMMANDS = [" ".join(w) for n in range(1, 5) for w in itertools.product(WORDS, repeat=n)]


def test_every_merge_the_original_pattern_caught_is_still_caught(hook):
    assert [c for c in COMMANDS if ORIGINAL.search(c) and not hook.merges_a_pr(c)] == []


def test_the_hook_catches_exactly_the_widened_set_of_merges(hook):
    assert [c for c in COMMANDS if bool(WIDENED.search(c)) != hook.merges_a_pr(c)] == []
