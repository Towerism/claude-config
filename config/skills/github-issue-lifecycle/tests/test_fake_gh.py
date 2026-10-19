"""The behaviour evals' fake gh: it answers from per-case state that the run's own writes update."""

import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import SKILL_DIR

FAKE = SKILL_DIR / "evals" / "fake_gh.py"


def load():
    spec = importlib.util.spec_from_file_location("fake_gh", FAKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ISSUE_42 = {
    "number": 42,
    "title": "Add CSV export",
    "body": "Users want CSV.",
    "labels": ["in progress"],
    "assignees": [],
    "comments": [],
    "events": [{"event": "labeled", "label": {"name": "in progress"}, "actor": {"login": "me"}}],
}


@pytest.fixture
def gh(tmp_path):
    """Run the fake with a seeded state; returns a function gh(*args) -> CompletedProcess."""
    state = tmp_path / "state"
    state.mkdir()

    def setup(issues=None, prs=None, case=(), defaults=(), next_number=None):
        seed = {"login": "mfracker", "repo": "o/r", "issues": issues or {}, "prs": prs or {}}
        if next_number:
            seed["next"] = next_number
        (state / "state.json").write_text(json.dumps(seed))
        (state / "responses.json").write_text(json.dumps({"case": list(case),
                                                          "defaults": list(defaults)}))  # fmt: skip

    setup({"42": dict(ISSUE_42)})
    env = {**os.environ, "GH_STATE": str(state), "GH_LOG": str(tmp_path / "gh.log")}
    env.pop("REAL_GIT", None)

    def run(*args, stdin=None):
        return subprocess.run([sys.executable, "-I", str(FAKE), *args], env=env, input=stdin,
                              capture_output=True, text=True, cwd=tmp_path)  # fmt: skip

    run.setup = setup
    run.dir = tmp_path
    return run


def view(gh, *args):
    return json.loads(gh("issue", "view", "42", *args).stdout)


def test_a_read_back_shows_the_runs_own_edits(gh):
    (gh.dir / "b.md").write_text(
        "Users want CSV.\n\n<!-- spec:start -->\nspec\n<!-- spec:end -->\n"
    )
    (gh.dir / "c.md").write_text("Review: clean.")
    assert gh("issue", "edit", "42", "--body-file", "b.md").returncode == 0
    assert gh("issue", "edit", "42", "--add-label", "draft").returncode == 0
    url = gh("issue", "comment", "42", "--body-file", "c.md").stdout
    assert url.startswith("https://github.com/o/r/issues/42#issuecomment-")
    data = view(gh, "--json", "body,labels,comments")
    assert "spec:start" in data["body"]
    assert [x["name"] for x in data["labels"]] == ["in progress", "draft"]
    assert data["comments"] == [{"author": {"login": "mfracker"}, "body": "Review: clean."}]
    events = json.loads(gh("api", "repos/{owner}/{repo}/issues/42/events").stdout)
    assert events[-1] == {"event": "labeled", "label": {"name": "draft"},
                          "actor": {"login": "mfracker"}}  # fmt: skip


def test_json_fields_are_limited_and_unknown_fields_are_errors(gh):
    assert set(view(gh, "--json", "number,title")) == {"number", "title"}
    bad = gh("issue", "view", "42", "--json", "nope")
    assert bad.returncode == 1 and "Unknown JSON field" in bad.stderr


def test_labels_given_with_commas_and_removed_labels_record_events(gh):
    gh("issue", "edit", "42", "--add-label", "draft,approval: manual", "--remove-label", "nope")
    gh("issue", "edit", "42", "--add-label", "approved", "--remove-label", "draft")
    names = [x["name"] for x in view(gh, "--json", "labels")["labels"]]
    assert names == ["in progress", "approval: manual", "approved"]
    events = json.loads(gh("api", "repos/o/r/issues/42/events").stdout)
    assert [e["event"] for e in events[-4:]] == ["labeled", "labeled", "labeled", "unlabeled"]


def test_assigning_me_uses_the_users_login(gh):
    gh("issue", "edit", "42", "--add-assignee", "@me", "--add-label", "in progress")
    assert view(gh, "--json", "assignees")["assignees"] == [{"login": "mfracker"}]


def test_created_issues_and_prs_share_the_next_number(gh):
    gh.setup({"42": dict(ISSUE_42)}, next_number=77)
    out = gh("issue", "create", "--title", "Login 500s", "--body", "Prod.", "--label", "hotfix")
    assert out.stdout.strip() == "https://github.com/o/r/issues/77"
    data = json.loads(gh("issue", "view", "77", "--json", "labels,title").stdout)
    assert data == {"labels": [{"name": "hotfix"}], "title": "Login 500s"}
    pr = gh("pr", "create", "--title", "fix", "--body", "Fixes it.\n\nCloses #77\n", "--head", "x")
    assert pr.stdout.strip() == "https://github.com/o/r/pull/78"
    refs = json.loads(gh("pr", "view", "78", "--json", "closingIssuesReferences").stdout)
    assert refs == {"closingIssuesReferences": [{"number": 77}]}


def test_pr_edit_reparses_closing_references(gh):
    gh("pr", "create", "--title", "t", "--body", "For #42", "--head", "feature/42-x")
    view_refs = ("pr", "view", "43", "--json", "closingIssuesReferences", "--jq",
                 "[.closingIssuesReferences[].number]")  # fmt: skip
    assert gh(*view_refs).stdout.strip() == "[]"
    gh("pr", "edit", "43", "--body", "Closes #42")
    assert gh(*view_refs).stdout.strip() == "[42]"
    listed = json.loads(gh("pr", "list", "--head", "feature/42-x", "--json", "number").stdout)
    assert listed == [{"number": 43}]


def test_unknown_issues_are_not_found(gh):
    r = gh("issue", "view", "999")
    assert r.returncode == 1 and "Could not resolve" in r.stderr
    assert gh("api", "repos/o/r/issues/999").returncode == 1


def test_a_canned_response_wins_and_can_close_issues(gh):
    merged = {"match": "^pr merge", "stdout": "Merged #43", "closes": [42]}
    late = {
        "match": "^pr view.*closingIssuesReferences",
        "stdout": '{"closingIssuesReferences": []}',
    }
    gh.setup({"42": dict(ISSUE_42)}, case=[merged, late])
    gh("pr", "create", "--body", "Closes #42", "--head", "x")
    assert gh("pr", "view", "43", "--json", "closingIssuesReferences").stdout.strip() == (
        '{"closingIssuesReferences": []}'
    )
    assert gh("pr", "merge", "43", "--merge").stdout.strip() == "Merged #43"
    assert view(gh, "--json", "state,stateReason") == {
        "state": "CLOSED",
        "stateReason": "COMPLETED",
    }


def test_defaults_answer_what_the_state_and_the_case_dont(gh):
    gh.setup({}, defaults=[{"match": "^auth status", "stdout": "Logged in"}])
    assert gh("auth", "status").stdout.strip() == "Logged in"
    assert gh("repo", "view").returncode == 0


def test_every_call_is_logged_with_its_body(gh):
    gh("issue", "comment", "42", "--body", "hello")
    log = [json.loads(x) for x in (gh.dir / "gh.log").read_text().splitlines()]
    assert log == [{"args": ["issue", "comment", "42", "--body", "hello"], "body": "hello"}]


def test_concurrent_writes_keep_every_update(gh):
    labels = [f"l{i}" for i in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda n: gh("issue", "edit", "42", "--add-label", n), labels))
    names = {x["name"] for x in view(gh, "--json", "labels")["labels"]}
    assert set(labels) <= names


EVENTS = [
    {"event": "labeled", "label": {"name": "approved"}, "actor": {"login": "a"}},
    {"event": "unlabeled", "label": {"name": "draft"}, "actor": {"login": "b"}},
    {"event": "assigned", "label": None, "actor": {"login": "c"}},
]


@pytest.mark.parametrize(
    "flt,expected",
    [
        ('[.[] | select(.event=="labeled") | {label: .label.name, actor: .actor.login}]',
         ['[{"label": "approved", "actor": "a"}]']),
        ('[.[] | select(.event=="labeled" or .event=="unlabeled") | {event, label: .label.name}]',
         ['[{"event": "labeled", "label": "approved"}, {"event": "unlabeled", "label": "draft"}]']),
        ('.[] | select(.event != "assigned" and .actor.login == "b") | .label.name', ["draft"]),
        (".[].actor.login", ["a", "b", "c"]),
        (".", [json.dumps(EVENTS)]),
        ('.[0]', None),
        ("map(.event)", None),
        ("length", None),
    ],
)  # fmt: skip
def test_the_jq_subset(flt, expected):
    fake = load()
    if expected is None:
        with pytest.raises(fake.JqError):
            fake.jq(flt, EVENTS)
    else:
        assert fake.jq(flt, EVENTS) == expected


def test_an_unsupported_jq_filter_is_an_error_the_model_can_act_on(gh):
    r = gh("issue", "view", "42", "--json", "labels", "--jq", "map(.name)")
    assert r.returncode == 1 and "without --jq" in r.stderr
    plain = gh("issue", "view", "42", "--json", "labels", "--jq", ".labels[].name")
    assert plain.stdout.strip() == "in progress"


def test_a_view_without_json_reads_like_gh(gh):
    gh("issue", "comment", "42", "--body", "Looks good.")
    out = gh("issue", "view", "42", "--comments").stdout
    assert "Add CSV export #42" in out and "Users want CSV." in out and "Looks good." in out
