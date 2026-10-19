#!/usr/bin/env python3
"""Development-only trigger eval for github-issue-lifecycle (never run by the skill).

The skill itself is copied into an isolated CLAUDE_CONFIG_DIR built from the repo by
run_behaviour_evals.isolated_config (other repo skills linked in, no global CLAUDE.md, no live
skills or hooks; credentials removed afterwards). Each query in evals/trigger-evals.json runs as
  claude -p "<query>" --output-format stream-json --verbose --max-turns 3 --allowedTools Skill,Read
in a fresh directory outside $HOME: twice, and a third time only when the two disagree. A run is
triggered when the stream holds an assistant tool_use named Skill whose input.skill is
github-issue-lifecycle; a query is triggered when most of its runs were. Runs that end at the
turn limit without loading the skill are counted as capped.
Pass: accuracy >= 90% and no should-not-trigger query triggered.

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
MAX_TURNS = "3"
ACCURACY = 0.9

_spec = importlib.util.spec_from_file_location("harness", EVALS / "run_behaviour_evals.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


def read_run(stdout):
    """(triggered, capped) from one run's stream-json output."""
    triggered = capped = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result" and event.get("subtype") == "error_max_turns":
            capped = True
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if (
                block.get("type") == "tool_use"
                and block.get("name") == "Skill"
                and block.get("input", {}).get("skill", "") == SKILL_NAME
            ):
                triggered = True
    return triggered, capped and not triggered


def run_once(query, cfg, model):
    with tempfile.TemporaryDirectory(prefix="gil-trigger-", dir=harness.eval_tmp_root()) as cwd:
        cmd = ["claude", "-p", query, "--output-format", "stream-json", "--verbose",
               "--max-turns", MAX_TURNS, "--allowedTools", "Skill,Read"]  # fmt: skip
        if model:
            cmd += ["--model", model]
        proc = subprocess.run(
            cmd, cwd=cwd, env=harness.base_env(cfg), capture_output=True, text=True, timeout=300
        )
        return read_run(proc.stdout)


def decide(run):
    """Two runs, and a third only when they disagree; the majority decides."""
    outcomes = [run(), run()]
    if outcomes[0][0] != outcomes[1][0]:
        outcomes.append(run())
    triggers = sum(t for t, _ in outcomes)
    return {
        "runs": len(outcomes),
        "triggers": triggers,
        "capped": sum(c for _, c in outcomes),
        "triggered": triggers * 2 > len(outcomes),
    }


def summarise(queries, decisions):
    results = []
    for q, d in zip(queries, decisions, strict=True):
        ok = d["triggered"] == q["should_trigger"]
        results.append({**q, **{k: d[k] for k in ("runs", "triggers", "capped")}, "pass": ok})
    near_miss = [r["query"] for r in results if not r["should_trigger"] and not r["pass"]]
    accuracy = sum(r["pass"] for r in results) / len(results)
    summary = {
        "accuracy": round(accuracy, 3),
        "passed": sum(r["pass"] for r in results),
        "total": len(results),
        "near_misses_triggering": near_miss,
        "ok": accuracy >= ACCURACY and not near_miss,
    }
    return {"summary": summary, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--model", help="model for claude -p (default: the user's configured model)"
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", default="trigger-results.json")
    args = parser.parse_args()
    queries = json.loads((EVALS / "trigger-evals.json").read_text())
    with tempfile.TemporaryDirectory(prefix="gil-config-", dir=harness.eval_tmp_root()) as root:
        with harness.isolated_config(root) as cfg:
            canary = harness.probe(cfg, root, args.model)
            if not canary["ok"]:
                print(f"isolation probe failed: {canary}", file=sys.stderr)
                Path(args.out).write_text(json.dumps({"probe": canary, "results": []}, indent=1))
                return 2
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                decisions = list(
                    pool.map(
                        lambda q: decide(lambda: run_once(q["query"], cfg, args.model)), queries
                    )  # fmt: skip
                )
    out = summarise(queries, decisions)
    out["summary"] = {"model": args.model or "default", "probe": canary, **out["summary"]}
    Path(args.out).write_text(json.dumps(out, indent=1))
    for r in out["results"]:
        mark = "OK " if r["pass"] else "BAD"
        print(f"{mark} {str(r['should_trigger']):5} {r['triggers']}/{r['runs']}  {r['query'][:70]}")
    print(json.dumps(out["summary"]))
    return 0 if out["summary"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
