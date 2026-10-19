"""Install / restore the layered config (spec FR-23). Stdlib only.

install:  python3 -I install_v2.py install
restore:  python3 -I install_v2.py restore <backup-dir>   (normally via <backup-dir>/restore.sh)

Paths come from $HOME and, for tests, CMV2_BASELINE / CMV2_STAGING / CMV2_DELIVERABLE / CMV2_TS.
"""
import difflib
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

HOOK_FILES = ("skill_overlay.py", "merge_confirm.py")
OLD_SKILL = "issue-spec-driven-workflow"
NEW_SKILL = "github-issue-lifecycle"
KEEP = {"SPEC.md", "REVIEW-NOTE.md"}
SCRATCH = Path("/home/mfracker/dev/scratch/claude-md-v2")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def paths():
    home = Path(os.environ["HOME"])
    c = home / ".claude"
    return {
        "home": home, "c": c,
        "md": c / "CLAUDE.md", "settings": c / "settings.json",
        "old_src": c / "skills" / OLD_SKILL,
        "old_dst": home / ".old-claude" / "skills" / OLD_SKILL,
        "new_dst": c / "skills" / NEW_SKILL,
        "baseline": Path(os.environ.get("CMV2_BASELINE", "/home/mfracker/dev/scratch/claude-md-rewrite/baseline/CLAUDE.md.orig")),
        "staging": Path(os.environ.get("CMV2_STAGING", str(SCRATCH / "staging" / NEW_SKILL))),
        "deliv": Path(os.environ.get("CMV2_DELIVERABLE", str(SCRATCH / "impl" / "deliverable"))),
    }


def hook_entries(c: Path):
    cmd = lambda f: f"python3 -I {c / 'hooks' / f}"
    return {
        "PostToolUse": {"matcher": "Skill", "hooks": [{"type": "command", "command": cmd("skill_overlay.py")}]},
        "UserPromptExpansion": {"hooks": [{"type": "command", "command": cmd("skill_overlay.py")}]},
        "PreToolUse": {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd("merge_confirm.py")}]},
    }


def ours(command: str, c: Path) -> bool:
    return any(command == f"python3 -I {c / 'hooks' / f}" for f in HOOK_FILES)


def fail(msg: str, code: int = 3) -> int:
    print(f"install aborted: {msg}", file=sys.stderr)
    return code


def preflight(p) -> str:
    if not p["md"].is_file() or not p["baseline"].is_file() or sha(p["md"]) != sha(p["baseline"]):
        return "sha256 of live ~/.claude/CLAUDE.md differs from the baseline; rebuild eval/clauses.json and re-run AC-1/AC-6"
    if p["settings"].is_symlink():
        return "~/.claude/settings.json is a symlink; refusing to rewrite it"
    if p["settings"].exists():
        try:
            if not isinstance(json.loads(p["settings"].read_text()), dict):
                return "~/.claude/settings.json is not a JSON object"
        except (ValueError, OSError) as e:
            return f"~/.claude/settings.json does not parse ({e})"
    extra = sorted(x.name for x in p["new_dst"].iterdir() if x.name not in KEEP) if p["new_dst"].is_dir() else []
    if extra:
        return f"{p['new_dst']} contains files other than SPEC.md/REVIEW-NOTE.md: {extra}"
    if p["old_dst"].exists():
        return f"{p['old_dst']} already exists"
    if not (p["old_src"] / "SKILL.md").is_file():
        return f"{p['old_src']} is missing"
    if not (p["staging"] / "SKILL.md").is_file():
        return f"staged bundle missing at {p['staging']}"
    if any((p["staging"] / k).exists() for k in KEEP):
        return "staged bundle contains SPEC.md or REVIEW-NOTE.md"
    need = [p["deliv"] / "CLAUDE.md"] + [p["deliv"] / "hooks" / h for h in HOOK_FILES]
    if not all(f.is_file() for f in need) or not (p["deliv"] / "skill-overlays").is_dir():
        return f"deliverable incomplete at {p['deliv']}"
    clash = [p["c"] / "hooks" / h for h in HOOK_FILES] + [p["c"] / "skill-overlays"]
    if any(f.exists() for f in clash):
        return "~/.claude/hooks/<hook>.py or ~/.claude/skill-overlays already exists"
    return ""


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, then os.replace (FR-23 step 3)."""
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.environ.get("CMV2_FAIL_SETTINGS_RENAME"):
            raise OSError("injected failure before rename")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_manifest(path: Path, files, root: Path):
    path.write_text("".join(f"{sha(f)}  {f.relative_to(root)}\n" for f in files))


def install() -> int:
    """Preflight and back up; any unexpected error after the first write names restore.sh."""
    p = paths()
    problem = preflight(p)
    if problem:
        return fail(problem)
    ts = os.environ.get("CMV2_TS") or time.strftime("%Y%m%dT%H%M%S")
    b = p["c"] / "backups" / f"claude-md-v2-{ts}"
    try:
        return install_steps(p, b)
    except Exception as e:  # noqa: BLE001 — must never leave the user without the undo path
        print(f"install failed unexpectedly ({type(e).__name__}: {e}). "
              f"Run {b / 'restore.sh'} to undo what was written.", file=sys.stderr)
        return 4


def install_steps(p, b: Path) -> int:
    c = p["c"]
    b.mkdir(parents=True)
    # 2. back up
    shutil.copy2(p["md"], b / "CLAUDE.md")
    if p["settings"].exists():
        shutil.copy2(p["settings"], b / "settings.json")
    shutil.copy2(Path(__file__), b / "install_v2.py")
    (b / "restore.sh").write_text('#!/usr/bin/env bash\nexec python3 -I "$(dirname "$0")/install_v2.py" restore "$(dirname "$0")"\n')
    (b / "restore.sh").chmod(0o755)
    # 3. deliverable + hooks
    written = []
    (c / "hooks").mkdir(exist_ok=True)
    for h in HOOK_FILES:
        shutil.copy2(p["deliv"] / "hooks" / h, c / "hooks" / h)
        written.append(c / "hooks" / h)
        if os.environ.get("CMV2_FAIL_AFTER_FIRST_WRITE"):
            raise RuntimeError("injected failure after first write")
    shutil.copytree(p["deliv"] / "skill-overlays", c / "skill-overlays")
    written += sorted(f for f in (c / "skill-overlays").rglob("*") if f.is_file())
    shutil.copy2(p["deliv"] / "CLAUDE.md", p["md"])
    written.append(p["md"])
    (b / "installed-CLAUDE.md.sha256").write_text(sha(p["md"]) + "\n")
    write_manifest(b / "deliverable-manifest.sha256", written, c)
    settings = json.loads(p["settings"].read_text()) if p["settings"].exists() else {}
    for event, entry in hook_entries(c).items():
        settings.setdefault("hooks", {}).setdefault(event, []).append(entry)
    try:
        write_atomic(p["settings"], (json.dumps(settings, indent=2) + "\n").encode())
    except OSError as e:
        print(f"install failed writing settings.json ({e}); settings.json is unchanged. "
              f"Run {b / 'restore.sh'} to undo the files already written.", file=sys.stderr)
        return 4
    # 4. swap skills: move old out first, then copy the bundle in
    p["old_dst"].parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p["old_src"]), str(p["old_dst"]))
    copied = []
    fail_after = int(os.environ.get("CMV2_FAIL_COPY_AFTER", "-1"))
    try:
        for src in sorted(f for f in p["staging"].rglob("*") if f.is_file()):
            if len(copied) == fail_after:
                raise OSError("injected copy failure")
            dst = p["new_dst"] / src.relative_to(p["staging"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst)
    except OSError as e:
        for f in copied:
            f.unlink(missing_ok=True)
        prune(p["new_dst"])
        shutil.move(str(p["old_dst"]), str(p["old_src"]))
        prune(p["old_dst"].parent.parent)
        print(f"install failed copying the bundle ({e}); old skill restored. "
              f"Run {b / 'restore.sh'} to undo the rest.", file=sys.stderr)
        return 4
    write_manifest(b / "bundle-manifest.sha256", copied, c)
    print(f"installed. backup: {b}\nundo: {b / 'restore.sh'}")
    return 0


def prune(d: Path):
    """Remove empty directories under and including d (never non-empty ones)."""
    if not d.is_dir():
        return
    for sub in sorted((x for x in d.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
        if not any(sub.iterdir()):
            sub.rmdir()
    if not any(d.iterdir()):
        d.rmdir()


def undo_manifest(manifest: Path, c: Path, snap: Path, keep_dirs=(), skip=()):
    if not manifest.exists():
        return
    for line in manifest.read_text().splitlines():
        digest, rel = line.split("  ", 1)
        if rel in skip:
            continue
        f = c / rel
        if not f.exists():
            continue
        if sha(f) == digest:
            f.unlink()
        else:
            dst = snap / "changed" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
        parent = f.parent
        while parent != c and parent.is_dir() and not any(parent.iterdir()) and parent not in keep_dirs:
            parent.rmdir()
            parent = parent.parent


def restore(b: Path) -> int:
    p = paths()
    c = p["c"]
    problems = []
    snap = b / f"restore-{time.strftime('%Y%m%dT%H%M%S')}"
    snap.mkdir()
    for f in ("CLAUDE.md", "settings.json"):
        if (c / f).exists():
            shutil.copy2(c / f, snap / f)
    if (c / "skills").exists():
        shutil.copytree(c / "skills", snap / "skills", symlinks=True)
    # settings: remove only our hook entries
    if p["settings"].exists():
        s = json.loads(p["settings"].read_text())
        hooks = s.get("hooks", {})
        for event in list(hooks):
            kept = []
            for m in hooks[event]:
                m["hooks"] = [h for h in m.get("hooks", []) if not ours(h.get("command", ""), c)]
                if m["hooks"]:
                    kept.append(m)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        if "hooks" in s and not s["hooks"]:
            del s["hooks"]
        orig = b / "settings.json"
        data = orig.read_bytes() if orig.exists() and json.loads(orig.read_text()) == s \
            else (json.dumps(s, indent=2) + "\n").encode()
        try:
            write_atomic(p["settings"], data)
        except OSError as e:
            print(f"restore: writing settings.json failed ({e}); nothing restored, settings.json unchanged. "
                  f"Snapshot: {snap}", file=sys.stderr)
            return 6
    # skills: bundle files by manifest, then move the old skill back
    undo_manifest(b / "bundle-manifest.sha256", c, snap, keep_dirs=(p["new_dst"],))
    if p["old_dst"].exists():
        if p["old_src"].exists():
            problems.append(f"{p['old_src']} already exists; old skill left in {p['old_dst']}")
        else:
            shutil.move(str(p["old_dst"]), str(p["old_src"]))
            for d in (p["old_dst"].parent, p["old_dst"].parent.parent):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
    # deliverable files (hooks, overlays) by manifest
    undo_manifest(b / "deliverable-manifest.sha256", c, snap, skip=("CLAUDE.md",))  # CLAUDE.md: rule below
    # CLAUDE.md last: only if unchanged since install
    installed = (b / "installed-CLAUDE.md.sha256").read_text().strip()
    if sha(p["md"]) == installed:
        shutil.copy2(b / "CLAUDE.md", p["md"])
    else:
        diff = difflib.unified_diff((b / "CLAUDE.md").read_text().splitlines(), p["md"].read_text().splitlines(),
                                    "backup/CLAUDE.md", "~/.claude/CLAUDE.md (edited)", lineterm="")
        problems.append("CLAUDE.md was edited after install; not overwritten. Diff vs the pre-install file:\n"
                        + "\n".join(diff))
    for msg in problems:
        print(f"restore: {msg}", file=sys.stderr)
    print(f"restore snapshot: {snap}")
    return 5 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "install":
        sys.exit(install())
    if len(sys.argv) >= 3 and sys.argv[1] == "restore":
        sys.exit(restore(Path(sys.argv[2])))
    print(__doc__, file=sys.stderr)
    sys.exit(2)
