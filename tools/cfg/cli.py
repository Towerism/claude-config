"""cfg, the repo's deploy tool. This issue (#6) adds the read-only commands.

    python3 -I tools/cfg/cli.py plan --host hosts/personal.json [--ref <commit>] [--json]
    python3 -I tools/cfg/cli.py status [--json]
    python3 -I tools/cfg/cli.py diff [<managed path> | settings.json] [--json]
    python3 -I tools/cfg/cli.py check|lock|eval [<arguments for that tool>]

plan shows what a deploy would do to the live config; status compares the live
config with the last deploy; diff shows the differences. None of them writes
anything. check, lock and eval pass their arguments unchanged to
tools/check_config.py, tools/cfg/lock.py and tools/cfg/eval.py. deploy,
rollback, capture and restore arrive with #8.

Every command takes --claude-home (default ~/.claude) and --repo (default:
the clone this file sits in). Deploy state lives in $XDG_DATA_HOME/claude-config,
or ~/.local/share/claude-config when XDG_DATA_HOME isn't an absolute path.

Exit codes: 0 nothing to report; 1 something to report (differences, drift,
not deployed); 2 a usage error or a refusal, with the reason on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Run as a script with python3 -I, the repo root isn't on sys.path. Tests import the
# module as a package, so this line only runs in a subprocess, where coverage isn't measured.
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.cfg import drift  # noqa: E402
from tools.cfg.errors import CfgError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FORWARDED = ("check", "lock", "eval")


def data_dir() -> Path:
    """Where releases and deploy state live; absolute, with symlinks left as written."""
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local" / "share"
    return Path(os.path.abspath(base / "claude-config"))


def _parse(argv: list[str]) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--claude-home", default="~/.claude", help="default: ~/.claude")
    common.add_argument("--repo", default=str(REPO_ROOT), help="default: this clone")
    common.add_argument("--json", action="store_true", help="print JSON")
    parser = argparse.ArgumentParser(prog="cfg", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", parents=[common], help="what a deploy would do")
    plan.add_argument("--host", required=True, help="host profile, e.g. hosts/personal.json")
    plan.add_argument("--ref", help="commit to plan for (default: origin/main)")
    sub.add_parser("status", parents=[common], help="compare with the last deploy")
    diff = sub.add_parser("diff", parents=[common], help="show live changes")
    diff.add_argument("path", nargs="?", help="one managed path, or settings.json")
    for name in FORWARDED:
        sub.add_parser(name, help=f"run {name} with these arguments")
    return parser.parse_args(argv)


def _home(raw: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(raw)))


def _plan(args: argparse.Namespace) -> tuple[int, Any, str]:
    result = drift.plan(Path(args.repo), args.ref, args.host, _home(args.claude_home), data_dir())
    payload = {
        "ref": result.ref,
        "paths": [{"live": p.live, "state": p.state} for p in result.paths],
        "settings": [{"key": i.key, "state": i.state} for i in result.settings],
        "wouldBackUp": result.would_back_up,
        "wouldStop": result.would_stop,
        "diffs": [{"path": p, "text": t} for p, t in result.diffs],
    }
    changes = [p for p in result.paths if p.state != "linked"]
    changes_settings = [i for i in result.settings if i.state != "same"]
    lines = [f"plan for {args.host} at {result.ref[:12]}"]
    lines += [f"  {p.live:<40} {p.state}" for p in result.paths]
    lines.append("settings:")
    lines += [f"  {i.key:<40} {i.state}" for i in result.settings]
    if result.would_back_up:
        lines.append(f"a deploy would back up: {', '.join(result.would_back_up)}")
    if result.would_stop:
        lines.append(
            "a deploy would stop until each difference is captured or discarded (ADR-0004)"
        )
    if not changes and not changes_settings:
        lines.append("nothing to change")
    for path, text in result.diffs:
        lines += [f"== {path}", text.rstrip("\n")]
    return (1 if changes or changes_settings else 0), payload, "\n".join(lines)


def _finding(f: drift.Finding) -> dict[str, Any]:
    return {"kind": f.kind, "path": f.path, "detail": f.detail}


def _status(args: argparse.Namespace) -> tuple[int, Any, str]:
    result = drift.status(Path(args.repo), data_dir(), _home(args.claude_home))
    payload = {
        "result": result.result,
        "sha": result.sha,
        "release": result.release,
        "findings": [_finding(f) for f in result.findings],
        "warnings": [_finding(f) for f in result.warnings],
    }
    where = f" (release {result.release})" if result.release else ""
    lines = [f"status: {result.result}{where}"]
    for f in result.findings:
        lines.append(f"  {f.kind}  {f.path}: {f.detail}" if f.path else f"  {f.kind}: {f.detail}")
    lines += [f"  warning {w.kind}: {w.detail}" for w in result.warnings]
    return (0 if result.result == "clean" else 1), payload, "\n".join(lines)


def _diff(args: argparse.Namespace) -> tuple[int, Any, str]:
    diffs = drift.diff(Path(args.repo), data_dir(), _home(args.claude_home), args.path)
    payload = {"diffs": [{"path": p, "text": t} for p, t in diffs]}
    text = "\n".join(f"== {p}\n{t.rstrip(chr(10))}" for p, t in diffs) or "no differences"
    return (1 if diffs else 0), payload, text


def _forward(name: str, rest: list[str]) -> int:
    if name == "check":
        from tools import check_config

        return check_config.main(rest)
    if name == "lock":
        from tools.cfg import lock

        return lock.main(rest)
    from tools.cfg import eval as cfg_eval

    return cfg_eval.main(rest)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in FORWARDED:
        return _forward(argv[0], argv[1:])
    args = _parse(argv)
    commands = {"plan": _plan, "status": _status, "diff": _diff}
    try:
        code, payload, text = commands[args.command](args)
    except CfgError as e:
        print(f"cfg: {e}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2) if args.json else text)
    return code


if __name__ == "__main__":
    sys.exit(main())
