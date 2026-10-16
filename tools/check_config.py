"""Checker for the layered config: CLAUDE.md + skill overlays + clause inventory.

python3 -I tools/check_config.py --claude-md <path> --overlays <dir> [--claude-home <dir>]
    [--skill-root <dir>]... [--clauses eval/clauses.json] [--lifecycle-skill <name>]
    [--repo-name <name>]... [--plugin-index plugins.lock.json] [--path-rule report|fail] [--json]
python3 -I tools/check_config.py --host hosts/<name>.json [--claude-home <dir>]
    [--clauses eval/clauses.json] [--repo-name <name>]... [--plugin-index plugins.lock.json]
    [--path-rule report|fail] [--json]
exit 0 = pass, 1 = findings, 2 = usage error

With --plugin-index, plugin and synced skills resolve from the plugin lock
instead of the plugin cache, so the check runs where no plugins are installed,
such as CI. tools/cfg/lock.py writes the lock.

With --host, the CLAUDE.md, overlays, repo skills and lifecycle skill come
from a host profile, and only the skills and plugins the profile lists
resolve. The checker also reports anything the profile lists that the repo
or the plugins don't have.

--path-rule lists every line under tools/, config/ and eval/ that holds a
home-directory or scratch path, which would break on another machine
(ADR-0008). In report mode the findings don't fail the run; in fail mode
they do. The rule is report-only until #4 removes the lifecycle skill's
paths.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_FILE = REPO_ROOT / "config" / "hooks" / "skill_overlay.py"
LOCK_VERSION = 1
SYNCED_PREFIX = "anthropic-skills:"
PATH_RULE_DIRS = ("tools", "config", "eval")
PATH_RULE_SKIP = {".venv", "__pycache__", ".pytest_cache"}
# Built from parts so this file doesn't trip the rule it defines.
PATH_RULE_NEEDLES = (b"/" + b"home" + b"/", b"dev" + b"/" + b"scratch")


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
Lock = dict[str, Any]


class UsageError(Exception):
    """An input the checker can't use. main() reports it and exits 2."""


@dataclass(frozen=True)
class Skill:
    """A resolved skill.

    Either a directory on disk, or (offline) a plugin skill known only from
    the lock, with its plugin id, its name and the files the lock recorded
    for it, if any.
    """

    path: Path | None = None
    files: frozenset[str] | None = None
    plugin: str | None = None
    name: str | None = None

    def has(self, rel: str) -> bool:
        if self.path is not None:
            return (self.path / rel).exists()
        return self.files is not None and rel in self.files


def estimate_tokens(text: str) -> int:
    """Same estimate as prompt_optimizer.py's default family (chars / 4)."""
    return int(len(text) / 4.0)


def norm(s: str) -> str:
    return " ".join(s.lower().split())


def is_skill_token(tok: str) -> bool:
    return bool(NAME.match(tok)) and (":" in tok or "-" in tok)


def resolve(name: str, home: Path, roots: list[Path]) -> Path | None:
    """Return the skill directory, or None if it isn't installed/staged."""
    if name.startswith(SYNCED_PREFIX):
        hits = list((home / "skills" / "synced").glob(f"*/{name.split(':', 1)[1]}/SKILL.md"))
        return hits[0].parent if hits else None
    try:
        found: Path = skill_dir(home, name)
        return found
    except OverlayError:
        pass
    if ":" not in name:
        found_on_disk = resolve_repo_skill(name, roots)
        if found_on_disk is not None:
            return found_on_disk
        hits = resolve_plugin(home, name)
        if len(hits) == 1:
            return hits[0]
    return None


def resolve_repo_skill(name: str, roots: list[Path]) -> Path | None:
    for root in roots:
        if (root / name / "SKILL.md").is_file():
            return root / name
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


def load_lock(path: Path) -> Lock:
    """Read and shape-check a plugin lock; raise UsageError if it can't be used."""
    try:
        lock = json.loads(path.read_text())
    except OSError as e:
        raise UsageError(f"cannot read plugin lock {path}: {e.strerror}") from e
    except ValueError as e:
        raise UsageError(f"plugin lock {path} is not valid JSON: {e}") from e
    if not isinstance(lock, dict):
        raise UsageError(f"plugin lock {path} is not a JSON object")
    if lock.get("lockVersion") != LOCK_VERSION:
        raise UsageError(f"plugin lock {path} has lockVersion {lock.get('lockVersion')!r}")
    plugins = lock.get("plugins")
    if not isinstance(plugins, dict):
        raise UsageError(f"plugin lock {path} has no plugins object")
    for pid, entry in plugins.items():
        if not (isinstance(entry, dict) and _str_list(entry.get("skills"))):
            raise UsageError(f"plugin lock {path}: {pid} has no skills list")
        if not isinstance(entry.get("files", {}), dict):
            raise UsageError(f"plugin lock {path}: {pid} has a files entry that isn't an object")
    if not _str_list(lock.get("synced")):
        raise UsageError(f"plugin lock {path} has no synced list")
    return lock


def _str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def resolve_offline(name: str, lock: Lock, home: Path, roots: list[Path]) -> Skill | None:
    """Resolve a skill name the way resolve() does, but plugin skills come from the lock."""
    plugins: dict[str, Any] = lock["plugins"]
    if name.startswith(SYNCED_PREFIX):
        return Skill() if name.split(":", 1)[1] in lock["synced"] else None
    if ":" in name:
        bundle, short = name.split(":", 1)
        for pid, entry in plugins.items():
            if pid.split("@", 1)[0] == bundle and short in entry["skills"]:
                return _lock_skill(pid, entry, short)
        return None
    user = home / "skills" / name
    if (user / "SKILL.md").is_file():
        return Skill(path=user)
    on_disk = resolve_repo_skill(name, roots)
    if on_disk is not None:
        return Skill(path=on_disk)
    hits = [(pid, entry) for pid, entry in plugins.items() if name in entry["skills"]]
    return _lock_skill(*hits[0], name) if len(hits) == 1 else None


def _lock_skill(pid: str, entry: dict[str, Any], short: str) -> Skill:
    files = entry.get("files", {}).get(short)
    return Skill(files=frozenset(files) if files is not None else None, plugin=pid, name=short)


@dataclass(frozen=True)
class Profile:
    """A host profile (hosts/<name>.json), with paths resolved against the repo root."""

    root: Path
    claude_md: Path
    overlays: list[str] | None  # None selects every overlay
    skills: list[str]
    hooks: list[str]
    plugins: list[str]
    default_lifecycle: str

    @property
    def overlay_dir(self) -> Path:
        return self.root / "config" / "skill-overlays"

    def skill_path(self, name: str) -> Path:
        return self.root / "config" / "skills" / name


def load_profile(path: Path) -> Profile:
    """Read and shape-check a host profile; raise UsageError if it can't be used."""
    try:
        data = json.loads(path.read_text())
    except OSError as e:
        raise UsageError(f"cannot read host profile {path}: {e.strerror}") from e
    except ValueError as e:
        raise UsageError(f"host profile {path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise UsageError(f"host profile {path} is not a JSON object")
    for key in ("claudeMd", "defaultLifecycle"):
        if not isinstance(data.get(key), str):
            raise UsageError(f"host profile {path} needs a string {key}")
    for key in ("skills", "hooks", "plugins"):
        if not _str_list(data.get(key)):
            raise UsageError(f"host profile {path} needs a list of strings in {key}")
    overlays = data.get("overlays")
    if overlays != "all" and not _str_list(overlays):
        raise UsageError(f'host profile {path} needs overlays to be "all" or a list of names')
    root = path.resolve().parent.parent
    claude_md = root / data["claudeMd"]
    if not claude_md.is_file():
        raise UsageError(f"host profile {path}: claudeMd {data['claudeMd']} not found")
    return Profile(
        root=root,
        claude_md=claude_md,
        overlays=None if overlays == "all" else overlays,
        skills=data["skills"],
        hooks=data["hooks"],
        plugins=data["plugins"],
        default_lifecycle=data["defaultLifecycle"],
    )


def installed_plugins(home: Path) -> dict[str, list[dict[str, Any]]]:
    """The plugins section of installed_plugins.json, or {} if it can't be read."""
    try:
        installed = json.loads((home / "plugins" / "installed_plugins.json").read_text())
        plugins = installed.get("plugins") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return plugins if isinstance(plugins, dict) else {}


@dataclass(frozen=True)
class Scope:
    """Where skill names resolve from: live or from a lock, and limited by a profile or not."""

    home: Path
    roots: list[Path]
    lock: Lock | None = None
    profile: Profile | None = None

    def find(self, name: str) -> Skill | None:
        if self.profile is not None:
            return self._find_for_host(name, self.profile)
        if self.lock is not None:
            return resolve_offline(name, self.lock, self.home, self.roots)
        found = resolve(name, self.home, self.roots)
        return Skill(path=found) if found is not None else None

    def _find_for_host(self, name: str, profile: Profile) -> Skill | None:
        if name.startswith(SYNCED_PREFIX):
            if self.lock is not None:
                return resolve_offline(name, self.lock, self.home, [])
            found = resolve(name, self.home, [])
            return Skill(path=found) if found is not None else None
        if ":" in name:
            bundle, short = name.split(":", 1)
            for pid in profile.plugins:
                if pid.split("@", 1)[0] == bundle:
                    hit = self._plugin_skill(pid, short)
                    if hit is not None:
                        return hit
            return None
        if name in profile.skills and (profile.skill_path(name) / "SKILL.md").is_file():
            return Skill(path=profile.skill_path(name))
        hits = [h for pid in profile.plugins if (h := self._plugin_skill(pid, name)) is not None]
        return hits[0] if len(hits) == 1 else None

    def _plugin_skill(self, pid: str, short: str) -> Skill | None:
        if self.lock is not None:
            entry = self.lock["plugins"].get(pid)
            return _lock_skill(pid, entry, short) if entry and short in entry["skills"] else None
        for inst in installed_plugins(self.home).get(pid) or []:
            d = Path(inst.get("installPath", "")) / "skills" / short
            if (d / "SKILL.md").is_file():
                return Skill(path=d)
        return None

    def has_plugin(self, pid: str) -> bool:
        if self.lock is not None:
            return pid in self.lock["plugins"]
        return bool(installed_plugins(self.home).get(pid))


def profile_problems(profile: Profile, scope: Scope) -> list[dict[str, str]]:
    problems = [
        {"kind": "skill", "item": s}
        for s in profile.skills
        if not (profile.skill_path(s) / "SKILL.md").is_file()
    ]
    problems += [
        {"kind": "hook", "item": h}
        for h in profile.hooks
        if not (profile.root / "config" / "hooks" / h).is_file()
    ]
    problems += [
        {"kind": "overlay", "item": o}
        for o in profile.overlays or []
        if not (profile.overlay_dir / f"{o}.md").is_file()
    ]
    if profile.default_lifecycle not in profile.skills:
        problems.append({"kind": "default-lifecycle", "item": profile.default_lifecycle})
    problems += [{"kind": "plugin", "item": p} for p in profile.plugins if not scope.has_plugin(p)]
    return problems


def read_overlays(ov_dir: Path, only: list[str] | None) -> dict[str, str]:
    if not ov_dir.is_dir():
        return {}
    files = sorted(ov_dir.glob("*.md"))
    if only is not None:
        files = [f for f in files if f.name[:-3] in only]
    return {f.name[:-3]: f.read_text() for f in files}


def scan_paths(root: Path) -> list[dict[str, Any]]:
    """Every line under the path-rule directories that holds a home or scratch path.

    Files are read as bytes, so binary files are scanned too; symlinks are
    skipped, so nothing outside the repo is read.
    """
    found: list[dict[str, Any]] = []
    for top in PATH_RULE_DIRS:
        for path in sorted((root / top).rglob("*")):
            rel = path.relative_to(root)
            if PATH_RULE_SKIP & set(rel.parts) or path.is_symlink() or not path.is_file():
                continue
            for number, line in enumerate(path.read_bytes().split(b"\n"), start=1):
                found += [
                    {"file": rel.as_posix(), "line": number, "match": needle.decode()}
                    for needle in PATH_RULE_NEEDLES
                    if needle in line
                ]
    return sorted(found, key=lambda f: (f["file"], f["line"], f["match"]))


def bundle_text(d: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in sorted(d.rglob("*.md")))


def check(
    args: argparse.Namespace, lock: Lock | None = None, profile: Profile | None = None
) -> Report:
    home, roots = Path(args.claude_home), [Path(r) for r in args.skill_root]
    scope = Scope(home=home, roots=roots, lock=lock, profile=profile)
    if profile is not None:
        md_path = profile.claude_md
        overlays = read_overlays(profile.overlay_dir, profile.overlays)
        lifecycle_name: str | None = profile.default_lifecycle
    else:
        md_path = Path(args.claude_md)
        overlays = read_overlays(Path(args.overlays), None)
        lifecycle_name = args.lifecycle_skill
    md = md_path.read_text()
    rep: Report = {
        "ok": True,
        "unresolvedSkills": [],
        "missingPaths": [],
        "clauseProblems": [],
        "forbiddenContent": [],
        "tokenCounts": [],
        "profileProblems": profile_problems(profile, scope) if profile is not None else [],
        "pathProblems": [],
    }
    if args.path_rule:
        rep["pathProblems"] = scan_paths(profile.root if profile is not None else REPO_ROOT)
    find = scope.find

    names = sorted({t for t in SKILL_TOKEN.findall(md) if is_skill_token(t)} | set(overlays))
    skills = {n: find(n) for n in names}
    rep["unresolvedSkills"] = [n for n in names if skills[n] is None]

    for name, body in overlays.items():
        skill = skills.get(name)
        if skill is not None:
            rep["missingPaths"] += [
                {"skill": name, "path": rel} for rel in REL_PATH.findall(body) if not skill.has(rel)
            ]
        rep["tokenCounts"].append(
            {"file": f"{name}.md", "tokens": estimate_tokens(body), "limit": LIMITS["overlay"]}
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
        lifecycle = find(lifecycle_name) if lifecycle_name else None
        lifecycle_text = bundle_text(lifecycle.path) if lifecycle and lifecycle.path else ""
        texts = {"claude-md": md, "lifecycle-skill": lifecycle_text}
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
        or rep["profileProblems"]
        or (args.path_rule == "fail" and rep["pathProblems"])
        or over
    )
    return rep


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", help="host profile to take the inputs from")
    ap.add_argument("--claude-md")
    ap.add_argument("--overlays")
    ap.add_argument("--claude-home", default=str(Path.home() / ".claude"))
    ap.add_argument("--skill-root", action="append", default=[])
    ap.add_argument("--clauses")
    ap.add_argument("--lifecycle-skill")
    ap.add_argument("--repo-name", action="append")
    ap.add_argument("--plugin-index", help="plugin lock to resolve plugin skills from, offline")
    ap.add_argument(
        "--path-rule",
        choices=["report", "fail"],
        help="scan tools/, config/ and eval/ for home and scratch paths; fail decides the run",
    )
    ap.add_argument("--json", action="store_true")
    return ap.parse_args(argv)


def validate(args: argparse.Namespace) -> tuple[Lock | None, Profile | None]:
    """Check the flags and load the lock and profile; raise UsageError on a problem."""
    if args.host:
        clash = [
            flag
            for flag, value in [
                ("--claude-md", args.claude_md),
                ("--overlays", args.overlays),
                ("--skill-root", args.skill_root),
                ("--lifecycle-skill", args.lifecycle_skill),
            ]
            if value
        ]
        if clash:
            raise UsageError(f"--host takes its inputs from the profile; drop {', '.join(clash)}")
    elif not (args.claude_md and args.overlays):
        raise UsageError("give --host, or both --claude-md and --overlays")
    for f in ([args.claude_md] if args.claude_md else []) + (
        [args.clauses] if args.clauses else []
    ):
        if not Path(f).is_file():
            raise UsageError(f"file not found: {f}")
    lock = load_lock(Path(args.plugin_index)) if args.plugin_index else None
    profile = load_profile(Path(args.host)) if args.host else None
    return lock, profile


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        lock, profile = validate(args)
    except UsageError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    rep = check(args, lock, profile)
    print(json.dumps(rep, indent=None if args.json else 2))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
