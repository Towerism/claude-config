"""Model evals for a skill that works with companion skills, such as writing-docs with plain-prose.

tools/cfg/eval.py runs these for the suites in eval/gates.json whose runner is
`doc-trigger` or `doc-reader`. A suite names the skill, its case file and its
`companions`, the skills it runs beside once deployed (ADR-0005, ADR-0010).

They reuse eval/skill_evals.py's isolation helpers and keep that module unchanged,
so the records of the suites that use it stay valid. Each config directory holds
the skill and its companions together, or the companions alone for the baseline,
and each run's init event must list exactly the skills installed for it, each
once; otherwise the suite stops with an IsolationError and no record is written.

Both runners keep chance out of the verdict without sampling much, because each
model run costs the owner's subscription.

doc-trigger: each query runs twice, with a third run only when the first two
disagree, and triggers when two of its runs load the skill under test. Ordinary
queries stop after two turns, since the load decision comes first; delegated
ones get four, for the Agent call and the subagent's Skill call, and only a load
inside a subagent counts. A run that loads only a companion is a miss, and an
errored run counts against its class. The suite passes when at least 85% of the
should-trigger queries trigger, delegated ones included, and no near miss does.

doc-reader: both arms write, or rewrite the same draft; the skill arm has the
skill and its companions, the baseline only the companions. A reply wrapped
whole in one markdown fence is unwrapped first. Every check is done in code: the
case's mustContain patterns, doc_lint.py's findings, and one fresh reader's
answers matched against each question's expect patterns. The suite decides on
totals with no reruns: every skill-arm run loads every skill and matches every
pattern, the skill arm gets at least 90% of all questions right, and it has no
more lint findings than the baseline.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover  (only when run as a script)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import run_eval  # noqa: E402
from eval import skill_evals as se  # noqa: E402

LINT = "scripts/doc_lint.py"
CHECKER = "scripts/prose_check.py"
OUTER_FENCE = re.compile(r"^(`{3,}|~{3,})\s*(markdown|md)?\s*$")
FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# The load decision comes in the first turn or two; later turns only write text the
# trigger suite doesn't grade. A delegated query needs the Agent call, the
# subagent's turn and its Skill call.
ORDINARY_TURNS = "2"
DELEGATED_TURNS = "4"
FIRST_RUNS = 2
RIGHT_SHARE = 0.9

Case = dict[str, Any]


@contextmanager
def config_with(ctx: run_eval.Context, name: str, skills: list[Path]) -> Iterator[Path]:
    """A private config with an empty CLAUDE.md and exactly `skills` installed."""
    cfg = run_eval.make_config_dir(ctx, name, "")
    try:
        for skill in skills:
            shutil.copytree(skill, cfg / "skills" / skill.name)
        yield cfg
    finally:
        run_eval.remove_config_dir(cfg)


def checked_run(
    ctx: run_eval.Context, cfg: Path, args: list[str], prompt: str, box: se.Sandbox,
    present: list[str], absent: list[str],
) -> list[se.Event] | None:  # fmt: skip
    """One stream-json run whose init event lists each of `present` once, none of `absent`."""
    _, out = run_eval.run_claude(
        ctx, cfg, ["-p", "--output-format", "stream-json", "--verbose", *args], prompt, box.env
    )
    evs = se.events(out)
    listed = se.init_skills(evs)
    if listed is None:
        return None
    for name in present + absent:
        want = 1 if name in present else 0
        if listed.count(name) != want:
            raise run_eval.IsolationError(
                f"{name} is listed {listed.count(name)} times in a run that should "
                f"{'have it once' if want else 'not have it'}"
            )
    return evs


def _setup(ctx: run_eval.Context, suite: Any) -> tuple[str, list[str], Path, list[Case]]:
    skills_dir = ctx.head_tree / "config" / "skills"
    cases = json.loads((ctx.head_tree / suite.cases).read_text())
    return str(suite.skill), list(suite.companions), skills_dir, cases


# doc-trigger


def trigger_run(
    ctx: run_eval.Context, cfg: Path, box: se.Sandbox, case: Case, skill: str,
    everyone: list[str],
) -> tuple[bool, bool, bool]:  # fmt: skip
    """(counts as a load, errored, hit the turn limit before loading anything) for one run."""
    turns = DELEGATED_TURNS if case.get("delegated") else ORDINARY_TURNS
    args = ["--max-turns", turns, "--tools", se.TRIGGER_TOOLS,
            "--allowedTools", se.TRIGGER_TOOLS, "--permission-prompts", "none"]  # fmt: skip
    evs = checked_run(ctx, cfg, args, case["query"], box, everyone, [])
    if evs is None:
        # An errored run counts against its class: a miss, or a load for a near miss.
        return not case["should_trigger"], True, False
    calls = se.skill_calls(evs, skill)
    if case.get("delegated"):
        calls = [c for c in calls if c is not None]
    limited = any(e.get("type") == "result" and e.get("subtype") == "error_max_turns"
                  for e in evs) and not any(se.skill_calls(evs, s) for s in everyone)  # fmt: skip
    return bool(calls), False, limited


def run_doc_trigger(ctx: run_eval.Context, suite: Any) -> dict[str, Any]:
    skill, companions, skills_dir, cases = _setup(ctx, suite)
    everyone = [skill, *companions]
    runs: dict[int, list[tuple[bool, bool, bool]]] = {i: [] for i in range(len(cases))}
    with (
        se.sandbox(ctx) as box,
        config_with(ctx, "trigger", [skills_dir / n for n in everyone]) as cfg,
    ):

        def one(index: int) -> tuple[int, tuple[bool, bool, bool]]:
            return index, trigger_run(ctx, cfg, box, cases[index], skill, everyone)

        first = [i for i in range(len(cases)) for _ in range(FIRST_RUNS)]
        for index, run in se.run_all(ctx, one, first):
            runs[index].append(run)
        split = [i for i, r in runs.items() if r[0][0] != r[1][0]]
        for index, run in se.run_all(ctx, one, split):
            runs[index].append(run)
    results = []
    for index, case in enumerate(cases):
        loads = sum(r[0] for r in runs[index])
        entry: dict[str, Any] = {
            "id": case["query"][:60],
            "result": "pass" if (loads >= 2) == case["should_trigger"] else "fail",
            "triggered": loads,
            "runs": len(runs[index]),
            "shouldTrigger": case["should_trigger"],
            "delegated": bool(case.get("delegated")),
        }
        errors = sum(r[1] for r in runs[index])
        if errors:
            entry["errors"] = errors
        results.append(entry)
    se._write_out(ctx, "trigger-results.json", results)
    positive = [r for r in results if r["shouldTrigger"]]
    recall = (
        round(sum(r["result"] == "pass" for r in positive) / len(positive), 3) if positive else 1.0
    )
    false_triggers = sum(1 for r in results if not r["shouldTrigger"] and r["result"] == "fail")
    delegated = [r for r in results if r["delegated"]]
    delegated_runs = sum(r["runs"] for r in delegated)
    return {
        "pass": recall >= se.TRIGGER_RECALL and false_triggers == 0,
        "recall": recall,
        "falseTriggers": false_triggers,
        "delegatedRate": round(sum(r["triggered"] for r in delegated) / delegated_runs, 3)
        if delegated_runs
        else None,
        "accuracy": round(sum(r["result"] == "pass" for r in results) / len(results), 3)
        if results
        else 0.0,
        "runs": sum(len(r) for r in runs.values()),
        "turnLimitRuns": sum(r[2] for rs in runs.values() for r in rs),
        "cases": results,
    }


# doc-reader


def unwrap(text: str) -> str:
    """The text without one outer markdown fence wrapping all of it; inner fences stay."""
    lines = text.strip().splitlines()
    first = OUTER_FENCE.match(lines[0]) if len(lines) >= 2 else None
    if first is None:
        return text
    fence, label = first.group(1), first.group(2)
    last, inner = lines[-1].strip(), lines[1:-1]
    if set(last) != {fence[0]} or len(last) < len(fence):
        return text
    # A bare fence counts as a wrapper only when nothing inside it looks like a fence,
    # so a document that starts and ends with plain code blocks is left alone.
    if label is None and any(FENCE_LINE.match(line) for line in inner):
        return text
    return "\n".join(inner) + "\n"


def lint_count(ctx: run_eval.Context, skill: str, text: str) -> int:
    """Structure and link findings from the HEAD commit's doc_lint.py, reading `text` on stdin."""
    lint = ctx.head_tree / "config" / "skills" / skill / LINT
    out = subprocess.run(  # noqa: S603  (the committed lint, run on a local string)
        [sys.executable, "-I", str(lint), "-", "--json"],
        input=text, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    counts = json.loads(out.stdout)["counts"]
    return int(counts["structure"]) + int(counts["links"])


def reader_prompt(case: Case, text: str) -> str:
    questions = "\n".join(f"{i}. {q}" for i, q in enumerate(case["questions"], 1))
    return (
        "You are a reader with no context: you have only the text below. Answer each "
        'question using only the text, and write "not stated" when the text doesn\'t say. '
        'Respond with ONLY a JSON object: {"answers": [one string per question]}.\n\n'
        f"TEXT:\n{text}\n\nQUESTIONS:\n{questions}\n"
    )


def parse_answers(raw: str, questions: int) -> list[str] | None:
    data = se.first_json(raw)
    answers = data.get("answers") if isinstance(data, dict) else None
    if not (isinstance(answers, list) and len(answers) == questions):
        return None
    return [str(a) for a in answers]


def grade_answers(case: Case, answers: list[str] | None) -> list[bool]:
    """Whether each answer matches all of its question's expect patterns; all wrong if None."""
    if answers is None:
        return [False] * len(case["questions"])
    return [
        all(re.search(p, answer, re.IGNORECASE) for p in patterns)
        for answer, patterns in zip(answers, case["expect"], strict=True)
    ]


def missing_patterns(case: Case, text: str) -> list[str]:
    return [p for p in case["mustContain"] if not re.search(p, text, re.IGNORECASE)]


def run_doc_reader(ctx: run_eval.Context, suite: Any) -> dict[str, Any]:
    skill, companions, skills_dir, cases = _setup(ctx, suite)
    everyone = [skill, *companions]
    with (
        se.sandbox(ctx) as box,
        config_with(ctx, "skill", [skills_dir / n for n in everyone]) as skill_cfg,
        config_with(ctx, "baseline", [skills_dir / n for n in companions]) as base_cfg,
        config_with(ctx, "reader", []) as reader_cfg,
    ):

        def write_args(cfg: Path, scripts: list[str]) -> list[str]:
            allowed = [f"Bash(python3 -I {cfg / 'skills' / s} *)" for s in scripts]
            return ["--tools", se.WRITE_TOOLS, "--allowedTools", *allowed,
                    "--permission-prompts", "none"]  # fmt: skip

        skill_args = write_args(skill_cfg, [f"{skill}/{LINT}"]
                                + [f"{c}/{CHECKER}" for c in companions])  # fmt: skip
        base_args = write_args(base_cfg, [f"{c}/{CHECKER}" for c in companions])

        def write(cfg: Path, args: list[str], case: Case, load: str, present: list[str]) -> tuple[
            str, list[se.Event] | None
        ]:  # fmt: skip
            absent = [s for s in everyone if s not in present]
            evs = checked_run(ctx, cfg, args, se.writer_prompt(case, load), box, present, absent)
            return unwrap(se.result_text(evs or [])), evs

        def graded(case: Case, text: str) -> dict[str, Any]:
            evs = checked_run(ctx, reader_cfg, ["--tools", ""], reader_prompt(case, text), box,
                              [], everyone)  # fmt: skip
            answers = parse_answers(se.result_text(evs or []), len(case["questions"]))
            return {
                "right": grade_answers(case, answers),
                "unparsable": answers is None,
                "lint": lint_count(ctx, skill, text),
                "missing": missing_patterns(case, text),
                "diagnostics": se.diagnostics(ctx, text),
            }

        def one(case: Case) -> dict[str, Any]:
            skill_text, evs = write(skill_cfg, skill_args, case, skill, everyone)
            base_text, _ = write(base_cfg, base_args, case, " and ".join(companions), companions)
            return {"id": case["id"], "skill": graded(case, skill_text),
                    "baseline": graded(case, base_text),
                    "writerError": evs is None,
                    "notLoaded": [s for s in everyone if not se.skill_calls(evs or [], s)],
                    "texts": {"skill": skill_text, "baseline": base_text}}  # fmt: skip

        results = sorted(se.run_all(ctx, one, cases), key=lambda r: str(r["id"]))
    se._write_out(ctx, "doc-reader-results.json", results)
    reasons = []
    for r in results:
        del r["texts"]  # the full texts stay in eval/out; the record keeps the figures
        if r["writerError"]:
            reasons.append(f"{r['id']}: the skill arm's writing run errored")
        elif r["notLoaded"]:
            reasons.append(f"{r['id']}: the skill arm never loaded {', '.join(r['notLoaded'])}")
        if r["skill"]["missing"]:
            reasons.append(
                f"{r['id']}: the skill arm's text is missing {', '.join(r['skill']['missing'])}"
            )
    answers = [a for r in results for a in r["skill"]["right"]]
    right_share = round(sum(answers) / len(answers), 3) if answers else 0.0
    if right_share < RIGHT_SHARE:
        reasons.append(f"right answers {right_share:.0%} of all questions, under {RIGHT_SHARE:.0%}")
    lint = {arm: sum(r[arm]["lint"] for r in results) for arm in ("skill", "baseline")}
    if lint["skill"] > lint["baseline"]:
        reasons.append(
            f"more lint findings than the baseline ({lint['skill']} vs {lint['baseline']})"
        )
    return {
        "pass": not reasons,
        "reasons": reasons,
        "rightShare": right_share,
        "lint": lint,
        "unparsable": [
            f"{r['id']}/{arm}"
            for r in results
            for arm in ("skill", "baseline")
            if r[arm]["unparsable"]
        ],  # fmt: skip
        "cases": results,
    }
