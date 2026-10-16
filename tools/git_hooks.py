"""Git hooks for this repo: keep secrets, email addresses and private terms out of commits.

The wrappers in .githooks/ call this file as
``python3 -I <repo root>/tools/git_hooks.py <hook> [args]``. Standard library only.

Hooks:
  pre-commit, pre-merge-commit  identity, tokens, emails, deny-list, excluded terms, gitleaks
  commit-msg <file>             tokens, emails and excluded terms in the message
  pre-push <remote> <url>       refuses pushes to main and commits with a non-noreply identity
  check-range <base>            all of the above over the tree at HEAD and every commit in
                                <base>..HEAD, for commits that ran no hooks (rebase,
                                cherry-pick, --no-verify)

Exit codes: 0 pass (warnings allowed), 1 refused, 2 usage error.
Findings never print a matched token, email or term, and a path containing a term is redacted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TextIO

NOREPLY_RE = re.compile(
    r"(\d+\+)?[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?@users\.noreply\.github\.com"
)

# The left boundary stops words such as "task-management-..." matching the sk- pattern.
_LEFT = r"(?<![A-Za-z0-9_-])"
TOKEN_RE = re.compile(
    _LEFT
    + r"(?:ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}"
    + r"|sk-[A-Za-z0-9_-]{20,}|xox[bp]-[A-Za-z0-9-]{10,})"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ALLOWED_EMAILS = frozenset({"noreply@anthropic.com"})
ALLOWED_EMAIL_SUFFIXES = ("@users.noreply.github.com",)
# Reserved for documentation by RFC 2606, so they can't be anyone's real address.
ALLOWED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net"})

# The never-versioned parts of ~/.claude, plus common secret files.
DENY_BASENAMES = (
    ".credentials.json",
    "*.jsonl",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    ".claude.json",
)
DENY_TOP_FILES = (
    "history*",
    "daemon.*",
    "*.log",
    "gh-pr-status-cache.json",
    ".last-cleanup",
    "settings.json",
)
DENY_TOP_DIRS = (
    "projects",
    "sessions",
    "session-env",
    "jobs",
    "tasks",
    "todos",
    "shell-snapshots",
    "file-history",
    "paste-cache",
    "cache",
    "backups",
    "state",
    "statsig",
    "ide",
    "chrome",
    "daemon",
    "feedback",
    "plugins/cache",
    "plugins/marketplaces",
    "skills/synced",
    "skills/.trash",
)
DENY_ROOT_PREFIXES = ("eval/out/",)

HOOKS = ("pre-commit", "pre-merge-commit", "commit-msg", "pre-push", "check-range")
GITLEAKS_MISSING = "WARNING: gitleaks not found; only the built-in regex scan ran"


class UsageError(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    hook: str
    check: str
    path: str
    line: int | None = None


@dataclass(frozen=True)
class PushRef:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


# ---------------------------------------------------------------- pure checks


def is_noreply(email: str) -> bool:
    return NOREPLY_RE.fullmatch(email) is not None


def ident_email(ident: str) -> str:
    start, end = ident.rfind("<"), ident.rfind(">")
    return ident[start + 1 : end] if 0 <= start < end else ""


def email_allowed(address: str) -> bool:
    lowered = address.lower()
    if lowered in ALLOWED_EMAILS or lowered.endswith(ALLOWED_EMAIL_SUFFIXES):
        return True
    return lowered.rpartition("@")[2] in ALLOWED_EMAIL_DOMAINS


def scan_text(text: str) -> list[tuple[str, int]]:
    """Return (check, line) for each token and each address that isn't allow-listed."""
    hits: list[tuple[str, int]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if TOKEN_RE.search(line):
            hits.append(("token", number))
        if any(not email_allowed(m.group()) for m in EMAIL_RE.finditer(line)):
            hits.append(("email", number))
    return hits


def denied_path(path: str) -> bool:
    parts = path.split("/")
    if any(fnmatchcase(parts[-1], glob) for glob in DENY_BASENAMES):
        return True
    rel = path[len("config/") :] if path.startswith("config/") else path
    if "/" not in rel and any(fnmatchcase(rel, glob) for glob in DENY_TOP_FILES):
        return True
    if any(rel.startswith(d + "/") for d in DENY_TOP_DIRS):
        return True
    return path.startswith(DENY_ROOT_PREFIXES)


def gitignore_entries() -> list[str]:
    """The .gitignore lines that mirror the deny-list, anchored the same way."""
    entries = list(DENY_BASENAMES)
    for glob in DENY_TOP_FILES:
        entries += [f"/{glob}", f"/config/{glob}"]
    for directory in DENY_TOP_DIRS:
        entries += [f"/{directory}/", f"/config/{directory}/"]
    entries += [f"/{prefix}" for prefix in DENY_ROOT_PREFIXES]
    return entries


def terms_path() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DENY_TERMS")
    if override:
        return Path(override)
    return Path(os.environ.get("HOME", "~")) / ".config" / "claude-config" / "deny-terms"


def load_terms(path: Path) -> list[str] | None:
    """Lower-cased terms, or None when the file is missing or unreadable."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    terms = [line.strip().lower() for line in raw.splitlines()]
    return [t for t in terms if t and not t.startswith("#")]


def term_lines(text: str, terms: list[str]) -> list[int]:
    if not terms:
        return []
    return [
        number
        for number, line in enumerate(text.lower().splitlines(), start=1)
        if any(term in line for term in terms)
    ]


def clean_message(raw: str, comment_char: str) -> str:
    """The message as git stores it: comment lines and the scissors section dropped."""
    kept: list[str] = []
    for line in raw.splitlines(keepends=True):
        if line.startswith(comment_char + " ------------------------ >8"):
            break
        if not line.startswith(comment_char):
            kept.append(line)
    return "".join(kept)


def is_zero(sha: str) -> bool:
    return len(sha) in (40, 64) and set(sha) == {"0"}


def parse_push_line(line: str) -> PushRef | None:
    fields = line.split()
    if len(fields) != 4:
        return None
    return PushRef(*fields)


def format_findings(findings: list[Finding], terms: list[str]) -> list[str]:
    redacted: dict[str, str] = {}
    lines = []
    for f in findings:
        path = f.path
        if any(term in path.lower() for term in terms):
            path = redacted.setdefault(path, f"<redacted path #{len(redacted) + 1}>")
        suffix = f":{f.line}" if f.line is not None else ""
        lines.append(f"{f.hook}: {f.check}: {path}{suffix}")
    return lines


# ---------------------------------------------------------------- git access


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, check=True)
    return result.stdout.decode("utf-8", errors="surrogateescape")


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], capture_output=True).returncode == 0


def blob_text(spec: str) -> str:
    data = subprocess.run(["git", "cat-file", "blob", spec], capture_output=True, check=True)
    return data.stdout.decode("utf-8", errors="replace")


def staged_paths() -> list[str]:
    out = git(
        "diff", "--cached", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--diff-filter=d"
    )
    return [p for p in out.split("\0") if p]


def content_findings(hook: str, path: str, text: str, terms: list[str]) -> list[Finding]:
    found = [Finding(hook, check, path, line) for check, line in scan_text(text)]
    found += [Finding(hook, "deny-term", path, line) for line in term_lines(text, terms)]
    return found


def path_findings(hook: str, path: str, terms: list[str]) -> list[Finding]:
    found = []
    if denied_path(path):
        found.append(Finding(hook, "deny-list", path))
    if any(term in path.lower() for term in terms):
        found.append(Finding(hook, "deny-term", path))
    return found


def run_gitleaks(hook: str, warnings: list[str]) -> list[Finding]:
    exe = shutil.which("gitleaks")
    if exe is None:
        warnings.append(GITLEAKS_MISSING)
        return []
    root = git("rev-parse", "--show-toplevel").strip()
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        code = subprocess.run(
            [
                exe,
                "git",
                "--pre-commit",
                "--staged",
                "--redact",
                "--no-banner",
                "--report-format",
                "json",
                "--report-path",
                str(report),
                "--config",
                str(Path(root) / ".gitleaks.toml"),
                root,
            ],
            capture_output=True,
        ).returncode
        if code == 0:
            return []
        found: list[Finding] = []
        if code == 1:
            try:
                entries = json.loads(report.read_text())
                found = [
                    Finding(hook, "gitleaks", str(e["File"]), int(e["StartLine"])) for e in entries
                ]
            except (OSError, ValueError, TypeError, KeyError):
                found = []
        return found or [Finding(hook, "gitleaks", f"exit {code}")]


# ---------------------------------------------------------------- hooks


def check_pre_commit(hook: str, terms: list[str], warnings: list[str]) -> list[Finding]:
    found = []
    for role, var in (("author", "GIT_AUTHOR_IDENT"), ("committer", "GIT_COMMITTER_IDENT")):
        if not is_noreply(ident_email(git("var", var))):
            found.append(Finding(hook, "identity", role))
    for path in staged_paths():
        found += path_findings(hook, path, terms)
        found += content_findings(hook, path, blob_text(":" + path), terms)
    return found + run_gitleaks(hook, warnings)


def check_commit_msg(msg_file: str, terms: list[str]) -> list[Finding]:
    try:
        raw = Path(msg_file).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UsageError(f"cannot read {msg_file}") from exc
    comment_char = git("config", "--default", "#", "core.commentChar").strip()
    if comment_char in ("", "auto"):
        comment_char = "#"
    return content_findings("commit-msg", "message", clean_message(raw, comment_char), terms)


def identity_findings(hook: str, commits: list[str]) -> list[Finding]:
    found = []
    for sha in commits:
        author, committer, short = git("log", "-1", "--format=%ae%x00%ce%x00%h", sha).split("\0")
        for role, email in (("author", author), ("committer", committer)):
            if not is_noreply(email):
                found.append(Finding(hook, "identity", f"{short.strip()} {role}"))
    return found


def check_pre_push(stdin: TextIO) -> list[Finding]:
    found = []
    for line in stdin:
        ref = parse_push_line(line)
        if ref is None:
            continue
        if ref.remote_ref == "refs/heads/main":
            found.append(Finding("pre-push", "main", ref.remote_ref))
            continue
        if is_zero(ref.local_sha):
            continue
        if not is_zero(ref.remote_sha) and git_ok("cat-file", "-e", ref.remote_sha):
            spec = [f"{ref.remote_sha}..{ref.local_sha}"]
        else:
            spec = [ref.local_sha, "--not", "--remotes"]
        commits = git("rev-list", *spec).split()
        found += identity_findings("pre-push", commits)
    return found


def check_range(base: str, terms: list[str]) -> list[Finding]:
    if not git_ok("rev-parse", "--verify", "--quiet", base + "^{commit}"):
        raise UsageError(f"unknown base {base}")
    hook = "check-range"
    found: list[Finding] = []
    for path in [p for p in git("ls-tree", "-r", "-z", "--name-only", "HEAD").split("\0") if p]:
        found += path_findings(hook, path, terms)
        found += content_findings(hook, path, blob_text("HEAD:" + path), terms)
    commits = git("rev-list", f"{base}..HEAD").split()
    found += identity_findings(hook, commits)
    for sha in commits:
        short = git("rev-parse", "--short", sha).strip()
        found += content_findings(
            hook, f"{short} message", git("log", "-1", "--format=%B", sha), terms
        )
    return found


def main(argv: list[str], stdin: TextIO | None = None) -> int:
    if not argv or argv[0] not in HOOKS:
        print("usage: git_hooks.py {" + ",".join(HOOKS) + "} [args]", file=sys.stderr)
        return 2
    hook, args = argv[0], argv[1:]
    warnings: list[str] = []
    path = terms_path()
    loaded = load_terms(path) if hook != "pre-push" else []
    if loaded is None:
        warnings.append(f"WARNING: excluded-terms file missing or unreadable: {path}")
    terms = loaded or []
    try:
        if hook in ("pre-commit", "pre-merge-commit"):
            findings = check_pre_commit(hook, terms, warnings)
        elif hook == "commit-msg":
            if len(args) != 1:
                raise UsageError("commit-msg needs the message file")
            findings = check_commit_msg(args[0], terms)
        elif hook == "pre-push":
            findings = check_pre_push(stdin if stdin is not None else sys.stdin)
        else:
            if len(args) != 1:
                raise UsageError("check-range needs a base ref")
            findings = check_range(args[0], terms)
            warnings.append(f"check-range: terms checked: {len(terms)}")
    except UsageError as exc:
        print(f"{hook}: {exc}", file=sys.stderr)
        return 2
    for line in format_findings(findings, terms):
        print(line, file=sys.stderr)
    for warning in warnings:
        print(warning, file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
