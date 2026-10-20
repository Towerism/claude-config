"""The scenario and rubric evals for the global CLAUDE.md, run with headless `claude -p`.

tools/cfg/eval.py calls these suites and writes their records; see ADR-0005.

The scenario suite compares two arms: the config/ tree at the merge-base (old)
and at HEAD (new). Both come from commits, never the working tree. Each arm,
and the grader, runs with CLAUDE_CONFIG_DIR set to its own temporary
directory: that arm's CLAUDE.md with a canary line, the live settings.json
without its hooks, a symlink to the live plugins and a private copy of the
credentials. A probe proves each arm sees only its own canary before any
scenario runs. Every temporary directory lives under the system temp root,
outside $HOME, because ancestor discovery would otherwise load the live
~/.claude/CLAUDE.md into every arm.

The rubric suite scores HEAD's CLAUDE.md against the claude-md-improver
quality criteria (v2 spec AC-4).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# (command, stdin, environment, working directory, timeout) -> (exit code, stdout)
Runner = Callable[[list[str], str, dict[str, str], Path, float], tuple[int, str]]

PREAMBLE = (
    "Answer in prose only. Do not use any tools. Describe exactly what you would do next, "
    "in order, and why, naming any skills you would use.\n\n"
)
CANARY = {"old": "Eval arm marker: CANARY-OLD-5150", "new": "Eval arm marker: CANARY-NEW-6260"}
GRADER_MD = "# Grader\nFollow the grading instructions exactly.\n"
RUBRIC_PLUGIN = "claude-md-management"
RUBRIC_FILE = Path("skills/claude-md-improver/references/quality-criteria.md")
RUBRIC_PASS = 80
REASON_LIMIT = 200
CLAUDE_TIMEOUT = 400
REMOVED_PLUGINS = (
    "engineering-skills@claude-code-skills",
    "engineering-advanced-skills@claude-code-skills",
)


class Refusal(Exception):
    """A precondition failed before any model ran; eval.py exits 2."""


class IsolationError(Exception):
    """An arm's probe saw the wrong config; eval.py exits 1 and writes no record."""


@dataclass
class Context:
    live: Path  # the owner's Claude home: credentials, settings and plugins come from here
    root: Path  # temp root every temporary directory goes under
    runner: Runner
    model: str | None  # model for the scenario arms; the grader keeps the default
    workers: int
    out: Path  # eval/out/<suite>: raw answers, never committed
    head_tree: Path  # the HEAD commit's config/ and eval/, extracted
    base_tree: Path | None  # the merge-base's config/, extracted


def live_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".claude"


def temp_root(repo: Path) -> Path:
    """The system temp root, refused when it sits inside the repo or inside $HOME."""
    root = Path(tempfile.gettempdir()).resolve()
    if root.is_relative_to(repo.resolve()):
        raise Refusal(f"the temp root {root} is inside the repo; set TMPDIR=/tmp")
    if root.is_relative_to(Path.home().resolve()):
        raise Refusal(
            f"the temp root {root} is inside $HOME, where the live CLAUDE.md would load "
            "into every arm; set TMPDIR=/tmp"
        )
    return root


def default_runner(
    cmd: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: float
) -> tuple[int, str]:
    # The command is built by this harness from fixed arguments; no shell is involved.
    out = subprocess.run(  # noqa: S603
        cmd, input=stdin, text=True, capture_output=True, env=env, cwd=cwd, timeout=timeout
    )
    return out.returncode, out.stdout


def make_config_dir(ctx: Context, name: str, claude_md: str) -> Path:
    """A private CLAUDE_CONFIG_DIR: given CLAUDE.md, live settings without hooks, credentials."""
    creds = ctx.live / ".credentials.json"
    if not creds.is_file():
        raise Refusal(f"no {creds}; log in to Claude Code first")
    d = Path(tempfile.mkdtemp(prefix=f"arm-{name}.", dir=ctx.root))  # mode 0700
    try:
        (d / "CLAUDE.md").write_text(claude_md)
        settings_file = ctx.live / "settings.json"
        settings = json.loads(settings_file.read_text()) if settings_file.is_file() else {}
        settings.pop("hooks", None)
        # The repo replaced these plugins' skills with its own copies (#62). Until every
        # machine has uninstalled them, a run would still load their 72 skills, so they are
        # switched off; a missing key may not count as off. #63 removes this once they're gone.
        settings["enabledPlugins"] = {
            **settings.get("enabledPlugins", {}),
            **dict.fromkeys(REMOVED_PLUGINS, False),
        }
        (d / "settings.json").write_text(json.dumps(settings, indent=2))
        (d / "plugins").symlink_to(ctx.live / "plugins")
        fd = os.open(d / ".credentials.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(creds.read_bytes())
    except BaseException:
        remove_config_dir(d)
        raise
    return d


def remove_config_dir(d: Path) -> None:
    (d / ".credentials.json").unlink(missing_ok=True)
    shutil.rmtree(d, ignore_errors=True)


def run_claude(
    ctx: Context,
    cfg: Path,
    args: list[str],
    prompt: str,
    extra_env: dict[str, str] | None = None,
    timeout: float = CLAUDE_TIMEOUT,
) -> tuple[int, str]:
    """Run `claude <args>` with `cfg` as its config, in a fresh directory under the temp root."""
    cwd = Path(tempfile.mkdtemp(prefix="eval-cwd.", dir=ctx.root))
    # Started from inside Claude Code, the environment carries that session's CLAUDE*
    # variables (its effort level, session id, messaging socket). They would change how
    # the eval runs behave, so only CLAUDE_CONFIG_DIR is passed on.
    inherited = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env = {**inherited, **(extra_env or {}), "CLAUDE_CONFIG_DIR": str(cfg)}
    try:
        return ctx.runner(["claude", *args], prompt, env, cwd, timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return -1, f"<run error: {e}>"
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


def claude(ctx: Context, cfg: Path, prompt: str, model: str | None = None) -> str:
    """The `result` text of a tool-less `claude -p` run; a run error becomes a marked answer."""
    args = ["-p", "--tools", "", "--output-format", "json"]
    if model:
        args += ["--model", model]
    code, out = run_claude(ctx, cfg, args, prompt)
    try:
        return str(json.loads(out)["result"] or "")
    except (ValueError, KeyError, TypeError):
        return f"<run error: exit {code}: {out[:200]}>"


def overlay_text(ctx: Context, tree: Path, skill: str) -> str | None:
    """What `tree`'s overlay hook injects for `skill`, run against `tree`'s overlays."""
    hook = tree / "config" / "hooks" / "skill_overlay.py"
    if not hook.is_file():
        return None
    home = Path(tempfile.mkdtemp(prefix="overlay-home.", dir=ctx.root))
    try:
        overlays = tree / "config" / "skill-overlays"
        if overlays.is_dir():
            shutil.copytree(overlays, home / "skill-overlays")
        # Repo skills come from the tree, so a branch's overlays resolve before it is
        # deployed. The plugins stay linked for older trees whose overlays are namespaced.
        (home / "skills").mkdir()
        for skill_md in sorted((tree / "config" / "skills").glob("*/SKILL.md")):
            (home / "skills" / skill_md.parent.name).symlink_to(skill_md.parent)
        (home / "plugins").symlink_to(ctx.live / "plugins")
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Skill",
                   "tool_input": {"skill": skill}}  # fmt: skip
        out = subprocess.run(  # noqa: S603
            [sys.executable, "-I", str(hook)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", ""), "CLAUDE_HOME": str(home)},
            timeout=30,
        )
        if not out.stdout.strip():
            return None
        return str(json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def overlay_prefix(ctx: Context, tree: Path, skills: list[str]) -> str:
    parts = []
    for skill in skills:
        text = overlay_text(ctx, tree, skill)
        if text is None:
            parts.append(f"[You loaded {skill}.]\n\n")
        else:
            parts.append(f"[You loaded {skill}. A hook then injected this context:]\n{text}\n\n")
    return "".join(parts)


def criteria(sc: dict[str, Any], arm: str) -> dict[str, list[str]]:
    """`mustIncludeByArm` adds checks for the named arm only."""
    extra = (sc.get("mustIncludeByArm") or {}).get(arm, [])
    return {"mustInclude": sc["mustInclude"] + extra, "mustNotInclude": sc["mustNotInclude"]}


def _bools(value: object, length: int) -> bool:
    return (
        isinstance(value, list) and len(value) == length and all(isinstance(v, bool) for v in value)
    )


def score_grade(raw: str, crit: dict[str, list[str]]) -> dict[str, Any]:
    """Pass only if every required behaviour is present and no forbidden one is."""
    match = re.search(r"\{.*\}", raw, re.S)
    try:
        g = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        g = None
    if not isinstance(g, dict):
        return {"pass": False, "reason": "grader reply unparsable"}
    reason = str(g.get("reason", ""))
    must, must_not = g.get("mustInclude"), g.get("mustNotInclude")
    if not (
        _bools(must, len(crit["mustInclude"])) and _bools(must_not, len(crit["mustNotInclude"]))
    ):
        return {"pass": False, "reason": reason or "grader reply had the wrong shape"}
    return {"pass": all(must) and not any(must_not), "reason": reason}  # type: ignore[arg-type]


def grade(ctx: Context, grader: Path, sc: dict[str, Any], answer: str, arm: str) -> dict[str, Any]:
    crit = criteria(sc, arm)
    prompt = (
        "You are grading an AI assistant's answer against binary criteria. Judge only what "
        "the answer says it would do. Respond with ONLY a JSON object: "
        '{"mustInclude": [true/false per item], "mustNotInclude": [true/false per item: true '
        'means the forbidden behaviour IS present], "reason": "one sentence"}.\n\n'
        f"SCENARIO:\n{sc['prompt']}\n\nANSWER:\n{answer}\n\n"
        f"CRITERIA:\n{json.dumps(crit, indent=1)}\n"
    )
    return score_grade(claude(ctx, grader, prompt), crit)


def run_case(
    ctx: Context, arm_dir: Path, arm: str, grader: Path, sc: dict[str, Any], prefix: str
) -> dict[str, Any]:
    """One scenario on one arm, re-run once on failure (v2 spec EC-8)."""
    attempts = []
    for _ in range(2):
        answer = claude(ctx, arm_dir, PREAMBLE + prefix + sc["prompt"], model=ctx.model)
        graded = grade(ctx, grader, sc, answer, arm)
        attempts.append({"answer": answer, **graded})
        if graded["pass"]:
            break
    return {"id": sc["id"], "arm": arm, "pass": attempts[-1]["pass"], "attempts": attempts}


def summarize(results: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    """The record's result: no old-pass/new-fail regressions, and every new rule passing."""
    by: dict[str, dict[str, dict[str, Any]]] = {}
    for r in results:
        by.setdefault(r["id"], {})[r["arm"]] = r
    regressions, new_rule_failures, cases = [], [], []
    for sc in scenarios:
        old, new = by[sc["id"]]["old"], by[sc["id"]]["new"]
        if "new-rule-only" in sc.get("tags", []):
            if not new["pass"]:
                new_rule_failures.append(sc["id"])
        elif old["pass"] and not new["pass"]:
            regressions.append(sc["id"])
        cases.append(
            {
                "id": sc["id"],
                "old": "pass" if old["pass"] else "fail",
                "new": "pass" if new["pass"] else "fail",
                "graderReason": new["attempts"][-1]["reason"][:REASON_LIMIT],
            }
        )
    return {
        "pass": not regressions and not new_rule_failures,
        "regressions": regressions,
        "newRuleFailures": new_rule_failures,
        "rerunsUsed": sum(len(r["attempts"]) - 1 for r in results),
        "cases": cases,
    }


def probe(ctx: Context, arms: dict[str, Path]) -> None:
    """Stop the run unless each arm's answer shows its own canary and not the other's."""
    question = "Without tools: quote every line in your instructions containing 'Eval arm marker'."
    probes = {}
    for name, d in arms.items():
        answer = claude(ctx, d, question)
        other = CANARY["new" if name == "old" else "old"]
        isolated = CANARY[name] in answer and other not in answer
        probes[name] = {"answer": answer, "isolated": isolated}
    (ctx.out / "probes.json").write_text(json.dumps(probes, indent=1))
    if not all(p["isolated"] for p in probes.values()):
        raise IsolationError(f"arm isolation failed; see {ctx.out / 'probes.json'}")


def run_scenario(ctx: Context) -> dict[str, Any]:
    if ctx.base_tree is None or not (ctx.base_tree / "config" / "CLAUDE.global.md").is_file():
        raise Refusal("the merge-base has no config/CLAUDE.global.md to compare against")
    trees = {"old": ctx.base_tree, "new": ctx.head_tree}
    scenarios = json.loads((ctx.head_tree / "eval" / "scenarios.json").read_text())
    ctx.out.mkdir(parents=True, exist_ok=True)
    dirs: list[Path] = []
    try:
        arms = {}
        for name, tree in trees.items():
            md = (tree / "config" / "CLAUDE.global.md").read_text()
            arms[name] = make_config_dir(ctx, name, md + "\n" + CANARY[name] + "\n")
            dirs.append(arms[name])
        grader = make_config_dir(ctx, "grader", GRADER_MD)
        dirs.append(grader)
        probe(ctx, arms)
        jobs = [
            (arms[name], name, sc, overlay_prefix(ctx, trees[name], sc["loadsSkills"]))
            for sc in scenarios
            for name in ("old", "new")
        ]
        with ThreadPoolExecutor(ctx.workers) as ex:
            results = list(ex.map(lambda j: run_case(ctx, j[0], j[1], grader, j[2], j[3]), jobs))
        (ctx.out / "results.json").write_text(json.dumps(results, indent=1))
    finally:
        for d in dirs:
            remove_config_dir(d)
    return summarize(results, scenarios)


def find_rubric(live: Path) -> Path:
    """quality-criteria.md from the installed claude-md-management plugin."""
    try:
        installed = json.loads((live / "plugins" / "installed_plugins.json").read_text())
    except (OSError, ValueError) as e:
        raise Refusal(f"cannot read installed_plugins.json: {e}") from e
    for key, installs in (installed.get("plugins") or {}).items():
        if key.split("@", 1)[0] != RUBRIC_PLUGIN:
            continue
        for inst in installs:
            path = Path(inst.get("installPath", "")) / RUBRIC_FILE
            if path.is_file():
                return path
    raise Refusal(f"the {RUBRIC_PLUGIN} plugin's quality-criteria.md is not installed")


def parse_rubric(raw: str) -> int | None:
    match = re.search(r"\{.*\}", raw, re.S)
    try:
        doc = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        return None
    total = doc.get("total") if isinstance(doc, dict) else None
    return total if isinstance(total, int) else None


def run_rubric(ctx: Context) -> dict[str, Any]:
    """Score HEAD's CLAUDE.md; at least 80 passes, with one re-run allowed (v2 spec AC-4)."""
    rubric = find_rubric(ctx.live).read_text()
    md = (ctx.head_tree / "config" / "CLAUDE.global.md").read_text()
    prompt = (
        "Score this global CLAUDE.md (a user-wide file, not a project file) against the rubric "
        "below. Score 'Commands/workflows' and 'Architecture clarity' on how clearly it maps "
        "the workflow and which skill is used where, not on build commands. Respond with ONLY "
        'JSON: {"criteria": {name: score}, "total": n, "issues": [..]}.\n\nRUBRIC:\n'
        f"{rubric}\n\nCLAUDE.md:\n{md}"
    )
    ctx.out.mkdir(parents=True, exist_ok=True)
    grader = make_config_dir(ctx, "rubric", GRADER_MD)
    replies: list[str] = []
    scores: list[int | None] = []
    try:
        for _ in range(2):
            reply = claude(ctx, grader, prompt)
            replies.append(reply)
            scores.append(parse_rubric(reply))
            if (scores[-1] or 0) >= RUBRIC_PASS:
                break
    finally:
        remove_config_dir(grader)
    (ctx.out / "rubric.json").write_text(json.dumps(replies, indent=1))
    return {
        "pass": (scores[-1] or 0) >= RUBRIC_PASS,
        "scores": scores,
        "rerunsUsed": len(scores) - 1,
        "cases": [],
    }
