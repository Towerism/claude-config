#!/usr/bin/env python3
"""A fake `gh` for the behaviour evals (development only; never run by the skill).

It records every call, then answers in this order:
  1. the case's canned responses (`case` in responses.json), so a case can simulate what the
     state doesn't model, such as an error, a merge, or a closing reference that appears late;
  2. the per-case state in state.json, which the run's own writes update, so a read-back shows
     the change: issues (body, labels, comments, label events), pull requests, and `gh api`
     reads of an issue, its comments or its events;
  3. the harness's default canned responses (`defaults` in responses.json).

`--jq` supports the filters the skill and the cases use (see `jq`); any other filter is an
error, so the model can retry without it.

Environment: GH_STATE (folder with responses.json and state.json), GH_LOG (call log).
"""

import fcntl
import itertools
import json
import os
import re
import sys

CLOSING = re.compile(r"^\s*(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\s*$", re.I | re.M)


class JqError(Exception):
    pass


# ── a small jq ───────────────────────────────────────────────────────────────

TOKEN = re.compile(
    r'\s*(?:(?P<str>"(?:[^"\\]|\\.)*")|(?P<num>-?\d+(?:\.\d+)?)|(?P<op>==|!=|\[|\]|\{|\}|\(|\)'
    r"|\||,|:|\.)|(?P<ident>[A-Za-z_][A-Za-z0-9_]*))"
)


def tokenize(text):
    tokens, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise JqError(f"unsupported filter near {text[pos:]!r}")
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        pos = m.end()
    return tokens


class Parser:
    """pipe := term ('|' term)* ; term := '[' pipe ']' | '{' fields '}' | 'select(' cond ')'
    | path | literal. Paths are '.', '.a.b', '.a[].b', '.[]'."""

    def __init__(self, tokens):
        self.tokens, self.i = tokens, 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self, value=None):
        kind, val = self.peek()
        if kind is None or (value is not None and val != value):
            raise JqError(f"unsupported filter: expected {value or 'more'}, got {val!r}")
        self.i += 1
        return kind, val

    def parse(self):
        node = self.pipe()
        if self.peek()[0] is not None:
            raise JqError(f"unsupported filter near {self.peek()[1]!r}")
        return node

    def pipe(self):
        node = self.term()
        while self.peek()[1] == "|":
            self.take("|")
            node = ("pipe", node, self.term())
        return node

    def term(self):
        kind, val = self.peek()
        if val == "[":
            self.take("[")
            inner = self.pipe()
            self.take("]")
            return ("array", inner)
        if val == "{":
            return self.obj()
        if kind == "ident" and val == "select":
            self.take()
            self.take("(")
            cond = self.cond()
            self.take(")")
            return ("select", cond)
        if val == ".":
            return self.path()
        if kind in ("str", "num") or val in ("true", "false", "null"):
            self.take()
            return ("lit", json.loads(val))
        raise JqError(f"unsupported filter near {val!r}")

    def path(self):
        steps = []
        self.take(".")
        if self.peek()[0] == "ident":
            steps.append(("key", self.take()[1]))
        while True:
            kind, val = self.peek()
            nxt = self.tokens[self.i + 1][1] if self.i + 1 < len(self.tokens) else None
            if val == "[" and nxt == "]":
                self.take("[")
                self.take("]")
                steps.append(("each",))
            elif (
                val == "."
                and self.i + 1 < len(self.tokens)
                and self.tokens[self.i + 1][0] == "ident"
            ):
                self.take(".")
                steps.append(("key", self.take()[1]))
            else:
                return ("path", steps)

    def obj(self):
        self.take("{")
        fields = []
        while self.peek()[1] != "}":
            _, key = self.take()
            key = json.loads(key) if key.startswith('"') else key
            if self.peek()[1] == ":":
                self.take(":")
                fields.append((key, self.alternative()))
            else:
                fields.append((key, ("path", [("key", key)])))
            if self.peek()[1] == ",":
                self.take(",")
        self.take("}")
        return ("object", fields)

    def alternative(self):
        """An object field's value: a pipe that stops at ',' or '}'."""
        node = self.term()
        while self.peek()[1] == "|":
            self.take("|")
            node = ("pipe", node, self.term())
        return node

    def cond(self):
        node = self.conj()
        while self.peek()[1] == "or":
            self.take()
            node = ("or", node, self.conj())
        return node

    def conj(self):
        node = self.cmp()
        while self.peek()[1] == "and":
            self.take()
            node = ("and", node, self.cmp())
        return node

    def cmp(self):
        left = self.term()
        if self.peek()[1] in ("==", "!="):
            _, op = self.take()
            return (op, left, self.term())
        return ("truthy", left)


def evaluate(node, value):
    kind = node[0]
    if kind == "lit":
        return [node[1]]
    if kind == "path":
        out = [value]
        for step in node[1]:
            nxt = []
            for v in out:
                if step[0] == "key":
                    nxt.append(v.get(step[1]) if isinstance(v, dict) else None)
                elif isinstance(v, list):
                    nxt.extend(v)
                elif isinstance(v, dict):
                    nxt.extend(v.values())
                else:
                    raise JqError("cannot iterate over a non-list value")
            out = nxt
        return out
    if kind == "pipe":
        return [r for v in evaluate(node[1], value) for r in evaluate(node[2], v)]
    if kind == "array":
        return [evaluate(node[1], value)]
    if kind == "object":
        keys = [k for k, _ in node[1]]
        choices = [evaluate(sub, value) for _, sub in node[1]]
        return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*choices)]
    if kind == "select":
        return [value] if test(node[1], value) else []
    raise JqError(f"unsupported filter {kind}")  # pragma: no cover  (the parser can't build it)


def test(node, value):
    kind = node[0]
    if kind == "or":
        return test(node[1], value) or test(node[2], value)
    if kind == "and":
        return test(node[1], value) and test(node[2], value)
    if kind == "truthy":
        return any(v not in (None, False) for v in evaluate(node[1], value))
    left, right = evaluate(node[1], value), evaluate(node[2], value)
    same = any(a == b for a in left for b in right)
    return same if kind == "==" else not same


def jq(filter_text, data):
    """Every output of the filter applied to data, as gh prints them: strings raw, the rest
    as compact JSON."""
    out = evaluate(Parser(tokenize(filter_text)).parse(), data)
    return [v if isinstance(v, str) else json.dumps(v) for v in out]


# ── arguments ────────────────────────────────────────────────────────────────


def option(args, *names, many=False):
    """Values of an option, as `--flag value` or `--flag=value`."""
    found = []
    for i, arg in enumerate(args):
        for name in names:
            if arg == name and i + 1 < len(args):
                found.append(args[i + 1])
            elif arg.startswith(name + "="):
                found.append(arg.split("=", 1)[1])
    return found if many else (found[-1] if found else None)


def body_of(args):
    path = option(args, "--body-file", "-F")
    if path is not None:
        if path == "-":
            return sys.stdin.read()
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None
    return option(args, "--body", "-b")


def labels_of(args, *names):
    return [x.strip() for v in option(args, *names, many=True) for x in v.split(",") if x.strip()]


def positional(args, start):
    """The first argument after `start` that isn't an option or an option's value."""
    i = start
    while i < len(args):
        if args[i].startswith("-"):
            i += 1 if "=" in args[i] else 2
            continue
        return args[i]
    return None


# ── state ────────────────────────────────────────────────────────────────────


def current_branch(state):
    """The branch checked out where gh runs, as `gh pr view` with no number uses it."""
    git = os.environ.get("REAL_GIT")
    if git:
        import subprocess

        out = subprocess.run([git, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                             text=True, check=False)  # fmt: skip
        if out.returncode == 0:
            return out.stdout.strip()
    return state.get("branch", "")


def issue_url(state, n):
    return f"https://github.com/{state['repo']}/issues/{n}"


def issue_json(state, issue):
    return {
        "number": issue["number"],
        "title": issue.get("title", ""),
        "body": issue.get("body", ""),
        "state": issue.get("state", "OPEN"),
        "stateReason": issue.get("stateReason"),
        "labels": [{"name": n} for n in issue.get("labels", [])],
        "assignees": [{"login": a} for a in issue.get("assignees", [])],
        "comments": [
            {"author": {"login": c["author"]}, "body": c["body"]} for c in issue.get("comments", [])
        ],
        "url": issue_url(state, issue["number"]),
    }


def pr_json(state, pr):
    return {
        "number": pr["number"],
        "title": pr.get("title", ""),
        "body": pr.get("body", ""),
        "state": pr.get("state", "OPEN"),
        "headRefName": pr.get("head", ""),
        "baseRefName": pr.get("base", "main"),
        "closingIssuesReferences": [{"number": n} for n in pr.get("closes", [])],
        "url": f"https://github.com/{state['repo']}/pull/{pr['number']}",
    }


def pick(data, args):
    """Limit data to the --json fields asked for, as gh does; None if --json wasn't given."""
    fields = option(args, "--json")
    if fields is None:
        return None
    wanted = [f for f in fields.split(",") if f]
    unknown = [f for f in wanted if f not in data]
    if unknown:
        raise JqError(f"Unknown JSON field: {unknown[0]!r}")
    return {f: data[f] for f in wanted}


def next_number(state):
    """GitHub numbers issues and pull requests in one sequence; a case may seed where it starts."""
    used = [int(n) for n in state["issues"]] + [int(n) for n in state["prs"]]
    floor = state["next"] - 1 if "next" in state else (0 if used else 98)
    state["next"] = max([floor, *used]) + 1
    return state["next"]


def set_labels(state, issue, add, remove):
    for name in add:
        if name not in issue.setdefault("labels", []):
            issue["labels"].append(name)
            issue.setdefault("events", []).append(event(state, "labeled", name))
    for name in remove:
        if name in issue.get("labels", []):
            issue["labels"].remove(name)
            issue.setdefault("events", []).append(event(state, "unlabeled", name))


def event(state, kind, label):
    return {"event": kind, "label": {"name": label}, "actor": {"login": state["login"]}}


def human_issue(issue, with_comments):
    lines = [f"{issue.get('title', '')} #{issue['number']}", issue.get("state", "OPEN")]
    if issue.get("labels"):
        lines.append("labels: " + ", ".join(issue["labels"]))
    lines += ["--", issue.get("body", "")]
    if with_comments:
        for c in issue.get("comments", []):
            lines += ["--", f"{c['author']}:", c["body"]]
    return "\n".join(lines)


def answer_from_state(state, args):
    """(handled, stdout, exit code) for the commands the state models."""
    group, verb = (args + ["", ""])[:2]
    issues, prs = state["issues"], state["prs"]
    if group == "issue" and verb in ("view", "edit", "comment"):
        n = positional(args, 2)
        issue = issues.get(str(n).lstrip("#"))
        if issue is None:
            msg = f"GraphQL: Could not resolve to an issue or pull request with the number of {n}."
            return True, None, msg
        if verb == "view":
            data = pick(issue_json(state, issue), args)
            if data is None:
                return True, human_issue(issue, "--comments" in args), 0
            return True, data, 0
        if verb == "edit":
            body = body_of(args)
            if body is not None:
                issue["body"] = body
            if option(args, "--title"):
                issue["title"] = option(args, "--title")
            for who in option(args, "--add-assignee", many=True):
                login = state["login"] if who == "@me" else who
                if login not in issue.setdefault("assignees", []):
                    issue["assignees"].append(login)
            set_labels(
                state, issue, labels_of(args, "--add-label"), labels_of(args, "--remove-label")
            )
            return True, issue_url(state, issue["number"]), 0
        issue.setdefault("comments", []).append({"author": state["login"], "body": body_of(args)})
        url = f"{issue_url(state, issue['number'])}#issuecomment-{len(issue['comments'])}"
        return True, url, 0
    if group == "issue" and verb == "create":
        n = next_number(state)
        issues[str(n)] = {"number": n, "title": option(args, "--title") or "",
                          "body": body_of(args) or "", "labels": [], "events": []}  # fmt: skip
        set_labels(state, issues[str(n)], labels_of(args, "--label", "-l"), [])
        return True, issue_url(state, n), 0
    if group == "pr" and verb in ("create", "edit"):
        if verb == "create":
            n = next_number(state)
            head = option(args, "--head", "-H") or current_branch(state)
            prs[str(n)] = {
                "number": n,
                "head": head,
                "base": option(args, "--base", "-B") or "main",
            }
        else:
            target = positional(args, 2)
            pr = prs.get(str(target)) or next(
                (p for p in prs.values() if p.get("head") == current_branch(state)), None
            )
            if pr is None:
                return True, None, f"no pull requests found for {target or 'the current branch'}"
            n = pr["number"]
        pr = prs[str(n)]
        body = body_of(args)
        if body is not None:
            pr["body"] = body
            pr["closes"] = [int(x) for x in CLOSING.findall(body)]
        if option(args, "--title"):
            pr["title"] = option(args, "--title")
        return True, f"https://github.com/{state['repo']}/pull/{n}", 0
    if group == "pr" and verb == "view":
        target = positional(args, 2)
        pr = prs.get(str(target).lstrip("#")) if target else next(
            (p for p in prs.values() if p.get("head") == current_branch(state)), None
        )  # fmt: skip
        if pr is None:
            return True, None, f"no pull requests found for {target or 'the current branch'}"
        data = pick(pr_json(state, pr), args)
        return True, data if data is not None else f"{pr.get('title', '')} #{pr['number']}", 0
    if group == "pr" and verb == "list":
        head = option(args, "--head", "-H")
        found = [pr_json(state, p) for p in prs.values() if head in (None, p.get("head"))]
        fields = option(args, "--json")
        if fields:
            found = [{f: p.get(f) for f in fields.split(",")} for p in found]
        return True, found, 0
    if group == "api":
        path = positional(args, 1) or ""
        m = re.match(r"^/?repos/[^/]+/[^/]+/issues/(\d+)(?:/(events|comments))?$", path)
        if m and not option(args, "-X", "--method") and not option(args, "-f", "-F", "--field"):
            issue = issues.get(m.group(1))
            if issue is None:
                return True, None, "gh: Not Found (HTTP 404)"
            if m.group(2) == "events":
                return True, issue.get("events", []), 0
            if m.group(2) == "comments":
                comments = issue.get("comments", [])
                return (
                    True,
                    [{"user": {"login": c["author"]}, "body": c["body"]} for c in comments],
                    0,
                )
            data = issue_json(state, issue)
            data["labels"] = [{"name": n} for n in issue.get("labels", [])]
            return True, data, 0
    return False, None, 0


def canned(responses, args, log_lines, state):
    """The first canned response whose pattern matches, as (stdout, stderr, exit), or None."""
    joined = " ".join(args)
    for i, r in enumerate(responses):
        if not re.search(r["match"], joined):
            continue
        out = r.get("stdout", "")
        if "after_call" in r and any(re.search(r["after_call"], " ".join(a)) for a in log_lines):
            out = r["after_stdout"]
        if "sequence" in r:
            counter = os.path.join(os.environ["GH_STATE"], f"seq-{i}")
            n = int(open(counter).read()) if os.path.exists(counter) else 0
            out = r["sequence"][min(n, len(r["sequence"]) - 1)]
            with open(counter, "w") as fh:
                fh.write(str(n + 1))
        for n in r.get("closes", []):  # a canned merge also closes these issues
            issue = state["issues"].get(str(n))
            if issue is not None:
                issue["state"], issue["stateReason"] = "CLOSED", "COMPLETED"
        return out, r.get("stderr", ""), r.get("exit", 0)
    return None


def emit(out, args):
    """Print out (text, or JSON data) with --jq applied when it's JSON; return the exit code."""
    flt = option(args, "--jq", "-q")
    if flt is not None:
        data = out
        if isinstance(out, str):
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                data = None
        if data is not None:
            try:
                lines = jq(flt, data)
            except JqError as exc:
                sys.stderr.write(f"fake gh: {exc}; this fake doesn't support that --jq filter. "
                                 "Run the command without --jq instead.\n")  # fmt: skip
                return 1
            if lines:
                sys.stdout.write("\n".join(lines) + "\n")
            return 0
    text = out if isinstance(out, str) else json.dumps(out)
    if text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def main():
    args = sys.argv[1:]
    folder = os.environ["GH_STATE"]
    entry = {"args": args}
    body = body_of(args) if "--body-file" in args or "-F" in args or "--body" in args else None
    if body is not None:
        entry["body"] = body
    with open(os.path.join(folder, "state.lock"), "w") as lock:
        # The model can run two gh commands at once; one at a time keeps every update.
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(os.environ["GH_LOG"], "a") as fh:
            fh.write(json.dumps(entry) + "\n")
        with open(os.environ["GH_LOG"]) as fh:
            log_lines = [json.loads(line)["args"] for line in fh.read().splitlines()[:-1]]
        with open(os.path.join(folder, "responses.json")) as fh:
            responses = json.load(fh)
        state_path = os.path.join(folder, "state.json")
        with open(state_path) as fh:
            state = json.load(fh)
        code = 0
        hit = canned(responses.get("case", []), args, log_lines, state)
        if hit is None:
            try:
                handled, out, status = answer_from_state(state, args)
            except JqError as exc:
                handled, out, status = True, None, str(exc)
            if handled:
                hit = (out, status, 1) if isinstance(status, str) else (out, "", status)
        if hit is None:
            hit = canned(responses.get("defaults", []), args, log_lines, state)
        with open(state_path, "w") as fh:
            json.dump(state, fh)
    if hit is None:
        return 0
    out, err, code = hit
    if out is not None and out != "":
        code = emit(out, args) or code
    if err:
        sys.stderr.write(err + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
