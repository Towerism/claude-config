#!/usr/bin/env python3
"""Development-only trigger eval for github-issue-lifecycle (AC-16; never run by the skill).

The staged skill itself is copied into an isolated CLAUDE_CONFIG_DIR (no global CLAUDE.md, no
issue-spec-driven-workflow; credentials removed afterwards) and each query in
evals/trigger-evals.json runs 3 times as
  claude -p "<query>" --output-format stream-json --verbose --max-turns 3 --allowedTools Skill,Read
in a fresh directory outside $HOME. A run is triggered when the stream holds an assistant tool_use
named Skill whose input.skill is github-issue-lifecycle; a query is triggered on 2+ of 3 runs.
Pass: accuracy >= 90% and no should-not-trigger query triggers on 2+ runs.

Usage: python3 -I evals/run_trigger_evals.py [--model M] [--workers 6] [--out trigger-results.json]
"""

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EVALS = Path(__file__).resolve().parent
SKILL_NAME = "github-issue-lifecycle"
RUNS = 3

_spec = importlib.util.spec_from_file_location("harness", EVALS / "run_behaviour_evals.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


def triggered(stdout):
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if (
                block.get("type") == "tool_use"
                and block.get("name") == "Skill"
                and block.get("input", {}).get("skill", "") == SKILL_NAME
            ):
                return True
    return False


def run_once(query, cfg, model):
    with tempfile.TemporaryDirectory(prefix="gil-trigger-", dir=harness.eval_tmp_root()) as cwd:
        cmd = [
            "claude",
            "-p",
            query,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "3",
            "--allowedTools",
            "Skill,Read",
        ]
        if model:
            cmd += ["--model", model]
        proc = subprocess.run(
            cmd, cwd=cwd, env=harness.base_env(cfg), capture_output=True, text=True, timeout=300
        )
        return triggered(proc.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--model", help="model for claude -p (default: the user's configured model)"
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", default="trigger-results.json")
    args = parser.parse_args()
    queries = json.loads((EVALS / "trigger-evals.json").read_text())
    jobs = [(i, q["query"]) for i, q in enumerate(queries) for _ in range(RUNS)]
    with tempfile.TemporaryDirectory(prefix="gil-config-", dir=harness.eval_tmp_root()) as root:
        with harness.isolated_config(root) as cfg:
            canary = harness.probe(cfg, root, args.model)
            if not canary["ok"]:
                print(f"isolation probe failed: {canary}", file=sys.stderr)
                return 2
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                hits = list(pool.map(lambda job: run_once(job[1], cfg, args.model), jobs))
    results = []
    for i, q in enumerate(queries):
        count = sum(hit for (idx, _), hit in zip(jobs, hits, strict=True) if idx == i)
        ok = count >= 2 if q["should_trigger"] else count < 2
        results.append({**q, "triggers": count, "runs": RUNS, "pass": ok})
    near_miss = [r["query"] for r in results if not r["should_trigger"] and r["triggers"] >= 2]
    accuracy = sum(r["pass"] for r in results) / len(results)
    summary = {
        "model": args.model or "default",
        "probe": canary,
        "accuracy": round(accuracy, 3),
        "passed": sum(r["pass"] for r in results),
        "total": len(results),
        "near_misses_triggering": near_miss,
        "ok": accuracy >= 0.9 and not near_miss,
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    for r in results:
        mark = "OK " if r["pass"] else "BAD"
        print(f"{mark} {str(r['should_trigger']):5} {r['triggers']}/{RUNS}  {r['query'][:70]}")
    print(json.dumps(summary))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
