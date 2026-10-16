"""PreToolUse(Bash) hook: every PR merge waits for the user's click.

Prints a permissionDecision of "ask" for `gh … pr merge` and `gh api …/merge`;
prints nothing otherwise. Always exits 0 and never denies.
"""
import json
import re
import sys

REASON = "Merging needs your confirmation (global rule: merge only with your authorization)"
MERGE = re.compile(r"\bgh\b(\s+-\S+(\s+\S+)?)*\s+pr\s+merge\b|\bgh\s+api\b.*/merge\b")


def main() -> None:
    try:
        command = json.loads(sys.stdin.read())["tool_input"]["command"]
    except Exception:
        return
    if isinstance(command, str) and MERGE.search(command):
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
