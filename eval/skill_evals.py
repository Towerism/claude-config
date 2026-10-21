"""Model evals for one skill: does its description trigger it, and does it help a cold reader?

tools/cfg/eval.py runs these for the suites in eval/gates.json whose runner is
`trigger` or `cold-reader`; the suite names the skill and its case file, so any
skill can use them (ADR-0005, ADR-0010).

Every run gets the isolation eval/run_eval.py gives the scenario suite (a private
CLAUDE_CONFIG_DIR outside $HOME, the live settings without hooks, a private
credentials copy) plus three limits. For a trigger run, `skills/` holds a copy of
every skill in the HEAD commit's config/skills/ and config/vendored-skills/, so
the session lists what a deployed session lists (#62, #75); for a cold-reader
run, only the HEAD commit's copy of the skill under test, or nothing for a
baseline or a grader. The tools are set with --tools, which removes the others
rather than merely skipping their prompts.
And a stub gh comes first on PATH with an empty GH_CONFIG_DIR, so prompts about
issues and PRs can't reach GitHub with the owner's login. Each run's init event
must list the skill exactly once with it installed and not at all without it;
otherwise the suite stops with an IsolationError and no record is written.

trigger: each query runs three times with the repo skills installed and an empty
CLAUDE.md, so only the descriptions can make Claude load the skill. Two triggering runs
of three trigger the query. For a delegated query, only a load inside a subagent
counts. The suite passes when at least 70% of the queries that should trigger do
(85% before #62; #67 restores it),
no near miss triggers or errors, and every delegated query triggers. Overall
accuracy is reported but never decides, because the two classes differ in size.
A run that errors fails its query. A run that hits the turn limit without loading
the skill under test is counted in turnLimitRuns, whatever else it loaded.

cold-reader: a write case asks the skill arm (told to load the skill) and a
baseline arm (no skill, no tools) for the same text; a rewrite case compares the
skill arm's rewrite with the draft itself. A fresh reader sees only the text and
answers the case's questions, then says whether it reads as AI-written. A judge
given the facts marks the answers and lists lost facts. The skill arm must get
at least as many answers right, lose nothing, and be flagged no more than the
baseline; a write case's skill arm must also have loaded the skill. A failing
case is rerun once and the rerun counted. Checker figures for every text go in
the record as diagnostics only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover  (only when run as a script)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import hook_harness, run_eval  # noqa: E402

TRIGGER_RUNS = 3
TRIGGER_NEEDED = 2
# Each class gets its own rule: recall over the queries that should load the skill, and
# no near miss at all. Overall accuracy is reported but never decides.
# The skills' specs set 85%. With the other repo skills listed (#62) both trigger suites
# fell below it, so the owner lowered the gate to 70% on 2026-10-20 until #67 raises
# their recall and puts it back.
TRIGGER_RECALL = 0.70
TRIGGER_TOOLS = "Skill,Read,Agent"
TRIGGER_MAX_TURNS = "4"
WRITE_TOOLS = "Skill,Read,Bash"
DIAGNOSTIC_CHECKER = Path("config/skills/plain-prose/scripts/prose_check.py")

Event = dict[str, Any]
Case = dict[str, Any]


@dataclass
class Sandbox:
    """Where every run in a suite gets its stub gh and empty gh config."""

    home: Path
    env: dict[str, str]


@contextmanager
def sandbox(ctx: run_eval.Context) -> Iterator[Sandbox]:
    home = Path(tempfile.mkdtemp(prefix="skill-eval.", dir=ctx.root))
    try:
        (home / "bin").mkdir()
        (home / "bin" / "gh").write_text(hook_harness.STUB_GH)
        (home / "bin" / "gh").chmod(0o700)
        (home / "gh-config").mkdir()
        env = {
            "PATH": f"{home / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "GH_CONFIG_DIR": str(home / "gh-config"),
            "GH_TOKEN": "",
            "GITHUB_TOKEN": "",
        }
        yield Sandbox(home, env)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def repo_skills(ctx: run_eval.Context) -> list[Path]:
    """Every skill directory in the HEAD commit's two skill trees, as a deployed session has."""
    return run_eval.tree_skills(ctx.head_tree)


@contextmanager
def trigger_config(ctx: run_eval.Context, name: str) -> Iterator[Path]:
    """A private config with an empty CLAUDE.md and one copy of every repo skill."""
    cfg = run_eval.make_config_dir(ctx, name, "")
    try:
        for skill in repo_skills(ctx):
            shutil.copytree(skill, cfg / "skills" / skill.name)
        yield cfg
    finally:
        run_eval.remove_config_dir(cfg)


def hit_turn_limit(evs: list[Event]) -> bool:
    return any(e.get("type") == "result" and e.get("subtype") == "error_max_turns" for e in evs)


@contextmanager
def config_dir(ctx: run_eval.Context, name: str, skill: Path | None) -> Iterator[Path]:
    """A private config with an empty CLAUDE.md and, if given, only that skill installed."""
    cfg = run_eval.make_config_dir(ctx, name, "")
    try:
        if skill is not None:
            shutil.copytree(skill, cfg / "skills" / skill.name)
        yield cfg
    finally:
        run_eval.remove_config_dir(cfg)


def events(out: str) -> list[Event]:
    found = []
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append(event)
    return found


def init_skills(evs: list[Event]) -> list[str] | None:
    for e in evs:
        if e.get("type") == "system" and e.get("subtype") == "init":
            return [str(s) for s in e.get("skills") or []]
    return None


def skill_calls(evs: list[Event], skill: str) -> list[str | None]:
    """The parent_tool_use_id of each Skill call for `skill`: None at top level."""
    calls = []
    for e in evs:
        if e.get("type") != "assistant":
            continue
        for block in (e.get("message") or {}).get("content") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "Skill"
                and (block.get("input") or {}).get("skill") == skill
            ):
                calls.append(e.get("parent_tool_use_id"))
    return calls


def result_text(evs: list[Event]) -> str:
    for e in reversed(evs):
        if e.get("type") == "result":
            return str(e.get("result") or "")
    return ""


def stream_run(
    ctx: run_eval.Context, cfg: Path, args: list[str], prompt: str, box: Sandbox, skill: str,
    installed: bool,
) -> list[Event] | None:  # fmt: skip
    """One stream-json run; None if it produced no init event (a run error)."""
    _, out = run_eval.run_claude(
        ctx, cfg, ["-p", "--output-format", "stream-json", "--verbose", *args], prompt, box.env
    )
    evs = events(out)
    listed = init_skills(evs)
    if listed is None:
        return None
    if listed.count(skill) != (1 if installed else 0):
        raise run_eval.IsolationError(
            f"{skill} is listed {listed.count(skill)} times in a run that should "
            f"{'have it once' if installed else 'not have it'}"
        )
    return evs


def run_all(ctx: run_eval.Context, fn: Callable[[Any], Any], jobs: list[Any]) -> list[Any]:
    """`fn` over `jobs` in parallel; an isolation failure cancels the runs still queued."""
    with ThreadPoolExecutor(ctx.workers) as ex:
        futures = [ex.submit(fn, job) for job in jobs]
        try:
            return [f.result() for f in futures]
        except run_eval.IsolationError:
            for f in futures:
                f.cancel()
            raise


def _write_out(ctx: run_eval.Context, name: str, data: Any) -> None:
    ctx.out.mkdir(parents=True, exist_ok=True)
    (ctx.out / name).write_text(json.dumps(data, indent=1))


def _cases(ctx: run_eval.Context, suite: Any) -> tuple[str, Path, list[Case]]:
    skill_dir = ctx.head_tree / "config" / "skills" / suite.skill
    cases = json.loads((ctx.head_tree / suite.cases).read_text())
    return str(suite.skill), skill_dir, cases


# Trigger


def run_trigger(ctx: run_eval.Context, suite: Any) -> dict[str, Any]:
    skill, skill_dir, cases = _cases(ctx, suite)
    args = ["--max-turns", TRIGGER_MAX_TURNS, "--tools", TRIGGER_TOOLS,
            "--allowedTools", TRIGGER_TOOLS, "--permission-prompts", "none"]  # fmt: skip
    with sandbox(ctx) as box, trigger_config(ctx, "trigger") as cfg:

        def one(job: tuple[int, int]) -> tuple[int, str]:
            index, attempt = job
            case = cases[index]
            evs = stream_run(ctx, cfg, args, case["query"], box, skill, installed=True)
            if evs is None:
                return index, "error"
            calls = skill_calls(evs, skill)
            if case.get("delegated"):
                calls = [c for c in calls if c is not None]
            if calls:
                return index, "triggered"
            return index, "limited" if hit_turn_limit(evs) else "quiet"

        jobs = [(i, a) for i in range(len(cases)) for a in range(TRIGGER_RUNS)]
        outcomes = sorted(run_all(ctx, one, jobs))
    results = []
    for index, case in enumerate(cases):
        runs = [o for i, o in outcomes if i == index]
        triggered = runs.count("triggered") >= TRIGGER_NEEDED
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
    _write_out(ctx, "trigger-results.json", results)
    positive = [r for r in results if r["shouldTrigger"]]
    recall = (
        round(sum(r["result"] == "pass" for r in positive) / len(positive), 3) if positive else 1.0
    )
    false_triggers = sum(
        1 for r in results if not r["shouldTrigger"] and r["triggered"] >= TRIGGER_NEEDED
    )
    near_misses_clean = all(r["result"] == "pass" for r in results if not r["shouldTrigger"])
    delegated_ok = all(r["result"] == "pass" for r in results if r["delegated"])
    right = sum(r["result"] == "pass" for r in results)
    return {
        "pass": recall >= TRIGGER_RECALL and near_misses_clean and delegated_ok,
        "recall": recall,
        "falseTriggers": false_triggers,
        "accuracy": round(right / len(results), 3) if results else 0.0,
        "turnLimitRuns": sum(o == "limited" for _, o in outcomes),
        "cases": results,
    }


# Cold reader


def first_json(raw: str) -> Any:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


def parse_reader(raw: str, questions: int) -> dict[str, Any] | None:
    data = first_json(raw)
    if not isinstance(data, dict):
        return None
    answers, ai = data.get("answers"), data.get("aiSounding")
    if not (isinstance(answers, list) and len(answers) == questions and isinstance(ai, bool)):
        return None
    return {"answers": [str(a) for a in answers], "aiSounding": ai,
            "quote": str(data.get("quote", ""))}  # fmt: skip


def parse_judge(raw: str, questions: int) -> dict[str, Any] | None:
    data = first_json(raw)
    if not isinstance(data, dict):
        return None
    correct, lost = data.get("correct"), data.get("lost")
    if not (
        isinstance(correct, list)
        and len(correct) == questions
        and all(isinstance(c, bool) for c in correct)
        and isinstance(lost, list)
        and all(isinstance(x, str) for x in lost)
    ):
        return None
    return {"correct": correct, "lost": lost}


def writer_prompt(case: Case, skill: str | None) -> str:
    parts = [f"Load the {skill} skill first, then do this task.\n"] if skill else []
    parts.append(case["task"])
    if case["kind"] == "rewrite":
        parts.append(f"\nDRAFT:\n{case['draft']}")
    else:
        parts.append("\nFACTS:\n" + "\n".join(f"- {f}" for f in case["facts"]))
    parts.append("\nReply with only the text, nothing else.")
    return "\n".join(parts)


def reader_prompt(case: Case, text: str) -> str:
    questions = "\n".join(f"{i}. {q}" for i, q in enumerate(case["questions"], 1))
    return (
        "You are a reader with no context: you have only the text below. Answer each "
        'question using only the text, and write "not stated" when the text doesn\'t say. '
        "Then, as a reader who knows AI-generated writing well, say whether anything in the "
        "text reads as AI-written and distracted you, quoting the passage. Respond with ONLY "
        'a JSON object: {"answers": [one string per question], "aiSounding": true/false, '
        '"quote": "..."}.\n\n'
        f"TEXT:\n{text}\n\nQUESTIONS:\n{questions}\n"
    )


def judge_prompt(case: Case, text: str, answers: list[str]) -> str:
    return (
        "You are checking a reader's answers about a text against the facts the text was "
        "meant to convey. Mark each answer right only if it agrees with the facts. Then list "
        "every MUST-KEEP fact that the text leaves out or gets wrong, quoting it exactly. "
        'Respond with ONLY a JSON object: {"correct": [true/false per answer], '
        '"lost": [facts]}.\n\n'
        f"FACTS:\n{json.dumps(case['facts'], indent=1)}\n\n"
        f"MUST-KEEP:\n{json.dumps(case['mustKeep'], indent=1)}\n\n"
        f"QUESTIONS:\n{json.dumps(case['questions'], indent=1)}\n\n"
        f"ANSWERS:\n{json.dumps(answers, indent=1)}\n\nTEXT:\n{text}\n"
    )


def diagnostics(ctx: run_eval.Context, text: str) -> dict[str, Any]:
    """plain-prose's checker figures for `text`, from the HEAD commit; never a verdict."""
    checker = ctx.head_tree / DIAGNOSTIC_CHECKER
    out = subprocess.run(  # noqa: S603  (the committed checker, run on a local string)
        [sys.executable, "-I", str(checker), "-", "--json"],
        input=text, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    report = json.loads(out.stdout)
    return {
        "per_1000_words": report["per_1000_words"],
        "detectability": report["detectability"]["score"],
    }


@dataclass
class Arms:
    skill: str
    skill_cfg: Path
    base_cfg: Path
    reader_cfg: Path
    box: Sandbox
    write_args: list[str]


def grade(ctx: run_eval.Context, arms: Arms, case: Case, text: str) -> dict[str, Any]:
    """Reader then judge on `text`; an `error` key says why it couldn't be graded."""
    n = len(case["questions"])
    no_tools = ["--tools", ""]
    evs = stream_run(ctx, arms.reader_cfg, no_tools, reader_prompt(case, text), arms.box,
                     arms.skill, installed=False)  # fmt: skip
    reader = parse_reader(result_text(evs or []), n)
    if reader is None:
        return {"error": "reader reply unparsable"}
    evs = stream_run(ctx, arms.reader_cfg, no_tools, judge_prompt(case, text, reader["answers"]),
                     arms.box, arms.skill, installed=False)  # fmt: skip
    judge = parse_judge(result_text(evs or []), n)
    if judge is None:
        return {"error": "judge reply unparsable"}
    return {
        "correct": sum(judge["correct"]),
        "lost": judge["lost"],
        "aiSounding": reader["aiSounding"],
        "quote": reader["quote"][:200],
        "diagnostics": diagnostics(ctx, text),
    }


def verdict(case: Case, skill_arm: dict[str, Any], base: dict[str, Any], loaded: bool) -> str:
    """Why the case fails, or '' when it passes."""
    for arm in (skill_arm, base):
        if "error" in arm:
            return str(arm["error"])
    if case["kind"] == "write" and not loaded:
        return "the skill arm never loaded the skill"
    if skill_arm["correct"] < base["correct"]:
        return f"fewer answers right ({skill_arm['correct']} vs {base['correct']})"
    if skill_arm["lost"]:
        return f"lost or distorted: {'; '.join(skill_arm['lost'])}"
    if skill_arm["aiSounding"] and not base["aiSounding"]:
        return f"flagged as AI-sounding when the baseline wasn't: {skill_arm['quote']}"
    return ""


def attempt(ctx: run_eval.Context, arms: Arms, case: Case) -> dict[str, Any]:
    evs = stream_run(ctx, arms.skill_cfg, arms.write_args, writer_prompt(case, arms.skill),
                     arms.box, arms.skill, installed=True)  # fmt: skip
    skill_text = result_text(evs or [])
    loaded = bool(skill_calls(evs or [], arms.skill))
    if case["kind"] == "rewrite":
        base_text = case["draft"]
    else:
        base_evs = stream_run(ctx, arms.base_cfg, ["--tools", ""], writer_prompt(case, None),
                              arms.box, arms.skill, installed=False)  # fmt: skip
        base_text = result_text(base_evs or [])
    skill_arm = grade(ctx, arms, case, skill_text)
    base = grade(ctx, arms, case, base_text)
    reason = verdict(case, skill_arm, base, loaded)
    return {"id": case["id"], "result": "fail" if reason else "pass", "reason": reason,
            "skill": skill_arm, "baseline": base,
            "texts": {"skill": skill_text, "baseline": base_text}}  # fmt: skip


def run_cold_reader(ctx: run_eval.Context, suite: Any) -> dict[str, Any]:
    skill, skill_dir, cases = _cases(ctx, suite)
    with (
        sandbox(ctx) as box,
        config_dir(ctx, "skill", skill_dir) as skill_cfg,
        config_dir(ctx, "baseline", None) as base_cfg,
        config_dir(ctx, "reader", None) as reader_cfg,
    ):
        checker = skill_cfg / "skills" / skill / "scripts" / "prose_check.py"
        write_args = ["--tools", WRITE_TOOLS, "--allowedTools", f"Bash(python3 -I {checker} *)",
                      "--permission-prompts", "none"]  # fmt: skip
        arms = Arms(skill, skill_cfg, base_cfg, reader_cfg, box, write_args)

        def one(case: Case) -> tuple[dict[str, Any], int]:
            first = attempt(ctx, arms, case)
            if first["result"] == "pass":
                return first, 0
            return attempt(ctx, arms, case), 1

        done = run_all(ctx, one, cases)
    results = sorted((r for r, _ in done), key=lambda r: str(r["id"]))
    _write_out(ctx, "cold-reader-results.json", results)
    for r in results:
        del r["texts"]  # the full texts stay in eval/out; the record keeps the figures
    return {
        "pass": all(r["result"] == "pass" for r in results),
        "rerunsUsed": sum(used for _, used in done),
        "cases": results,
    }
