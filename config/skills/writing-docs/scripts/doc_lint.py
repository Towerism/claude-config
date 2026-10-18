#!/usr/bin/env python3
"""Report structure and link problems in a Markdown document, plus plain-prose's report.

Usage:
    python3 -I doc_lint.py FILE [--json]
    python3 -I doc_lint.py - [--json]        (read stdin)

Report-only: exits 0 whatever it finds, and 2 only for bad arguments or an
unreadable file. Front matter, fenced code and inline code are skipped, and line
numbers refer to the original text. Links with a scheme are never fetched; from
stdin, relative file links can't be resolved, so they're counted instead.

The prose findings, including acronyms used before they're defined and bare
requirement IDs, come from the sibling plain-prose skill's prose_check.py.
"""

from __future__ import annotations

import sys

# Set before prose_check is imported: it only turns caching off once its own module
# runs, and a cache written into a deployed skill directory would show up as drift.
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from types import ModuleType  # noqa: E402
from typing import Any  # noqa: E402

PROSE_CHECK = Path(__file__).resolve().parents[2] / "plain-prose" / "scripts" / "prose_check.py"
PURPOSE_WORDS = 8
PURPOSE_WINDOW = 15

HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(.*)$")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
# A link or image: [text](target "optional title"). The target may hold one level of
# balanced parentheses, as in a(b).md, or be wrapped in <...>.
LINK = re.compile(
    r"!?\[[^\]]*\]\(\s*(<[^>]*>|(?:[^()\s]|\([^()\s]*\))*)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:|^//")
MARKER = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b|lorem ipsum", re.IGNORECASE)
CASED_MARKER = {"TODO", "TBD", "FIXME", "XXX"}
METADATA = re.compile(r"^\*\*[^*]+:\*\*")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
MARKDOWN_SUFFIXES = {".md", ".markdown"}

Finding = dict[str, Any]


@dataclass
class Line:
    number: int
    text: str  # inline code blanked out; empty for front matter and code
    code: bool = False  # front matter, a fence line or inside a fence
    fence: str | None = None  # the info string, for a line that opens a fence


@dataclass
class Doc:
    lines: list[Line] = field(default_factory=list)
    headings: list[tuple[int, int, str]] = field(default_factory=list)  # line, level, text


def scan(text: str) -> Doc:
    """The document's lines with front matter and code blanked, and its # headings."""
    doc = Doc()
    fence: str | None = None
    in_front = False
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if number == 1 and stripped == "---":
            in_front = True
            doc.lines.append(Line(number, "", code=True))
            continue
        if in_front:
            in_front = stripped not in ("---", "...")
            doc.lines.append(Line(number, "", code=True))
            continue
        opened = FENCE.match(raw)
        if fence is not None:
            # Only a run of the same character, at least as long, closes the fence.
            if (
                opened
                and opened.group(1)[0] == fence[0]
                and len(opened.group(1)) >= len(fence)
                and not opened.group(2)
            ):
                fence = None
            doc.lines.append(Line(number, "", code=True))
            continue
        if opened:
            fence = opened.group(1)
            doc.lines.append(Line(number, "", code=True, fence=opened.group(2).strip()))
            continue
        line = INLINE_CODE.sub(lambda m: " " * len(m.group(0)), raw)
        doc.lines.append(Line(number, line))
        heading = HEADING.match(line)
        if heading:
            doc.headings.append((number, len(heading.group(1)), (heading.group(2) or "").strip()))
    return doc


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: link text kept, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(doc: Doc) -> set[str]:
    seen: dict[str, int] = {}
    found = set()
    for _, _, text in doc.headings:
        base = slug(text)
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def _finding(line: int, kind: str, text: str, fix: str) -> Finding:
    return {"line": line, "kind": kind, "text": text, "fix": fix}


def _purpose(doc: Doc, title_line: int) -> bool:
    """Whether a prose paragraph of PURPOSE_WORDS words follows the title soon enough."""
    heading_lines = {n for n, _, _ in doc.headings}
    seen = words = 0
    for line in doc.lines[title_line:]:
        if line.number in heading_lines:
            return False
        stripped = line.text.strip()
        if not stripped and not line.code:
            words = 0
            continue
        seen += 1
        if seen > PURPOSE_WINDOW:
            return False
        if (
            line.code
            or stripped[0] in "|<>"
            or LIST_ITEM.match(stripped)
            or METADATA.match(stripped)
        ):
            words = 0
            continue
        words += len(WORD.findall(stripped))
        if words >= PURPOSE_WORDS:
            return True
    return False


def structure(doc: Doc) -> list[Finding]:
    found = []
    levels = [level for _, level, _ in doc.headings]
    if not levels or levels[0] != 1:
        found.append(
            _finding(
                doc.headings[0][0] if doc.headings else 0,
                "structure",
                "no title: the first heading isn't a level-1 # heading (setext headings, "
                "underlined with === or ---, aren't read)",
                "start with one '# Title' line naming the document",
            )
        )
    else:
        title_line = doc.headings[0][0]
        if not _purpose(doc, title_line):
            found.append(
                _finding(
                    title_line,
                    "structure",
                    "no purpose: no paragraph near the top says what the document is for",
                    "add a sentence or two under the title naming the reader and their task",
                )
            )
    for number, level, _ in doc.headings[1:]:
        if level == 1:
            found.append(
                _finding(
                    number,
                    "structure",
                    "more than one title: a second level-1 heading",
                    "make it a level-2 heading, or split the file",
                )
            )
    for (_, before, _), (number, level, _) in zip(doc.headings, doc.headings[1:], strict=False):
        if level > before + 1:
            found.append(
                _finding(
                    number,
                    "structure",
                    f"skipped heading level: level {level} under level {before}",
                    f"use a level-{before + 1} heading",
                )
            )
    for line in doc.lines:
        if line.fence == "":
            found.append(
                _finding(
                    line.number,
                    "structure",
                    "code fence without a language",
                    "name the language after the fence, such as ```bash or ```text",
                )
            )
        for match in MARKER.finditer(line.text):
            word = match.group(0)
            if match.group(1) and word not in CASED_MARKER:
                continue
            found.append(
                _finding(
                    line.number,
                    "structure",
                    f"leftover marker: {word}",
                    "replace the placeholder with the real content",
                )
            )
    return sorted(found, key=lambda f: f["line"])


def percent_decode(text: str) -> str:
    """URL escapes such as %20 decoded as UTF-8; anything malformed is left as it is."""
    raw = bytearray()
    i = 0
    while i < len(text):
        hexpair = text[i + 1 : i + 3]
        if (
            text[i] == "%"
            and len(hexpair) == 2
            and all(c in "0123456789abcdefABCDEF" for c in hexpair)
        ):
            raw.append(int(hexpair, 16))
            i += 3
        else:
            raw += text[i].encode()
            i += 1
    return raw.decode("utf-8", errors="replace")


def links(doc: Doc, base: Path | None) -> tuple[list[Finding], int]:
    """Broken relative links and anchors, and how many relative links went unchecked."""
    found: list[Finding] = []
    unchecked = 0
    own = anchors(doc)
    for line in doc.lines:
        for match in LINK.finditer(line.text):
            target = match.group(1).strip("<>")
            if not target or SCHEME.match(target) or target.startswith("/"):
                continue
            path, _, anchor = target.partition("#")
            if not path:
                if anchor not in own:
                    found.append(
                        _finding(
                            line.number,
                            "links",
                            f"missing anchor: #{anchor}",
                            "link to a heading that exists in this document",
                        )
                    )
                continue
            if base is None:
                unchecked += 1
                continue
            resolved = base / percent_decode(path)
            if not resolved.exists():
                found.append(
                    _finding(
                        line.number,
                        "links",
                        f"missing file: {path}",
                        "fix the path; it is resolved from this document's folder",
                    )
                )
            elif anchor and resolved.is_file() and resolved.suffix.lower() in MARKDOWN_SUFFIXES:
                theirs = anchors(scan(resolved.read_text(errors="replace")))
                if anchor not in theirs:
                    found.append(
                        _finding(
                            line.number,
                            "links",
                            f"missing anchor: {target}",
                            "link to a heading that exists in that file",
                        )
                    )
    return found, unchecked


_prose: ModuleType | None = None


def prose_module() -> ModuleType | None:
    """plain-prose's prose_check, loaded once from the sibling skill; None if it isn't there."""
    global _prose
    if _prose is None and PROSE_CHECK.is_file():
        spec = importlib.util.spec_from_file_location("prose_check", PROSE_CHECK)
        if spec is None or spec.loader is None:  # pragma: no cover  (a .py file has a loader)
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _prose = module
    return _prose


def lint(text: str, base: Path | None) -> dict[str, Any]:
    """The full report for `text`; `base` is the document's folder, or None for stdin."""
    doc = scan(text)
    link_findings, unchecked = links(doc, base)
    findings = {"structure": structure(doc), "links": link_findings}
    module = prose_module()
    prose: dict[str, Any] = (
        module.check(text)
        if module is not None
        else {"error": f"plain-prose's prose_check.py isn't at {PROSE_CHECK}; prose not checked"}
    )
    return {
        "words": sum(len(WORD.findall(line.text)) for line in doc.lines),
        "findings": findings,
        "counts": {k: len(v) for k, v in findings.items()},
        "linksNotChecked": unchecked,
        "prose": prose,
    }


def render(path: str, report: dict[str, Any]) -> str:
    out = [f"doc_lint: {path}, {report['words']} words"]
    for key, title in (("structure", "Structure"), ("links", "Links")):
        out.append(f"\n{title} ({report['counts'][key]})")
        for f in report["findings"][key]:
            where = f"L{f['line']}" if f["line"] else "doc"
            out.append(f"  {where:>5}  {f['text']}  → {f['fix']}")
    if report["linksNotChecked"]:
        out.append(
            f"  relative links not checked, because the input came from stdin: "
            f"{report['linksNotChecked']}"
        )
    out.append("\nProse, from plain-prose")
    module = prose_module()
    if module is None:
        out.append(f"  {report['prose']['error']}")
    else:
        out.append(module.render(report["prose"]))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report structure, link and prose findings in a document (report-only)."
    )
    ap.add_argument("path", help="Markdown file to check, or - for stdin")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read() if args.path == "-" else Path(args.path).read_bytes()
    except OSError as e:
        print(f"doc_lint: cannot read {args.path}: {e}", file=sys.stderr)
        return 2
    base = None if args.path == "-" else Path(args.path).resolve().parent
    report = lint(raw.decode("utf-8", errors="replace"), base)
    if args.json:
        print(json.dumps({"path": args.path, **report}, indent=2))
    else:
        print(render(args.path, report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
