#!/usr/bin/env python3
"""Development-only behaviour eval harness for jira-ticket-lifecycle (never run by the skill).

For each case in evals/evals.json it:
  1. builds an isolated Claude config dir from the repo: this skill copied in, every other skill
     under the repo's config/skills/ linked in (github-issue-lifecycle, plain-prose and
     writing-docs among them), the live settings.json without its hooks, and credentials,
     ~/.claude.json and the plugins link from the live config; the dir (and the credential copy)
     is removed when the run ends, whatever happens;
  2. proves isolation with a canary probe that reads the skill list from the stream-json init event;
  3. creates a throwaway git repo with a local bare `origin`, the stateful fake CLI on PATH as
     both `jira` and `gh`, a local REST server over the same state, a recording `git` that
     refuses any network remote, an empty GH_CONFIG_DIR, and temp XDG_CONFIG_HOME,
     XDG_STATE_HOME and jira CLI config holding only invented values;
  4. runs `claude -p` once per turn (resuming the session for later turns) and grades the case's
     expectations against the recorded jira, gh, REST and git calls, the tool calls in order,
     the answers and the fake's final state.

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
SKILL_NAME = SKILL_DIR.name
# The repo's config/ directory: config/skills/<this skill>/evals/ is three levels down. When
# tools/cfg/eval.py runs this script from an extracted HEAD tree, this is that tree's config/.
CONFIG_DIR = SKILL_DIR.parents[1]
HOME = Path.home()
HOME_CLAUDE = HOME / ".claude"
OTHER_SHARE = 0.9
ALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep,Skill"
TURN_TIMEOUT = 900
FAKE_CLI = Path(__file__).resolve().parent / "fake_cli.py"
REPO_URL = "https://github.com/o/r.git"
SITE = "https://example.atlassian.net"
TOKEN = "eval-placeholder-token"  # noqa: S105  (a fake value the fake REST server accepts)
SETTINGS = {
    "project": "PAY",
    "site": SITE,
    "states": {"inProgress": "In Progress", "review": "In Review", "done": "Done"},
    "stateOrder": ["To Do", "In Progress", "In Review", "Done"],
    "addToActiveSprint": True,
}
ME = {"accountId": "acc-owner", "email": "owner@example.com", "displayName": "Owner"}
OTHER = {
    "accountId": "acc-other",
    "email": "someone-else@example.com",
    "displayName": "Someone Else",
}

FAKE_GIT = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
real = os.environ["REAL_GIT"]
with open(os.environ["GIT_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
rest = list(args)
while rest and rest[0].startswith("-"):
    rest = rest[2:] if rest[0] in ("-c", "-C") else rest[1:]
rewrite = []
if os.environ.get("EVAL_ORIGIN") and rest and rest[0] in ("push", "fetch", "pull", "ls-remote"):
    rewrite = ["-c", f"url.{os.environ['EVAL_ORIGIN']}.insteadOf={os.environ['EVAL_REPO_URL']}"]
if rest and rest[0] in ("push", "fetch", "pull"):
    words = [a for a in rest[1:] if not a.startswith("-")]
    remote = words[0] if words else "origin"
    got = subprocess.run([real, *rewrite, "ls-remote", "--get-url", remote],
                         capture_output=True, text=True)
    url = got.stdout.strip() if got.returncode == 0 else remote
    if not (url.startswith(("/", "./", "../", "file://")) or os.path.isdir(url)):
        sys.stderr.write(f"eval: git {rest[0]} to {url} refused; evals never reach a network\n")
        sys.exit(1)
os.execv(real, [real, *rewrite, *args])
"""


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
        if (HOME_CLAUDE / "plugins").exists():
            (cfg / "plugins").symlink_to(HOME_CLAUDE / "plugins")
        (cfg / "gh-config").mkdir()
        skills = cfg / "skills"
        skills.mkdir()
        for entry in sorted((CONFIG_DIR / "skills").iterdir()):
            if entry.is_dir() and entry.name != SKILL_NAME:
                (skills / entry.name).symlink_to(entry.resolve())
        shutil.copytree(
            SKILL_DIR,
            skills / SKILL_NAME,
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


def parse_stream(stdout, resume=None):
    """The parts of a stream-json run the grader needs."""
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    session = next((e.get("session_id") for e in events if e.get("type") == "system"), resume)
    answer = next((e.get("result", "") for e in reversed(events) if e.get("type") == "result"), "")
    texts, bash, skills, loaded, tools, calls = [], [], [], [], [], []
    for e in events:
        if e.get("type") == "system" and e.get("subtype") == "init":
            skills = e.get("skills") or e.get("slash_commands") or []
        if e.get("type") != "assistant":
            continue
        for block in e.get("message", {}).get("content", []):
            if block.get("type") == "text":
                texts.append(block["text"])
            if block.get("type") != "tool_use":
                continue
            name, data = block.get("name", ""), block.get("input", {})
            tools.append(name)
            calls.append({"name": name, "input": data})
            if name == "Bash":
                bash.append(data.get("command", ""))
            elif name == "Skill":
                loaded.append(data.get("skill", ""))
    return {
        "session": session,
        "answer": answer or "\n".join(texts),
        "bash": bash,
        "skills": skills,
        "loaded": loaded,
        "tools": tools,
        "calls": calls,
    }


def claude(prompt, cwd, env, model=None, resume=None):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    cmd += ["--allowedTools", ALLOWED_TOOLS]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd += ["--resume", resume]
    proc = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=TURN_TIMEOUT
    )
    return {
        **parse_stream(proc.stdout, resume),
        "stderr": proc.stderr[-2000:],
        "code": proc.returncode,
    }


def base_env(cfg):
    # Started from inside Claude Code, the environment carries that session's CLAUDE*
    # variables, which would change how the eval runs behave; only CLAUDE_CONFIG_DIR passes.
    # Jira variables from the owner's shell never reach a run.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CLAUDE", "JIRA_", "LIFECYCLE_", "XDG_CONFIG", "XDG_STATE"))
    }
    env["CLAUDE_CONFIG_DIR"] = str(cfg)
    env["GH_CONFIG_DIR"] = str(Path(cfg) / "gh-config")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ALLOW_PROTOCOL"] = "file"
    return env


def probe(cfg, tmp, model):
    out = claude("Reply with the single word OK.", tmp, base_env(cfg), model)
    names = [s if isinstance(s, str) else s.get("name", "") for s in out["skills"]]
    joined = " ".join(names)
    return {
        "skills_seen": len(names),
        "has_skill": SKILL_NAME in joined,
        "ok": SKILL_NAME in joined,
    }


def write_files(ws, files):
    for rel, content in files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(content)


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
    git("commit", "-q", "-m", "chore: initial commit (PAY-1)")
    git("remote", "add", "origin", str(origin))
    if case.get("commit_files"):
        write_files(ws, case.get("files", {}))
        git("add", ".")
        git("commit", "-q", "-m", "chore: add the case's files (PAY-1)")
    git("push", "-q", "origin", "main")
    for branch in case.get("remote_branches", []):
        git("push", "-q", "origin", f"main:refs/heads/{branch}")
    if case.get("commits"):
        git("switch", "-q", "-c", case.get("branch", "feature/PAY-142-add-csv-export"))
        for i, commit in enumerate(case["commits"]):
            if isinstance(commit, str):
                commit = {"message": commit, "files": {f"change{i}.txt": commit + "\n"}}
            write_files(ws, commit["files"])
            git("add", ".")
            git("commit", "-q", "-m", commit["message"])
    if not case.get("commit_files"):
        write_files(ws, case.get("files", {}))
    git("remote", "set-url", "origin", REPO_URL)
    return ws


def seed_state(case):
    """The fake CLI's starting state: the case's tickets, sprints and pull requests."""
    tickets = {}
    for key, t in case.get("tickets", {}).items():
        tickets[key] = {
            "summary": "",
            "type": "Task",
            "state": "To Do",
            "description": "",
            "labels": [],
            "assignee": None,
            "comments": [],
            "attachments": [],
            "links": [],
            "changelog": [],
            **t,
        }
    prs = {
        str(n): {
            "number": int(n),
            "url": f"https://github.com/o/r/pull/{n}",
            "state": "OPEN",
            "isDraft": True,
            "reviewDecision": "",
            "body": "",
            "title": "",
            "mergedAt": None,
            **pr,
        }
        for n, pr in case.get("prs", {}).items()
    }
    return {
        "me": ME,
        "users": [OTHER],
        "tickets": tickets,
        "sprints": case.get(
            "sprints", [{"id": 7, "name": "Sprint 7", "state": "active", "issues": []}]
        ),
        "prs": prs,
        "canned": case.get("fake_cli", []),
        "restCanned": case.get("rest_canned", []),
        "nextAttachment": 0,
    }


def case_environment(case, root, cfg):
    """bin/ with the fakes, temp settings, the jira CLI config, and the REST server's address."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name in ("jira", "gh"):
        shutil.copy(FAKE_CLI, bin_dir / name)
        (bin_dir / name).chmod(0o755)
    (bin_dir / "git").write_text(FAKE_GIT)
    (bin_dir / "git").chmod(0o755)
    xdg = root / "xdg-config"
    (xdg / "claude-config").mkdir(parents=True)
    local = {"defaultLifecycle": SKILL_NAME, "jira": {**SETTINGS, **case.get("settings", {})}}
    (xdg / "claude-config" / "local.json").write_text(json.dumps(local, indent=1))
    jira_config = root / "jira-config.yml"
    jira_config.write_text(
        f"server: {SITE}\nlogin: {ME['email']}\nproject:\n  key: PAY\nboard:\n  id: 1\n"
    )
    (root / "state.json").write_text(json.dumps(seed_state(case), indent=1))
    env = base_env(cfg)
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "XDG_CONFIG_HOME": str(xdg),
            "XDG_STATE_HOME": str(root / "xdg-state"),
            "JIRA_CONFIG_FILE": str(jira_config),
            "JIRA_API_TOKEN": TOKEN,
            "FAKE_CLI_STATE": str(root / "state.json"),
            "FAKE_CLI_LOG": str(root / "calls.log"),
            "GIT_LOG": str(root / "git.log"),
            "REAL_GIT": shutil.which("git"),
            "EVAL_ORIGIN": str(root / "origin.git"),
            "EVAL_REPO_URL": REPO_URL,
        }
    )
    for name in case.get("unset_env", []):
        env.pop(name, None)
    return env


@contextmanager
def rest_server(env, root):
    port_file = root / "rest-port"
    proc = subprocess.Popen(
        [sys.executable, "-I", str(FAKE_CLI), "serve", "--port-file", str(port_file)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(1000):
            if port_file.exists():
                break
            time.sleep(0.01)
        env["LIFECYCLE_JIRA_REST"] = f"http://127.0.0.1:{port_file.read_text()}"
        yield env
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def inspect_git(ws, *args):
    """Plumbing git in the workspace after a run, with hooks and fsmonitor switched off,
    because the model may have changed the repo's config."""
    safe = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
    return subprocess.run(
        [shutil.which("git"), *safe, *args], cwd=ws, capture_output=True, text=True, check=True
    ).stdout


def branch_heads(ws):
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


class Run:
    """Everything a case produced, for grading."""

    def __init__(
        self,
        cli_calls=(),
        git_calls=(),
        answer="",
        bash=(),
        loaded=(),
        tools=(),
        calls=(),
        touched=(),
        state=None,
        root=None,
    ):
        self.jira = [" ".join(c["argv"]) for c in cli_calls if c.get("prog") == "jira"]
        self.gh = [" ".join(c["argv"]) for c in cli_calls if c.get("prog") == "gh"]
        self.rest = [f"{c['method']} {c['path']}" for c in cli_calls if c.get("prog") == "rest"]
        self.git = [git_subcommand(c) for c in git_calls]
        self.answer, self.bash, self.loaded = answer, list(bash), list(loaded)
        self.tools, self.calls, self.touched = list(tools), list(calls), list(touched)
        self.state = state or {"tickets": {}}
        self.root = root


def any_match(pattern, strings):
    return any(re.search(pattern, s) for s in strings)


def check(exp, run):
    kind, _, arg = exp.partition(": ")
    streams = {"jira": run.jira, "gh": run.gh, "rest": run.rest, "git": run.git, "cmd": run.bash}
    prog, _, how = kind.partition("-")
    if prog in streams and how in ("called", "not-called", "run", "not-run"):
        found = any_match(arg, streams[prog])
        return found if how in ("called", "run") else not found
    if kind in ("jira-count", "gh-count"):
        n, _, pattern = arg.partition(" ")
        return sum(1 for s in streams[prog] if re.search(pattern, s)) == int(n)
    if kind in ("ticket-has-label", "ticket-lacks-label"):
        key, _, label = arg.partition(" ")
        has = label in run.state["tickets"].get(key, {}).get("labels", [])
        return has if kind == "ticket-has-label" else not has
    if kind == "ticket-state":
        key, _, state = arg.partition(" ")
        return run.state["tickets"].get(key, {}).get("state") == state
    if kind == "ticket-comment-matches":
        key, _, pattern = arg.partition(" ")
        return any_match(pattern, run.state["tickets"].get(key, {}).get("comments", []))
    if kind == "ticket-description-matches":
        key, _, pattern = arg.partition(" ")
        return bool(re.search(pattern, run.state["tickets"].get(key, {}).get("description", "")))
    if kind == "ticket-created":
        return any(
            re.search(arg, t.get("summary", "") + "\n" + t.get("description", ""))
            for k, t in run.state["tickets"].items()
            if k not in run.state.get("seeded", [])
        )
    if kind == "no-file-named":
        return run.root is None or not list(Path(run.root).rglob(arg))
    if kind == "answer-matches":
        return bool(re.search(arg, run.answer))
    if kind == "answer-not-matches":
        return not re.search(arg, run.answer)
    if kind == "skill-loaded":
        return any(skill_matches(name, arg) for name in run.loaded)
    if kind == "commits-only-touch":
        return all(re.fullmatch(arg, path) for path in run.touched)
    if kind == "loaded-before":
        skill, _, command_re = arg.partition(" ||| ")
        return loaded_before(run.calls, skill, command_re)
    if kind == "tool-not-used":
        return not any_match(arg, run.tools)
    return False


def grade(expectations, run):
    results = []
    for exp in expectations:
        try:
            results.append({"text": exp, "passed": bool(check(exp, run))})
        except re.error as exc:
            results.append({"text": exp, "passed": False, "error": f"bad pattern: {exc}"})
    return results


def read_log(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_case_once(case, cfg, model):
    if case.get("global_config"):
        with tempfile.TemporaryDirectory(prefix="jtl-globalcfg-", dir=eval_tmp_root()) as groot:
            with isolated_config(groot, global_config=True) as gcfg:
                return _run_case_in(case, gcfg, model)
    return _run_case_in(case, cfg, model)


def _run_case_in(case, cfg, model):
    with tempfile.TemporaryDirectory(prefix=f"jtl-eval-{case['id']}-", dir=eval_tmp_root()) as tmp:
        root = Path(tmp)
        ws = make_workspace(case, root)
        start = branch_heads(ws)
        env = case_environment(case, root, cfg)
        with rest_server(env, root) as env:
            session, answers, bash, loaded, tools, calls = None, [], [], [], [], []
            for prompt in case["turns"]:
                out = claude(prompt, ws, env, model, resume=session)
                session = out["session"]
                answers.append(out["answer"])
                bash += out["bash"]
                loaded += out["loaded"]
                tools += out["tools"]
                calls += out["calls"]
        state = json.loads((root / "state.json").read_text())
        state["seeded"] = list(case.get("tickets", {}))
        cli_calls, git_calls = read_log(root / "calls.log"), read_log(root / "git.log")
        run = Run(
            cli_calls,
            git_calls,
            "\n\n".join(answers),
            bash,
            loaded,
            tools,
            calls,
            files_touched(ws, start),
            state,
            root,
        )
        results = grade(case["expectations"], run)
        return {
            "passed": all(r["passed"] for r in results),
            "expectations": results,
            "jira_calls": run.jira,
            "gh_calls": run.gh,
            "rest_calls": run.rest,
            "skills_loaded": loaded,
            "tools_used": tools,
            "git_calls": [
                g
                for g in run.git
                if not g.startswith(("config", "status", "rev-parse", "ls-files"))
            ],
            "answers": [a[-2500:] for a in answers],
            "answer": answers[-1][-3000:] if answers else "",
        }


def run_case(case, cfg, model):
    """Run a case once. A failure counts as it ran: the suite decides on totals (verdict)."""
    started = time.time()
    result = run_case_once(case, cfg, model)
    return {
        "id": case["id"],
        "name": case["name"],
        "critical": bool(case.get("critical")),
        **result,
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--model", help="model for claude -p (default: the user's configured model)"
    )
    parser.add_argument("--ids", help="comma-separated case ids to run")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default="behaviour-results.json")
    args = parser.parse_args(argv)
    cases = json.loads((SKILL_DIR / "evals" / "evals.json").read_text())["evals"]
    if args.ids:
        wanted = {int(i) for i in args.ids.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    with tempfile.TemporaryDirectory(prefix="jtl-config-", dir=eval_tmp_root()) as root:
        with isolated_config(root) as cfg:
            canary = probe(cfg, root, args.model)
            print(f"isolation probe: {canary}", file=sys.stderr)
            if not canary["ok"]:
                Path(args.out).write_text(json.dumps({"probe": canary, "results": []}, indent=1))
                return 2
            partial = Path(args.out + ".partial.jsonl")

            def run_and_save(case):
                result = run_case(case, cfg, args.model)
                with partial.open("a") as fh:
                    fh.write(json.dumps(result) + "\n")
                return result

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = list(pool.map(run_and_save, cases))
    summary = verdict(results)
    out = {
        "model": args.model or "default",
        "probe": canary,
        "results": results,
        "summary": summary,
    }
    Path(args.out).write_text(json.dumps(out, indent=1))
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        crit = " (critical)" if r["critical"] else ""
        print(f"{mark}  {r['id']:>2} {r['name']}{crit}  ({r['seconds']}s)")
    print(json.dumps(summary))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
