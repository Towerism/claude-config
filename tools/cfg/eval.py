"""Run the eval suites a branch needs and write their records: `cfg eval`.

    python3 -I tools/cfg/eval.py [--suite <name>[,<name>...]] [--base origin/main]
                                 [--model <id>] [--workers N] [--tier auto|full]

Model evals run only on the owner's machine (ADR-0005). This refuses a dirty
working tree, works out from eval/gates.json which suites the branch needs
(or runs the ones named), runs each against trees extracted from the HEAD
commit and the merge-base, writes eval/records/<suite>.json, and prints a
markdown summary for the PR body. It never commits. Raw answers go to
eval/out/<suite>/, which git ignores.

Each suite runs in the tier the diff needs (#77): the smoke tier, which covers
a few named cases once each, when nothing that could move its result changed,
and the full tier otherwise; --tier full forces the full tier. All suites run
at the same time, and --workers caps the claude calls open at once across all
of them. The summary gives each suite's time and the run's total against its
budget, 60 s for smoke and 180 s for full; a run over budget is reported and
still passes or fails on its results.

Exit codes: 0 every suite passed or none was needed; 1 a suite failed, an
isolation probe failed, or HEAD moved during a run (no record is written for
that suite); 2 refused before or during a run; 130 interrupted.

`python3 -I tools/cfg/cli.py eval` passes its arguments to this file unchanged.
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
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Run as a script with python3 -I, the repo root isn't on sys.path. Tests import the
# module as a package, so this line only runs in a subprocess, where coverage isn't measured.
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import doc_evals, hook_harness, run_eval, session_harness, skill_evals  # noqa: E402
from tools.cfg import eval_records as er  # noqa: E402

SuiteRunner = Callable[[run_eval.Context, er.Suite], dict[str, Any]]
# The owner's limits for a whole run (#77), in seconds of wall time. A run over its
# budget is reported, and passes or fails on its results alone.
SMOKE_BUDGET = 60
FULL_BUDGET = 180
# A script suite takes this many permits from the shared limit, or fewer when the
# limit is lower: the lower of the lifecycle runners' own defaults.
SCRIPT_WORKERS = 4
SUMMARY_KEYS = (
    "regressions",
    "newRuleFailures",
    "scores",
    "p95Ms",
    "recall",
    "falseTriggers",
    "accuracy",
    "delegatedRate",
    "runs",
    "turnLimitRuns",
    "rightShare",
    "lint",
    "reasons",
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
    workers = min(SCRIPT_WORKERS, run_eval.call_limit())
    cmd = [sys.executable, "-I", str(ctx.head_tree / suite.script), "--out", str(results)]
    cmd += ["--workers", str(workers)]
    if ctx.model:
        cmd += ["--model", ctx.model]
    # The script is the skill's committed eval runner, taken from the HEAD commit. It
    # holds its share of the shared limit on claude calls while it runs.
    with run_eval.permits(workers):
        if run_eval.stopped():  # interrupted while waiting for its permits
            return {"pass": False, "exitCode": -1, "cases": []}
        env = {**os.environ, "TMPDIR": str(ctx.root)}
        code, out, err = run_eval.run_tracked(cmd, "", env, ctx.root, None)
    (ctx.out / "stdout.txt").write_text(out + err)
    return {"pass": code == 0, "exitCode": code, "cases": script_cases(results)}


RUNNERS: dict[str, SuiteRunner] = {
    "scenario": lambda ctx, suite: run_eval.run_scenario(ctx),
    "rubric": lambda ctx, suite: run_eval.run_rubric(ctx),
    "hook-overlay": lambda ctx, suite: hook_harness.run_overlay(ctx),
    "hook-merge": lambda ctx, suite: hook_harness.run_merge(ctx),
    "hook-session-start": lambda ctx, suite: session_harness.run_session(ctx),
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


def summary(name: str, result: dict[str, Any], label: str = "") -> str:
    """A markdown section for the PR body: the verdict, key figures and the per-case table."""
    verdict = "pass" if result.get("pass") else "FAIL"
    lines = [f"### {name}: {verdict}" + (f" ({label})" if label else ""), ""]
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
    elif cases and all("result" in c for c in cases):
        # A suite that decides on totals, such as doc-reader, has no per-case result.
        lines += ["", "| Case | Result |", "|---|---|"]
        lines += [f"| {_cell(c['id'])} | {c['result']} |" for c in cases]
    return "\n".join(lines) + "\n"


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--suite", help="comma-separated suites to run instead of the required ones")
    ap.add_argument("--base", default="origin/main", help="branch to compare against")
    ap.add_argument(
        "--model",
        help="model for the arms under test; graders, judges and readers use the model "
        "eval/criteria.json names",
    )
    ap.add_argument(
        "--workers",
        type=_positive,
        default=run_eval.DEFAULT_CALLS,
        help="most claude calls open at once, across all suites "
        f"(default {run_eval.DEFAULT_CALLS})",
    )
    ap.add_argument(
        "--tier",
        choices=("auto", "full"),
        default="auto",
        help="auto: the tier the diff needs for each suite; full: the full tier for every suite",
    )
    ap.add_argument("--no-record", action="store_true", help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def smoke_problems(repo: Path, head: str, name: str, suite: er.Suite) -> list[str]:
    """Smoke cases that aren't in the suite's case file at HEAD, each named."""
    path = suite.cases or ("eval/scenarios.json" if suite.runner == "scenario" else None)
    blob = er.read_blob(repo, head, path) if path and suite.smoke else None
    try:
        cases = json.loads(blob) if blob is not None else None
    except ValueError:
        cases = None
    if not isinstance(cases, list):
        return [f"{name}: no case file to take its smoke cases from"] if suite.smoke else []
    ids = {str(c.get("id")) for c in cases if isinstance(c, dict)}
    return [
        f"{name}: smoke case {entry!r} is not in {path}"
        for entry in suite.smoke
        if (entry >= len(cases) if isinstance(entry, int) else entry not in ids)
    ]


def grader_model(repo: Path, head: str) -> str:
    """The grader model HEAD's eval/criteria.json names, or the harness copy's."""
    blob = er.read_blob(repo, head, run_eval.CRITERIA)
    text = (
        blob.decode()
        if blob is not None
        else (Path(run_eval.__file__).parent / "criteria.json").read_text()
    )
    return str(json.loads(text)["graderModel"])


def tier_label(tier: str, seconds: float) -> str:
    return f"{tier}, {seconds:.1f} s"


def main(
    argv: list[str] | None = None,
    *,
    runners: dict[str, SuiteRunner] | None = None,
    runner: run_eval.Runner = run_eval.default_runner,
    version: Callable[[], str] = claude_version,
    clock: Callable[[], str] = utc_now,
    which: Callable[[str], str | None] = shutil.which,
    timer: Callable[[], float] = time.monotonic,
) -> int:
    started = timer()
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
        tiers = {
            n: "full" if args.tier == "full" else er.needed_tier(repo, n, base, head)[0]
            for n in names
        }
        smoke = [n for n in names if tiers[n] == "smoke"]
        bad = [p for n in smoke for p in smoke_problems(repo, head, n, suites[n])]
        if bad:
            raise run_eval.Refusal("; ".join(bad))
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
        "models": {"arm": args.model or "default", "grader": grader_model(repo, head)},
    }
    run_eval.set_call_limit(args.workers)
    # Cleared here only: after an interrupt it stays set, so no queued call starts.
    run_eval.reset_stop()
    trees = Path(tempfile.mkdtemp(prefix="eval-trees.", dir=root))
    sections: dict[str, str] = {}
    code = 0
    try:
        extract(repo, head, trees / "head", ["config", "eval"])
        has_base = extract(repo, base, trees / "base", ["config"])

        def one(name: str) -> tuple[dict[str, Any], float, bool]:
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
                tier=tiers[name],
                smoke=suite.smoke,
            )
            begun = timer()
            result = runners[suite.runner](ctx, suite)
            moved = er.resolve_commit(repo, "HEAD") != head
            return result, timer() - begun, moved

        with ThreadPoolExecutor(len(names)) as pool:
            futures = {pool.submit(one, name): name for name in names}
            try:
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        result, seconds, moved = future.result()
                    except run_eval.IsolationError as e:
                        sections[name] = f"### {name}: isolation failed, no record\n\n{e}\n"
                        code = max(code, 1)
                        continue
                    if moved:
                        sections[name] = f"### {name}: HEAD moved during the run, no record\n"
                        code = max(code, 1)
                        continue
                    if not args.no_record:
                        record = er.build_record(
                            repo, name, suites[name], head, base, result, meta,
                            tier=tiers[name], duration=seconds,
                            smoke_cases=list(suites[name].smoke),
                        )  # fmt: skip
                        path = repo / er.RECORDS_DIR / f"{name}.json"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(er.dump_record(record))
                    sections[name] = summary(name, result, tier_label(tiers[name], seconds))
                    if not result.get("pass"):
                        code = max(code, 1)
            except BaseException:
                run_eval.stop_all()
                for f in futures:
                    f.cancel()
                raise
    except run_eval.Refusal as e:
        print(f"eval: {e}", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        print("eval: interrupted; no record was written for the suites still running")
        code = 130
    finally:
        shutil.rmtree(trees, ignore_errors=True)
    print("\n".join(sections[n] for n in names if n in sections), end="")
    if code in (0, 1):
        print(budget_line(timer() - started, set(tiers.values())))
    return code


def budget_line(seconds: float, tiers: set[str]) -> str:
    """The run's wall time against its tier's budget: smoke when every suite ran smoke."""
    budget = SMOKE_BUDGET if tiers == {"smoke"} else FULL_BUDGET
    over = " over budget" if seconds > budget else ""
    return f"\nelapsed: {seconds:.1f} s (budget {budget} s){over}"


if __name__ == "__main__":
    sys.exit(main())
