"""Real-harness check for the default-lifecycle hook, run with headless `claude -p`.

    python3 -I eval/session_harness.py

Run alone, it checks HEAD and prints the result without writing a record;
tools/cfg/eval.py runs the same check as the hook-session-start suite and
records it (ADR-0005, #15).

It builds a temporary layout shaped like a cfg deployment: a data directory
with releases/r holding HEAD's lifecycle_default.py and a host profile whose
defaultLifecycle is a fresh canary skill name, a `current` link, a state.json
naming that profile, and a Claude home whose hook links through `current`.
XDG_CONFIG_HOME points at an empty directory, so the owner's own local.json is
never read. Then:

- main: a run with no tools must quote the canary in its final answer;
- subagent: a run that starts one general-purpose subagent, whose prompt
  mustn't contain the canary, must get the canary back in that subagent's own
  reply (the Agent call's tool_result), because SessionStart context doesn't
  reach subagents and only the SubagentStart hook can have put it there. A
  fork fails the check, because it inherits the parent's context;
- control: a run without the hook settings mustn't see the canary.

Each gets one rerun on failure. The hook's 95th-percentile time over 20 direct
runs must stay under 500 ms. Every run uses a private config directory (see
run_eval.make_config_dir), a stub gh and an empty GH_CONFIG_DIR, as
hook_harness does.
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Run as a script with python3 -I, the repo root isn't on sys.path. Tests import the
# module as a package, so this line only runs in a subprocess, where coverage isn't measured.
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import hook_harness, run_eval  # noqa: E402

HOOK = "lifecycle_default.py"
ENTRIES = ["SessionStart", "SubagentStart"]
HOST = "hosts/eval.json"
CANARY_PREFIX = "canary-lifecycle-"
AGENT_TOOLS = {"Agent", "Task"}
# A fork inherits the parent's context, SessionStart line included, so only a
# fresh subagent's reply shows that SubagentStart delivered the line.
FRESH_AGENTS = {None, "general-purpose"}
PROMPTS = {
    "main": (
        "Without using any tools, quote word for word any context a hook gave you at "
        "session start, including the name of the default lifecycle skill."
    ),
    "subagent": (
        "Start one general-purpose subagent with the Agent tool. Ask it to reply with, word "
        "for word, any context a hook gave it when it started, including the name of any "
        "default lifecycle skill. Don't add anything else to its prompt. Then quote the "
        "subagent's reply."
    ),
}

Timer = Callable[[Path, Path, int], list[float]]


@dataclass
class Layout:
    root: Path  # everything below is inside it, removed afterwards
    home: Path  # the Claude home whose hooks/ links into the release
    xdg: Path  # an empty XDG_CONFIG_HOME
    canary: str


def new_canary() -> str:
    """A skill name the hook accepts and no model could guess."""
    return CANARY_PREFIX + secrets.token_hex(6)


def build_layout(ctx: run_eval.Context, canary: str) -> Layout:
    root = Path(tempfile.mkdtemp(prefix="session-home.", dir=ctx.root))
    data, release = root / "data", root / "data" / "releases" / "r"
    (release / "config" / "hooks").mkdir(parents=True)
    shutil.copy(ctx.head_tree / "config" / "hooks" / HOOK, release / "config" / "hooks")
    (release / "hosts").mkdir()
    (release / HOST).write_text(json.dumps({"defaultLifecycle": canary}))
    (data / "current").symlink_to("releases/r")
    (data / "state.json").write_text(json.dumps({"host": HOST}))
    home = root / "claude"
    (home / "hooks").mkdir(parents=True)
    (home / "hooks" / HOOK).symlink_to(data / "current" / "config" / "hooks" / HOOK)
    (root / "xdg").mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "gh").write_text(hook_harness.STUB_GH)
    (root / "bin" / "gh").chmod(0o700)
    (root / "gh-config").mkdir()
    return Layout(root=root, home=home, xdg=root / "xdg", canary=canary)


def remove_layout(layout: Layout) -> None:
    shutil.rmtree(layout.root, ignore_errors=True)


def render(fragment: dict[str, Any], layout: Layout) -> dict[str, Any]:
    """The fragment's two entries, pointed at the layout, each with the empty config dir."""
    settings = hook_harness.render_entries(fragment, ENTRIES, layout.home)
    prefix = f"XDG_CONFIG_HOME={shlex.quote(str(layout.xdg))} "
    for entries in settings["hooks"].values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                hook["command"] = prefix + hook["command"]
    return settings


def _events(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    content = (event.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def final_answer(text: str) -> str:
    answers = [str(e.get("result") or "") for e in _events(text) if e.get("type") == "result"]
    return answers[-1] if answers else ""


def subagent_quoted(text: str, canary: str) -> bool:
    """Whether a fresh subagent's own reply holds the canary, and no Agent call's input does."""
    calls: dict[str, str] = {}
    replies: list[str] = []
    for event in _events(text):
        for block in _blocks(event):
            if block.get("type") == "tool_use" and block.get("name") in AGENT_TOOLS:
                given = block.get("input")
                kind = given.get("subagent_type") if isinstance(given, dict) else None
                if kind not in FRESH_AGENTS:
                    return False
                calls[str(block.get("id"))] = json.dumps(given)
            elif block.get("type") == "tool_result" and str(block.get("tool_use_id")) in calls:
                replies.append(json.dumps(block.get("content")))
    if any(canary in prompt for prompt in calls.values()):
        return False
    return any(canary in reply for reply in replies)


def time_hook(hook: Path, xdg: Path, runs: int) -> list[float]:
    """Milliseconds per direct run of the hook with a SessionStart payload."""
    payload = json.dumps({"hook_event_name": "SessionStart"})
    env = {"PATH": os.environ.get("PATH", ""), "XDG_CONFIG_HOME": str(xdg)}
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(  # noqa: S603
            [sys.executable, "-I", str(hook)], input=payload, capture_output=True, text=True,
            env=env, timeout=30, check=False,
        )  # fmt: skip
        timings.append((time.perf_counter() - start) * 1000)
    return timings


def _check(
    ctx: run_eval.Context, cfg: Path, name: str, args: list[str], env: dict[str, str], canary: str
) -> Callable[[int], bool]:
    prompt = PROMPTS["subagent" if name == "subagent" else "main"]

    def check(attempt: int) -> bool:
        _, out = run_eval.run_claude(ctx, cfg, args, prompt, extra_env=env)
        (ctx.out / f"{name}-{attempt}.stream").write_text(out)
        if name == "subagent":
            return subagent_quoted(out, canary)
        quoted = canary in final_answer(out)
        return quoted if name == "main" else not quoted

    return check


def run_session(ctx: run_eval.Context, timer: Timer = time_hook) -> dict[str, Any]:
    ctx.out.mkdir(parents=True, exist_ok=True)
    fragment = json.loads((ctx.head_tree / "config" / "settings.fragment.json").read_text())
    layout = build_layout(ctx, new_canary())
    try:
        cfg = run_eval.make_config_dir(ctx, "session", "")
        try:
            settings = layout.root / "hook-settings.json"
            settings.write_text(json.dumps(render(fragment, layout)))
            env = {
                "PATH": f"{layout.root / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
                "GH_CONFIG_DIR": str(layout.root / "gh-config"),
                "GH_TOKEN": "",
                "GITHUB_TOKEN": "",
            }
            base = ["-p", "--output-format", "stream-json", "--verbose"]
            hooked = ["--settings", str(settings)]
            runs = [
                ("main", base + ["--tools", ""] + hooked),
                ("subagent", base + hooked),
                ("control", base + ["--tools", ""]),
            ]
            cases, reruns = [], 0
            for name, args in runs:
                check = _check(ctx, cfg, name, args, env, layout.canary)
                ok, used = hook_harness._attempts(check)
                reruns += used
                cases.append({"id": name, "result": "pass" if ok else "fail"})
        finally:
            run_eval.remove_config_dir(cfg)
        timings = timer(layout.home / "hooks" / HOOK, layout.xdg, hook_harness.TIMING_RUNS)
    finally:
        remove_layout(layout)
    (ctx.out / "timings.json").write_text(json.dumps(timings))
    slowest = round(hook_harness.p95(timings), 1)
    limit = hook_harness.P95_LIMIT_MS
    cases.append({"id": "p95", "result": "pass" if slowest < limit else "fail"})
    return {
        "pass": all(c["result"] == "pass" for c in cases),
        "rerunsUsed": reruns,
        "p95Ms": slowest,
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    from tools.cfg import eval as eval_cmd

    args = sys.argv[1:] if argv is None else argv
    if args:
        print("usage: session_harness.py", file=sys.stderr)
        return 2
    return eval_cmd.main(["--suite", "hook-session-start", "--no-record"])


if __name__ == "__main__":
    sys.exit(main())
