"""PreToolUse(Bash) hook: every PR merge waits for the user's click.

Prints a permissionDecision of "ask" for `gh … pr merge` and `gh api …/merge`;
prints nothing otherwise. Always exits 0 and never denies.

The check runs on every Bash command, so it reads the command once, left to
right, in time proportional to its length. A regular expression with nested
repeats did the same job but backtracked exponentially on commands holding
many dash-prefixed words (#20).
"""

import json
import re
import sys

REASON = "Merging needs your confirmation (global rule: merge only with your authorization)"

GH_END = re.compile(r"\bgh$")  # a word ending in a standalone "gh": gh, "gh, x&&gh
MERGE_WORD = re.compile(r"merge\b")
GH_API = re.compile(r"\bgh\s+api\b")
API_MERGE = re.compile(r"/merge\b")

# Where a scan of `gh [flags] pr merge` stands after a word. Flags are words
# starting with "-", each optionally followed by one value word.
AFTER_GH, FLAG, VALUE, AFTER_PR = range(4)


def _step(state: int, word: str) -> set[int]:
    """The states a scan can be in after `word`; empty when the scan fails."""
    dash = len(word) > 1 and word.startswith("-")  # a lone "-" is not a flag
    if state == AFTER_PR:
        return set()
    nxt: set[int] = set()
    if word == "pr":
        nxt.add(AFTER_PR)
    if dash:
        nxt.add(FLAG)
    elif state == FLAG:
        nxt.add(VALUE)
    return nxt


def merges_a_pr(command: str) -> bool:
    """Whether the command runs `gh … pr merge` or calls the merge API through `gh api`."""
    states: set[int] = set()
    for word in command.split():
        if AFTER_PR in states and MERGE_WORD.match(word):
            return True
        states = {s for state in states for s in _step(state, word)}
        if GH_END.search(word):
            states.add(AFTER_GH)
    return _calls_merge_api(command)


def _calls_merge_api(command: str) -> bool:
    """`gh api` followed, on the same line, by a /merge path."""
    scanned_to = -1
    for m in GH_API.finditer(command):
        if m.end() <= scanned_to:
            continue  # this line's rest was already searched from an earlier `gh api`
        line_end = command.find("\n", m.end())
        line_end = len(command) if line_end < 0 else line_end
        if API_MERGE.search(command, m.end(), line_end):
            return True
        scanned_to = line_end
    return False


def main() -> None:
    try:
        command = json.loads(sys.stdin.buffer.read().decode("utf-8"))["tool_input"]["command"]
    except Exception:
        return
    if isinstance(command, str) and merges_a_pr(command):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": REASON,
        }}))


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
