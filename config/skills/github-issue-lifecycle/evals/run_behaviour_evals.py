#!/usr/bin/env python3
"""Development-only behaviour eval harness for github-issue-lifecycle (never run by the skill).

For each case in evals/evals.json it:
  1. builds an isolated Claude config dir from the repo: this skill copied in, every other skill
     under the repo's config/skills/ linked in, the live settings.json without its hooks, and
     credentials, ~/.claude.json and the plugins link from the live config; the dir (and the
     credential copy) is removed when the run ends, whatever happens;
  2. proves isolation with a canary probe that reads the skill list from the stream-json init event;
  3. creates a throwaway git repo with a local bare `origin`, a recording fake `gh` and a recording
     `git` that refuses any network remote, first on PATH, with an empty GH_CONFIG_DIR;
  4. runs `claude -p` once per turn (resuming the session for later turns) and grades the case's
     expectations against the recorded gh/git calls, the tool calls in order and the answers.

No case is run twice. The suite passes when every expectation of every critical case passes,
at least 90% of all other expectations pass, and no other case fails more than half of its own.

Usage: python3 -I evals/run_behaviour_evals.py [--model M] [--ids 1,2] [--workers 4]
                                               [--out results.json]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
# The repo's config/ directory: config/skills/<this skill>/evals/ is three levels down. When
# tools/cfg/eval.py runs this script from an extracted HEAD tree, this is that tree's config/.
CONFIG_DIR = SKILL_DIR.parents[1]
HOME = Path.home()
HOME_CLAUDE = HOME / ".claude"
OTHER_SHARE = 0.9
ALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep,Skill"
TURN_TIMEOUT = 900

FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"
REPO_URL = "https://github.com/o/r.git"

FAKE_GIT = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
real = os.environ["REAL_GIT"]
with open(os.environ["GIT_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
rest = list(args)
while rest and rest[0].startswith("-"):
    rest = rest[2:] if rest[0] in ("-c", "-C") else rest[1:]
# origin is stored as the GitHub address the fake gh reports. Only commands that move data
# get the rewrite to the local copy, so `git remote -v` still shows the GitHub address.
rewrite = []
if os.environ.get("EVAL_ORIGIN") and rest and rest[0] in ("push", "fetch", "pull", "ls-remote"):
    rewrite = ["-c", f"url.{os.environ['EVAL_ORIGIN']}.insteadOf={os.environ['EVAL_REPO_URL']}"]
if rest and rest[0] in ("push", "fetch", "pull"):
    words = [a for a in rest[1:] if not a.startswith("-")]
    remote = words[0] if words else "origin"
    # ls-remote --get-url applies insteadOf, so this is the address git would really use.
    got = subprocess.run([real, *rewrite, "ls-remote", "--get-url", remote],
                         capture_output=True, text=True)
    url = got.stdout.strip() if got.returncode == 0 else remote
    # Only a local path or a file:// URL may pass; anything else could reach a network.
    if not (url.startswith(("/", "./", "../", "file://")) or os.path.isdir(url)):
        sys.stderr.write(f"eval: git {rest[0]} to {url} refused; evals never reach a network\n")
        sys.exit(1)
os.execv(real, [real, *rewrite, *args])
"""

DEFAULT_GH = [
    {
        "match": r"^auth status",
        "stdout": "github.com\n  ✓ Logged in to github.com account mfracker",
    },
    {"match": r"^api user", "stdout": json.dumps({"login": "mfracker"})},
    {
        "match": r"^repo view",
        "stdout": json.dumps({"defaultBranchRef": {"name": "main"}, "nameWithOwner": "o/r"}),
    },
    {"match": r"^issue list", "stdout": "[]"},
]


def eval_tmp_root():
    """Temp root for eval config and working dirs; must be outside $HOME, where ancestor
    discovery would load ~/.claude/CLAUDE.md into every run."""
    root = tempfile.gettempdir()
    if Path(root).resolve().is_relative_to(Path.home().resolve()):
        sys.exit(f"refusing to run: temp dir {root} is under {Path.home()}; set TMPDIR=/tmp")
    return root


@contextmanager
def isolated_config(root, global_config=False):
    """A throwaway CLAUDE_CONFIG_DIR built from the repo. Removed on exit, credentials included.

    global_config=True adds the repo's global CLAUDE.md and skill overlays, to test routing
    into this skill. With the hooks removed, the overlays are present but not injected."""
    cfg = Path(root) / "config"
    cfg.mkdir(parents=True)
    try:
        if (HOME_CLAUDE / ".credentials.json").exists():
            shutil.copy2(HOME_CLAUDE / ".credentials.json", cfg / ".credentials.json")
            os.chmod(cfg / ".credentials.json", 0o600)
        if (HOME_CLAUDE / "settings.json").exists():
            settings = json.loads((HOME_CLAUDE / "settings.json").read_text())
            settings.pop("hooks", None)
            (cfg / "settings.json").write_text(json.dumps(settings, indent=2))
        if (HOME / ".claude.json").exists():
            shutil.copy2(HOME / ".claude.json", cfg / ".claude.json")
        (cfg / "plugins").symlink_to(HOME_CLAUDE / "plugins")
        (cfg / "gh-config").mkdir()
        skills = cfg / "skills"
        skills.mkdir()
        for entry in sorted((CONFIG_DIR / "skills").iterdir()):
            if entry.is_dir() and entry.name != SKILL_DIR.name:
                (skills / entry.name).symlink_to(entry.resolve())
        shutil.copytree(
            SKILL_DIR,
            skills / SKILL_DIR.name,
            ignore=shutil.ignore_patterns("tests", "evals", "__pycache__", ".pytest_cache"),
        )
        if global_config:
            shutil.copy2(CONFIG_DIR / "CLAUDE.global.md", cfg / "CLAUDE.md")
            shutil.copytree(
                CONFIG_DIR / "skill-overlays",
                cfg / "skill-overlays",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        yield cfg
    finally:
        shutil.rmtree(cfg, ignore_errors=True)


def claude(prompt, cwd, env, model=None, resume=None):
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--allowedTools",
        ALLOWED_TOOLS,
    ]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd += ["--resume", resume]
    proc = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=TURN_TIMEOUT
    )
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    session = next((e.get("session_id") for e in events if e.get("type") == "system"), resume)
    answer = next((e.get("result", "") for e in reversed(events) if e.get("type") == "result"), "")
    texts, bash, skills, loaded, tools, calls = [], [], [], [], [], []
    for e in events:
        if e.get("type") == "system" and e.get("subtype") == "init":
            skills = e.get("skills") or e.get("slash_commands") or []
        if e.get("type") != "assistant":
            continue
        for block in e.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                tools.append(block.get("name", ""))
                calls.append({"name": block.get("name", ""), "input": block.get("input", {})})
            if block.get("type") == "text":
                texts.append(block["text"])
            elif block.get("type") == "tool_use" and block.get("name") == "Bash":
                bash.append(block.get("input", {}).get("command", ""))
            elif block.get("type") == "tool_use" and block.get("name") == "Skill":
                loaded.append(block.get("input", {}).get("skill", ""))
    return {
        "session": session,
        "answer": answer or "\n".join(texts),
        "bash": bash,
        "skills": skills,
        "loaded": loaded,
        "tools": tools,
        "calls": calls,
        "stderr": proc.stderr[-2000:],
        "code": proc.returncode,
    }


def base_env(cfg):
    # Started from inside Claude Code, the environment carries that session's CLAUDE*
    # variables, which would change how the eval runs behave; only CLAUDE_CONFIG_DIR passes.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["CLAUDE_CONFIG_DIR"] = str(cfg)
    env["GH_CONFIG_DIR"] = str(Path(cfg) / "gh-config")
    env["GIT_TERMINAL_PROMPT"] = "0"
    # git refuses every transport but file://, for clone, ls-remote and submodules as well
    # as push, fetch and pull, so no git command can use the owner's SSH key.
    env["GIT_ALLOW_PROTOCOL"] = "file"
    return env


def probe(cfg, tmp, model):
    out = claude("Reply with the single word OK.", tmp, base_env(cfg), model)
    names = [s if isinstance(s, str) else s.get("name", "") for s in out["skills"]]
    joined = " ".join(names)
    return {
        "skills_seen": len(names),
        "has_new": "github-issue-lifecycle" in joined,
        "has_old": "issue-spec-driven-workflow" in joined,
        "ok": "github-issue-lifecycle" in joined and "issue-spec-driven-workflow" not in joined,
    }


def make_workspace(case, root):
    real_git = shutil.which("git")
    ws = root / "ws"
    origin = root / "origin.git"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Eval",
        "GIT_AUTHOR_EMAIL": "eval@example.invalid",
        "GIT_COMMITTER_NAME": "Eval",
        "GIT_COMMITTER_EMAIL": "eval@example.invalid",
    }

    def git(*args, cwd=ws):
        subprocess.run([real_git, *args], cwd=cwd, env=env, check=True, capture_output=True)

    subprocess.run([real_git, "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    ws.mkdir()
    git("init", "-q", "-b", "main")
    (ws / "README.md").write_text("# roster app\n")
    git("add", ".")
    git("commit", "-q", "-m", "chore: initial commit (#1)")
    git("remote", "add", "origin", str(origin))
    if case.get("commit_files"):
        write_files(ws, case.get("files", {}))
        git("add", ".")
        git("commit", "-q", "-m", "chore: add the case's files (#1)")
    git("push", "-q", "origin", "main")
    for branch in case.get("remote_branches", []):
        git("push", "-q", "origin", f"main:refs/heads/{branch}")
    if case.get("commits"):
        git("switch", "-q", "-c", "feature/42-add-csv-export")
        for i, commit in enumerate(case["commits"]):
            if isinstance(commit, str):
                commit = {"message": commit, "files": {f"change{i}.txt": commit + "\n"}}
            write_files(ws, commit["files"])
            git("add", ".")
            git("commit", "-q", "-m", commit["message"])
    if not case.get("commit_files"):
        write_files(ws, case.get("files", {}))
    # origin shows as the GitHub repo the fake gh reports; the git shim sends data commands to
    # the local copy (EVAL_ORIGIN), so the remote looks real and nothing leaves the machine.
    git("remote", "set-url", "origin", case.get("origin_url", REPO_URL))
    return ws


def seed_state(case):
    """The fake gh's starting state: the case's issues and pull requests."""
    state = {"login": "mfracker", "repo": "o/r", "issues": {}, "prs": {}}
    for n, issue in case.get("issues", {}).items():
        state["issues"][str(n)] = {"number": int(n), "labels": [], "assignees": [],
                                   "comments": [], "events": [], **issue}  # fmt: skip
    for n, pr in case.get("prs", {}).items():
        state["prs"][str(n)] = {"number": int(n), **pr}
    if "next_number" in case:
        state["next"] = case["next_number"]
    return state


def write_files(ws, files):
    for rel, content in files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(content)


def inspect_git(ws, *args):
    """Plumbing git in the workspace after a run, with hooks and fsmonitor switched off,
    because the model may have changed the repo's config."""
    safe = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
    cmd = [shutil.which("git"), *safe, *args]
    return subprocess.run(cmd, cwd=ws, capture_output=True, text=True, check=True).stdout


def branch_heads(ws):
    """Commits at the tips of every branch, pushed branch and worktree HEAD (never the stash)."""
    tips = inspect_git(ws, "for-each-ref", "--format=%(objectname)", "refs/heads", "refs/remotes")
    heads = [
        line.split()[1]
        for line in inspect_git(ws, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("HEAD ")
    ]
    return sorted(set(tips.split()) | set(heads))


def files_touched(ws, start):
    """Files changed by commits made since `start` (a branch_heads() list), sorted."""
    new = inspect_git(ws, "rev-list", *branch_heads(ws), "--not", *start).split()
    files = set()
    for commit in new:
        out = inspect_git(ws, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit)
        files.update(out.split())
    return sorted(files)


def git_subcommand(args):
    """Drop git's global options (-c k=v, -C dir, ...) so grading sees the subcommand."""
    args = list(args)
    while args and args[0].startswith("-"):
        takes_value = args[0] in ("-c", "-C", "--git-dir", "--work-tree", "--namespace")
        args = args[2:] if takes_value else args[1:]
    return " ".join(args)


def grade(
    expectations, gh_calls, git_calls, answer, bash, loaded=(), tools=(), calls=(), touched=()
):
    gh_strs = [" ".join(c["args"]) for c in gh_calls]
    git_strs = [git_subcommand(c) for c in git_calls]
    results = []
    for exp in expectations:
        try:
            results.append(
                {
                    "text": exp,
                    "passed": bool(
                        check(
                            exp,
                            gh_calls,
                            gh_strs,
                            git_strs,
                            answer,
                            bash,
                            loaded,
                            tools,
                            calls,
                            touched,
                        )
                    ),
                }
            )
        except re.error as exc:
            results.append({"text": exp, "passed": False, "error": f"bad pattern: {exc}"})
    return results


def skill_matches(name, wanted):
    return name == wanted or name.endswith(":" + wanted)


def loaded_before(calls, skill, command_re):
    """The skill's Skill call comes before the first Bash command matching command_re."""

    def first(match):
        return next((i for i, c in enumerate(calls) if match(c["name"], c["input"])), None)

    load = first(lambda name, data: name == "Skill" and skill_matches(data.get("skill", ""), skill))
    run = first(
        lambda name, data: name == "Bash" and re.search(command_re, data.get("command", ""))
    )
    return load is not None and run is not None and load < run


def check(
    exp, gh_calls, gh_strs, git_strs, answer, bash, loaded=(), tools=(), calls=(), touched=()
):
    kind, _, arg = exp.partition(": ")
    if kind == "gh-called":
        ok = any(re.search(arg, s) for s in gh_strs)
    elif kind == "gh-not-called":
        ok = not any(re.search(arg, s) for s in gh_strs)
    elif kind == "gh-count":
        n, _, pat = arg.partition(" ")
        ok = sum(1 for s in gh_strs if re.search(pat, s)) == int(n)
    elif kind == "gh-order":
        first, _, second = arg.partition(" ||| ")
        idx = [i for i, s in enumerate(gh_strs) if re.search(first, s)]
        ok = bool(idx) and any(re.search(second, s) for s in gh_strs[idx[0] + 1 :])
    elif kind in ("gh-body-matches", "gh-body-not-matches"):
        call_re, _, body_re = arg.partition(" ::: ")
        hits = [
            c.get("body", "")
            for c, s in zip(gh_calls, gh_strs, strict=True)
            if re.search(call_re, s)
        ]
        found = any(re.search(body_re, b) for b in hits)
        ok = found if kind == "gh-body-matches" else (bool(hits) and not found)
    elif kind == "gh-body-never-matches":
        call_re, _, body_re = arg.partition(" ::: ")
        ok = not any(
            re.search(body_re, c.get("body", ""))
            for c, s in zip(gh_calls, gh_strs, strict=True)
            if re.search(call_re, s)
        )
    elif kind == "git-called":
        ok = any(re.search(arg, s) for s in git_strs)
    elif kind == "git-not-called":
        ok = not any(re.search(arg, s) for s in git_strs)
    elif kind == "answer-matches":
        ok = bool(re.search(arg, answer))
    elif kind == "answer-not-matches":
        ok = not re.search(arg, answer)
    elif kind == "skill-loaded":
        ok = any(skill_matches(name, arg) for name in loaded)
    elif kind == "commits-only-touch":
        ok = all(re.fullmatch(arg, path) for path in touched)
    elif kind == "loaded-before":
        skill, _, command_re = arg.partition(" ||| ")
        ok = loaded_before(calls, skill, command_re)
    elif kind == "tool-not-used":
        ok = not any(re.search(arg, t) for t in tools)
    elif kind == "cmd-not-run":
        ok = not any(re.search(arg, b) for b in bash)
    else:
        ok = False
    return ok


def read_log(path, as_entries=True):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_case_once(case, cfg, model):
    if case.get("global_config"):
        with tempfile.TemporaryDirectory(prefix="gil-globalcfg-", dir=eval_tmp_root()) as groot:
            with isolated_config(groot, global_config=True) as gcfg:
                return _run_case_in(case, gcfg, model)
    return _run_case_in(case, cfg, model)


def _run_case_in(case, cfg, model):
    with tempfile.TemporaryDirectory(prefix=f"gil-eval-{case['id']}-", dir=eval_tmp_root()) as tmp:
        root = Path(tmp)
        ws = make_workspace(case, root)
        start = branch_heads(ws)
        bin_dir, state = root / "bin", root / "state"
        bin_dir.mkdir()
        state.mkdir()
        for name, src in (("gh", FAKE_GH.read_text()), ("git", FAKE_GIT)):
            (bin_dir / name).write_text(src)
            (bin_dir / name).chmod(0o755)
        responses = {"case": case.get("fake_gh", []), "defaults": DEFAULT_GH}
        (state / "responses.json").write_text(json.dumps(responses))
        (state / "state.json").write_text(json.dumps(seed_state(case)))
        env = base_env(cfg)
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "GH_STATE": str(state),
                "GH_LOG": str(root / "gh.log"),
                "GIT_LOG": str(root / "git.log"),
                "REAL_GIT": shutil.which("git"),
                "EVAL_ORIGIN": str(root / "origin.git"),
                "EVAL_REPO_URL": case.get("origin_url", REPO_URL),
            }
        )
        session, answers, bash, turn1, loaded, tools, calls = None, [], [], None, [], [], []
        for n, prompt in enumerate(case["turns"]):
            out = claude(prompt, ws, env, model, resume=session)
            session = out["session"]
            answers.append(out["answer"])
            bash += out["bash"]
            loaded += out["loaded"]
            tools += out["tools"]
            calls += out["calls"]
            if n == 0 and case.get("turn1_expectations"):
                turn1 = grade(
                    case["turn1_expectations"],
                    read_log(root / "gh.log"),
                    read_log(root / "git.log"),
                    out["answer"],
                    out["bash"],
                )
        gh_calls, git_calls = read_log(root / "gh.log"), read_log(root / "git.log")
        results = (turn1 or []) + grade(
            case["expectations"],
            gh_calls,
            git_calls,
            "\n\n".join(answers),
            bash,
            loaded,
            tools,
            calls,
            files_touched(ws, start),
        )
        return {
            "passed": all(r["passed"] for r in results),
            "expectations": results,
            "gh_calls": [" ".join(c["args"]) for c in gh_calls],
            "skills_loaded": loaded,
            "tools_used": tools,
            "answers": [a[-2500:] for a in answers],
            "git_calls": [
                git_subcommand(c)
                for c in git_calls
                if not git_subcommand(c).startswith(
                    ("config", "status", "remote get-url", "ls-files", "rev-parse")
                )
            ],
            "answer": answers[-1][-3000:],
        }


def run_case(case, cfg, model):
    """Run a case once. A failure counts as it ran: the suite decides on totals (verdict)."""
    started = time.time()
    run = run_case_once(case, cfg, model)
    return {
        "id": case["id"],
        "name": case["name"],
        "covers": case.get("covers", []),
        "critical": bool(case.get("critical")),
        **run,
        "seconds": round(time.time() - started, 1),
    }


def verdict(results):
    """Every critical expectation passes, at least 90% of the others pass, and no other case
    fails more than half of its own expectations."""
    critical = [r["id"] for r in results if r["critical"] and not r["passed"]]
    others = [r for r in results if not r["critical"]]
    flags = [e["passed"] for r in others for e in r["expectations"]]
    collapsed = [
        r["id"]
        for r in others
        if sum(not e["passed"] for e in r["expectations"]) * 2 > len(r["expectations"])
    ]
    share = sum(flags) / len(flags) if flags else 1.0
    return {
        "pass": not critical and share >= OTHER_SHARE and not collapsed,
        "criticalFailures": critical,
        "collapsedCases": collapsed,
        "otherPassed": sum(flags),
        "otherTotal": len(flags),
        "otherShare": round(share, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--model", help="model for claude -p (default: the user's configured model)"
    )
    parser.add_argument("--ids", help="comma-separated case ids to run")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default="behaviour-results.json")
    args = parser.parse_args()
    cases = json.loads((SKILL_DIR / "evals" / "evals.json").read_text())["evals"]
    if args.ids:
        wanted = {int(i) for i in args.ids.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    with tempfile.TemporaryDirectory(prefix="gil-config-", dir=eval_tmp_root()) as root:
        with isolated_config(root) as cfg:
            canary = probe(cfg, root, args.model)
            print(f"isolation probe: {canary}", file=sys.stderr)
            if not canary["ok"]:
                Path(args.out).write_text(json.dumps({"probe": canary, "results": []}, indent=1))
                return 2
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                partial = Path(args.out + ".partial.jsonl")

                def run_and_save(case):
                    result = run_case(case, cfg, args.model)
                    with partial.open("a") as fh:
                        fh.write(json.dumps(result) + "\n")
                    return result

                results = list(pool.map(run_and_save, cases))
    summary = verdict(results)
    out = {"model": args.model or "default", "probe": canary, "results": results}
    Path(args.out).write_text(json.dumps({**out, "summary": summary}, indent=1))
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        crit = " (critical)" if r["critical"] else ""
        print(f"{mark}  {r['id']:>2} {r['name']}{crit}  ({r['seconds']}s)")
    print(json.dumps(summary))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
