"""Decide which eval suites a change needs, write eval records, and verify them.

    python3 -I tools/cfg/eval_records.py verify [--base origin/main]

Model evals run only on the owner's machine (ADR-0005). Each run leaves a
record in eval/records/<suite>.json holding the sha256 of every input it ran
against. `verify` needs no model and no network: for each suite that
eval/gates.json says the branch requires, it checks that the record exists,
that its hashes match the files at HEAD (and, for suites compared against the
merge-base, the files there), and that the recorded run passed.

A record is from the smoke tier or the full tier (#77). The smoke tier is
enough only when nothing that could move the suite's result changed; see
smoke_enough. verify fails a smoke record anywhere else, and prints the tier
each required suite needs and the tier its record has.

Everything is read from commits through git, never from the working tree, so
a record describes exactly the files a commit holds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

GATES_PATH = "eval/gates.json"
RECORDS_DIR = "eval/records"
RUNNERS = {
    "scenario", "rubric", "hook-overlay", "hook-merge", "hook-session-start", "script",
    "trigger", "cold-reader", "doc-trigger", "doc-reader",
}  # fmt: skip
# Runners from eval/skill_evals.py and eval/doc_evals.py, which need the skill under
# test and its case file; the doc_evals ones also need the companions it runs beside.
SKILL_RUNNERS = {"trigger", "cold-reader", "doc-trigger", "doc-reader"}
COMPANION_RUNNERS = {"doc-trigger", "doc-reader"}
SKILL_PARTS = {"description", "body"}
# The smoke tier (#77). These runners name their smoke cases in a `smoke` list:
# trigger suites by 0-based position, because their queries have no ids, and the
# others by id. Those below run a fixed smoke run and take no list. Script suites
# always run in full.
SMOKE_BY_INDEX = {"trigger", "doc-trigger"}
SMOKE_BY_ID = {"scenario", "cold-reader", "doc-reader"}
SMOKE_FIXED = {"rubric", "hook-overlay", "hook-merge", "hook-session-start"}
SMOKE_MAX = 3
TIERS = ("smoke", "full")


class GatesError(Exception):
    """eval/gates.json is missing a field or has one of the wrong type."""


class GitError(Exception):
    """A git command failed, for example on an unknown ref."""


class _Missing:
    """Marks a JSON pointer that leads nowhere, which differs from a JSON null."""


MISSING = _Missing()


@dataclass(frozen=True)
class Suite:
    runner: str
    paths: tuple[str, ...]
    harness: tuple[str, ...]
    selectors: tuple[dict[str, str], ...] = ()
    compare_base: bool = False
    script: str | None = None
    skill: str | None = None
    cases: str | None = None
    companions: tuple[str, ...] = ()
    smoke: tuple[str | int, ...] = ()
    version_gated: bool = False


def git(repo: Path, *args: str) -> str:
    # Only fixed git subcommands run here, with argument lists and no shell.
    out = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    """The commit sha for `ref`; `--end-of-options` stops a ref being read as a flag."""
    return git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()


def merge_base(repo: Path, base_ref: str, head: str) -> str:
    base = resolve_commit(repo, base_ref)
    try:
        return git(repo, "merge-base", base, head).strip()
    except GitError as e:
        raise GitError(
            f"no merge-base between {base_ref} and HEAD (a shallow clone needs more history)"
        ) from e


def read_blob(repo: Path, commit: str, path: str) -> bytes | None:
    out = subprocess.run(  # noqa: S603
        ["git", "cat-file", "blob", f"{commit}:{path}"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        check=False,
    )
    return out.stdout if out.returncode == 0 else None


def list_files(repo: Path, commit: str) -> list[str]:
    return git(repo, "ls-tree", "-r", "-z", "--name-only", commit).split("\0")[:-1]


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    out = git(repo, "diff", "--no-renames", "--name-only", "-z", base, head)
    return out.split("\0")[:-1]


def _str_list(value: object, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GatesError(f"{what} must be a list of strings")
    return tuple(value)


def _selectors(value: object, what: str) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise GatesError(f"{what} must be a list")
    out = []
    for sel in value:
        if not isinstance(sel, dict) or not isinstance(sel.get("file"), str):
            raise GatesError(f"{what}: each selector needs a file")
        if isinstance(sel.get("pointer"), str):
            out.append({"file": sel["file"], "pointer": sel["pointer"]})
        elif sel.get("part") in SKILL_PARTS:
            out.append({"file": sel["file"], "part": sel["part"]})
        else:
            raise GatesError(f"{what}: a selector needs a pointer or a part of description/body")
    return tuple(out)


def _smoke(value: object, runner: str, what: str) -> tuple[str | int, ...]:
    if value is None:
        return ()
    if runner in SMOKE_BY_INDEX:
        kind, ok = "0-based case positions", lambda v: type(v) is int and v >= 0
    elif runner in SMOKE_BY_ID:
        kind, ok = "case ids", lambda v: isinstance(v, str)
    else:
        raise GatesError(f"{what} smoke: the {runner} runner takes no smoke list")
    if not (isinstance(value, list) and 1 <= len(value) <= SMOKE_MAX and all(map(ok, value))):
        raise GatesError(f"{what} smoke must list 1 to {SMOKE_MAX} {kind}")
    return tuple(value)


def _load(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise GatesError(f"{GATES_PATH} does not parse: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("suites"), dict):
        raise GatesError(f"{GATES_PATH} needs a suites object")
    return data


def parse_equivalences(text: str) -> tuple[tuple[str, str], ...]:
    """The pairs of path prefixes whose files the harness treats the same (#77)."""
    value = _load(text).get("equivalentPaths", [])
    pairs = value if isinstance(value, list) else None
    if pairs is None or not all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in pairs
    ):
        raise GatesError(f"{GATES_PATH} equivalentPaths must be a list of [prefix, prefix] pairs")
    return tuple((a, b) for a, b in pairs)


def parse_gates(text: str) -> dict[str, Suite]:
    data = _load(text)
    suites = {}
    for name, spec in data["suites"].items():
        what = f"{GATES_PATH} suite {name}"
        if not isinstance(spec, dict):
            raise GatesError(f"{what} must be an object")
        runner = spec.get("runner")
        if runner not in RUNNERS:
            raise GatesError(f"{what} has an unknown runner {runner!r}")
        script = spec.get("script")
        if runner == "script" and not isinstance(script, str):
            raise GatesError(f"{what} needs a script")
        skill, cases = spec.get("skill"), spec.get("cases")
        if runner in SKILL_RUNNERS and not (isinstance(skill, str) and isinstance(cases, str)):
            raise GatesError(f"{what} needs a skill and a cases file")
        companions = spec.get("companions", [])
        if runner in COMPANION_RUNNERS and not companions:
            raise GatesError(f"{what} needs a non-empty companions list")
        compare_base = spec.get("compareBase", False)
        if not isinstance(compare_base, bool):
            raise GatesError(f"{what}: compareBase must be true or false")
        version_gated = spec.get("versionGated", False)
        if not isinstance(version_gated, bool):
            raise GatesError(f"{what}: versionGated must be true or false")
        suites[name] = Suite(
            runner=runner,
            paths=_str_list(spec.get("paths"), f"{what} paths"),
            harness=_str_list(spec.get("harness"), f"{what} harness"),
            selectors=_selectors(spec.get("selectors", []), f"{what} selectors"),
            compare_base=compare_base,
            script=script if isinstance(script, str) else None,
            skill=skill if isinstance(skill, str) else None,
            cases=cases if isinstance(cases, str) else None,
            companions=_str_list(companions, f"{what} companions"),
            smoke=_smoke(spec.get("smoke"), runner, what),
            version_gated=version_gated,
        )
    return suites


def smoke_capable(suite: Suite) -> bool:
    """Whether the suite has a smoke run: a fixed one, or a list of smoke cases."""
    return suite.runner in SMOKE_FIXED or bool(suite.smoke)


def gates_at(repo: Path, commit: str) -> dict[str, Suite]:
    """The suites eval/gates.json defines at `commit`; none if the file doesn't exist there."""
    blob = read_blob(repo, commit, GATES_PATH)
    return {} if blob is None else parse_gates(blob.decode())


def split_skill_md(text: str) -> tuple[str, str]:
    """The frontmatter's description entry (with its continuation lines) and the body."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 3)
    if end == -1:
        return "", text
    front = text[4 : end + 1].splitlines(keepends=True)
    body = text[end + 5 :]
    description: list[str] = []
    for line in front:
        if description and line[:1] not in (" ", "\t"):
            break
        if description or line.startswith("description:"):
            description.append(line)
    return "".join(description), body


def resolve_pointer(doc: object, pointer: str) -> object:
    """Resolve a JSON pointer (RFC 6901); MISSING if any step doesn't exist."""
    if pointer == "":
        return doc
    node = doc
    for raw in pointer.lstrip("/").split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return MISSING
    return node


def selector_key(sel: dict[str, str]) -> str:
    return f"{sel['file']}#{sel.get('pointer', sel.get('part'))}"


def selector_value(repo: Path, commit: str, sel: dict[str, str]) -> bytes | None:
    """The bytes a selector picks out at `commit`, or None when the file or key is absent."""
    blob = read_blob(repo, commit, sel["file"])
    if blob is None:
        return None
    if "part" in sel:
        description, body = split_skill_md(blob.decode())
        return (description if sel["part"] == "description" else body).encode()
    try:
        doc = json.loads(blob)
    except json.JSONDecodeError:
        return blob
    value = resolve_pointer(doc, sel["pointer"])
    if value is MISSING:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _matches(path: str, globs: tuple[str, ...]) -> bool:
    return any(PurePosixPath(path).full_match(g) for g in globs)


def _sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def expand_selectors(repo: Path, commit: str, suite: Suite) -> list[dict[str, str]]:
    """The suite's selectors with each glob `file` replaced by every file at `commit` it matches.

    A selector whose `file` has no wildcard stands for itself, present or not. A glob that
    matches nothing stands for nothing.
    """
    files = list_files(repo, commit)
    out = []
    for sel in suite.selectors:
        if not any(c in sel["file"] for c in "*?["):
            out.append(sel)
        else:
            out += [{**sel, "file": p} for p in files if _matches(p, (sel["file"],))]
    return out


def selector_values(repo: Path, commit: str, suite: Suite) -> dict[str, str | None]:
    """Each concrete selector's key (file#part) and the sha256 of the bytes it picks out."""
    return {
        selector_key(sel): _sha(selector_value(repo, commit, sel))
        for sel in expand_selectors(repo, commit, suite)
    }


def is_required(repo: Path, suite: Suite, base: str, head: str, changed: list[str]) -> bool:
    if any(_matches(p, suite.paths + suite.harness) for p in changed):
        return True
    pairs = equivalences_at(repo, base)
    before, after = (selector_values(repo, c, suite) for c in (base, head))
    return _input_change(before, after, pairs) is not None


def suite_definitions(repo: Path, base: str, head: str) -> dict[str, Suite]:
    """Suites from both sides' gates.json; HEAD's definition wins where both have one."""
    return {**gates_at(repo, base), **gates_at(repo, head)}


def required_suites(repo: Path, base: str, head: str) -> list[str]:
    """Suites required by either side's gates.json, so a PR can't drop the gate it trips."""
    changed = changed_paths(repo, base, head)
    names = set()
    for gates in (gates_at(repo, base), gates_at(repo, head)):
        names |= {n for n, s in gates.items() if is_required(repo, s, base, head, changed)}
    return sorted(names)


def suite_inputs(repo: Path, commit: str, suite: Suite) -> dict[str, str | None]:
    inputs: dict[str, str | None] = {
        p: _sha(read_blob(repo, commit, p))
        for p in list_files(repo, commit)
        if _matches(p, suite.paths)
    }
    inputs.update(selector_values(repo, commit, suite))
    return inputs


def suite_harness(repo: Path, commit: str, suite: Suite) -> dict[str, str | None]:
    return {p: _sha(read_blob(repo, commit, p)) for p in suite.harness}


def equivalences_at(repo: Path, commit: str) -> tuple[tuple[str, str], ...]:
    blob = read_blob(repo, commit, GATES_PATH)
    return () if blob is None else parse_equivalences(blob.decode())


def _equivalents(path: str, pairs: tuple[tuple[str, str], ...]) -> list[str]:
    out = []
    for a, b in pairs:
        for one, other in ((a, b), (b, a)):
            if path.startswith(one):
                out.append(other + path[len(one) :])
    return out


def _input_change(
    base_files: dict[str, str | None],
    head_files: dict[str, str | None],
    pairs: tuple[tuple[str, str], ...],
    noun: str = "",
) -> str | None:
    """Why the inputs differ beyond declared moves, or None when each one pairs up unchanged.

    `noun` goes before the name of an input whose content changed ("the selector ...").
    """
    unpaired = set(head_files)
    for path, digest in sorted(base_files.items()):
        if path in head_files:
            if head_files[path] != digest:
                return f"{noun}{path} changed"
            unpaired.discard(path)
            continue
        moved = [q for q in _equivalents(path, pairs) if q in unpaired and q not in base_files]
        if not moved:
            return f"{path} was removed or renamed"
        if head_files[moved[0]] != digest:
            return f"{path} moved to {moved[0]} and changed"
        unpaired.discard(moved[0])
    return f"{min(unpaired)} was added" if unpaired else None


def smoke_enough(repo: Path, name: str, base: str, head: str) -> tuple[bool, str | None]:
    """Whether the smoke tier is enough for suite `name`, and if not, the first reason why.

    HEAD's definition of the suite is applied at both commits. Smoke is enough only
    when the definition (apart from its smoke list) and every selector are unchanged
    (a harness edit alone needs smoke), and each input either keeps its path or moves to a path
    the merge-base's eval/gates.json declares equivalent, with the same content.
    """
    suite = gates_at(repo, head)[name]
    before = gates_at(repo, base).get(name)
    if before is None:
        return False, "the suite is new in this branch"
    if replace(before, smoke=()) != replace(suite, smoke=()):
        return False, "the suite's definition in eval/gates.json changed"
    # A harness edit, eval.py's included, needs only the smoke tier (#77's cost cuts): the
    # harness is unit-tested, and the values that decide a verdict live in an input.
    # The merge-base's pairs again, so a skill moved between the trees isn't a change.
    pairs = equivalences_at(repo, base)
    reason = _input_change(
        selector_values(repo, base, suite),
        selector_values(repo, head, suite),
        pairs,
        "the selector ",
    )
    if reason is not None:
        return False, reason
    files = {
        commit: {
            p: _sha(read_blob(repo, commit, p))
            for p in list_files(repo, commit)
            if _matches(p, suite.paths)
        }
        for commit in (base, head)
    }
    # The merge-base's pairs, so a branch can't declare the equivalence it relies on.
    reason = _input_change(files[base], files[head], pairs)
    if reason is not None:
        return False, reason
    if not smoke_capable(suite):
        return False, f"the {suite.runner} runner has no smoke tier"
    return True, None


# Changes that reach every scenario: the global CLAUDE.md and the clause inventory; any hook
# in the suite's paths, matched below (#77's cost cuts); and a change to a criteria key the
# suite reads, which is a selector on the criteria file (#92), or to the whole file when the
# suite still lists it in `paths`.
SCENARIO_WIDE = ("config/CLAUDE.global.md", "eval/clauses.json")
CRITERIA_FILE = "eval/criteria.json"
OVERLAYS = "config/skill-overlays/"
SCENARIOS = "eval/scenarios.json"


def _scenarios_at(repo: Path, commit: str) -> dict[str, str]:
    """Each scenario at `commit`, by id, as canonical JSON."""
    blob = read_blob(repo, commit, SCENARIOS)
    try:
        items = json.loads(blob) if blob is not None else []
    except json.JSONDecodeError:
        items = []
    return {
        str(sc["id"]): json.dumps(sc, sort_keys=True)
        for sc in items if isinstance(items, list) and isinstance(sc, dict) and "id" in sc
    }  # fmt: skip


def scenario_selection(
    repo: Path, name: str, suite: Suite, base: str, head: str
) -> tuple[list[str] | None, list[str]]:
    """The scenario ids a full run must cover, or None for all; and changed overlays none loads.

    A scenario is selected when an overlay for a skill in its loadsSkills was added,
    changed, removed or renamed (a rename shows as both names), or when its own entry in
    eval/scenarios.json changed or is new.
    """
    if name not in gates_at(repo, base):
        return None, []
    changed = changed_paths(repo, base, head)
    hooks = [p for p in changed if p.startswith("config/hooks/") and _matches(p, suite.paths)]
    if hooks or any(p in SCENARIO_WIDE for p in changed):
        return None, []
    # A criteria edit that no selector of the suite covers must not leave the selection empty
    # while the suite is required, so a whole-file input or a changed key runs everything.
    if CRITERIA_FILE in changed and _matches(CRITERIA_FILE, suite.paths):
        return None, []
    criteria = [s for s in suite.selectors if s["file"] == CRITERIA_FILE]
    if any(selector_value(repo, base, s) != selector_value(repo, head, s) for s in criteria):
        return None, []
    skills = {
        PurePosixPath(p).stem
        for p in changed
        if p.startswith(OVERLAYS) and p.endswith(".md")
    }  # fmt: skip
    before, after = _scenarios_at(repo, base), _scenarios_at(repo, head)
    if SCENARIOS in changed and not after:
        # A scenarios file with no scenarios in it runs everything, so the runner fails
        # loudly instead of a record passing with nothing run.
        return None, []
    loads = {sid: set(json.loads(text).get("loadsSkills") or []) for sid, text in after.items()}
    picked = {sid for sid, text in after.items() if before.get(sid) != text}
    picked |= {sid for sid, used in loads.items() if used & skills}
    loaded = set().union(*loads.values()) if loads else set()
    return sorted(picked), sorted(skills - loaded)


def build_record(
    repo: Path,
    name: str,
    suite: Suite,
    head: str,
    base: str,
    result: dict[str, Any],
    meta: dict[str, Any],
    tier: str = "full",
    duration: float | None = None,
    smoke_cases: list[str | int] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "suite": name,
        **meta,
        "tier": tier,
        "harness": suite_harness(repo, head, suite),
        "inputs": suite_inputs(repo, head, suite),
        "result": result,
    }
    if duration is not None:
        record["durationSeconds"] = round(duration, 1)
    if tier == "smoke":
        record["smokeCases"] = list(smoke_cases or [])
    if suite.compare_base:
        record["base"] = {"ref": base, "inputs": suite_inputs(repo, base, suite)}
    return record


def dump_record(record: dict[str, Any]) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def _stale_inputs(
    name: str,
    check: str,
    want: dict[str, Any],
    got: object,
    why: str,
    pairs: tuple[tuple[str, str], ...],
) -> list[str]:
    """Like _stale, but an input that moved to a declared equivalent path, unchanged, matches.

    Selector entries ("file#part") move with their file, so they pair the same way.
    """
    have = got if isinstance(got, dict) else {}
    unpaired = set(want)
    stale = []
    for key in sorted(have):
        if key in want:
            unpaired.discard(key)
            if want[key] != have[key]:
                stale.append(key)
            continue
        moved = [q for q in _equivalents(key, pairs) if q in unpaired and q not in have]
        if moved and want[moved[0]] == have[key]:
            unpaired.discard(moved[0])
        else:
            stale.append(key)
    return [f"{name}: {check}: {key} {why}" for key in sorted(set(stale) | unpaired)]


def _stale(name: str, check: str, want: dict[str, Any], got: object, why: str) -> list[str]:
    have = got if isinstance(got, dict) else {}
    return [
        f"{name}: {check}: {key} {why}"
        for key in sorted(set(want) | set(have))
        if want.get(key, MISSING) != have.get(key, MISSING)
    ]


def read_record(repo: Path, name: str, commit: str) -> dict[str, Any] | None:
    blob = read_blob(repo, commit, f"{RECORDS_DIR}/{name}.json")
    if blob is None:
        return None
    try:
        record = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def record_tier(record: dict[str, Any] | None) -> str | None:
    """A record's tier; one written before #77 has none and counts as full."""
    return None if record is None else str(record.get("tier", "full"))


def case_count(repo: Path, commit: str, suite: Suite) -> tuple[str, int] | None:
    """The suite's case file and how many cases it holds, for suites that have one."""
    path = suite.cases or ("eval/scenarios.json" if suite.runner == "scenario" else None)
    blob = read_blob(repo, commit, path) if path else None
    try:
        cases = json.loads(blob) if blob is not None else None
    except json.JSONDecodeError:
        cases = None
    return (str(path), len(cases)) if isinstance(cases, list) else None


def _tier_failures(
    repo: Path,
    name: str,
    suite: Suite,
    head: str,
    base: str,
    record: dict[str, Any],
    why_full: str | None,
) -> list[str]:
    if record_tier(record) == "smoke":
        if why_full is not None:
            return [f"{name}: tier: this change needs the full tier ({why_full})"]
        ran, want = record.get("smokeCases"), list(suite.smoke)
        if ran != want:
            return [f"{name}: tier: the smoke record ran {ran}, not the smoke list {want}"]
        return []
    ran = (record.get("result") or {}).get("selectedCases")
    if suite.runner == "scenario" and isinstance(ran, list):
        picked, _ = scenario_selection(repo, name, suite, base, head)
        wanted = list(_scenarios_at(repo, head)) if picked is None else picked
        return [
            f"{name}: cases: the record didn't run {sid}, which this change selects"
            for sid in wanted
            if sid not in ran
        ]
    count = case_count(repo, head, suite)
    cases = (record.get("result") or {}).get("cases")
    if count is not None and isinstance(cases, list) and len(cases) != count[1]:
        return [f"{name}: cases: the full record has {len(cases)} cases, {count[0]} has {count[1]}"]
    return []


def check_record(
    repo: Path,
    name: str,
    suite: Suite,
    head: str,
    base: str,
    why_full: str | None = "no tier was worked out",
) -> list[str]:
    """Every failed check on suite `name`'s record; `why_full` is None when smoke is enough."""
    path = f"{RECORDS_DIR}/{name}.json"
    if read_blob(repo, head, path) is None:
        return [f"{name}: record: {path} is missing"]
    record = read_record(repo, name, head)
    if record is None:
        return [f"{name}: record: {path} does not parse"]
    failures = _tier_failures(repo, name, suite, head, base, record, why_full)
    pairs = equivalences_at(repo, base)
    failures += _stale_inputs(
        name,
        "inputs",
        suite_inputs(repo, head, suite),
        record.get("inputs"),
        "changed since the record",
        pairs,
    )
    failures += _stale(
        name,
        "harness",
        suite_harness(repo, head, suite),
        record.get("harness"),
        "changed since the record",
    )
    if suite.compare_base:
        recorded_base = record.get("base")
        failures += _stale_inputs(
            name,
            "base",
            suite_inputs(repo, base, suite),
            recorded_base.get("inputs") if isinstance(recorded_base, dict) else None,
            "differs from the merge-base",
            pairs,
        )
    result = record.get("result")
    if not (isinstance(result, dict) and result.get("pass") is True):
        failures.append(f"{name}: result: the recorded run did not pass")
    return failures


def needed_tier(repo: Path, name: str, base: str, head: str) -> tuple[str, str | None]:
    """The tier suite `name` needs, and why it needs the full one (None for smoke)."""
    if name not in gates_at(repo, head):
        return "full", "the suite is gone from eval/gates.json at HEAD"
    ok, reason = smoke_enough(repo, name, base, head)
    return ("smoke", None) if ok else ("full", reason)


def verify(
    repo: Path, base_ref: str
) -> tuple[list[str], list[str], dict[str, tuple[str, str | None]]]:
    """The required suites, every failed check (sorted), and each suite's needed and found tier."""
    head = resolve_commit(repo, "HEAD")
    base = merge_base(repo, base_ref, head)
    names = required_suites(repo, base, head)
    suites = suite_definitions(repo, base, head)
    failures: list[str] = []
    tiers: dict[str, tuple[str, str | None]] = {}
    for name in names:
        needed, why_full = needed_tier(repo, name, base, head)
        tiers[name] = (needed, record_tier(read_record(repo, name, head)))
        failures += check_record(repo, name, suites[name], head, base, why_full)
    return names, sorted(failures), tiers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="check the eval records the branch needs")
    v.add_argument("--base", default="origin/main", help="branch to compare against")
    args = ap.parse_args(argv)
    try:
        names, failures, tiers = verify(Path.cwd(), args.base)
    except (GatesError, GitError) as e:
        print(f"eval_records: {e}", file=sys.stderr)
        return 2
    if not names:
        print("no eval suites required")
        return 0
    for name in names:
        needed, found = tiers[name]
        print(f"{name}: needs {needed}, found {found or 'no record'}")
    for line in failures:
        print(line)
    if failures:
        return 1
    print(f"eval records valid: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
