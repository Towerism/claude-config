"""AC-6 scenario eval (old vs new CLAUDE.md) and AC-4 rubric, headless and arm-isolated.

python3 -I run_eval.py [--only S-01,S-02] [--workers 6]

Isolation (see impl/FR-18a-results.md): each arm runs `claude -p` with its own
CLAUDE_CONFIG_DIR (arm CLAUDE.md + settings.json + symlinked plugins + credentials,
removed afterwards) and a cwd outside /home/mfracker, so ~/.claude/CLAUDE.md isn't
picked up by ancestor discovery. A canary probe proves isolation before scoring.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IMPL = Path(__file__).resolve().parent.parent
REAL = Path("/home/mfracker/.claude")
JOBTMP = Path("/home/mfracker/.claude/jobs/9130cbd8/tmp")
OLD_MD = Path("/home/mfracker/dev/scratch/claude-md-rewrite/baseline/CLAUDE.md.orig")
NEW_MD = IMPL / "deliverable" / "CLAUDE.md"
QUALITY = Path("/home/mfracker/.claude/plugins/cache/claude-plugins-official/claude-md-management/1.0.0/skills/claude-md-improver/references/quality-criteria.md")
OUT = IMPL / "eval" / "results"
PREAMBLE = ("Answer in prose only. Do not use any tools. Describe exactly what you would do next, in order, "
            "and why, naming any skills you would use.\n\n")


def make_arm(name: str, md_text: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"arm-{name}.", dir=JOBTMP))
    (d / "CLAUDE.md").write_text(md_text)
    shutil.copy2(REAL / "settings.json", d / "settings.json")
    (d / "plugins").symlink_to(REAL / "plugins")
    shutil.copy2(REAL / ".credentials.json", d / ".credentials.json")
    os.chmod(d / ".credentials.json", 0o600)
    return d


MODEL = None  # --model for the scenario arms only; the grader uses the default model


def claude(arm: Path, prompt: str, timeout=400, model=None) -> str:
    cwd = tempfile.mkdtemp(prefix="cmv2-eval.", dir="/tmp")
    cmd = ["claude", "-p", "--tools", "", "--output-format", "json"] + (["--model", model] if model else [])
    try:
        r = subprocess.run(cmd, input=prompt, text=True,
                           capture_output=True, cwd=cwd, timeout=timeout,
                           env={**os.environ, "CLAUDE_CONFIG_DIR": str(arm)})
        return json.loads(r.stdout).get("result") or ""
    except Exception as e:  # recorded as an empty answer; graded as a failure
        return f"<run error: {e}>"
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


def overlay_text(skill: str) -> str:
    """What the real hook injects for `skill`, using the deliverable overlays."""
    home = Path(tempfile.mkdtemp(prefix="ovl.", dir=JOBTMP))
    try:
        shutil.copytree(IMPL / "deliverable" / "skill-overlays", home / "skill-overlays")
        (home / "plugins").symlink_to(REAL / "plugins")
        payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": {"skill": skill}})
        r = subprocess.run([sys.executable, "-I", str(IMPL / "deliverable" / "hooks" / "skill_overlay.py")],
                           input=payload, capture_output=True, text=True, env={"PATH": os.environ["PATH"], "CLAUDE_HOME": str(home)})
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    finally:
        shutil.rmtree(home, ignore_errors=True)


def criteria(sc: dict, arm_name: str) -> dict:
    """Arm-specific additions (e.g. S-03's new-arm check) extend mustInclude for that arm only."""
    extra = (sc.get("mustIncludeByArm") or {}).get(arm_name, [])
    return {"mustInclude": sc["mustInclude"] + extra, "mustNotInclude": sc["mustNotInclude"]}


def grade(grader: Path, sc: dict, answer: str, arm_name: str) -> dict:
    crit = criteria(sc, arm_name)
    prompt = (
        "You are grading an AI assistant's answer against binary criteria. Judge only what the answer says it "
        "would do. Respond with ONLY a JSON object: {\"mustInclude\": [true/false per item], "
        "\"mustNotInclude\": [true/false per item: true means the forbidden behaviour IS present]}.\n\n"
        f"SCENARIO:\n{sc['prompt']}\n\nANSWER:\n{answer}\n\nCRITERIA:\n{json.dumps(crit, indent=1)}\n")
    raw = claude(grader, prompt)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        g = json.loads(m.group(0))
        ok = all(g["mustInclude"]) and not any(g["mustNotInclude"]) \
            and len(g["mustInclude"]) == len(crit["mustInclude"]) and len(g["mustNotInclude"]) == len(crit["mustNotInclude"])
    except Exception:
        g, ok = {"unparsed": raw[:500]}, False
    return {"grade": g, "pass": ok}


def run_case(arm: Path, arm_name: str, grader: Path, sc: dict) -> dict:
    prefix = ""
    if arm_name == "new":
        for s in sc["loadsSkills"]:
            prefix += f"[You loaded {s}. A hook then injected this context:]\n{overlay_text(s)}\n\n"
    elif sc["loadsSkills"]:
        prefix = "".join(f"[You loaded {s}.]\n" for s in sc["loadsSkills"]) + "\n"
    attempts = []
    for _ in range(2):  # EC-8: one re-run; fails only if both fail
        ans = claude(arm, PREAMBLE + prefix + sc["prompt"], model=MODEL)
        g = grade(grader, sc, ans, arm_name)
        attempts.append({"answer": ans, **g})
        if g["pass"]:
            break
    return {"id": sc["id"], "arm": arm_name, "pass": any(a["pass"] for a in attempts), "attempts": attempts}


def summarize(results, scenarios):
    """Markdown table plus exit code: 1 on any regression or any new-rule-only scenario failing on the new arm."""
    by = {}
    for r in results:
        by.setdefault(r["id"], {})[r["arm"]] = r["pass"]
    tags = {s["id"]: s.get("tags", []) for s in scenarios}
    byarm = {s["id"] for s in scenarios if s.get("mustIncludeByArm")}
    lines = ["| Scenario | Old | New | Note |", "|---|---|---|---|"]
    regress, new_rule_fail = [], []
    for sid, v in sorted(by.items()):
        note = ""
        if "new-rule-only" in tags[sid]:
            note = "new-rule coverage; old arm not a regression gate"
            if not v.get("new"):
                new_rule_fail.append(sid)
                note += "; NEW-RULE FAIL"
        elif v.get("old") and not v.get("new"):
            note = "exempt (superseded:D-4)" if "superseded:D-4" in tags[sid] else "REGRESSION"
            if note == "REGRESSION":
                regress.append(sid)
        if sid in byarm:
            note = (note + "; " if note else "") + "new arm graded with an extra arm-specific check"
        lines.append(f"| {sid} | {'pass' if v.get('old') else 'FAIL'} | {'pass' if v.get('new') else 'FAIL'} | {note} |")
    text = "\n".join(lines) + f"\n\nRegressions: {regress or 'none'}\nNew-rule failures: {new_rule_fail or 'none'}\n"
    return text, 1 if (regress or new_rule_fail) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--suffix", default="", help="output file suffix, e.g. -addendum")
    ap.add_argument("--no-rubric", action="store_true")
    ap.add_argument("--model", help="model for the scenario arms (grader stays on the default)")
    ap.add_argument("--arms", default="old,new")
    args = ap.parse_args()
    global MODEL
    MODEL = args.model
    arm_names = args.arms.split(",")
    OUT.mkdir(parents=True, exist_ok=True)
    scenarios = json.loads((IMPL / "eval" / "scenarios.json").read_text())
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in args.only.split(",")]
    canary = {"old": "Eval arm marker: CANARY-OLD-5150", "new": "Eval arm marker: CANARY-NEW-6260"}
    arms = {"old": make_arm("old", OLD_MD.read_text() + "\n" + canary["old"] + "\n"),
            "new": make_arm("new", NEW_MD.read_text() + "\n" + canary["new"] + "\n")}
    grader = make_arm("grader", "# Grader\nFollow the grading instructions exactly.\n")
    try:
        probes = {}
        for name, arm in arms.items():
            if name not in arm_names:
                continue
            a = claude(arm, "Without tools: quote every line in your instructions containing 'Eval arm marker', "
                            "and say whether your context contains a heading starting 'Adversarial review:'.")
            other = canary["new" if name == "old" else "old"]
            probes[name] = {"answer": a, "isolated": canary[name] in a and other not in a}
        (OUT / f"ac6-probes{args.suffix}.json").write_text(json.dumps(probes, indent=1))
        if not probes or not all(p["isolated"] for p in probes.values()):
            print("ARM ISOLATION FAILED", json.dumps(probes, indent=1))
            return 1
        jobs = [(arms[n], n, grader, s) for s in scenarios for n in arm_names]
        with ThreadPoolExecutor(args.workers) as ex:
            results = list(ex.map(lambda j: run_case(*j), jobs))
        (OUT / f"ac6-results{args.suffix}.json").write_text(json.dumps(results, indent=1))
        rubric = "" if args.no_rubric else claude(grader, "Score this global CLAUDE.md (a user-wide file, not a project file) against the rubric "
                        "below. Score 'Commands/workflows' and 'Architecture clarity' on how clearly it maps the "
                        "workflow and which skill is used where, not on build commands. Respond with ONLY JSON: "
                        "{\"criteria\": {name: score}, \"total\": n, \"issues\": [..]}.\n\nRUBRIC:\n"
                        + QUALITY.read_text() + "\n\nCLAUDE.md:\n" + NEW_MD.read_text())
        if rubric:
            (OUT / "ac4-rubric.json").write_text(rubric)
    finally:
        for d in list(arms.values()) + [grader]:
            (d / ".credentials.json").unlink(missing_ok=True)
            shutil.rmtree(d, ignore_errors=True)
    summary, code = summarize(results, scenarios)
    (OUT / f"ac6-summary{args.suffix}.md").write_text(summary)
    print(summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
