#!/usr/bin/env python3
"""Report what makes prose hard for a cold reader, including AI tells.

Usage:
    python3 -I prose_check.py FILE [--json]
    python3 -I prose_check.py - [--json]        (read stdin)

Report-only: exits 0 whatever it finds, and 2 only for bad arguments or an
unreadable file. Front matter, code, tables and HTML lines are blanked before
analysis, but line numbers refer to the original text.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

sys.dont_write_bytecode = True  # keep the deployed skill directory free of __pycache__

VENDORED = Path(__file__).resolve().parent / "vendored"


def _load(name: str) -> ModuleType:
    # The vendored files stay byte-identical to upstream, so they're loaded by path
    # instead of being turned into a package.
    spec = importlib.util.spec_from_file_location(name, VENDORED / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover  (a missing file fails here)
        raise ImportError(f"can't load the vendored {name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


readability = _load("readability_scorer")
detector = _load("ai_content_detector")

Finding = dict[str, Any]
Report = dict[str, Any]

SESSION_REFS = re.compile(
    r"\b(as (?:discussed|mentioned|noted) (?:above|earlier|before)?|as discussed|per our|"
    r"see above|mentioned above|noted above|the above|earlier (?:draft|version|approach|fix)|"
    r"previously discussed|as we (?:said|agreed)|like I said)\b",
    re.IGNORECASE,
)
BARE_ID = re.compile(r"\b(?:FR|NFR|AC|EC|OS|Q|D|C|W|N|T)-?\d{1,3}[a-z]?\b")
ACRONYM = re.compile(r"\b[A-Z]{2,6}s?\b")
DEFINED = re.compile(r"\(([A-Z]{2,6})s?\)|\b([A-Z]{2,6})s? \(")
COMMON_ACRONYMS = {
    "API", "CLI", "CI", "CD", "PR", "URL", "JSON", "YAML", "HTML", "CSS", "HTTP", "HTTPS",
    "SSH", "OK", "ID", "OS", "UI", "UX", "AI", "LLM", "PDF", "SQL", "UTF", "MIT", "GPU",
    "CPU", "RAM", "USB", "DNS", "TLS", "README", "TODO", "TBD", "NOTE", "FAQ", "IMPORTANT",
    "MUST", "SHOULD", "MAY", "NOT", "GNU", "XDG", "ADR", "GB", "MB", "KB", "CODE",
}  # fmt: skip
# Tell words from the vendored checklist (categories 1 and 4) and the detector's phrase
# list. Words that are usually technical in this repo's prose ("ensure", "framework",
# "dynamic", "ecosystem", "prioritize") are left out; the checklist allows them.
TELL_WORDS = {
    "delve": "look at, dig into", "leverage": "use", "utilize": "use",
    "foster": "build, encourage", "facilitate": "help, allow",
    "streamline": "say what gets faster", "seamless": "describe the experience",
    "seamlessly": "describe the experience", "holistic": "say what it covers",
    "synergy": "say what happens", "paradigm": "approach", "landscape": "how X works today",
    "tapestry": "cut", "multifaceted": "name the parts", "pivotal": "show why it matters",
    "crucial": "show why it matters", "vital": "show why it matters",
    "comprehensive": "say what it covers", "cutting-edge": "say what is new",
    "state-of-the-art": "say what is new", "innovative": "say what differs",
    "empower": "let, allow", "elevate": "improve, say how", "unlock": "allow, enable",
    "embark": "start", "myriad": "many", "plethora": "many", "testament": "evidence, proof",
    "realm": "area, field", "navigate": "handle, work through", "robust": "state the property",
    "paramount": "most important", "meticulous": "careful", "meticulously": "carefully",
    "underscore": "show", "underscores": "shows", "showcase": "show", "intricate": "detailed",
    "game-changer": "say what changes",
}  # fmt: skip
TELL_PHRASES = [
    r"it'?s (?:worth|important to) not(?:e|ing)", r"it is important to note",
    r"it'?s worth mentioning", r"it should be noted", r"needless to say",
    r"it goes without saying", r"in today'?s \w+", r"at the end of the day",
    r"in conclusion", r"in summary", r"to summari[sz]e", r"let'?s dive in", r"dive deep",
    r"great question", r"i hope this helps", r"let me know if", r"feel free to",
    r"happy to help", r"rest assured", r"harness the power", r"take your \w+ to the next level",
    r"not (?:just|only|merely) [^.,;]{1,40}(?:,|;)? but",
    r"isn'?t (?:just|about) [^.]{1,40}[,;] it'?s",
    r"plays a (?:crucial|key|vital|pivotal) role", r"a (?:testament|reminder) to",
    r"whether you'?re",
]  # fmt: skip
TELL_PHRASE_RES = [re.compile(p, re.IGNORECASE) for p in TELL_PHRASES]
FILLER = {w.lower() for w in readability.FILLER_WORDS}
PASSIVE: re.Pattern[str] = readability.PASSIVE_PATTERN
LIST_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
BOLD = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")
SENTENCE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")

# Thresholds for technical prose; the vendored scorer's own targets are for marketing copy.
LONG_SENTENCE = 30
RHYTHM_RUN, RHYTHM_MIN_WORDS, RHYTHM_SPREAD = 4, 8, 4
REPEATED_OPENINGS = 3
LIST_SHARE, LIST_MIN_LINES = 0.5, 8
BOLD_PER_100 = 1.5
HEADINGS_PER_100, HEADINGS_MIN = 2.5, 4
TEXT_REPORT_LIMIT = 40
CATEGORIES = {"cold_reader": "Cold reader", "readability": "Readability", "ai_tells": "AI tells"}


def strip(text: str) -> list[tuple[int, str]]:
    """(line number, line) pairs with front matter, code, tables and HTML blanked."""
    out: list[tuple[int, str]] = []
    in_fence = in_front = False
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if i == 1 and s == "---":
            in_front = True
            out.append((i, ""))
            continue
        if in_front:
            in_front = s not in ("---", "...")
            out.append((i, ""))
            continue
        if s.startswith(("```", "~~~")):
            in_fence = not in_fence
            out.append((i, ""))
            continue
        if in_fence or s.startswith(("|", "<")):
            out.append((i, ""))
            continue
        line = re.sub(r"`[^`]*`", "CODE", line)
        out.append((i, re.sub(r"\]\([^)]*\)", "]", line)))
    return out


def _split(text: str) -> list[str]:
    return [m.group(0).strip() for m in SENTENCE.finditer(text) if len(m.group(0).split()) >= 3]


def sentences(prose: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Sentences of three or more words, each tagged with the line it starts on."""
    result: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    for no, line in [*prose, (0, "")]:
        block = HEADING.match(line) or LIST_LINE.match(line)
        if line.strip() and not block:
            start = start or no
            buf.append(line.strip())
            continue
        result += [(start, s) for s in _split(" ".join(buf))]
        buf, start = [], 0
        if block:
            result += [(no, s) for s in _split(LIST_LINE.sub("", HEADING.sub("", line)))]
    return result


def _finding(line: int, kind: str, text: str, fix: str) -> Finding:
    return {"line": line, "kind": kind, "text": text, "fix": fix}


def cold_reader(prose: list[tuple[int, str]]) -> list[Finding]:
    found: list[Finding] = []
    defined: set[str] = set()
    for no, line in prose:
        for m in SESSION_REFS.finditer(line):
            found.append(
                _finding(
                    no,
                    "session-reference",
                    m.group(0),
                    "name the issue, file, decision or date instead",
                )
            )
        for m in BARE_ID.finditer(line):
            found.append(
                _finding(
                    no,
                    "bare-id",
                    m.group(0),
                    "say what it is; keep the ID in parentheses if needed",
                )
            )
        on_line = {a or b for a, b in DEFINED.findall(line)}
        for m in ACRONYM.finditer(line):
            word = m.group(0)
            acronym = word[:-1] if word.endswith("s") else word
            hyphenated = line[m.end() : m.end() + 1] == "-"
            if acronym in COMMON_ACRONYMS or acronym in defined or hyphenated:
                continue
            if acronym not in on_line:
                found.append(
                    _finding(no, "undefined-acronym", acronym, "spell it out on first use")
                )
            defined.add(acronym)
    return found


def readability_findings(
    prose: list[tuple[int, str]], sents: list[tuple[int, str]]
) -> list[Finding]:
    found: list[Finding] = []
    for no, s in sents:
        n = len(s.split())
        if n > LONG_SENTENCE:
            found.append(
                _finding(
                    no,
                    "long-sentence",
                    f"{n} words: {s[:70]}…",
                    "split it, or move detail to a list or table",
                )
            )
        for m in PASSIVE.finditer(s):
            found.append(
                _finding(
                    no, "passive", m.group(0), "name who does it, unless the actor is irrelevant"
                )
            )
    for no, line in prose:
        for w in re.findall(r"[A-Za-z]+", line):
            if w.lower() in FILLER:
                found.append(_finding(no, "filler", w, "cut it"))
    return found


def _word_tells(no: int, line: str) -> list[Finding]:
    found = [
        _finding(
            no, "tell-word", w, TELL_WORDS[w] + " (keep it if used in its exact technical sense)"
        )
        for w in re.findall(r"[a-z]+(?:-[a-z]+)*", line.lower())
        if w in TELL_WORDS
    ]
    for r in TELL_PHRASE_RES:
        found += [
            _finding(no, "tell-phrase", m.group(0), "cut it, or say the specific thing")
            for m in r.finditer(line)
        ]
    dashes = line.count("—")
    if dashes >= 2 or (dashes == 1 and len(line) < 120):
        found.append(_finding(no, "em-dash", "—", "use a period, comma or parentheses"))
    tri = re.search(r"\b(\w+), (\w+),? and (\w+)\b", line)
    if tri and "CODE" not in tri.group(0) and len({len(g) for g in tri.groups()}) == 1:
        found.append(
            _finding(no, "tricolon", tri.group(0), "keep only the items that carry information")
        )
    return found


def _rhythm(sents: list[tuple[int, str]], prose: list[tuple[int, str]]) -> list[Finding]:
    found: list[Finding] = []
    lengths = [len(s.split()) for _, s in sents]
    for k in range(len(lengths) - RHYTHM_RUN + 1):
        window = lengths[k : k + RHYTHM_RUN]
        if min(window) >= RHYTHM_MIN_WORDS and max(window) - min(window) <= RHYTHM_SPREAD:
            found.append(
                _finding(
                    sents[k][0],
                    "uniform-rhythm",
                    f"{RHYTHM_RUN} sentences of {min(window)}–{max(window)} words",
                    "vary length: combine two, or cut one short",
                )
            )
            break
    openers: dict[str, list[int]] = {}
    for no, line in prose:
        if line.strip() and not LIST_LINE.match(line) and not HEADING.match(line):
            openers.setdefault(" ".join(line.split()[:2]).lower(), []).append(no)
    for first, nos in openers.items():
        if len(nos) >= REPEATED_OPENINGS and len(first) > 3:
            found.append(
                _finding(
                    nos[0],
                    "repeated-opening",
                    f"'{first}' ×{len(nos)}",
                    "open paragraphs differently",
                )
            )
    return found


def _structure(prose: list[tuple[int, str]], words: int) -> tuple[dict[str, float], list[Finding]]:
    nonblank = [line for _, line in prose if line.strip()]
    list_share = sum(1 for line in nonblank if LIST_LINE.match(line)) / (len(nonblank) or 1)
    bold = sum(len(BOLD.findall(line)) for line in nonblank)
    headings = sum(1 for line in nonblank if HEADING.match(line))
    per = max(words, 1) / 100
    measures = {
        "list_line_share": round(list_share, 2),
        "bold_per_100_words": round(bold / per, 2),
        "headings_per_100_words": round(headings / per, 2),
    }
    found: list[Finding] = []
    if list_share > LIST_SHARE and len(nonblank) >= LIST_MIN_LINES:
        found.append(
            _finding(
                0,
                "over-structured",
                f"{list_share:.0%} of lines are list items",
                "use prose for reasoning; lists only for list-shaped content",
            )
        )
    if measures["bold_per_100_words"] > BOLD_PER_100:
        found.append(
            _finding(
                0,
                "bold-overuse",
                f"{bold} bold spans in {words} words",
                "bold only what a scanner must see",
            )
        )
    if measures["headings_per_100_words"] > HEADINGS_PER_100 and headings >= HEADINGS_MIN:
        found.append(
            _finding(
                0,
                "heading-overuse",
                f"{headings} headings in {words} words",
                "merge short sections",
            )
        )
    return measures, found


def check(text: str) -> Report:
    prose = strip(text)
    plain = "\n".join(line for _, line in prose)
    words = len(re.findall(r"[A-Za-z]+", plain))
    sents = sentences(prose)
    measures, structural = _structure(prose, words)
    tells = [f for no, line in prose for f in _word_tells(no, line)]
    findings: dict[str, list[Finding]] = {
        "cold_reader": cold_reader(prose),
        "readability": readability_findings(prose, sents),
        "ai_tells": tells + _rhythm(sents, prose) + structural,
    }
    flat = re.sub(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+", "", plain, flags=re.M)
    read = readability.analyze_text(flat) if words else {}
    det = detector.analyze(flat) if words else {}
    lengths = [len(s.split()) for _, s in sents]
    return {
        "words": words,
        "findings": findings,
        "counts": {k: len(v) for k, v in findings.items()},
        "per_1000_words": {k: round(len(v) / max(words, 1) * 1000, 1) for k, v in findings.items()},
        "structure": measures,
        "readability_info": {
            "flesch_kincaid_grade": (read.get("flesch_kincaid_grade") or {}).get("grade_level"),
            "avg_sentence_length": (read.get("stats") or {}).get("avg_sentence_length"),
            "note": "information only; technical vocabulary raises the grade",
        },
        "detectability": {
            "score": det.get("composite_score"),
            "verdict": det.get("verdict"),
            "note": "0–20 reads human, 21–50 mixed, 51+ reads AI; a diagnostic, never a gate",
        },
        "sentence_length_stdev": round(statistics.pstdev(lengths), 1) if len(lengths) > 1 else None,
    }


def render(report: Report) -> str:
    out = [f"prose_check: {report['words']} words of prose"]
    for key, title in CATEGORIES.items():
        items = sorted(report["findings"][key], key=lambda f: f["line"])
        out.append(f"\n{title} ({len(items)})")
        for f in items[:TEXT_REPORT_LIMIT]:
            where = f"L{f['line']}" if f["line"] else "doc"
            out.append(f"  {where:>5}  {f['kind']}: {f['text']}  → {f['fix']}")
        if len(items) > TEXT_REPORT_LIMIT:
            out.append(f"  … {len(items) - TEXT_REPORT_LIMIT} more")
    r, d = report["readability_info"], report["detectability"]
    out.append(
        f"\nInfo: grade {r['flesch_kincaid_grade']}, avg sentence {r['avg_sentence_length']} "
        f"words, sentence-length stdev {report['sentence_length_stdev']}"
    )
    out.append(f"Detectability: {d['score']} ({d['verdict']}). {d['note']}")
    return "\n".join(out)


def print_report(report: Report, as_json: bool) -> None:
    print(json.dumps(report, indent=2) if as_json else render(report))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report cold-reader, readability and AI-tell findings (report-only)."
    )
    ap.add_argument("path", help="file to check, or - for stdin")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read() if args.path == "-" else Path(args.path).read_bytes()
    except OSError as e:
        print(f"prose_check: cannot read {args.path}: {e}", file=sys.stderr)
        return 2
    print_report(check(raw.decode("utf-8", errors="replace")), args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
