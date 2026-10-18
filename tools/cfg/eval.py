"""Run the eval suites a branch needs and write their records: the interim `cfg eval`.

    python3 -I tools/cfg/eval.py [--suite <name>[,<name>...]] [--base origin/main]
                                 [--model <id>] [--workers N]

Model evals run only on the owner's machine (ADR-0005). This refuses a dirty
working tree, works out from eval/gates.json which suites the branch needs
(or runs the ones named), runs each against trees extracted from the HEAD
commit and the merge-base, writes eval/records/<suite>.json, and prints a
markdown summary for the PR body. It never commits. Raw answers go to
eval/out/<suite>/, which git ignores.

Exit codes: 0 every suite passed or none was needed; 1 a suite failed, an
isolation probe failed, or HEAD moved during a run (no record is written for
that suite); 2 refused before or during a run; 130 interrupted.

The `cfg` command arrives with #6; until then this file is its eval subcommand.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Run as a script with python3 -I, the repo root isn't on sys.path. Tests import the
# module as a package, so this line only runs in a subprocess, where coverage isn't measured.
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import doc_evals, hook_harness, run_eval, skill_evals  # noqa: E402
from tools.cfg import eval_records as er  # noqa: E402

SuiteRunner = Callable[[run_eval.Context, er.Suite], dict[str, Any]]
SUMMARY_KEYS = (
    "regressions",
    "newRuleFailures",
    "scores",
    "p95Ms",
    "recall",
    "falseTriggers",
    "accuracy",
    "rerunsUsed",
)


def claude_version() -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["claude", "--version"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError:
        return "unknown"
    return out.stdout.strip() or "unknown"


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def script_cases(path: Path) -> list[dict[str, str]]:
    """Per-case results from a skill eval script's output file; empty if it doesn't parse."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    entries = data.get("results") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    return [
        {
            "id": str(e.get("id", e.get("query", "")))[:80],
            "result": "pass" if e.get("pass", e.get("passed")) else "fail",
        }
        for e in entries
        if isinstance(e, dict)
    ]


def run_script_suite(ctx: run_eval.Context, suite: er.Suite) -> dict[str, Any]:
    """Run a skill's own eval script from the HEAD tree; exit 0 passes."""
    if suite.script is None:  # pragma: no cover  (parse_gates refuses this; for the type checker)
        raise run_eval.Refusal("a script suite needs a script")
    ctx.out.mkdir(parents=True, exist_ok=True)
    results = ctx.out / "results.json"
    cmd = [sys.executable, "-I", str(ctx.head_tree / suite.script), "--out", str(results)]
    if ctx.model:
        cmd += ["--model", ctx.model]
    # The script is the skill's committed eval runner, taken from the HEAD commit.
    out = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        cwd=ctx.root,
        env={**os.environ, "TMPDIR": str(ctx.root)},
        check=False,
    )
    (ctx.out / "stdout.txt").write_text(out.stdout + out.stderr)
    return {"pass": out.returncode == 0, "exitCode": out.returncode, "cases": script_cases(results)}


RUNNERS: dict[str, SuiteRunner] = {
    "scenario": lambda ctx, suite: run_eval.run_scenario(ctx),
    "rubric": lambda ctx, suite: run_eval.run_rubric(ctx),
    "hook-overlay": lambda ctx, suite: hook_harness.run_overlay(ctx),
    "hook-merge": lambda ctx, suite: hook_harness.run_merge(ctx),
    "script": run_script_suite,
    "trigger": skill_evals.run_trigger,
    "cold-reader": skill_evals.run_cold_reader,
    "doc-trigger": doc_evals.run_doc_trigger,
    "doc-reader": doc_evals.run_doc_reader,
}


def extract(repo: Path, commit: str, dest: Path, paths: list[str]) -> bool:
    """Extract `paths` from `commit` into `dest`; False if none of them exist there."""
    top = set(er.git(repo, "ls-tree", "--name-only", commit).split())
    present = [p for p in paths if p in top]
    if not present:
        return False
    tar = subprocess.run(  # noqa: S603
        ["git", "archive", "--format=tar", commit, "--", *present],  # noqa: S607
        cwd=repo,
        capture_output=True,
        check=True,
    )
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as archive:
        archive.extractall(dest, filter="data")
    return True


def is_dirty(repo: Path) -> bool:
    return bool(er.git(repo, "status", "--porcelain").strip())


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def summary(name: str, result: dict[str, Any]) -> str:
    """A markdown section for the PR body: the verdict, key figures and the per-case table."""
    lines = [f"### {name}: {'pass' if result.get('pass') else 'FAIL'}", ""]
    for key in SUMMARY_KEYS:
        if key in result:
            value = result[key]
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value) or "none"
            lines.append(f"- {key}: {value}")
    cases = result.get("cases") or []
    if cases and "old" in cases[0]:
        lines += ["", "| Case | Old | New | Grader's reason |", "|---|---|---|---|"]
        lines += [
            f"| {c['id']} | {c['old']} | {c['new']} | {_cell(c.get('graderReason', ''))} |"
            for c in cases
        ]
    elif cases:
        lines += ["", "| Case | Result |", "|---|---|"]
        lines += [f"| {_cell(c['id'])} | {c['result']} |" for c in cases]
    return "\n".join(lines) + "\n"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--suite", help="comma-separated suites to run instead of the required ones")
    ap.add_argument("--base", default="origin/main", help="branch to compare against")
    ap.add_argument("--model", help="model for the scenario arms (the grader keeps the default)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-record", action="store_true", help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    runners: dict[str, SuiteRunner] | None = None,
    runner: run_eval.Runner = run_eval.default_runner,
    version: Callable[[], str] = claude_version,
    clock: Callable[[], str] = utc_now,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    args = _parse(argv)
    runners = {**RUNNERS, **(runners or {})}
    try:
        repo = Path(er.git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        if is_dirty(repo):
            raise run_eval.Refusal("the working tree has changes; commit or stash them first")
        head = er.resolve_commit(repo, "HEAD")
        base = er.merge_base(repo, args.base, head)
        suites = er.suite_definitions(repo, base, head)
        names = args.suite.split(",") if args.suite else er.required_suites(repo, base, head)
        unknown = [n for n in names if n not in suites]
        if unknown:
            raise er.GatesError(f"unknown suite: {', '.join(unknown)}")
        if not names:
            print("no eval suites required")
            return 0
        if which("claude") is None:
            raise run_eval.Refusal("claude is not on PATH")
        live = run_eval.live_home()
        if not (live / ".credentials.json").is_file():
            raise run_eval.Refusal(f"no {live / '.credentials.json'}; log in to Claude Code first")
        root = run_eval.temp_root(repo)
    except (run_eval.Refusal, er.GatesError, er.GitError) as e:
        print(f"eval: {e}", file=sys.stderr)
        return 2

    meta = {
        "ranAt": clock(),
        "claudeVersion": version(),
        "models": {"arm": args.model or "default", "grader": "default"},
    }
    trees = Path(tempfile.mkdtemp(prefix="eval-trees.", dir=root))
    sections, code = [], 0
    try:
        extract(repo, head, trees / "head", ["config", "eval"])
        has_base = extract(repo, base, trees / "base", ["config"])
        for name in names:
            suite = suites[name]
            ctx = run_eval.Context(
                live=live,
                root=root,
                runner=runner,
                model=args.model,
                workers=args.workers,
                out=repo / "eval" / "out" / name,
                head_tree=trees / "head",
                base_tree=trees / "base" if has_base else None,
            )
            try:
                result = runners[suite.runner](ctx, suite)
            except run_eval.IsolationError as e:
                sections.append(f"### {name}: isolation failed, no record\n\n{e}\n")
                code = max(code, 1)
                continue
            if er.resolve_commit(repo, "HEAD") != head:
                sections.append(f"### {name}: HEAD moved during the run, no record written\n")
                code = max(code, 1)
                continue
            if not args.no_record:
                record = er.build_record(repo, name, suite, head, base, result, meta)
                path = repo / er.RECORDS_DIR / f"{name}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(er.dump_record(record))
            sections.append(summary(name, result))
            if not result.get("pass"):
                code = max(code, 1)
    except run_eval.Refusal as e:
        print(f"eval: {e}", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        print("eval: interrupted; no record was written for the suite that was running")
        code = 130
    finally:
        shutil.rmtree(trees, ignore_errors=True)
    print("\n".join(sections), end="")
    return code


if __name__ == "__main__":
    sys.exit(main())
