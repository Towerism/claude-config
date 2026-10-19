"""cfg, the repo's deploy tool.

    python3 -I tools/cfg/cli.py plan --host hosts/personal.json [--ref <commit>] [--json]
    python3 -I tools/cfg/cli.py status [--json]
    python3 -I tools/cfg/cli.py diff [<managed path> | settings.json] [--json]
    python3 -I tools/cfg/cli.py deploy --host hosts/personal.json [--ref <commit>]
        [--discard-live <path>]... [--yes] [--json]
    python3 -I tools/cfg/cli.py rollback [--json]
    python3 -I tools/cfg/cli.py capture [<path>...] [--into <worktree>] [--host <profile>]
    python3 -I tools/cfg/cli.py restore [<backup dir>]
    python3 -I tools/cfg/cli.py check|lock|eval [<arguments for that tool>]

plan shows what a deploy would do to the live config; status compares the live
config with the last deploy; diff shows the differences. None of the three
writes anything. deploy installs origin/main (or --ref) and stops on every
conflict until it is captured or passed with --discard-live; rollback switches
back to the previous release; capture writes live changes into a feature-branch
worktree; restore runs a backup's own restore.py. check, lock and eval pass
their arguments unchanged to tools/check_config.py, tools/cfg/lock.py and
tools/cfg/eval.py.

Every command takes --claude-home (default ~/.claude) and --repo (default:
the clone this file sits in). Deploy state lives in $XDG_DATA_HOME/claude-config,
or ~/.local/share/claude-config when XDG_DATA_HOME isn't an absolute path.
--target DIR uses DIR as the Claude home and DIR-cfg-data for the state, for a
trial deploy into a temporary config directory.

Exit codes: 0 done or nothing to report; 1 something to report, or a deploy that
stopped with nothing changed; 2 a usage error or a refusal, with the reason on
stderr and nothing changed; 3 a deploy that failed after the live config
started changing, with the restore.sh that undoes it named on stderr.
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

import subprocess  # noqa: E402

from tools.cfg import capture, deploy, drift, state  # noqa: E402
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
    common.add_argument("--claude-home", help="default: ~/.claude")
    common.add_argument("--target", help="a temporary Claude home; state goes in TARGET-cfg-data")
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
    dep = sub.add_parser("deploy", parents=[common], help="install a commit")
    dep.add_argument("--host", required=True, help="host profile, e.g. hosts/personal.json")
    dep.add_argument("--ref", help="commit to deploy (default: origin/main)")
    dep.add_argument(
        "--discard-live",
        action="append",
        default=[],
        metavar="PATH",
        help="let the repo's version of PATH win; the live one goes into the backup",
    )
    dep.add_argument("--yes", action="store_true", help="don't ask before changing anything")
    sub.add_parser("rollback", parents=[common], help="switch back to the previous release")
    cap = sub.add_parser("capture", parents=[common], help="write live changes to a branch")
    cap.add_argument("paths", nargs="*", help="managed paths or settings.json (default: all)")
    cap.add_argument("--into", help="a worktree on a feature branch (default: --repo)")
    cap.add_argument("--host", help="host profile, when nothing is deployed yet")
    res = sub.add_parser("restore", parents=[common], help="run a backup's restore.py")
    res.add_argument("backup", nargs="?", help="backup directory (default: the newest)")
    for name in FORWARDED:
        sub.add_parser(name, help=f"run {name} with these arguments")
    return parser.parse_args(argv)


def _home(raw: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(raw)))


def _where(args: argparse.Namespace) -> tuple[Path, Path]:
    """The Claude home and the data directory this command works on."""
    if args.target is not None:
        if args.claude_home is not None:
            raise CfgError("--target and --claude-home can't be given together")
        home = _home(args.target)
        return home, Path(f"{home}-cfg-data")
    return _home(args.claude_home or "~/.claude"), data_dir()


def _plan(args: argparse.Namespace) -> tuple[int, Any, str]:
    home, data = _where(args)
    result = drift.plan(Path(args.repo), args.ref, args.host, home, data)
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
    home, data = _where(args)
    result = drift.status(Path(args.repo), data, home)
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
    home, data = _where(args)
    diffs = drift.diff(Path(args.repo), data, home, args.path)
    payload = {"diffs": [{"path": p, "text": t} for p, t in diffs]}
    text = "\n".join(f"== {p}\n{t.rstrip(chr(10))}" for p, t in diffs) or "no differences"
    return (1 if diffs else 0), payload, text


def ask(lines: list[str]) -> bool:
    """Show the change list and ask on the terminal; end of input means no."""
    print("cfg deploy will:")
    print("\n".join(f"  {line}" for line in lines))
    try:
        answer = input("continue? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _deploy(args: argparse.Namespace) -> tuple[int, Any, str]:
    home, data = _where(args)
    confirm = ask if sys.stdin.isatty() and not args.json else None
    result = deploy.deploy(
        Path(args.repo),
        home,
        data,
        args.host,
        ref=args.ref,
        discard=tuple(args.discard_live),
        yes=args.yes,
        confirm=confirm,
    )
    payload = {
        "result": result.result,
        "sha": result.sha,
        "release": result.release,
        "conflicts": [
            {"kind": c.kind, "path": c.path, "detail": c.detail, "diff": c.diff}
            for c in result.conflicts
        ],
        "changes": result.changes,
        "linked": result.linked,
        "matched": result.matched,
        "discarded": result.discarded,
        "removed": result.removed,
        "settingsChanges": result.settings_changes,
        "backup": result.backup,
        "warnings": result.warnings,
    }
    lines = [f"deploy {result.sha[:12]}: {result.result}"]
    lines += [f"  warning: {w}" for w in result.warnings]
    for c in result.conflicts:
        where = c.path if c.kind == "path" else f"{c.path} ({c.detail})"
        lines += [
            f"== conflict: {where}",
            c.diff.rstrip("\n"),
            f"   resolve with: cfg capture {c.path}, or cfg deploy ... --discard-live {c.path}",
        ]
    if result.result == "needs-confirmation":
        lines.append("cfg deploy would:")
        lines += [f"  {line}" for line in result.changes]
        lines.append("nothing was changed; rerun with --yes to go ahead")
    if result.result == "deployed":
        lines += [f"  {line}" for line in result.changes]
        if result.discarded:
            lines.append(f"  discarded (kept in the backup): {', '.join(result.discarded)}")
        if result.backup:
            lines.append(f"backup: {result.backup}")
            undo = f"{result.backup}/restore.sh"
            lines.append(f"undo: {undo}" if result.first else f"undo: cfg rollback, or {undo}")
    code = 0 if result.result in ("deployed", "already-deployed") else 1
    return code, payload, "\n".join(lines)


def _rollback(args: argparse.Namespace) -> tuple[int, Any, str]:
    home, data = _where(args)
    result = deploy.rollback(Path(args.repo), home, data)
    payload = {
        "from": result.left,
        "to": result.to,
        "relinked": result.relinked,
        "removed": result.removed,
        "leftAlone": result.left_alone,
        "settingsDiffer": result.settings_differ,
        "command": result.command,
        "warnings": result.warnings,
    }
    lines = [f"rolled back from {result.left} to {result.to}"]
    lines += [f"  relinked {p}" for p in result.relinked]
    lines += [f"  removed cfg's link {p}" for p in result.removed]
    lines += [f"  left alone (not cfg's link): {p}" for p in result.left_alone]
    if result.command:
        lines.append(
            f"settings.json still has the newer release's values; to roll them back: "
            f"{result.command}"
        )
    lines += [f"  warning: {w}" for w in result.warnings]
    lines.append("to return: cfg deploy")
    return 0, payload, "\n".join(lines)


def _capture(args: argparse.Namespace) -> tuple[int, Any, str]:
    home, data = _where(args)
    into = Path(args.into or args.repo)
    result = capture.capture(Path(args.repo), home, data, args.paths, into, args.host)
    payload = {
        "into": result.into,
        "written": result.written,
        "deleted": result.deleted,
        "suites": result.suites,
    }
    if not result.written and not result.deleted:
        return 0, payload, "nothing to capture"
    lines = [f"captured into {result.into} (nothing committed):"]
    lines += [f"  wrote   {p}" for p in result.written]
    lines += [f"  deleted {p}" for p in result.deleted]
    if result.suites:
        lines.append(f"eval suites this change needs: {', '.join(result.suites)}")
    lines.append(
        "next: commit on this branch through the lifecycle flow. Once it merges, cfg deploy "
        "treats these paths as resolved; to deploy before then, pass --discard-live <path>."
    )
    return 0, payload, "\n".join(lines)


def _restore(args: argparse.Namespace) -> int:
    home, data = _where(args)
    if args.backup is not None:
        backup = Path(args.backup)
    else:
        st = state.load(data)
        if st is None or not st.backups:
            raise CfgError("no deploy backups are recorded; name the backup directory")
        backup = Path(st.backups[-1])
    script = backup / "restore.py"
    if not script.is_file():
        raise CfgError(f"{script} doesn't exist")
    return subprocess.run(["python3", "-I", str(script)], check=False).returncode


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
    commands = {
        "plan": _plan,
        "status": _status,
        "diff": _diff,
        "deploy": _deploy,
        "rollback": _rollback,
        "capture": _capture,
    }
    try:
        if args.command == "restore":
            return _restore(args)
        code, payload, text = commands[args.command](args)
    except deploy.DeployFailed as e:
        print(f"cfg: {e}", file=sys.stderr)
        return 3
    except (CfgError, OSError) as e:
        print(f"cfg: {e}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2) if args.json else text)
    return code


if __name__ == "__main__":
    sys.exit(main())
