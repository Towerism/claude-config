"""Checker for the layered config: CLAUDE.md + skill overlays + clause inventory.

python3 -I tools/check_config.py --claude-md <path> --overlays <dir> [--claude-home <dir>]
    [--skill-root <dir>]... [--clauses eval/clauses.json] [--lifecycle-skill <name>]
    [--repo-name <name>]... [--json]
exit 0 = pass, 1 = findings, 2 = usage error
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_FILE = REPO_ROOT / "config" / "hooks" / "skill_overlay.py"


def _load_hook() -> ModuleType:
    """Load the overlay hook by path: it defines skill names and reference paths."""
    spec = importlib.util.spec_from_file_location("skill_overlay", HOOK_FILE)
    if spec is None or spec.loader is None:  # pragma: no cover - the hook is in the repo
        raise ImportError(f"cannot load {HOOK_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_hook = _load_hook()
NAME: re.Pattern[str] = _hook.NAME
REL_PATH: re.Pattern[str] = _hook.REL_PATH
OverlayError: type[Exception] = _hook.OverlayError
skill_dir: Any = _hook.skill_dir

SKILL_TOKEN = re.compile(r"`([a-z0-9][a-z0-9-]*(?::[a-z0-9-]+)?)`")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
TOKEN = re.compile(r"(ghp_|gho_|github_pat_|sk-|xox[bp]-)[A-Za-z0-9_-]{16,}")
EMPHASIS = re.compile(r"\b(IMPORTANT|YOU MUST|NEVER|ALWAYS)\b")
DEFAULT_REPOS = ["play-pokemon-attendance", "discord-pokemon-tournament-genie", "selfhost-kit"]
LIMITS = {"claude-md": 1800, "claude-md-lines": 200, "overlay": 700}

Report = dict[str, Any]


def estimate_tokens(text: str) -> int:
    """Same estimate as prompt_optimizer.py's default family (chars / 4)."""
    return int(len(text) / 4.0)


def norm(s: str) -> str:
    return " ".join(s.lower().split())


def is_skill_token(tok: str) -> bool:
    return bool(NAME.match(tok)) and (":" in tok or "-" in tok)


def resolve(name: str, home: Path, roots: list[Path]) -> Path | None:
    """Return the skill directory, or None if it isn't installed/staged."""
    if name.startswith("anthropic-skills:"):
        hits = list((home / "skills" / "synced").glob(f"*/{name.split(':', 1)[1]}/SKILL.md"))
        return hits[0].parent if hits else None
    try:
        found: Path = skill_dir(home, name)
        return found
    except OverlayError:
        pass
    if ":" not in name:
        for root in roots:
            if (root / name / "SKILL.md").is_file():
                return root / name
        hits = resolve_plugin(home, name)
        if len(hits) == 1:
            return hits[0]
    return None


def resolve_plugin(home: Path, short: str) -> list[Path]:
    """Active plugin skill dirs named <short> in any bundle.

    The harness accepts a short name when exactly one bundle has it.
    """
    try:
        installed = json.loads((home / "plugins" / "installed_plugins.json").read_text())
    except (OSError, ValueError):
        return []
    found = []
    for installs in (installed.get("plugins") or {}).values():
        for inst in installs:
            d = Path(inst.get("installPath", "")) / "skills" / short
            if (d / "SKILL.md").is_file():
                found.append(d)
    return found


def bundle_text(d: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in sorted(d.rglob("*.md")))


def check(args: argparse.Namespace) -> Report:
    home, roots = Path(args.claude_home), [Path(r) for r in args.skill_root]
    md_path, ov_dir = Path(args.claude_md), Path(args.overlays)
    md = md_path.read_text()
    overlays = (
        {p.name[:-3]: p.read_text() for p in sorted(ov_dir.glob("*.md"))} if ov_dir.is_dir() else {}
    )
    rep: Report = {
        "ok": True,
        "unresolvedSkills": [],
        "missingPaths": [],
        "clauseProblems": [],
        "forbiddenContent": [],
        "tokenCounts": [],
    }

    names = sorted({t for t in SKILL_TOKEN.findall(md) if is_skill_token(t)} | set(overlays))
    dirs = {n: resolve(n, home, roots) for n in names}
    rep["unresolvedSkills"] = [n for n in names if dirs[n] is None]

    for skill, body in overlays.items():
        base = dirs.get(skill)
        if base is not None:
            rep["missingPaths"] += [
                {"skill": skill, "path": rel}
                for rel in REL_PATH.findall(body)
                if not (base / rel).exists()
            ]
        rep["tokenCounts"].append(
            {"file": f"{skill}.md", "tokens": estimate_tokens(body), "limit": LIMITS["overlay"]}
        )

    rep["tokenCounts"].insert(
        0, {"file": str(md_path), "tokens": estimate_tokens(md), "limit": LIMITS["claude-md"]}
    )
    rep["tokenCounts"].insert(
        1,
        {
            "file": f"{md_path} (lines)",
            "tokens": len(md.splitlines()),
            "limit": LIMITS["claude-md-lines"],
        },
    )

    targets = [("claude-md", md)] + [(f"overlay:{s}", b) for s, b in overlays.items()]
    for kind, text in targets:
        for name in args.repo_name or DEFAULT_REPOS:
            if name in text:
                rep["forbiddenContent"].append({"kind": "repo-name", "match": f"{kind}: {name}"})
        rep["forbiddenContent"] += [
            {"kind": "email", "match": f"{kind}: {m.group(0)}"} for m in EMAIL.finditer(text)
        ]
        rep["forbiddenContent"] += [
            {"kind": "token", "match": f"{kind}: {m.group(0)[:8]}…"} for m in TOKEN.finditer(text)
        ]
    emphasised = [line for line in md.splitlines() if EMPHASIS.search(line)]
    if len(emphasised) != 1 or "merge" not in emphasised[0].lower():
        rep["forbiddenContent"].append(
            {"kind": "caps", "match": f"{len(emphasised)} emphasised line(s): {emphasised[:3]}"}
        )

    if args.clauses:
        lifecycle = resolve(args.lifecycle_skill, home, roots) if args.lifecycle_skill else None
        texts = {"claude-md": md, "lifecycle-skill": bundle_text(lifecycle) if lifecycle else ""}
        texts.update({f"overlay:{s}": b for s, b in overlays.items()})
        seen = set()
        for row in json.loads(Path(args.clauses).read_text()):
            cid, disp = row.get("id"), row.get("disposition")
            if cid in seen:
                rep["clauseProblems"].append({"id": cid, "problem": "duplicate"})
                continue
            seen.add(cid)
            if not disp:
                rep["clauseProblems"].append({"id": cid, "problem": "no-disposition"})
            elif disp == "dropped" or disp.startswith("superseded-by:"):
                if not (row.get("reason") or "").strip():
                    rep["clauseProblems"].append({"id": cid, "problem": "no-reason"})
            elif not row.get("mustAppear") or norm(row["mustAppear"]) not in norm(
                texts.get(disp, "")
            ):
                rep["clauseProblems"].append({"id": cid, "problem": "phrase-missing"})

    over = [t for t in rep["tokenCounts"] if t["tokens"] > t["limit"]]
    rep["ok"] = not (
        rep["unresolvedSkills"]
        or rep["missingPaths"]
        or rep["clauseProblems"]
        or rep["forbiddenContent"]
        or over
    )
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude-md", required=True)
    ap.add_argument("--overlays", required=True)
    ap.add_argument("--claude-home", default=str(Path.home() / ".claude"))
    ap.add_argument("--skill-root", action="append", default=[])
    ap.add_argument("--clauses")
    ap.add_argument("--lifecycle-skill")
    ap.add_argument("--repo-name", action="append")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    for f in [args.claude_md] + ([args.clauses] if args.clauses else []):
        if not Path(f).is_file():
            print(json.dumps({"error": f"file not found: {f}"}))
            return 2
    rep = check(args)
    print(json.dumps(rep, indent=None if args.json else 2))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
