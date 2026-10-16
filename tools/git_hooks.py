"""Git hooks for this repo: keep secrets, email addresses and private terms out of commits.

The wrappers in .githooks/ call this file as
``python3 -I <repo root>/tools/git_hooks.py <hook> [args]``. Standard library only.

Hooks:
  pre-commit, pre-merge-commit  identity, tokens, emails, deny-list, excluded terms, gitleaks
  commit-msg <file>             tokens, emails and excluded terms in the raw message
  pre-push <remote> <url>       refuses pushes to main; checks every pushed commit (identity,
                                stored message, each file it adds or changes) and each
                                pushed annotated tag
  check-range <base>            the per-commit checks over <base>..HEAD plus the whole tree at
                                HEAD, for commits that ran no hooks (rebase, cherry-pick,
                                --no-verify)

Exit codes: 0 pass (warnings allowed), 1 refused, 2 usage error or a failing git command.
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
from typing import IO, TextIO, cast

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
    for number, line in enumerate(text.split("\n"), start=1):
        if TOKEN_RE.search(line):
            hits.append(("token", number))
        if any(not email_allowed(m.group()) for m in EMAIL_RE.finditer(line)):
            hits.append(("email", number))
    return hits


def denied_path(path: str) -> bool:
    # Compared lower-cased: on a case-insensitive filesystem .ENV is .env.
    path = path.lower()
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
        for number, line in enumerate(text.lower().split("\n"), start=1)
        if any(term in line for term in terms)
    ]


SCISSORS_RE = re.compile(r"(\S+) -{24} >8 -{24}")


def message_to_scan(raw: str) -> str:
    """The commit message as commit-msg scans it.

    Comment lines stay in, because git keeps them for -m and -F messages. Only the diff
    that `git commit -v` appends is cut: a scissors line whose next line not starting
    with the same comment prefix is a `diff --git` header. A hand-written imitation can
    pass here; pre-push scans the whole stored message.
    """
    lines = raw.split("\n")
    for index, line in enumerate(lines):
        match = SCISSORS_RE.fullmatch(line)
        if match is None:
            continue
        rest = [later for later in lines[index + 1 :] if not later.startswith(match.group(1))]
        if rest and rest[0].startswith("diff --git "):
            return "\n".join(lines[:index]) + "\n"
    return raw


_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)


def decode_blob(data: bytes) -> list[str]:
    """The texts to scan for one blob.

    A byte-order mark picks its codec (UTF-32 before UTF-16, since the UTF-32LE mark starts
    with the UTF-16LE one). A blob with NUL bytes is also scanned with them removed, which
    catches UTF-16 text that has no mark.
    """
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return [data[len(bom) :].decode(codec, errors="replace")]
    texts = [data.decode("utf-8", errors="replace")]
    if b"\0" in data:
        texts.append(data.replace(b"\0", b"").decode("utf-8", errors="replace"))
    return texts


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


class GitError(Exception):
    """A git command failed. main() reports it without a traceback."""


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True)
    if result.returncode != 0:
        raise GitError(args[0])
    return result.stdout.decode("utf-8", errors="surrogateescape")


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], capture_output=True).returncode == 0


class BlobReader:
    """Reads objects by full id through one `git cat-file --batch` process."""

    def __enter__(self) -> BlobReader:
        self._proc = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        # Both are pipes because of the PIPE arguments above.
        self._in = cast(IO[bytes], self._proc.stdin)
        self._out = cast(IO[bytes], self._proc.stdout)
        return self

    def __exit__(self, *exc: object) -> None:
        self._in.close()
        self._proc.wait()

    def read(self, oid: str) -> bytes:
        self._in.write(oid.encode() + b"\n")
        self._in.flush()
        header = self._out.readline().split()
        if len(header) != 3:
            raise GitError("cat-file")
        data = self._out.read(int(header[2]) + 1)
        return data[:-1]


@dataclass(frozen=True)
class Entry:
    mode: str
    oid: str
    path: str


SUBMODULE_MODE = "160000"


def parse_raw(out: str) -> list[Entry]:
    """Entries from `--raw -z` output, plain or combined (-c), skipping deletions.
    The last mode and id on each header are the result's."""
    fields = out.split("\0")
    entries = []
    for header, path in zip(fields[0::2], fields[1::2], strict=False):
        parents = len(header) - len(header.lstrip(":"))
        parts = header[parents:].split()
        mode, oid = parts[parents], parts[2 * parents + 1]
        if not is_zero(oid):
            entries.append(Entry(mode, oid, path))
    return entries


def staged_entries() -> list[Entry]:
    return parse_raw(
        git(
            "diff",
            "--cached",
            "--raw",
            "-z",
            "--no-abbrev",
            "--no-renames",
            "--no-ext-diff",
            "--diff-filter=d",
        )
    )


def commit_entries(sha: str) -> list[Entry]:
    return parse_raw(
        git(
            "diff-tree",
            "-r",
            "-z",
            "--raw",
            "--no-abbrev",
            "--no-renames",
            "--no-commit-id",
            "--diff-filter=d",
            "--root",
            "-c",
            sha,
        )
    )


def tree_entries(rev: str) -> list[Entry]:
    entries = []
    for record in git("ls-tree", "-r", "-z", "--full-tree", rev).split("\0"):
        if record:
            meta, path = record.split("\t", 1)
            mode, _kind, oid = meta.split()
            entries.append(Entry(mode, oid, path))
    return entries


def content_findings(hook: str, label: str, text: str, terms: list[str]) -> list[Finding]:
    found = [Finding(hook, check, label, line) for check, line in scan_text(text)]
    found += [Finding(hook, "deny-term", label, line) for line in term_lines(text, terms)]
    return found


def path_findings(hook: str, label: str, path: str, terms: list[str]) -> list[Finding]:
    found = []
    if denied_path(path):
        found.append(Finding(hook, "deny-list", label))
    if any(term in path.lower() for term in terms):
        found.append(Finding(hook, "deny-term", label))
    return found


def entry_findings(
    hook: str, entry: Entry, label: str, reader: BlobReader, terms: list[str]
) -> list[Finding]:
    found = path_findings(hook, label, entry.path, terms)
    if entry.mode == SUBMODULE_MODE:
        # A submodule's id names a commit in another repo; only its path is checked.
        return found
    seen: set[Finding] = set()
    for text in decode_blob(reader.read(entry.oid)):
        for finding in content_findings(hook, label, text, terms):
            if finding not in seen:
                seen.add(finding)
                found.append(finding)
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
        # gitleaks' default rules overlap the repo's, so one secret can be reported twice.
        return list(dict.fromkeys(found)) or [Finding(hook, "gitleaks", f"exit {code}")]


# ---------------------------------------------------------------- hooks


def check_pre_commit(hook: str, terms: list[str], warnings: list[str]) -> list[Finding]:
    found = []
    for role, var in (("author", "GIT_AUTHOR_IDENT"), ("committer", "GIT_COMMITTER_IDENT")):
        if not is_noreply(ident_email(git("var", var))):
            found.append(Finding(hook, "identity", role))
    with BlobReader() as reader:
        for entry in staged_entries():
            found += entry_findings(hook, entry, entry.path, reader, terms)
    return found + run_gitleaks(hook, warnings)


def check_commit_msg(msg_file: str, terms: list[str]) -> list[Finding]:
    try:
        raw = Path(msg_file).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UsageError(f"cannot read {msg_file}") from exc
    return content_findings("commit-msg", "message", message_to_scan(raw), terms)


def commit_findings(hook: str, sha: str, reader: BlobReader, terms: list[str]) -> list[Finding]:
    """Identity, whole stored message, and every file the commit adds or changes."""
    author, committer, short, message = git(
        "log", "-1", "--format=%ae%x00%ce%x00%h%x00%B", sha
    ).split("\0", 3)
    found = [
        Finding(hook, "identity", f"{short} {role}")
        for role, email in (("author", author), ("committer", committer))
        if not is_noreply(email)
    ]
    found += content_findings(hook, f"{short} message", message, terms)
    for entry in commit_entries(sha):
        found += entry_findings(hook, entry, f"{short}:{entry.path}", reader, terms)
    return found


def tag_findings(hook: str, ref: str, oid: str, terms: list[str]) -> list[Finding]:
    header, _, message = git("cat-file", "tag", oid).partition("\n\n")
    tagger = next((line for line in header.split("\n") if line.startswith("tagger ")), "")
    found = []
    if not is_noreply(ident_email(tagger)):
        found.append(Finding(hook, "identity", f"{ref} tagger"))
    return found + content_findings(hook, ref, message, terms)


def check_pre_push(stdin: TextIO, terms: list[str]) -> list[Finding]:
    hook = "pre-push"
    found: list[Finding] = []
    with BlobReader() as reader:
        for number, line in enumerate(stdin, start=1):
            if not line.strip():
                continue
            ref = parse_push_line(line)
            if ref is None:
                found.append(Finding(hook, "input", str(number)))
                continue
            if ref.remote_ref == "refs/heads/main":
                found.append(Finding(hook, "main", ref.remote_ref))
                continue
            if is_zero(ref.local_sha):
                continue
            if not git_ok("cat-file", "-e", ref.local_sha):
                found.append(Finding(hook, "input", str(number)))
                continue
            if git("cat-file", "-t", ref.local_sha).strip() == "tag":
                found += tag_findings(hook, ref.local_ref, ref.local_sha, terms)
            exclude = ["--remotes"]
            if not is_zero(ref.remote_sha) and git_ok("cat-file", "-e", ref.remote_sha):
                exclude.insert(0, ref.remote_sha)
            for sha in git("rev-list", ref.local_sha, "--not", *exclude).split():
                found += commit_findings(hook, sha, reader, terms)
    return found


def check_range(base: str, terms: list[str]) -> list[Finding]:
    if not git_ok("rev-parse", "--verify", "--quiet", base + "^{commit}"):
        raise UsageError(f"unknown base {base}")
    hook = "check-range"
    found: list[Finding] = []
    with BlobReader() as reader:
        for entry in tree_entries("HEAD"):
            found += entry_findings(hook, entry, entry.path, reader, terms)
        for sha in git("rev-list", f"{base}..HEAD").split():
            found += commit_findings(hook, sha, reader, terms)
    return found


def main(argv: list[str], stdin: TextIO | None = None) -> int:
    if not argv or argv[0] not in HOOKS:
        print("usage: git_hooks.py {" + ",".join(HOOKS) + "} [args]", file=sys.stderr)
        return 2
    hook, args = argv[0], argv[1:]
    warnings: list[str] = []
    path = terms_path()
    loaded = load_terms(path)
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
            findings = check_pre_push(stdin if stdin is not None else sys.stdin, terms)
        else:
            if len(args) != 1:
                raise UsageError("check-range needs a base ref")
            findings = check_range(args[0], terms)
            warnings.append(f"check-range: terms checked: {len(terms)}")
    except UsageError as exc:
        print(f"{hook}: {exc}", file=sys.stderr)
        return 2
    except GitError:
        print(f"{hook}: git failed", file=sys.stderr)
        return 2
    for line in format_findings(findings, terms):
        print(line, file=sys.stderr)
    for warning in warnings:
        print(warning, file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
