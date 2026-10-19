"""Check that Claude Code follows the symlinked config ADR-0001 deploys, with headless `claude -p`.

    python3 -I eval/symlink_check.py                  run the check and write docs/evidence/
    python3 -I eval/symlink_check.py --inventory DIR  print DIR's managed paths as JSON; read-only

ADR-0001 deploys the repo as symlinks: each live path links into a release
directory through a `current` link. This builds that layout in temporary config
directories and asks the installed Claude Code three things:

- Run M (main session, tools: Skill): is the linked skill discovered and
  loaded, does the linked CLAUDE.md load, does the overlay hook run through its
  linked script and read the linked skill-overlays/, and does SessionStart
  context arrive?
- Run S (a subagent, tools: Agent and Skill): the same, inside the subagent,
  judged on the subagent's own returned message.
- Run E (tools: Read, Edit and Write): do Edit and Write write through a file
  link, or replace it with a plain file?

Each file holds a fresh random canary, so a quoted canary proves its file
reached the model through the mechanism under test. Every verdict is computed
here from the stream and the file system, never from the model's opinion. A
failed result in Run M or S gets one rerun and then a run with plain files, so
a one-off miss can't be recorded as a link failure.

A fourth check runs `claude plugin` against a local marketplace, with no model,
and compares the links before and after. Isolation is the eval harness's (see
run_eval.make_config_dir): a temp root outside the repo and $HOME, copied
credentials removed afterwards, live hooks stripped, a stub gh first on PATH
and none of this session's CLAUDE* variables.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Run as a script with python3 -I, the repo root isn't on sys.path. Tests import the
# module as a package, so this line only runs in a subprocess, where coverage isn't measured.
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import run_eval  # noqa: E402
from eval.hook_harness import STUB_GH  # noqa: E402
from tools.cfg import eval_records as er  # noqa: E402
from tools.cfg.eval import claude_version, extract, is_dirty  # noqa: E402

SKILL = "probe-skill"
KINDS = ("claude_md", "skill", "overlay", "session")
EXCERPT = 4000
MAIN_RESULTS = (
    "skills-main",
    "skill-load-main",
    "claude-md-main",
    "hook-overlay-main",
    "session-start-main",
)
SUB_RESULTS = ("skill-load-sub", "claude-md-sub", "hook-overlay-sub", "session-start-sub")
REQUIRED = (
    "skills-main",
    "skill-load-main",
    "skill-load-sub",
    "claude-md-main",
    "hook-overlay-main",
)
# result id -> (tool the model must use, path in Run E's working directory)
EDIT_CASES = {
    "edit-file-link": ("Edit", "edit-link.md"),
    "write-file-link": ("Write", "write-link.md"),
    "edit-in-dir-link": ("Edit", "skill-link/SKILL.md"),
}
EDIT_LINKS = {
    "edit-file-link": "edit-link.md",
    "write-file-link": "write-link.md",
    "edit-in-dir-link": "skill-link",
}
OLD_LINE = "old line\n"
PLUGIN_KEYS = {"enabledPlugins", "extraKnownMarketplaces"}
LIVE_WATCHED = (
    "settings.json",
    "plugins/installed_plugins.json",
    "plugins/known_marketplaces.json",
)
STOP_OUTCOMES = {"inconclusive", "not-attempted", "failed"}

MAIN_PROMPT = (
    f"Use the Skill tool to load the {SKILL} skill. Then list every string that starts with "
    "CANARY- that you have been given anywhere in this session (system context, memory files, "
    "hook messages, the skill itself), one per line, each followed by where it came from. "
    "Copy each string exactly."
)
SUB_PROMPT = (
    "Use the Agent tool to start one general-purpose subagent. Do not load any skill yourself, "
    "and do not put any string that starts with CANARY- into the subagent's instructions. Give "
    f'the subagent exactly these instructions: "Use the Skill tool to load the {SKILL} skill. '
    "Then list every string that starts with CANARY- that you have been given anywhere in your "
    'context, one per line, each followed by where it came from. Copy each string exactly." '
    "When it finishes, reply with its answer."
)
EDIT_PROMPT = (
    "Make exactly these three changes in the current directory, then stop. Read each file "
    "before you change it, and don't read or change any other file.\n"
    "1. Use the Edit tool on edit-link.md: replace the line `old line` with `edited line`.\n"
    "2. Use the Write tool to overwrite write-link.md with the single line `written line`.\n"
    "3. Use the Edit tool on skill-link/SKILL.md: replace the line `old line` with `edited line`."
)
MARKET = "probe-market"
PLUGIN = "probe-plugin"


class LiveConfigChanged(Exception):
    """A file in the live config changed during the check; main exits 2."""


# ---- the layout ------------------------------------------------------------


def new_canaries() -> dict[str, str]:
    return {kind: f"CANARY-{kind}-{secrets.token_hex(4)}" for kind in KINDS}


def session_probe_source(canary: str) -> str:
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": canary}}
    return f"import json\n\nprint(json.dumps({payload!r}))\n"


def _release_files(config: Path, head_tree: Path, canaries: dict[str, str]) -> None:
    (config / "skills" / SKILL).mkdir(parents=True)
    (config / "skills" / "edit-skill").mkdir()
    (config / "skill-overlays").mkdir()
    (config / "hooks").mkdir()
    (config / "CLAUDE.global.md").write_text(f"# Probe\n\nMemory marker: {canaries['claude_md']}\n")
    (config / "skills" / SKILL / "SKILL.md").write_text(
        f"---\nname: {SKILL}\ndescription: A probe skill for the symlink check.\n---\n\n"
        f"Skill marker: {canaries['skill']}\n"
    )
    (config / "skill-overlays" / f"{SKILL}.md").write_text(
        f"Overlay marker: {canaries['overlay']}\n"
    )
    shutil.copy(head_tree / "config" / "hooks" / "skill_overlay.py", config / "hooks")
    (config / "hooks" / "session_probe.py").write_text(session_probe_source(canaries["session"]))
    (config / "skills" / "edit-skill" / "SKILL.md").write_text(
        "---\nname: edit-skill\ndescription: A file for the Edit check.\n---\n\n" + OLD_LINE
    )
    (config / "edit-target.md").write_text(OLD_LINE)
    (config / "write-target.md").write_text(OLD_LINE)


def _place(target: Path, path: Path, plain: bool) -> None:
    """Link `path` to `target`, or copy it there for the plain control."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not plain:
        path.symlink_to(target)
    elif target.is_dir():
        shutil.copytree(target, path)
    else:
        shutil.copy(target, path)


def add_hooks(cfg: Path) -> None:
    settings_file = cfg / "settings.json"
    settings = json.loads(settings_file.read_text()) if settings_file.is_file() else {}
    settings["hooks"] = {
        "PostToolUse": [
            {
                "matcher": "Skill",
                "hooks": [
                    {"type": "command", "command": f"python3 -I {cfg}/hooks/skill_overlay.py"}
                ],
            }
        ],
        "SessionStart": [
            {"hooks": [{"type": "command", "command": f"python3 -I {cfg}/hooks/session_probe.py"}]}
        ],
    }
    settings_file.write_text(json.dumps(settings, indent=2))


def build_layout(
    root: Path, cfg: Path, head_tree: Path, canaries: dict[str, str], sha: str, plain: bool = False
) -> Path:
    """ADR-0001's layout: releases/<sha>/config, `current`, and cfg's managed paths.

    Returns the data directory, which holds `releases/` and `current`. With
    `plain`, cfg gets copies instead of links: the control for a failed result.
    """
    data = Path(tempfile.mkdtemp(prefix="release-data.", dir=root))
    _release_files(data / "releases" / sha / "config", head_tree, canaries)
    (data / "current").symlink_to(Path("releases") / sha)
    config = data / "current" / "config"
    for rel, source in [
        ("CLAUDE.md", "CLAUDE.global.md"),
        ("skill-overlays", "skill-overlays"),
        ("hooks/skill_overlay.py", "hooks/skill_overlay.py"),
        ("hooks/session_probe.py", "hooks/session_probe.py"),
        (f"skills/{SKILL}", f"skills/{SKILL}"),
    ]:
        _place(config / source, cfg / rel, plain)
    add_hooks(cfg)
    return data


def build_edit_dir(data: Path) -> tuple[Path, dict[str, dict[str, str]]]:
    """Run E's working directory: only the three paths, linked into the release.

    It sits inside the data directory, so removing that removes it too.
    """
    workdir = Path(tempfile.mkdtemp(prefix="edit-cwd.", dir=data))
    config = data / "current" / "config"
    (workdir / "edit-link.md").symlink_to(config / "edit-target.md")
    (workdir / "write-link.md").symlink_to(config / "write-target.md")
    (workdir / "skill-link").symlink_to(config / "skills" / "edit-skill")
    before = {}
    for case, (_, rel) in EDIT_CASES.items():
        target = (workdir / rel).resolve()
        before[case] = {
            "link": os.readlink(workdir / EDIT_LINKS[case]),
            "target": str(target),
            "content": target.read_text(),
        }
    return workdir, before


def edit_args(workdir: Path) -> list[str]:
    rules = ["Read"]
    for tool, rel in EDIT_CASES.values():
        rules.append(f"{tool}(./{rel})")
        rules.append(f"{tool}(/{(workdir / rel).resolve()})")
    return [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "Read,Edit,Write",
        "--allowedTools",
        *rules,
    ]


# ---- reading streams -------------------------------------------------------


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    parent: str | None


@dataclass
class Stream:
    init_skills: list[str] | None = None
    uses: list[ToolUse] = field(default_factory=list)
    results: dict[str, tuple[bool, str]] = field(default_factory=dict)
    texts: list[tuple[str | None, str]] = field(default_factory=list)
    answer: str = ""


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(c.get("text", "")) for c in content if isinstance(c, dict) and "text" in c
        )
    return ""


def parse_stream(text: str) -> Stream:
    s = Stream()
    for raw in text.splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind, parent = event.get("type"), event.get("parent_tool_use_id")
        if kind == "system" and event.get("subtype") == "init":
            s.init_skills = [str(x) for x in event.get("skills") or []]
        elif kind == "result":
            s.answer = str(event.get("result") or "")
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                s.uses.append(
                    ToolUse(
                        str(block.get("id")),
                        str(block.get("name")),
                        dict(block.get("input") or {}),
                        parent,
                    )
                )
            elif block.get("type") == "tool_result":
                body = _text(block.get("content"))
                s.results[str(block.get("tool_use_id"))] = (bool(block.get("is_error")), body)
                s.texts.append((parent, body))
            elif block.get("type") == "text" and kind == "user":
                s.texts.append((parent, str(block.get("text", ""))))
    return s


def _verdict(ok: bool) -> str:
    return "pass" if ok else "fail"


def _loaded(s: Stream, parent: str | None) -> bool:
    for use in s.uses:
        if use.name == "Skill" and use.parent == parent and use.input.get("skill") == SKILL:
            error, _ = s.results.get(use.id, (True, ""))
            if not error:
                return True
    return False


def judge_main(s: Stream, canaries: dict[str, str]) -> dict[str, str]:
    if s.init_skills is None:
        skills = "failed"
    else:
        skills = _verdict(SKILL in s.init_skills)
    return {
        "skills-main": skills,
        "skill-load-main": _verdict(_loaded(s, None) and canaries["skill"] in s.answer),
        "claude-md-main": _verdict(canaries["claude_md"] in s.answer),
        "hook-overlay-main": _verdict(canaries["overlay"] in s.answer),
        "session-start-main": _verdict(canaries["session"] in s.answer),
    }


def _agent(s: Stream) -> ToolUse | None:
    return next((u for u in s.uses if u.name in ("Agent", "Task") and u.parent is None), None)


def judge_sub(s: Stream, canaries: dict[str, str]) -> dict[str, str]:
    """Judged on the subagent's returned message: the main session knows two canaries itself."""
    agent = _agent(s)
    if agent is None:
        return dict.fromkeys(SUB_RESULTS, "not-attempted")
    prompt = json.dumps(agent.input)
    main_skill = any(u.name == "Skill" and u.parent is None for u in s.uses)
    if main_skill or any(v in prompt for v in canaries.values()):
        return dict.fromkeys(SUB_RESULTS, "inconclusive")
    message = s.results.get(agent.id, (True, ""))[1]
    return {
        "skill-load-sub": _verdict(_loaded(s, agent.id) and canaries["skill"] in message),
        "claude-md-sub": _verdict(canaries["claude_md"] in message),
        "hook-overlay-sub": _verdict(canaries["overlay"] in message),
        "session-start-sub": _verdict(canaries["session"] in message),
    }


def base_directory(s: Stream, parent: str | None) -> str | None:
    for who, text in s.texts:
        found = re.search(r"Base directory for this skill: (\S+)", text)
        if who == parent and found:
            return found.group(1)
    return None


def judge_edit(workdir: Path, s: Stream, before: dict[str, dict[str, str]]) -> dict[str, str]:
    found = {}
    for case, (tool, rel) in EDIT_CASES.items():
        uses = [
            u for u in s.uses if u.name == tool and str(u.input.get("file_path", "")).endswith(rel)
        ]
        if not uses:
            found[case] = "not-attempted"
            continue
        if s.results.get(uses[-1].id, (True, ""))[0]:
            found[case] = "failed"
            continue
        link = workdir / EDIT_LINKS[case]
        target = Path(before[case]["target"])
        changed = not target.is_file() or target.read_text() != before[case]["content"]
        if link.is_symlink() and os.readlink(link) == before[case]["link"] and changed:
            found[case] = "through"
        elif not link.is_symlink() and not changed:
            found[case] = "replaced"
        else:
            found[case] = "failed"
    return found


# ---- inventories -----------------------------------------------------------


def _entry(base: Path, rel: str) -> dict[str, str]:
    path = base / rel
    if path.is_symlink():
        return {"path": rel, "type": "link", "target": os.readlink(path)}
    if path.is_dir():
        return {"path": rel, "type": "dir"}
    if path.is_file():
        return {"path": rel, "type": "file"}
    return {"path": rel, "type": "missing"}


def inventory(base: Path) -> list[dict[str, str]]:
    """The managed paths under a Claude home: CLAUDE.md, skill-overlays, hooks/*, skills/*."""
    rels = ["CLAUDE.md", "skill-overlays"]
    for sub in ("hooks", "skills"):
        if (base / sub).is_dir():
            rels += [f"{sub}/{p.name}" for p in (base / sub).iterdir()]
    return sorted((_entry(base, rel) for rel in rels), key=lambda e: e["path"])


def diff_inventory(before: list[dict[str, str]], after: list[dict[str, str]]) -> list[str]:
    old, new = {e["path"]: e for e in before}, {e["path"]: e for e in after}
    changes = []
    for path in sorted(old.keys() | new.keys()):
        if path not in old:
            changes.append(f"added: {path} ({new[path]['type']})")
        elif path not in new:
            changes.append(f"removed: {path}")
        elif old[path] != new[path]:
            changes.append(f"changed: {path}: {old[path]} -> {new[path]}")
    return changes


# ---- running ---------------------------------------------------------------


@contextmanager
def _removed(path: Path) -> Iterator[Path]:
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@contextmanager
def stub_home(root: Path) -> Iterator[Path]:
    """A directory with a stub gh (bin/gh) and an empty gh config (gh-config/)."""
    with _removed(Path(tempfile.mkdtemp(prefix="gh-home.", dir=root))) as home:
        (home / "bin").mkdir()
        (home / "bin" / "gh").write_text(STUB_GH)
        (home / "bin" / "gh").chmod(0o700)
        (home / "gh-config").mkdir()
        yield home


def isolated_env(cfg: Path, stub: Path, extra: dict[str, str]) -> dict[str, str]:
    inherited = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    return {
        **inherited,
        "PATH": f"{stub / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "GH_CONFIG_DIR": str(stub / "gh-config"),
        "GH_TOKEN": "",
        "GITHUB_TOKEN": "",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
        **extra,
        "CLAUDE_CONFIG_DIR": str(cfg),
    }


def _call(
    ctx: run_eval.Context, cmd: list[str], stdin: str, env: dict[str, str], cwd: Path | None = None
) -> tuple[int, str]:
    own = cwd is None
    where = Path(tempfile.mkdtemp(prefix="check-cwd.", dir=ctx.root)) if cwd is None else cwd
    try:
        return ctx.runner(cmd, stdin, env, where, run_eval.CLAUDE_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as e:
        return -1, f"<run error: {e}>"
    finally:
        if own:
            shutil.rmtree(where, ignore_errors=True)


@contextmanager
def _config(ctx: run_eval.Context, name: str) -> Iterator[Path]:
    cfg = run_eval.make_config_dir(ctx, name, "")
    try:
        yield cfg
    finally:
        run_eval.remove_config_dir(cfg)


def _evidence(s: Stream, sub: bool) -> list[str]:
    lines = [f"init skills: {s.init_skills}"]
    lines += [f"tool_use: {u.name} {json.dumps(u.input)[:300]} parent={u.parent}" for u in s.uses]
    agent = _agent(s)
    if sub and agent is not None:
        lines.append(f"subagent message: {s.results.get(agent.id, (True, ''))[1][:EXCERPT]}")
    lines.append(f"answer: {s.answer[:EXCERPT]}")
    return lines


@dataclass
class Tally:
    model_runs: int = 0
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    inventories: dict[str, dict[str, Any]] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)
    base: dict[str, str | None] = field(default_factory=dict)


def _model_attempt(
    ctx: run_eval.Context, tally: Tally, stub: Path, sha: str, run: str, attempt: int, plain: bool
) -> dict[str, str]:
    canaries = new_canaries()
    layout = "plain" if plain else "links"
    with _config(ctx, f"{run}{attempt}") as cfg:
        with _removed(build_layout(ctx.root, cfg, ctx.head_tree, canaries, sha, plain)):
            before = inventory(cfg)
            args = [
                "-p",
                "--output-format",
                "stream-json",
                "--verbose",
                "--tools",
                "Skill" if run == "M" else "Agent,Skill",
            ]
            env = isolated_env(cfg, stub, {"CLAUDE_HOME": str(cfg)})
            tally.model_runs += 1
            _, out = _call(ctx, ["claude", *args], MAIN_PROMPT if run == "M" else SUB_PROMPT, env)
            after = inventory(cfg)
    (ctx.out / f"{run}-{attempt}-{layout}.stream").write_text(out)
    key = f"{run}-{attempt}-{layout}"
    tally.inventories[key] = {"before": before, "after": after}
    if not plain:
        tally.changes += diff_inventory(before, after)
    s = parse_stream(out)
    if run == "M":
        found = judge_main(s, canaries)
        tally.base.setdefault("main", base_directory(s, None))
    else:
        found = judge_sub(s, canaries)
        agent = _agent(s)
        tally.base.setdefault("sub", base_directory(s, agent.id) if agent else None)
    for rid in found:
        ref = {
            "run": run,
            "attempt": attempt,
            "layout": layout,
            "evidence": _evidence(s, run == "S"),
        }
        tally.results.setdefault(rid, {"id": rid, "required": rid in REQUIRED, "runs": []})
        tally.results[rid]["runs"].append(ref)
    return found


def _judged_run(ctx: run_eval.Context, tally: Tally, stub: Path, sha: str, run: str) -> None:
    first = _model_attempt(ctx, tally, stub, sha, run, 1, False)
    outcome = dict(first)
    failing = [r for r, v in first.items() if v != "pass"]
    if failing:
        second = _model_attempt(ctx, tally, stub, sha, run, 2, False)
        failing = [r for r in failing if second[r] != "pass"]
        for r in outcome:
            if r not in failing:
                outcome[r] = "pass"
    if failing:
        plain = _model_attempt(ctx, tally, stub, sha, run, 3, True)
        for r in failing:
            outcome[r] = "fails-through-link" if plain[r] == "pass" else "inconclusive"
    for r, v in outcome.items():
        tally.results[r]["outcome"] = v


def _edit_run(ctx: run_eval.Context, tally: Tally, stub: Path, sha: str) -> None:
    with _config(ctx, "E") as cfg:
        with _removed(build_layout(ctx.root, cfg, ctx.head_tree, new_canaries(), sha)) as data:
            workdir, before = build_edit_dir(data)
            inv_before = inventory(cfg)
            env = isolated_env(cfg, stub, {"CLAUDE_HOME": str(cfg)})
            tally.model_runs += 1
            _, out = _call(ctx, ["claude", *edit_args(workdir)], EDIT_PROMPT, env, workdir)
            found = judge_edit(workdir, parse_stream(out), before)
            inv_after = inventory(cfg)
    (ctx.out / "E-1-links.stream").write_text(out)
    tally.inventories["E-1-links"] = {"before": inv_before, "after": inv_after}
    tally.changes += diff_inventory(inv_before, inv_after)
    s = parse_stream(out)
    for rid, outcome in found.items():
        tally.results[rid] = {
            "id": rid,
            "required": False,
            "outcome": outcome,
            "runs": [
                {"run": "E", "attempt": 1, "layout": "links", "evidence": _evidence(s, False)}
            ],
        }


def _sync_result(changes: list[str]) -> dict[str, Any]:
    clobbered = [c for c in changes if not c.startswith("added: skills/")]
    added = [c for c in changes if c.startswith("added: skills/")]
    if clobbered:
        outcome, note = "fail", "managed paths changed during a model run: " + "; ".join(clobbered)
    elif added:
        outcome, note = (
            "pass",
            "entries appeared under skills/ and the links stayed: " + "; ".join(sorted(set(added))),
        )
    else:
        outcome, note = "not-observed", "no entry appeared under skills/ during any headless run"
    return {"id": "sync-headless", "required": False, "outcome": outcome, "runs": [], "note": note}


# ---- the plugin check ------------------------------------------------------


def _write_market(market: Path) -> None:
    (market / ".claude-plugin").mkdir()
    (market / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": MARKET,
                "owner": {"name": "symlink-check"},
                "plugins": [
                    {
                        "name": PLUGIN,
                        "source": f"./{PLUGIN}",
                        "description": "A probe plugin for the symlink check.",
                    }
                ],
            },
            indent=2,
        )
    )
    plugin = market / PLUGIN
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": PLUGIN, "version": "0.0.1", "description": "A probe plugin."}, indent=2)
    )
    (plugin / "skills" / "probe-plugin-skill").mkdir(parents=True)
    (plugin / "skills" / "probe-plugin-skill" / "SKILL.md").write_text(
        "---\nname: probe-plugin-skill\ndescription: A probe plugin skill.\n---\n\nNothing here.\n"
    )


def _live_hashes(live: Path) -> dict[str, str | None]:
    return {
        rel: hashlib.sha256((live / rel).read_bytes()).hexdigest()
        if (live / rel).is_file()
        else None
        for rel in LIVE_WATCHED
    }


def _other_keys(settings_file: Path) -> dict[str, Any] | None:
    if settings_file.is_symlink() or not settings_file.is_file():
        return None
    data = json.loads(settings_file.read_text())
    return {k: v for k, v in data.items() if k not in PLUGIN_KEYS}


def run_plugin_check(ctx: run_eval.Context, sha: str) -> dict[str, Any]:
    """`claude plugin` against a local marketplace, in a config whose plugins/ is its own."""
    hashes = _live_hashes(ctx.live)
    commands: list[dict[str, Any]] = []
    with ExitStackDirs(ctx.root) as dirs, stub_home(ctx.root) as stub:
        cfg, home, market = (
            dirs.make("plugin-cfg."),
            dirs.make("plugin-home."),
            dirs.make("market."),
        )
        live_settings = ctx.live / "settings.json"
        settings = json.loads(live_settings.read_text()) if live_settings.is_file() else {}
        settings.pop("hooks", None)
        (cfg / "settings.json").write_text(json.dumps(settings, indent=2))
        (cfg / "plugins").mkdir()
        dirs.keep(build_layout(ctx.root, cfg, ctx.head_tree, new_canaries(), sha))
        _write_market(market)
        before, keys_before = inventory(cfg), _other_keys(cfg / "settings.json")
        env = isolated_env(cfg, stub, {"HOME": str(home)})
        steps = [
            ["marketplace", "add", str(market)],
            ["install", f"{PLUGIN}@{MARKET}"],
            ["uninstall", f"{PLUGIN}@{MARKET}"],
            ["marketplace", "remove", MARKET],
        ]
        refused = False
        for step in steps:
            code, out = _call(ctx, ["claude", "plugin", *step], "", env)
            commands.append(
                {"argv": ["claude", "plugin", *step], "exitCode": code, "output": out[:EXCERPT]}
            )
            if code != 0:
                refused = True
                break
        after, keys_after = inventory(cfg), _other_keys(cfg / "settings.json")
    changed = [rel for rel, h in _live_hashes(ctx.live).items() if hashes[rel] != h]
    if changed:
        raise LiveConfigChanged(f"the live {', '.join(changed)} changed during the plugin check")
    diffs = diff_inventory(before, after)
    if refused:
        outcome, note = "not-attempted", "a plugin command refused; see its output"
    elif diffs or keys_after is None or keys_after != keys_before:
        outcome = "fail"
        note = (
            "; ".join(diffs) or "settings.json lost its other keys or stopped being a regular file"
        )
    else:
        outcome, note = "pass", "the links and the other settings keys were unchanged"
    return {
        "outcome": outcome,
        "note": note,
        "commands": commands,
        "inventory": {"before": before, "after": after},
    }


class ExitStackDirs:
    """Temp directories under a root, all removed on exit."""

    def __init__(self, root: Path) -> None:
        self.root, self.paths = root, []  # type: Path, list[Path]

    def __enter__(self) -> ExitStackDirs:
        return self

    def make(self, prefix: str) -> Path:
        return self.keep(Path(tempfile.mkdtemp(prefix=prefix, dir=self.root)))

    def keep(self, path: Path) -> Path:
        self.paths.append(path)
        return path

    def __exit__(self, *exc: object) -> None:
        for path in self.paths:
            shutil.rmtree(path, ignore_errors=True)


# ---- the whole check -------------------------------------------------------


def run_check(ctx: run_eval.Context, sha: str) -> dict[str, Any]:
    ctx.out.mkdir(parents=True, exist_ok=True)
    tally = Tally()
    with stub_home(ctx.root) as stub:
        _judged_run(ctx, tally, stub, sha, "M")
        _judged_run(ctx, tally, stub, sha, "S")
        _edit_run(ctx, tally, stub, sha)
    plugin = run_plugin_check(ctx, sha)
    tally.inventories["plugin"] = plugin["inventory"]
    order = [*MAIN_RESULTS, *SUB_RESULTS, *EDIT_CASES]
    results = [tally.results[r] for r in order]
    results.append(_sync_result(tally.changes))
    results.append(
        {
            "id": "plugin-cli",
            "required": False,
            "outcome": plugin["outcome"],
            "runs": [],
            "note": plugin["note"],
        }
    )
    return {
        "claudeVersion": "unknown",
        "commit": sha,
        "date": datetime.date.today().isoformat(),
        "modelRuns": tally.model_runs,
        "results": results,
        "baseDirectory": {"main": tally.base.get("main"), "sub": tally.base.get("sub")},
        "inventories": tally.inventories,
        "pluginCommands": plugin["commands"],
    }


def exit_code(report: dict[str, Any]) -> int:
    results = report["results"]
    if any(r["outcome"] in STOP_OUTCOMES for r in results):
        return 2
    if any(r["required"] and r["outcome"] == "fails-through-link" for r in results):
        return 1
    return 0


# ---- writing the results ---------------------------------------------------


def scrub(value: Any, root: Path) -> Any:
    """Temp-root and home paths replaced, long strings cut; for the committed evidence."""
    if isinstance(value, dict):
        return {k: scrub(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, root) for v in value]
    if isinstance(value, str):
        text = value
        for r in {str(root), str(root.resolve())}:
            text = text.replace(r, "<tmp>")
        text = text.replace(str(Path.home()), "~")
        return text if len(text) <= EXCERPT else text[:EXCERPT] + " … [cut]"
    return value


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Symlink check results",
        "",
        f"Claude Code {data['claudeVersion']}, commit `{data['commit'][:12]}`, run on "
        f"{data['date']} with {data['modelRuns']} model runs. Written by "
        "`eval/symlink_check.py`; ADR-0001 explains what each result means for the deploy.",
        "",
        "| Result | Outcome | Required | Runs |",
        "|---|---|---|---|",
    ]
    for r in data["results"]:
        runs = ", ".join(f"{x['run']}{x['attempt']} {x['layout']}" for x in r["runs"]) or "none"
        lines.append(
            f"| `{r['id']}` | {r['outcome']} | {'yes' if r['required'] else 'no'} | {runs} |"
        )
    lines += ["", "## Notes", ""]
    lines += [f"- `{r['id']}`: {_cell(r['note'])}" for r in data["results"] if r.get("note")]
    base = data["baseDirectory"]
    lines += [
        "",
        "## Base directory shown to the model",
        "",
        f"- Main session: `{base['main']}`",
        f"- Subagent: `{base['sub']}`",
        "",
        "## Plugin commands",
        "",
    ]
    lines += [f"- `{' '.join(c['argv'])}`: exit {c['exitCode']}" for c in data["pluginCommands"]]
    lines += ["", "## Evidence", ""]
    for r in data["results"]:
        for x in r["runs"]:
            lines.append(f"### `{r['id']}`, run {x['run']}{x['attempt']} ({x['layout']})")
            lines += ["", "```text", *[e for e in x["evidence"]], "```", ""]
    return "\n".join(lines).rstrip() + "\n"


def write_results(report: dict[str, Any], out: Path, root: Path) -> None:
    data = scrub(report, root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "symlink-check.json").write_text(json.dumps(data, indent=2) + "\n")
    (out / "symlink-check.md").write_text(render_markdown(data))


# ---- the command line ------------------------------------------------------


def _summary(report: dict[str, Any]) -> str:
    rows = [f"{r['id']:<20} {r['outcome']}" for r in report["results"]]
    return "\n".join([f"model runs: {report['modelRuns']}", *rows])


def main(
    argv: list[str] | None = None,
    *,
    runner: run_eval.Runner = run_eval.default_runner,
    repo: Path | None = None,
    evidence: Path | None = None,
    out: Path | None = None,
    which: Callable[[str], str | None] | None = None,
) -> int:
    ap = argparse.ArgumentParser(description="Check that Claude Code follows symlinked config.")
    ap.add_argument("--inventory", type=Path, help="print DIR's managed paths as JSON; read-only")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.inventory is not None:
        if not args.inventory.is_dir():
            print(f"symlink_check: {args.inventory} is not a directory", file=sys.stderr)
            return 2
        print(json.dumps(inventory(args.inventory), indent=2))
        return 0
    try:
        top = repo or Path(er.git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        if is_dirty(top):
            raise run_eval.Refusal("the working tree has changes; commit or stash them first")
        if (which or shutil.which)("claude") is None:
            raise run_eval.Refusal("claude is not on PATH")
        live = run_eval.live_home()
        if not (live / ".credentials.json").is_file():
            raise run_eval.Refusal(f"no {live / '.credentials.json'}; log in to Claude Code first")
        root = run_eval.temp_root(top)
        head = er.resolve_commit(top, "HEAD")
    except (run_eval.Refusal, er.GitError) as e:
        print(f"symlink_check: {e}", file=sys.stderr)
        return 2
    trees = Path(tempfile.mkdtemp(prefix="check-trees.", dir=root))
    try:
        extract(top, head, trees / "head", ["config"])
        ctx = run_eval.Context(
            live=live,
            root=root,
            runner=runner,
            model=None,
            workers=1,
            out=out or top / "eval" / "out" / "symlink-check",
            head_tree=trees / "head",
            base_tree=None,
        )
        report = run_check(ctx, head)
    except LiveConfigChanged as e:
        print(f"symlink_check: {e}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(trees, ignore_errors=True)
    report["claudeVersion"] = claude_version()
    write_results(report, evidence or top / "docs" / "evidence", root)
    print(_summary(report))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
