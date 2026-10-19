"""Helpers for the CI workflow in .github/workflows/ci.yml (issue #10).

GitHub Actions sets GITHUB_EVENT_NAME and GITHUB_EVENT_PATH, and this script reads the
event from that file, never from text pasted into the workflow, so nothing a PR author
writes reaches a shell. Standard library only; no network.

Commands:
  base                 print the commit the range checks start from
  changes --base SHA   print code=true, or code=false when only docs/ changed in a PR
  commits --base SHA   run commit-check on every commit message; report prose findings
  specs [--root DIR]   run the spec validator on every SPEC.md

Exit codes: 0 ok, 1 a check failed, 2 a usage or environment error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
LIFECYCLE = REPO_ROOT / "config/skills/github-issue-lifecycle/scripts/issue_lifecycle.py"
PROSE_CHECK = REPO_ROOT / "config/skills/plain-prose/scripts/prose_check.py"
VALIDATOR = REPO_ROOT / "tools/vendor/claude-skills/spec_validator.py"
ZERO_SHA = "0" * 40
# tests/test_runbook_commands.py reads the runbook, so a change to it must run the tests.
DOCS_READ_BY_TESTS = frozenset({"docs/runbook.md"})


class CiError(Exception):
    """A usage or environment problem: exit 2."""


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise CiError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def commit_or_fail(rev: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{commit}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CiError(f"{rev} is not a commit in this clone")
    return result.stdout.strip()


def load_event() -> tuple[str, dict[str, Any]]:
    name = os.environ.get("GITHUB_EVENT_NAME")
    if not name:
        raise CiError("GITHUB_EVENT_NAME is not set")
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise CiError("GITHUB_EVENT_PATH is not set")
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CiError(f"cannot read the event file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CiError(f"the event file {path} is not a JSON object")
    return name, payload


def base_commit() -> str:
    name, payload = load_event()
    if name == "pull_request":
        base = (payload.get("pull_request") or {}).get("base", {}).get("sha")
        if not base:
            raise CiError("the pull_request event has no pull_request.base.sha")
        return git("merge-base", commit_or_fail(base), "HEAD").strip()
    if name == "push":
        before = payload.get("before")
        if not before:
            raise CiError("the push event has no before")
        if before == ZERO_SHA:
            raise CiError("the push has no earlier commit to start from")
        return commit_or_fail(before)
    raise CiError(f"unsupported event {name}")


def code_changed(base: str) -> bool:
    name, _ = load_event()
    if name != "pull_request":
        return True
    out = git("diff", "--name-only", "--no-renames", commit_or_fail(base), "HEAD", "--")
    paths = [line for line in out.splitlines() if line]
    if not paths:
        return True
    return any(not p.startswith("docs/") or p in DOCS_READ_BY_TESTS for p in paths)


def fence_for(text: str) -> str:
    """A backtick fence longer than any run of backticks in the text, so it can't close early."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def python_tool(script: Path, *args: str, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(script), *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


def prose_report(label: str, text: str) -> str:
    out = python_tool(PROSE_CHECK, "-", stdin=text).stdout
    fence = fence_for(out)
    return f"#### {label}\n\n{fence}text\n{out.rstrip()}\n{fence}\n"


def check_commits(base: str) -> int:
    name, payload = load_event()
    if name != "pull_request":
        raise CiError("commits runs only on a pull_request event")
    shas = git("rev-list", "--reverse", f"{commit_or_fail(base)}..HEAD").split()
    failed: list[str] = []
    reports: list[str] = []
    for sha in shas:
        message = git("log", "-1", "--format=%B", sha)
        subject = message.split("\n", 1)[0]
        if python_tool(LIFECYCLE, "commit-check", "--message", "-", stdin=message).returncode:
            failed.append(f"{sha[:7]} {subject}")
        reports.append(prose_report(f"Commit {sha[:7]}", message))
    pr = payload.get("pull_request") or {}
    reports.append(prose_report("PR title", pr.get("title") or ""))
    reports.append(prose_report("PR body", pr.get("body") or ""))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Prose findings (report-only, from prose_check)\n\n")
            handle.write("\n".join(reports))
    for line in failed:
        print(line)
    return 1 if failed else 0


def check_specs(root: Path) -> int:
    specs = sorted([*root.glob("config/SPEC.md"), *root.glob("config/skills/*/SPEC.md")])
    if not specs:
        print(f"no spec found under {root}/config", file=sys.stderr)
        return 1
    failed = []
    for spec in specs:
        rel = spec.relative_to(root).as_posix()
        result = python_tool(VALIDATOR, "--file", str(spec), "--strict", stdin="")
        score = next(
            (line for line in result.stdout.splitlines() if "Completeness Score" in line),
            "no score line",
        )
        print(f"{rel}: {score.strip()}")
        # The validator exits 1 for warnings alone, such as the HTTP-endpoint warning every
        # spec here carries; 2 means a score under 80 with --strict.
        if result.returncode not in (0, 1):
            failed.append(rel)
    for rel in failed:
        print(f"spec check failed: {rel}", file=sys.stderr)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ci.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("base")
    for name in ("changes", "commits"):
        sub.add_parser(name).add_argument("--base", required=True)
    sub.add_parser("specs").add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "base":
            print(base_commit())
            return 0
        if args.command == "changes":
            print(f"code={'true' if code_changed(args.base) else 'false'}")
            return 0
        if args.command == "commits":
            return check_commits(args.base)
        return check_specs(args.root)
    except CiError as exc:
        print(f"ci.py {args.command}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
