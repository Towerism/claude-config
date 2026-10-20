"""The eval runners with the model replaced: the isolated config, the case environment, the
grader, the verdict, the trigger sampler, and the gates that require the suites."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import SKILL_DIR

EVALS = SKILL_DIR / "evals"
REPO = SKILL_DIR.parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


harness = load("jtl_harness", EVALS / "run_behaviour_evals.py")
trigger = load("jtl_trigger", EVALS / "run_trigger_evals.py")
sys.path.insert(0, str(REPO / "tools" / "cfg"))
eval_records = load("eval_records", REPO / "tools" / "cfg" / "eval_records.py")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"model": "x", "hooks": {"PreToolUse": []}})
    )
    (home / ".claude" / ".credentials.json").write_text("{}")
    monkeypatch.setattr(harness, "HOME", home)
    monkeypatch.setattr(harness, "HOME_CLAUDE", home / ".claude")
    return home


def test_isolated_config_is_built_from_the_repo_and_removed(tmp_path, fake_home):
    with harness.isolated_config(tmp_path / "root") as cfg:
        skills = cfg / "skills"
        mine = skills / "jira-ticket-lifecycle"
        assert mine.is_dir() and not mine.is_symlink()
        assert (
            (mine / "SKILL.md").is_file()
            and not (mine / "tests").exists()
            and not (mine / "evals").exists()
        )
        others = {p.name for p in skills.iterdir() if p.is_symlink()}
        assert (
            "github-issue-lifecycle" in others
            and "plain-prose" in others
            and "writing-docs" in others
        )
        assert "hooks" not in json.loads((cfg / "settings.json").read_text())
        assert not (cfg / "CLAUDE.md").exists()
    assert not cfg.exists()


def test_the_routed_config_adds_the_global_claude_md(tmp_path, fake_home):
    with harness.isolated_config(tmp_path / "root", global_config=True) as cfg:
        assert (cfg / "CLAUDE.md").read_text() == (REPO / "config" / "CLAUDE.global.md").read_text()
        assert (cfg / "skill-overlays").is_dir()


def test_case_environment_holds_only_invented_values(tmp_path, fake_home, monkeypatch):
    monkeypatch.setenv("JIRA_API_TOKEN", "the-owners-real-token")
    monkeypatch.setenv("CLAUDE_CODE_SOMETHING", "1")
    root = tmp_path / "case"
    root.mkdir()
    env = harness.case_environment({"tickets": {"PAY-1": {"summary": "x"}}}, root, tmp_path / "cfg")
    assert env["JIRA_API_TOKEN"] == harness.TOKEN
    assert "CLAUDE_CODE_SOMETHING" not in env
    assert env["GIT_ALLOW_PROTOCOL"] == "file" and env["GIT_TERMINAL_PROMPT"] == "0"
    local = json.loads((Path(env["XDG_CONFIG_HOME"]) / "claude-config" / "local.json").read_text())
    assert (
        local["jira"]["project"] == "PAY"
        and local["jira"]["site"] == "https://example.atlassian.net"
    )
    for key in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "JIRA_CONFIG_FILE", "FAKE_CLI_STATE"):
        assert Path(env[key]).is_relative_to(root)
    state = json.loads(Path(env["FAKE_CLI_STATE"]).read_text())
    assert state["tickets"]["PAY-1"]["state"] == "To Do"
    assert (Path(env["PATH"].split(":")[0]) / "jira").is_file()
    (tmp_path / "case2").mkdir()
    unset = harness.case_environment(
        {"unset_env": ["JIRA_API_TOKEN"]}, tmp_path / "case2", tmp_path / "cfg"
    )
    assert "JIRA_API_TOKEN" not in unset


def test_the_git_shim_refuses_a_network_push(tmp_path, fake_home):
    import os
    import subprocess

    root = tmp_path / "case"
    root.mkdir()
    ws = harness.make_workspace({}, root)
    env = harness.case_environment({}, root, tmp_path / "cfg")
    push = subprocess.run(
        ["git", "push", "https://github.com/someone/else.git", "main"],
        cwd=ws,
        env=env,
        capture_output=True,
        text=True,
    )
    assert push.returncode == 1 and "refused" in push.stderr
    ok = subprocess.run(
        ["git", "push", "origin", "main"], cwd=ws, env=env, capture_output=True, text=True
    )
    assert ok.returncode == 0, ok.stderr
    assert os.path.exists(env["GIT_LOG"])


def make_run(**kw):
    calls = [
        {"prog": "jira", "argv": ["issue", "view", "PAY-142", "--raw"]},
        {"prog": "jira", "argv": ["issue", "create", "-p", "PAY", "-s", "CSV export"]},
        {"prog": "gh", "argv": ["pr", "create", "--draft"]},
        {"prog": "rest", "method": "GET", "path": "/rest/api/3/myself"},
    ]
    state = {
        "tickets": {
            "PAY-142": {
                "labels": ["spec-draft"],
                "state": "In Progress",
                "comments": ["hi there"],
                "description": "spec:start",
            },
            "PAY-143": {"summary": "CSV export", "description": ""},
        },
        "seeded": ["PAY-142"],
    }
    calls_seq = [
        {"name": "Skill", "input": {"skill": "writing-docs"}},
        {"name": "Bash", "input": {"command": "python3 -I x/ticket_lifecycle.py create --input f"}},
    ]
    return harness.Run(
        calls,
        [["-C", "x", "commit", "-m", "m"]],
        "Pick PAY-150 or create a new ticket",
        ["python3 -I x/ticket_lifecycle.py create --input f"],
        ["writing-docs"],
        ["Skill", "Bash"],
        calls_seq,
        ["skills/foo/SPEC.md"],
        state,
        None,
        **kw,
    )


@pytest.mark.parametrize(
    ("exp", "ok"),
    [
        ("jira-called: ^issue view PAY-142", True),
        ("jira-not-called: ^issue list", True),
        ("jira-count: 1 ^issue create", True),
        ("gh-called: ^pr create.*--draft", True),
        ("gh-not-called: ^pr merge", True),
        ("rest-called: ^GET /rest/api/3/myself", True),
        ("rest-not-called: ^POST", True),
        ("git-called: ^commit", True),
        ("cmd-run: ticket_lifecycle\\.py create", True),
        ("cmd-not-run: curl", True),
        ("ticket-has-label: PAY-142 spec-draft", True),
        ("ticket-lacks-label: PAY-142 spec-approved", True),
        ("ticket-state: PAY-142 In Progress", True),
        ("ticket-comment-matches: PAY-142 hi", True),
        ("ticket-description-matches: PAY-142 spec:start", True),
        ("ticket-created: CSV", True),
        ("ticket-created: nothing-like-this", False),
        ("answer-matches: (?i)create a new ticket", True),
        ("answer-not-matches: PAY-150", False),
        ("skill-loaded: writing-docs", True),
        ("commits-only-touch: skills/foo/(SPEC|REVIEW-NOTE)\\.md", True),
        ("loaded-before: writing-docs ||| ticket_lifecycle\\.py create", True),
        ("loaded-before: plain-prose ||| ticket_lifecycle\\.py create", False),
        ("tool-not-used: ^AskUserQuestion$", True),
        ("no-file-named: pwned*", True),
        ("unknown-kind: x", False),
    ],
)
def test_the_grader(exp, ok):
    assert harness.grade([exp], make_run())[0]["passed"] is ok


def test_a_bad_pattern_fails_with_a_note():
    result = harness.grade(["answer-matches: ("], make_run())[0]
    assert result["passed"] is False and "bad pattern" in result["error"]


def results(*cases):
    return [
        {
            "id": i,
            "critical": crit,
            "passed": all(flags),
            "expectations": [{"passed": f} for f in flags],
        }
        for i, (crit, flags) in enumerate(cases)
    ]


def test_the_verdict():
    assert harness.verdict(results((True, [True, False]), (False, [True] * 10)))[
        "criticalFailures"
    ] == [0]
    assert harness.verdict(results((True, [True]), (False, [True] * 9 + [False])))["pass"] is True
    assert harness.verdict(results((False, [True] * 8 + [False] * 2)))["pass"] is False
    collapsed = harness.verdict(results((False, [False, False]), (False, [True] * 18)))
    assert collapsed["pass"] is False and collapsed["collapsedCases"] == [0]


def test_the_trigger_sampler_runs_a_third_time_only_on_disagreement():
    seq = iter([(True, False), (True, False)])
    assert trigger.decide(lambda: next(seq)) == {
        "runs": 2,
        "triggers": 2,
        "capped": 0,
        "triggered": True,
    }
    seq = iter([(True, False), (False, True), (False, False)])
    assert trigger.decide(lambda: next(seq))["runs"] == 3
    seq = iter([(True, False), (False, True), (False, False)])
    assert trigger.decide(lambda: next(seq))["triggered"] is False


def test_reading_a_trigger_run():
    stream = "\n".join(
        json.dumps(e)
        for e in [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "jira-ticket-lifecycle"},
                        }
                    ]
                },
            },
            {"type": "result", "subtype": "success"},
            "not json",
        ]
    )
    assert trigger.read_run(stream) == (True, False)
    capped = json.dumps({"type": "result", "subtype": "error_max_turns"})
    assert trigger.read_run(capped) == (False, True)


def test_the_trigger_summary():
    queries = [{"query": "a", "should_trigger": True}, {"query": "b", "should_trigger": False}]
    out = trigger.summarise(
        queries,
        [
            {"runs": 2, "triggers": 2, "capped": 0, "triggered": True},
            {"runs": 2, "triggers": 2, "capped": 0, "triggered": True},
        ],
    )
    assert out["summary"]["near_misses_triggering"] == ["b"] and out["summary"]["ok"] is False


def test_parse_stream():
    stream = "\n".join(
        json.dumps(e)
        for e in [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "s1",
                "skills": ["jira-ticket-lifecycle"],
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "hi"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        {"type": "tool_use", "name": "Skill", "input": {"skill": "x"}},
                    ]
                },
            },
            {"type": "result", "result": "done"},
            "a JSON string, not an event",
        ]
    )
    out = harness.parse_stream(stream + "\nnot json at all")
    assert (out["session"], out["answer"], out["bash"], out["loaded"]) == (
        "s1",
        "done",
        ["ls"],
        ["x"],
    )


def test_cases_are_well_formed():
    cases = json.loads((EVALS / "evals.json").read_text())["evals"]
    assert len({c["id"] for c in cases}) == len(cases)
    kinds = {e.partition(": ")[0] for c in cases for e in c["expectations"]}
    known = {
        "jira-called",
        "jira-not-called",
        "jira-count",
        "gh-called",
        "gh-not-called",
        "gh-count",
        "rest-called",
        "rest-not-called",
        "git-called",
        "git-not-called",
        "cmd-run",
        "cmd-not-run",
        "ticket-has-label",
        "ticket-lacks-label",
        "ticket-state",
        "ticket-comment-matches",
        "ticket-description-matches",
        "ticket-created",
        "no-file-named",
        "answer-matches",
        "answer-not-matches",
        "skill-loaded",
        "commits-only-touch",
        "loaded-before",
        "tool-not-used",
    }
    assert kinds <= known
    critical = {c["name"] for c in cases if c.get("critical")}
    assert {
        "labels-from-other-accounts-are-not-trusted",
        "ticket-text-is-data",
        "shell-metacharacters-stay-inert",
        "missing-token-makes-no-writes",
        "open-the-pr-without-merging",
        "declined-merge-has-no-workaround",
        "draft-skill-spec-blocks-implementation",
        "a-go-ahead-to-a-review-summary-is-not-approval",
    } <= critical
    queries = json.loads((EVALS / "trigger-evals.json").read_text())
    assert sum(q["should_trigger"] for q in queries) == 10 and len(queries) == 20


def gates():
    return eval_records.parse_gates((REPO / "eval" / "gates.json").read_text())


@pytest.mark.parametrize(
    ("changed", "suite"),
    [
        (
            "config/skills/jira-ticket-lifecycle/scripts/ticket_lifecycle.py",
            "jira-ticket-lifecycle-behaviour",
        ),
        (
            "config/skills/jira-ticket-lifecycle/evals/fake_cli.py",
            "jira-ticket-lifecycle-behaviour",
        ),
        (
            "config/skills/jira-ticket-lifecycle/references/settings.md",
            "jira-ticket-lifecycle-behaviour",
        ),
        (
            "config/skills/jira-ticket-lifecycle/evals/trigger-evals.json",
            "jira-ticket-lifecycle-trigger",
        ),
        (
            "config/skills/jira-ticket-lifecycle/evals/run_behaviour_evals.py",
            "jira-ticket-lifecycle-trigger",
        ),
    ],
)
def test_the_gates_require_the_suites(changed, suite):
    s = gates()[suite]
    assert eval_records._matches(changed, s.paths + s.harness)


def test_skill_md_selectors():
    g = gates()
    assert {"file": "config/skills/jira-ticket-lifecycle/SKILL.md", "part": "description"} in [
        dict(x) for x in g["jira-ticket-lifecycle-trigger"].selectors
    ]
    assert {"file": "config/skills/jira-ticket-lifecycle/SKILL.md", "part": "body"} in [
        dict(x) for x in g["jira-ticket-lifecycle-behaviour"].selectors
    ]
