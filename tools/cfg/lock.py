"""Write the plugin lock, plugins.lock.json, from the plugins installed on this machine.

python3 -I tools/cfg/lock.py [--claude-home <dir>] [--repo-root <dir>] [--out <file>]
exit 0 = written, 2 = an input can't be used (nothing is written)
python3 -I tools/cfg/lock.py --check [--claude-home <dir>] [--repo-root <dir>]
exit 0 = the committed lock and plugin-vendored files match this machine, 1 = they don't,
2 = an input can't be used. --check never writes.
--claude-home defaults to $CLAUDE_CONFIG_DIR when it is set and not empty, else ~/.claude.

The lock lets tools/check_config.py --plugin-index resolve plugin skills
where no plugins are installed, such as CI (ADR-0005). For each plugin
installed at user scope it records the id, marketplace, version, git commit
and skill names. For each plugin skill that a host profile's CLAUDE.md or
overlays refer to, it also records the skill's files. It holds no install
paths, timestamps or other machine-specific values, so the same plugins
always give the same bytes. It never records the skills claude.ai syncs
into skills/synced/: each account syncs its own, so two machines on
different accounts write the same lock.

--check builds the lock in memory and lists how the committed one differs.
It also compares each vendored file, as listed in tools/vendor/*/VENDORED.json,
config/skills/*/VENDORED.json and config/vendored-skills/*/VENDORED.json, with
the upstream copy it came from. A file
taken from an installed plugin that has changed fails the check (`vendored`).
A file taken from a marketplace clone is only reported (`upstream`), because
ADR-0010 treats that change as a prompt to review, not a failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import check_config  # noqa: E402

LOCK_NAME = "plugins.lock.json"
SKIPPED_PARTS = {"__pycache__", ".pytest_cache"}

Lock = dict[str, Any]


class LockInputError(Exception):
    """An input the lock can't be built from. main() reports it and exits 2."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except OSError as e:
        raise LockInputError(f"cannot read {path}: {e.strerror}") from e
    except ValueError as e:
        raise LockInputError(f"{path} is not valid JSON: {e}") from e


def user_install(pid: str, installs: Any) -> dict[str, Any] | None:
    """The plugin's one user-scope install, or None if it has none."""
    user = [i for i in installs or [] if isinstance(i, dict) and i.get("scope") == "user"]
    if len(user) > 1:
        raise LockInputError(f"{pid} has {len(user)} user-scope installs; expected one")
    return user[0] if user else None


def skill_names(install: Path) -> list[str]:
    skills = install / "skills"
    if not skills.is_dir():
        return []
    return sorted(d.name for d in skills.iterdir() if (d / "SKILL.md").is_file())


def skill_files(skill: Path) -> list[str]:
    """The skill's files, relative to its directory, without caches or bytecode."""
    return sorted(
        rel.as_posix()
        for rel in (p.relative_to(skill) for p in skill.rglob("*") if p.is_file())
        if not SKIPPED_PARTS & set(rel.parts) and rel.suffix != ".pyc"
    )


def synced_names(home: Path) -> list[str]:
    root = home / "skills" / "synced"
    return sorted({d.name for d in root.glob("*/*") if (d / "SKILL.md").is_file()})


def marketplace_fields(name: str, marketplaces: dict[str, Any]) -> dict[str, Any]:
    """The marketplace's GitHub repo, if it has one. A local path is never recorded."""
    source = (marketplaces.get(name) or {}).get("source") or {}
    kind = source.get("source")
    return {
        "marketplace": name,
        "marketplaceRepo": source.get("repo") if kind == "github" else None,
        "marketplaceSource": kind,
    }


def referenced_skills(repo_root: Path, home: Path, lock: Lock) -> set[tuple[str, str]]:
    """(plugin id, skill name) for every plugin skill a host profile refers to.

    Names resolve with the checker's own rules, against the lock being
    built, so the lock records files for exactly the skills the checker
    will look up.
    """
    found: set[tuple[str, str]] = set()
    for path in sorted((repo_root / "hosts").glob("*.json")):
        try:
            profile = check_config.load_profile(path)
        except check_config.UsageError as e:
            raise LockInputError(str(e)) from e
        md = profile.claude_md.read_text()
        overlays = check_config.read_overlays(profile.overlay_dir, profile.overlays)
        names = {t for t in check_config.SKILL_TOKEN.findall(md) if check_config.is_skill_token(t)}
        scope = check_config.Scope(home=home, roots=[], lock=lock, profile=profile)
        for name in sorted(names | set(overlays)):
            skill = scope.find(name)
            if skill is not None and skill.plugin is not None and skill.name is not None:
                found.add((skill.plugin, skill.name))
    return found


def build_lock(home: Path, repo_root: Path) -> Lock:
    installed = read_json(home / "plugins" / "installed_plugins.json")
    if not isinstance(installed, dict) or not isinstance(installed.get("plugins"), dict):
        raise LockInputError(f"{home / 'plugins' / 'installed_plugins.json'} has no plugins object")
    known = home / "plugins" / "known_marketplaces.json"
    marketplaces = read_json(known) if known.exists() else {}

    plugins: dict[str, Any] = {}
    installs: dict[str, Path] = {}
    for pid, entries in sorted(installed["plugins"].items()):
        inst = user_install(pid, entries)
        if inst is None:
            continue
        if not inst.get("installPath"):
            # Path("") would be the current directory, and the lock would describe it.
            raise LockInputError(f"{pid}: its user-scope install has no installPath")
        install = Path(inst["installPath"])
        if not install.is_dir():
            raise LockInputError(f"{pid}: install directory {install} doesn't exist")
        installs[pid] = install
        plugins[pid] = {
            **marketplace_fields(pid.split("@", 1)[1], marketplaces),
            "version": inst.get("version"),
            "gitCommitSha": inst.get("gitCommitSha"),
            "skills": skill_names(install),
            "files": {},
        }
    lock: Lock = {"lockVersion": check_config.LOCK_VERSION, "plugins": plugins}

    for pid, name in sorted(referenced_skills(repo_root, home, lock)):
        plugins[pid]["files"][name] = skill_files(installs[pid] / "skills" / name)
    return lock


def lock_differences(committed: Lock, current: Lock) -> list[str]:
    """One line per difference between the committed lock and this machine's."""
    diffs = []
    old, new = committed.get("plugins") or {}, current["plugins"]
    for pid in sorted(set(old) | set(new)):
        if pid not in new:
            diffs.append(f"{pid}: in the lock but not installed")
        elif pid not in old:
            diffs.append(f"{pid}: installed but not in the lock")
        else:
            diffs += [
                f"{pid}: {key} was {old[pid].get(key)!r}, now {new[pid].get(key)!r}"
                for key in sorted(set(old[pid]) | set(new[pid]))
                if old[pid].get(key) != new[pid].get(key)
            ]
    if committed.get("lockVersion") != current["lockVersion"]:
        diffs.append(
            f"lockVersion: was {committed.get('lockVersion')!r}, now {current['lockVersion']!r}"
        )
    return diffs


def vendored_records(repo_root: Path) -> list[Path]:
    """Every VENDORED.json the repo has: the tools' vendored files and each skill's."""
    found = sorted(repo_root.glob("tools/vendor/*/VENDORED.json"))
    for tree in check_config.SKILL_TREES:
        found += sorted(repo_root.glob(f"{tree}/*/VENDORED.json"))
    return found


def _entries(record_path: Path) -> list[dict[str, str]]:
    entries = read_json(record_path)
    sources = (("plugin", "installedPath"), ("marketplace", "marketplacePath"))

    def usable(e: Any) -> bool:
        return (
            isinstance(e, dict)
            and all(isinstance(e.get(f), str) for f in ("path", "sha256"))
            and any(all(isinstance(e.get(f), str) for f in pair) for pair in sources)
        )

    if not isinstance(entries, list) or not all(usable(e) for e in entries):
        raise LockInputError(
            f"{record_path} needs a list of entries with path, sha256, and either "
            "plugin and installedPath or marketplace and marketplacePath"
        )
    return entries


def _copy_problem(copy: Path, sha256: str, where: str) -> str | None:
    if not copy.is_file():
        return f"the {where} copy is missing"
    if hashlib.sha256(copy.read_bytes()).hexdigest() != sha256:
        return f"the {where} copy has a different sha256"
    return None


def vendored_problems(
    repo_root: Path, home: Path
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Vendored files whose upstream copy is missing or has changed.

    The first list covers files taken from an installed plugin and fails the check.
    The second covers files taken from a marketplace clone; it is only reported,
    because ADR-0010 treats an upstream change there as a prompt to review.
    """
    records = vendored_records(repo_root)
    installed: dict[str, Any] = {}
    if records:
        installed = read_json(home / "plugins" / "installed_plugins.json").get("plugins") or {}
    failing: list[dict[str, str]] = []
    upstream: list[dict[str, str]] = []
    for record_path in records:
        base = record_path.parent.relative_to(repo_root)
        for entry in _entries(record_path):
            path = str(base / entry["path"])
            if "marketplace" in entry and "marketplacePath" in entry:
                clone = home / "plugins" / "marketplaces" / entry["marketplace"]
                problem = _copy_problem(clone / entry["marketplacePath"], entry["sha256"],
                                        "marketplace")  # fmt: skip
                if problem:
                    upstream.append({"path": path, "problem": problem})
                continue
            inst = user_install(entry["plugin"], installed.get(entry["plugin"]))
            if inst is None:
                problem = f"plugin {entry['plugin']} isn't installed"
            else:
                copy = Path(inst.get("installPath", "")) / entry["installedPath"]
                problem = _copy_problem(copy, entry["sha256"], "installed")
            if problem:
                failing.append({"path": path, "problem": problem})
    return failing, upstream


def check(home: Path, repo_root: Path, lock_path: Path) -> dict[str, Any]:
    current = build_lock(home, repo_root)
    try:
        committed = check_config.load_lock(lock_path)
    except check_config.UsageError as e:
        lock_diffs = [f"{lock_path.name}: can't be used as a lock ({e})"]
    else:
        lock_diffs = lock_differences(committed, current)
    vendored, upstream = vendored_problems(repo_root, home)
    return {
        "ok": not (lock_diffs or vendored),
        "lock": lock_diffs,
        "vendored": vendored,
        "upstream": upstream,
    }


def render(lock: Lock) -> str:
    return json.dumps(lock, indent=2, sort_keys=True) + "\n"


def write_atomically(path: Path, text: str) -> None:
    """Write to a temp file beside the target, then rename it over the target."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Write the plugin lock.")
    ap.add_argument(
        "--claude-home",
        default=check_config.default_claude_home(),
        help="default: $CLAUDE_CONFIG_DIR, else ~/.claude",
    )
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--out", help=f"where to write the lock (default: <repo-root>/{LOCK_NAME})")
    ap.add_argument("--check", action="store_true", help="compare instead of writing; never writes")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    home, repo_root = Path(args.claude_home), Path(args.repo_root)
    out = Path(args.out) if args.out else repo_root / LOCK_NAME
    try:
        if args.check:
            report = check(home, repo_root, out)
            print(json.dumps(report))
            return 0 if report["ok"] else 1
        lock = build_lock(home, repo_root)
    except LockInputError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    write_atomically(out, render(lock))
    print(json.dumps({"written": str(out), "plugins": sorted(lock["plugins"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
