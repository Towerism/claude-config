"""Real-harness checks for the two Claude Code hooks, run with headless `claude -p`.

    python3 -I eval/hook_harness.py overlay|merge

Run alone, it checks HEAD and prints the result without writing a record;
tools/cfg/eval.py runs the same checks as the hook-overlay and hook-merge
suites and records them (ADR-0005).

overlay (v2 spec AC-7): a canary overlay for senior-qa must
reach the model when the skill loads in the main session and in a subagent,
the stream must show no tool call touching skill-overlays/ (so the hook, not a
file read, delivered it), and a control run without the hook must not see it.
The hook's 95th-percentile run time over 20 direct runs must stay under 500 ms.

merge (v2 spec AC-9): with both gh commands pre-allowed, only the hook can stop
one. The hook run must deny `gh pr merge --help` and not `gh pr view --help`;
the control run must deny neither. A stub gh comes first on PATH and
GH_CONFIG_DIR is empty, so no run can reach GitHub with the owner's login.

Every run uses a private config directory (see run_eval.make_config_dir)
whose settings.json has no hooks, so the live hooks never interfere. senior-qa
is a repo skill (#62), so the overlay check takes it from the tree under test:
the session gets a copy, and the hook's home links to it.
"""

from __future__ import annotations

import copy
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Run as a script with python3 -I, the repo root isn't on sys.path. Tests import the
# module as a package, so this line only runs in a subprocess, where coverage isn't measured.
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import run_eval  # noqa: E402

SKILL = "senior-qa"
CANARY = "AC7-CANARY-90210"
OVERLAY_ENTRIES = ["PostToolUse", "UserPromptExpansion"]
MERGE_ENTRIES = ["PreToolUse"]
P95_LIMIT_MS = 500.0
TIMING_RUNS = 20
OVERLAY_PROMPTS = {
    "main": f"Load the {SKILL} skill, then quote any skill overlay you received.",
    "subagent": (
        "Start a subagent with the Agent tool. Tell it to load the "
        f"{SKILL} skill and to quote back, word for word, any skill overlay it "
        "received. Then quote the subagent's reply."
    ),
}
MERGE_PROMPT = (
    "Run `gh pr merge --help`, then run `gh pr view --help`. Report what happened with each."
)
# Prints a usage line for --help, as real gh does; a stub that printed nothing
# made the model investigate with commands nobody pre-allowed (#24).
STUB_GH = """#!/bin/sh
echo "$*" >> "$(dirname "$0")/gh-calls.log"
case " $* " in
*" --help "*)
    words=""
    for arg in "$@"; do [ "$arg" = --help ] || words="$words $arg"; done
    echo "usage: gh$words [flags]"
    ;;
esac
exit 0
"""

Timer = Callable[[Path, Path, int], list[float]]


def render_entries(fragment: dict[str, Any], keys: list[str], home: Path) -> dict[str, Any]:
    """Settings holding the fragment's `keys` hook entries, pointed at `home`.

    {claude_home} becomes `home`, and each command is prefixed with
    CLAUDE_HOME=<home> so the hook reads that home's overlays and plugins.
    """
    hooks = {}
    for key in keys:
        if key not in fragment.get("hooks", {}):
            continue
        entries = copy.deepcopy(fragment["hooks"][key])
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = hook["command"].replace("{claude_home}", str(home))
                hook["command"] = f"CLAUDE_HOME={shlex.quote(str(home))} {command}"
        hooks[key] = entries
    return {"hooks": hooks}


def _fragment(ctx: run_eval.Context) -> dict[str, Any]:
    data = json.loads((ctx.head_tree / "config" / "settings.fragment.json").read_text())
    return data if isinstance(data, dict) else {}


def _tree_skill(ctx: run_eval.Context) -> Path:
    skill = ctx.head_tree / "config" / "vendored-skills" / SKILL
    if not (skill / "SKILL.md").is_file():
        raise run_eval.Refusal(f"the tree under test has no {SKILL} skill at {skill}")
    return skill


def build_overlay_home(ctx: run_eval.Context) -> Path:
    """A Claude home with HEAD's overlay hook, a single canary overlay and HEAD's SKILL."""
    skill = _tree_skill(ctx)
    home = Path(tempfile.mkdtemp(prefix="hook-home.", dir=ctx.root))
    (home / "hooks").mkdir()
    shutil.copy(ctx.head_tree / "config" / "hooks" / "skill_overlay.py", home / "hooks")
    (home / "skill-overlays").mkdir()
    (home / "skill-overlays" / f"{SKILL}.md").write_text(
        f"Overlay check: quote this line exactly: {CANARY}\n"
    )
    (home / "plugins").symlink_to(ctx.live / "plugins")
    (home / "skills").mkdir()
    (home / "skills" / SKILL).symlink_to(skill)
    return home


def stream_findings(text: str) -> dict[str, Any]:
    """Whether the final answer quotes the canary, and which tool calls touched skill-overlays/."""
    canary, reads = False, []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            content = event.get("message", {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_use" and "skill-overlays" in json.dumps(
                    block.get("input")
                ):
                    reads.append(block.get("name"))
        elif event.get("type") == "result":
            canary = CANARY in str(event.get("result") or "")
    return {"canary": canary, "overlayReads": reads}


def p95(timings: list[float]) -> float:
    ordered = sorted(timings)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def time_hook(hook: Path, home: Path, runs: int) -> list[float]:
    """Milliseconds per direct run of the overlay hook with a Skill payload."""
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_input": {"skill": SKILL}})
    env = {"PATH": os.environ.get("PATH", ""), "CLAUDE_HOME": str(home)}
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(  # noqa: S603
            [sys.executable, "-I", str(hook)], input=payload, capture_output=True, text=True,
            env=env, timeout=30, check=False,
        )  # fmt: skip
        timings.append((time.perf_counter() - start) * 1000)
    return timings


def _attempts(check: Callable[[int], bool]) -> tuple[bool, int]:
    """Run `check(attempt)` once, and once more if it fails; return (passed, re-runs used)."""
    if check(1):
        return True, 0
    return check(2), 1


@contextmanager
def _removed_after(home: Path) -> Iterator[Path]:
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


@contextmanager
def _private_config(
    ctx: run_eval.Context, name: str, skills: tuple[Path, ...] = ()
) -> Iterator[Path]:
    cfg = run_eval.make_config_dir(ctx, name, "")
    try:
        for skill in skills:
            shutil.copytree(skill, cfg / "skills" / skill.name)
        yield cfg
    finally:
        run_eval.remove_config_dir(cfg)


def _overlay_check(
    ctx: run_eval.Context, cfg: Path, name: str, args: list[str], hooked: bool
) -> Callable[[int], bool]:
    prompt = OVERLAY_PROMPTS["main" if name == "control" else name]

    def check(attempt: int) -> bool:
        _, out = run_eval.run_claude(ctx, cfg, args, prompt)
        (ctx.out / f"{name}-{attempt}.stream").write_text(out)
        found = stream_findings(out)
        if hooked:
            return bool(found["canary"]) and not found["overlayReads"]
        return not found["canary"]

    return check


def run_overlay(ctx: run_eval.Context, timer: Timer = time_hook) -> dict[str, Any]:
    ctx.out.mkdir(parents=True, exist_ok=True)
    with (
        _removed_after(build_overlay_home(ctx)) as home,
        _private_config(ctx, "hooks", (_tree_skill(ctx),)) as cfg,
    ):
        settings = home / "hook-settings.json"
        settings.write_text(json.dumps(render_entries(_fragment(ctx), OVERLAY_ENTRIES, home)))
        cases, reruns = [], 0
        for name, hooked in [("main", True), ("subagent", True), ("control", False)]:
            args = ["-p", "--output-format", "stream-json", "--verbose"]
            if hooked:
                args += ["--settings", str(settings)]
            ok, used = _attempts(_overlay_check(ctx, cfg, name, args, hooked))
            reruns += used
            cases.append({"id": name, "result": "pass" if ok else "fail"})
        timings = timer(home / "hooks" / "skill_overlay.py", home, TIMING_RUNS)
    (ctx.out / "timings.json").write_text(json.dumps(timings))
    slowest = round(p95(timings), 1)
    cases.append({"id": "p95", "result": "pass" if slowest < P95_LIMIT_MS else "fail"})
    return {
        "pass": all(c["result"] == "pass" for c in cases),
        "rerunsUsed": reruns,
        "p95Ms": slowest,
        "cases": cases,
    }


def denied_commands(out: str) -> list[str] | None:
    """The Bash commands in a json result's permission_denials; None if it doesn't parse."""
    try:
        denials = json.loads(out).get("permission_denials")
    except (ValueError, AttributeError):
        return None
    if not isinstance(denials, list):
        return None
    return [
        str((d.get("tool_input") or {}).get("command", ""))
        for d in denials
        if isinstance(d, dict) and d.get("tool_name") == "Bash"
    ]


def _merge_home(ctx: run_eval.Context) -> Path:
    home = Path(tempfile.mkdtemp(prefix="merge-home.", dir=ctx.root))
    (home / "hooks").mkdir()
    shutil.copy(ctx.head_tree / "config" / "hooks" / "merge_confirm.py", home / "hooks")
    (home / "bin").mkdir()
    (home / "bin" / "gh").write_text(STUB_GH)
    (home / "bin" / "gh").chmod(0o700)
    (home / "gh-config").mkdir()
    return home


def _merge_check(
    ctx: run_eval.Context, cfg: Path, name: str, args: list[str], env: dict[str, str], hooked: bool
) -> Callable[[int], bool]:
    def check(attempt: int) -> bool:
        _, out = run_eval.run_claude(ctx, cfg, args, MERGE_PROMPT, extra_env=env)
        (ctx.out / f"{name}-{attempt}.json").write_text(out)
        denied = denied_commands(out)
        if denied is None:
            return False
        merge = any("gh pr merge" in c for c in denied)
        view = any("gh pr view" in c for c in denied)
        return merge and not view if hooked else not merge and not view

    return check


def run_merge(ctx: run_eval.Context) -> dict[str, Any]:
    ctx.out.mkdir(parents=True, exist_ok=True)
    base_args = ["-p", "--output-format", "json", "--allowedTools", "Bash(gh pr view *)",
                 "Bash(gh pr merge *)", "--permission-prompts", "none"]  # fmt: skip
    with _removed_after(_merge_home(ctx)) as home, _private_config(ctx, "merge") as cfg:
        settings = home / "hook-settings.json"
        settings.write_text(json.dumps(render_entries(_fragment(ctx), MERGE_ENTRIES, home)))
        env = {
            "PATH": f"{home / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "GH_CONFIG_DIR": str(home / "gh-config"),
            "GH_TOKEN": "",
            "GITHUB_TOKEN": "",
        }
        cases, reruns = [], 0
        for name, hooked in [("hook", True), ("control", False)]:
            args = base_args + (["--settings", str(settings)] if hooked else [])
            ok, used = _attempts(_merge_check(ctx, cfg, name, args, env, hooked))
            reruns += used
            cases.append({"id": name, "result": "pass" if ok else "fail"})
    return {"pass": all(c["result"] == "pass" for c in cases), "rerunsUsed": reruns,
            "cases": cases}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    from tools.cfg import eval as eval_cmd

    args = sys.argv[1:] if argv is None else argv
    if args not in (["overlay"], ["merge"]):
        print("usage: hook_harness.py overlay|merge", file=sys.stderr)
        return 2
    return eval_cmd.main(["--suite", f"hook-{args[0]}", "--no-record"])


if __name__ == "__main__":
    sys.exit(main())
