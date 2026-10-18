"""Model evals for a skill that works with companion skills, such as writing-docs with plain-prose.

tools/cfg/eval.py runs these for the suites in eval/gates.json whose runner is
`doc-trigger` or `doc-reader`. A suite names the skill, its case file and its
`companions`, the skills it runs beside once deployed (ADR-0005, ADR-0010).

They reuse eval/skill_evals.py's isolation and grading, and keep that module
unchanged, so the records of the suites that use it stay valid. The difference
is what each config directory holds: the skill and its companions together, or
the companions alone for the baseline. Each run's init event must list exactly
the skills installed for it, each once; otherwise the suite stops with an
IsolationError and no record is written.

doc-trigger: like `trigger`, three runs per query with an empty CLAUDE.md, but
with the companions installed too, so the skill competes with them for every
query as it will after deploying. Only a load of the skill under test counts; a
run that loads only a companion is a miss. The pass rule is the same per-class
rule: at least 85% of the queries that should trigger do, no near miss triggers
or errors, and every delegated query triggers.

doc-reader: both arms write, or rewrite the same draft. The skill arm has the
skill and its companions and is told to load the skill; the baseline has only
the companions and is told to load them. A reply wrapped whole in one markdown
fence is unwrapped first. Each text is graded by a fresh reader and a judge, as
in `cold-reader`, and linted with the HEAD commit's doc_lint.py. The skill arm
must get at least as many answers right, lose nothing, be flagged as AI-sounding
no more often, have no more structure and link findings, and have loaded the
skill and every companion. A failing case is rerun once and the rerun counted.
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


def score_trigger(cases: list[Case], outcomes: list[tuple[int, str]]) -> dict[str, Any]:
    """The per-class verdict: recall over the should-trigger queries, and no near miss."""
    results = []
    for index, case in enumerate(cases):
        runs = [o for i, o in outcomes if i == index]
        triggered = runs.count("triggered") >= se.TRIGGER_NEEDED
        errors = runs.count("error")
        entry = {
            "id": case["query"][:60],
            "result": "pass" if triggered == case["should_trigger"] and not errors else "fail",
            "triggered": runs.count("triggered"),
            "shouldTrigger": case["should_trigger"],
            "delegated": bool(case.get("delegated")),
        }
        if errors:
            entry["errors"] = errors
        results.append(entry)
    positive = [r for r in results if r["shouldTrigger"]]
    recall = (
        round(sum(r["result"] == "pass" for r in positive) / len(positive), 3) if positive else 1.0
    )
    near_misses_clean = all(r["result"] == "pass" for r in results if not r["shouldTrigger"])
    delegated_ok = all(r["result"] == "pass" for r in results if r["delegated"])
    return {
        "pass": recall >= se.TRIGGER_RECALL and near_misses_clean and delegated_ok,
        "recall": recall,
        "falseTriggers": sum(
            1 for r in results if not r["shouldTrigger"] and r["triggered"] >= se.TRIGGER_NEEDED
        ),
        "accuracy": round(sum(r["result"] == "pass" for r in results) / len(results), 3)
        if results
        else 0.0,
        "cases": results,
    }


def run_doc_trigger(ctx: run_eval.Context, suite: Any) -> dict[str, Any]:
    skill, companions, skills_dir, cases = _setup(ctx, suite)
    installed = [skills_dir / name for name in [skill, *companions]]
    args = ["--max-turns", se.TRIGGER_MAX_TURNS, "--tools", se.TRIGGER_TOOLS,
            "--allowedTools", se.TRIGGER_TOOLS, "--permission-prompts", "none"]  # fmt: skip
    with se.sandbox(ctx) as box, config_with(ctx, "trigger", installed) as cfg:

        def one(job: tuple[int, int]) -> tuple[int, str]:
            index, _ = job
            case = cases[index]
            evs = checked_run(ctx, cfg, args, case["query"], box, [skill, *companions], [])
            if evs is None:
                return index, "error"
            calls = se.skill_calls(evs, skill)
            if case.get("delegated"):
                calls = [c for c in calls if c is not None]
            return index, "triggered" if calls else "quiet"

        jobs = [(i, a) for i in range(len(cases)) for a in range(se.TRIGGER_RUNS)]
        outcomes = sorted(se.run_all(ctx, one, jobs))
    result = score_trigger(cases, outcomes)
    se._write_out(ctx, "trigger-results.json", result["cases"])
    return result


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


def doc_verdict(skill_arm: dict[str, Any], base: dict[str, Any], missing: list[str]) -> str:
    """Why the case fails, or '' when it passes."""
    for arm in (skill_arm, base):
        if "error" in arm:
            return str(arm["error"])
    if missing:
        return f"the skill arm never loaded {', '.join(missing)}"
    if skill_arm["correct"] < base["correct"]:
        return f"fewer answers right ({skill_arm['correct']} vs {base['correct']})"
    if skill_arm["lost"]:
        return f"lost or distorted: {'; '.join(skill_arm['lost'])}"
    if skill_arm["aiSounding"] and not base["aiSounding"]:
        return f"flagged as AI-sounding when the baseline wasn't: {skill_arm['quote']}"
    if skill_arm["lint"] > base["lint"]:
        return f"more lint findings than the baseline ({skill_arm['lint']} vs {base['lint']})"
    return ""


def run_doc_reader(ctx: run_eval.Context, suite: Any) -> dict[str, Any]:
    skill, companions, skills_dir, cases = _setup(ctx, suite)
    with (
        se.sandbox(ctx) as box,
        config_with(ctx, "skill", [skills_dir / n for n in [skill, *companions]]) as skill_cfg,
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
        arms = se.Arms(skill, skill_cfg, base_cfg, reader_cfg, box, skill_args)
        everyone = [skill, *companions]

        def write(cfg: Path, args: list[str], case: Case, load: str, present: list[str]) -> tuple[
            str, list[se.Event]
        ]:  # fmt: skip
            absent = [s for s in everyone if s not in present]
            evs = checked_run(ctx, cfg, args, se.writer_prompt(case, load), box, present, absent)
            return unwrap(se.result_text(evs or [])), evs or []

        def graded(case: Case, text: str) -> dict[str, Any]:
            report = se.grade(ctx, arms, case, text)
            if "error" not in report:
                report["lint"] = lint_count(ctx, skill, text)
            return report

        def attempt(case: Case) -> dict[str, Any]:
            skill_text, evs = write(skill_cfg, skill_args, case, skill, everyone)
            base_text, _ = write(base_cfg, base_args, case, " and ".join(companions), companions)
            missing = [s for s in everyone if not se.skill_calls(evs, s)]
            skill_arm, base = graded(case, skill_text), graded(case, base_text)
            reason = doc_verdict(skill_arm, base, missing)
            return {"id": case["id"], "result": "fail" if reason else "pass", "reason": reason,
                    "skill": skill_arm, "baseline": base,
                    "texts": {"skill": skill_text, "baseline": base_text}}  # fmt: skip

        def one(case: Case) -> tuple[dict[str, Any], int]:
            first = attempt(case)
            if first["result"] == "pass":
                return first, 0
            return attempt(case), 1

        done = se.run_all(ctx, one, cases)
    results = sorted((r for r, _ in done), key=lambda r: str(r["id"]))
    se._write_out(ctx, "doc-reader-results.json", results)
    for r in results:
        del r["texts"]  # the full texts stay in eval/out; the record keeps the figures
    return {
        "pass": all(r["result"] == "pass" for r in results),
        "rerunsUsed": sum(used for _, used in done),
        "cases": results,
    }
