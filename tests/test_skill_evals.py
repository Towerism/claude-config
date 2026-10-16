"""The skill trigger and cold-reader runners, against a scripted `claude`.

What the model says is faked. What the runners build (config directories, the
stub gh, the tool lists) and how they score the streams are real, and so is
plain-prose's checker, which supplies the diagnostics.
"""

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from eval import run_eval as re_
from eval import skill_evals as se
from tests.checker_helpers import REPO_ROOT
from tests.eval_fakes import leftovers, make_live_home
from tools.cfg import eval_records as er

SKILL = "plain-prose"


@pytest.fixture(autouse=True)
def fresh_tempdir(monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)


def make_head(root: Path, trigger=None, behaviour=None) -> Path:
    """A HEAD snapshot holding plain-prose (its real checker) and the given case files."""
    skill = root / "config" / "skills" / SKILL
    (skill / "evals").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: plain-prose\ndescription: d\n---\nBody\n")
    src = REPO_ROOT / "config" / "skills" / SKILL / "scripts"
    (skill / "scripts" / "vendored").mkdir(parents=True)
    for rel in ("prose_check.py", "vendored/readability_scorer.py",
                "vendored/ai_content_detector.py"):  # fmt: skip
        (skill / "scripts" / rel).write_text((src / rel).read_text())
    (skill / "evals" / "trigger-evals.json").write_text(json.dumps(trigger or []))
    (skill / "evals" / "behaviour-evals.json").write_text(json.dumps(behaviour or []))
    return root


@pytest.fixture
def make_ctx(tmp_path):
    def build(runner, trigger=None, behaviour=None):
        root = tmp_path / "tmproot"
        root.mkdir(exist_ok=True)
        return re_.Context(
            live=make_live_home(tmp_path),
            root=root,
            runner=runner,
            model=None,
            workers=2,
            out=tmp_path / "out",
            head_tree=make_head(tmp_path / "head", trigger, behaviour),
            base_tree=None,
        )

    return build


def suite(runner: str) -> er.Suite:
    cases = "trigger-evals.json" if runner == "trigger" else "behaviour-evals.json"
    return er.Suite(runner=runner, paths=(), harness=(), skill=SKILL,
                    cases=f"config/skills/{SKILL}/evals/{cases}")  # fmt: skip


def stream(skills, uses=(), result="done"):
    """stream-json lines: init listing `skills`, one Skill call per (skill, parent), the result."""
    lines = [{"type": "system", "subtype": "init", "skills": list(skills)}]
    for name, parent in uses:
        lines.append({"type": "assistant", "parent_tool_use_id": parent, "message": {"content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": name}}]}})  # fmt: skip
    lines.append({"type": "result", "result": result})
    return "\n".join(json.dumps(x) for x in lines) + "\n"


def installed(env) -> list[str]:
    skills = Path(env["CLAUDE_CONFIG_DIR"]) / "skills"
    return sorted(p.name for p in skills.iterdir()) if skills.is_dir() else []


class Recorder:
    """Keeps every call and checks the run limits while the run is live."""

    def __init__(self):
        self.calls = []

    def note(self, cmd, stdin, env):
        first = env["PATH"].split(os.pathsep)[0]
        gh_config = Path(env["GH_CONFIG_DIR"])
        self.calls.append({
            "cmd": cmd, "stdin": stdin, "skills": installed(env),
            "stub_gh": (Path(first) / "gh").is_file(),
            "gh_config_empty": gh_config.is_dir() and not any(gh_config.iterdir()),
        })  # fmt: skip


# Trigger runner


class TriggerFake(Recorder):
    """Per query, a list of run outcomes: 'top', 'sub', 'none' or 'error'."""

    def __init__(self, plan, dup=False):
        super().__init__()
        self.plan = {q: list(v) for q, v in plan.items()}
        self.dup = dup

    def __call__(self, cmd, stdin, env, cwd, timeout):
        self.note(cmd, stdin, env)
        outcome = self.plan[stdin].pop(0)
        if outcome == "error":
            return 1, "not json at all"
        skills = installed(env) * (2 if self.dup else 1)
        uses = {"top": [(SKILL, None)], "sub": [(SKILL, "toolu_1")], "none": []}[outcome]
        return 0, stream(skills, uses)


def trig(query, should, delegated=False):
    return {"query": query, "should_trigger": should, **({"delegated": True} if delegated else {})}


def run_trigger(make_ctx, cases, plan, **kw):
    fake = TriggerFake(plan, **kw)
    ctx = make_ctx(fake, trigger=cases)
    return se.run_trigger(ctx, suite("trigger")), fake, ctx


def test_a_top_level_skill_call_triggers_an_ordinary_query(make_ctx):
    result, _, _ = run_trigger(make_ctx, [trig("q", True)], {"q": ["top", "top", "none"]})
    assert result["pass"] is True
    assert result["cases"] == [
        {"id": "q", "result": "pass", "triggered": 2, "shouldTrigger": True, "delegated": False}
    ]


def test_two_of_three_runs_are_needed_to_trigger(make_ctx):
    result, _, _ = run_trigger(make_ctx, [trig("q", True)], {"q": ["top", "none", "none"]})
    assert result["cases"][0]["result"] == "fail"
    assert result["pass"] is False


def test_a_delegated_case_counts_only_subagent_calls(make_ctx):
    cases = [trig("own", True, delegated=True), trig("sub", True, delegated=True)]
    plan = {"own": ["top"] * 3, "sub": ["sub", "sub", "top"]}
    result, _, _ = run_trigger(make_ctx, cases, plan)
    assert [c["result"] for c in result["cases"]] == ["fail", "pass"]
    assert result["pass"] is False


def positives(n, misses=0):
    """`n` should-trigger queries, the last `misses` of which never trigger."""
    cases = [trig(f"p{i}", True) for i in range(n)]
    plan = {f"p{i}": (["none"] if i >= n - misses else ["top"]) * 3 for i in range(n)}
    return cases, plan


def test_a_near_miss_that_triggers_fails_the_suite_even_with_full_recall(make_ctx):
    cases, plan = positives(15)
    result, _, _ = run_trigger(make_ctx, cases + [trig("n", False)],
                               plan | {"n": ["sub", "sub", "none"]})  # fmt: skip
    assert result["recall"] == 1.0
    assert result["falseTriggers"] == 1
    assert result["pass"] is False


def test_thirteen_of_fifteen_should_trigger_queries_pass(make_ctx):
    cases, plan = positives(15, misses=2)
    result, _, _ = run_trigger(make_ctx, cases, plan)
    assert result["recall"] == round(13 / 15, 3)
    assert result["pass"] is True


def test_twelve_of_fifteen_fails_however_clean_the_near_misses(make_ctx):
    cases, plan = positives(15, misses=3)
    near = [trig(f"n{i}", False) for i in range(10)]
    result, _, _ = run_trigger(make_ctx, cases + near,
                               plan | {f"n{i}": ["none"] * 3 for i in range(10)})  # fmt: skip
    assert result["recall"] == 0.8 and result["falseTriggers"] == 0
    assert result["pass"] is False


def test_overall_accuracy_is_reported_but_never_decides(make_ctx):
    cases, plan = positives(15, misses=2)
    near = [trig(f"n{i}", False) for i in range(10)]
    result, _, _ = run_trigger(make_ctx, cases + near,
                               plan | {f"n{i}": ["none"] * 3 for i in range(10)})  # fmt: skip
    assert result["accuracy"] == round(23 / 25, 3)
    assert result["pass"] is True


def test_an_errored_near_miss_fails_the_suite(make_ctx):
    cases, plan = positives(15)
    result, _, _ = run_trigger(make_ctx, cases + [trig("n", False)],
                               plan | {"n": ["none", "error", "none"]})  # fmt: skip
    assert result["falseTriggers"] == 0
    assert result["pass"] is False


def test_a_run_error_fails_the_query_even_when_it_should_not_trigger(make_ctx):
    result, _, _ = run_trigger(make_ctx, [trig("n", False)], {"n": ["none", "error", "none"]})
    case = result["cases"][0]
    assert case["result"] == "fail" and case["errors"] == 1


def test_trigger_runs_have_the_skill_only_tools_and_no_github(make_ctx):
    _, fake, ctx = run_trigger(make_ctx, [trig("q", True)], {"q": ["top"] * 3})
    assert len(fake.calls) == 3
    for call in fake.calls:
        cmd = call["cmd"]
        assert cmd[cmd.index("--tools") + 1] == "Skill,Read,Agent"
        assert cmd[cmd.index("--max-turns") + 1] == "4"
        assert call["skills"] == [SKILL]
        assert call["stub_gh"] and call["gh_config_empty"]
    assert leftovers(ctx.root) == []


def test_isolation_failure_leaves_no_temp_directory(make_ctx, tmp_path):
    fake = TriggerFake({"q": ["top"] * 3}, dup=True)
    ctx = make_ctx(fake, trigger=[trig("q", True)])
    with pytest.raises(re_.IsolationError):
        se.run_trigger(ctx, suite("trigger"))
    assert leftovers(ctx.root) == []


# Cold-reader runner


WRITE = {
    "id": "w", "kind": "write", "task": "Write the PR text.", "facts": ["F1", "F2"],
    "questions": ["Q1?", "Q2?", "Q3?"], "mustKeep": ["F1"],
}  # fmt: skip
REWRITE = {
    "id": "r", "kind": "rewrite", "task": "Rewrite it.", "facts": ["F1"],
    "draft": "DRAFT TEXT about F1.", "questions": ["Q1?", "Q2?", "Q3?"], "mustKeep": ["F1"],
}  # fmt: skip


class ReaderFake(Recorder):
    """Writers answer TEXT<arm>; reader and judge replies come from `plan[(case, arm)]`.

    A plan entry is a list of attempts, each a dict with `correct` (how many answers
    the judge marks right), `lost`, `ai`, and optionally `skill_call` (default True)
    or `raw` (a reply to send instead).
    """

    def __init__(self, plan, baseline_has_skill=False):
        super().__init__()
        self.plan = {k: list(v) for k, v in plan.items()}
        self.baseline_has_skill = baseline_has_skill
        self.current: dict[tuple[str, str], dict] = {}

    def attempt(self, case, arm, take=False):
        attempts = self.plan[(case, arm)]
        if take:
            self.current[(case, arm)] = attempts.pop(0) if len(attempts) > 1 else attempts[0]
        return self.current.get((case, arm), attempts[0])

    def __call__(self, cmd, stdin, env, cwd, timeout):
        self.note(cmd, stdin, env)
        skills = installed(env)
        if self.baseline_has_skill and "Load the" not in stdin and not stdin.startswith("You are"):
            skills = [SKILL]
        if stdin.startswith("You are a reader"):
            a = self.attempt(*graded_text(stdin))
            if "raw" in a:
                return 0, stream(skills, result=a["raw"])
            reply = {"answers": ["x", "y", "z"], "aiSounding": a["ai"], "quote": "q"}
            return 0, stream(skills, result="Here: " + json.dumps(reply))
        if stdin.startswith("You are checking"):
            a = self.attempt(*graded_text(stdin))
            correct = [i < a["correct"] for i in range(3)]
            return 0, stream(skills, result=json.dumps({"correct": correct, "lost": a["lost"]}))
        case = "w" if "Write the PR text" in stdin else "r"
        arm = "skill" if "Load the" in stdin else "base"
        a = self.attempt(case, arm, take=True)
        uses = [(SKILL, None)] if arm == "skill" and a.get("skill_call", True) else []
        return 0, stream(skills, uses, result=f"TEXT<{case}><{arm}> " + a.get("text", "plain"))


def graded_text(stdin):
    """Which case and arm a reader or judge prompt is about; the draft is the rewrite baseline."""
    m = re.search(r"TEXT<(\w+)><(\w+)>", stdin)
    return m.groups() if m else ("r", "base")


GOOD = {"correct": 3, "lost": [], "ai": False}
BASE = {"correct": 2, "lost": [], "ai": True}


def run_reader(make_ctx, cases, plan, **kw):
    fake = ReaderFake(plan, **kw)
    ctx = make_ctx(fake, behaviour=cases)
    return se.run_cold_reader(ctx, suite("cold-reader")), fake, ctx


def test_a_write_case_passes_when_the_skill_arm_reads_at_least_as_well(make_ctx):
    result, fake, ctx = run_reader(
        make_ctx, [WRITE], {("w", "skill"): [GOOD], ("w", "base"): [BASE]}
    )
    assert result["pass"] is True and result["rerunsUsed"] == 0
    case = result["cases"][0]
    assert case["id"] == "w" and case["result"] == "pass"
    assert case["skill"]["correct"] == 3 and case["baseline"]["correct"] == 2
    assert leftovers(ctx.root) == []


@pytest.mark.parametrize(
    "skill_arm, reason",
    [
        ({"correct": 1, "lost": [], "ai": False}, "fewer answers right"),
        ({"correct": 3, "lost": ["F1"], "ai": False}, "lost or distorted"),
        ({"correct": 3, "lost": [], "ai": True}, "AI-sounding"),
        ({"correct": 3, "lost": [], "ai": False, "skill_call": False}, "never loaded"),
    ],
)
def test_a_write_case_fails_for_each_reason(make_ctx, skill_arm, reason):
    base = {"correct": 2, "lost": [], "ai": False}
    plan = {("w", "skill"): [skill_arm], ("w", "base"): [base]}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    case = result["cases"][0]
    assert case["result"] == "fail" and reason in case["reason"]
    assert result["pass"] is False and result["rerunsUsed"] == 1


def test_a_failing_case_is_rerun_once_and_the_rerun_counted(make_ctx):
    bad = {"correct": 1, "lost": [], "ai": False}
    plan = {("w", "skill"): [bad, GOOD], ("w", "base"): [BASE, BASE]}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    assert result["cases"][0]["result"] == "pass"
    assert result["rerunsUsed"] == 1 and result["pass"] is True


def test_a_rewrite_case_uses_the_draft_as_its_baseline(make_ctx):
    plan = {("r", "skill"): [GOOD], ("r", "base"): [BASE]}
    result, fake, _ = run_reader(make_ctx, [REWRITE], plan)
    assert result["cases"][0]["result"] == "pass"
    writers = [c for c in fake.calls if not c["stdin"].startswith("You are")]
    assert len(writers) == 1 and "Load the" in writers[0]["stdin"]
    assert "DRAFT TEXT" in writers[0]["stdin"]


def test_an_unparsable_reader_reply_fails_the_case_with_its_reason(make_ctx):
    plan = {("w", "skill"): [{"raw": "no json here"}], ("w", "base"): [BASE]}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    case = result["cases"][0]
    assert case["result"] == "fail" and "unparsable" in case["reason"]


def test_a_judge_reply_of_the_wrong_shape_fails_the_case(make_ctx, monkeypatch):
    monkeypatch.setattr(se, "parse_judge", lambda raw, n: None)
    result, _, _ = run_reader(make_ctx, [WRITE], {("w", "skill"): [GOOD], ("w", "base"): [BASE]})
    assert "unparsable" in result["cases"][0]["reason"]


def test_diagnostics_are_recorded_but_do_not_decide(make_ctx):
    heavy = "We leverage a robust, seamless, comprehensive paradigm. " * 5
    plan = {("w", "skill"): [GOOD | {"text": heavy}], ("w", "base"): [BASE]}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    case = result["cases"][0]
    assert case["result"] == "pass"
    diag = case["skill"]["diagnostics"]
    assert diag["per_1000_words"]["ai_tells"] > 0
    assert isinstance(diag["detectability"], (int, float))


def test_cold_reader_runs_use_their_tool_lists_and_no_github(make_ctx):
    _, fake, _ = run_reader(make_ctx, [WRITE], {("w", "skill"): [GOOD], ("w", "base"): [BASE]})
    tools = {}
    for call in fake.calls:
        cmd = call["cmd"]
        role = "reader" if call["stdin"].startswith("You are") else (
            "skill" if "Load the" in call["stdin"] else "base")  # fmt: skip
        tools.setdefault(role, set()).add(cmd[cmd.index("--tools") + 1])
        assert call["stub_gh"] and call["gh_config_empty"]
        assert call["skills"] == ([SKILL] if role == "skill" else [])
    assert tools == {"skill": {"Skill,Read,Bash"}, "base": {""}, "reader": {""}}
    skill_cmd = next(c["cmd"] for c in fake.calls if "Load the" in c["stdin"])
    allowed = skill_cmd[skill_cmd.index("--allowedTools") + 1]
    assert allowed.startswith("Bash(python3 -I ") and allowed.endswith("prose_check.py *)")


def test_the_skill_in_a_baseline_run_stops_the_cold_reader_suite(make_ctx):
    with pytest.raises(re_.IsolationError):
        run_reader(make_ctx, [WRITE], {("w", "skill"): [GOOD], ("w", "base"): [BASE]},
                   baseline_has_skill=True)  # fmt: skip


# Small parsers


def test_parse_reader_takes_the_first_json_object_of_the_right_shape():
    raw = 'Sure. {"answers": ["a", "b"], "aiSounding": false, "quote": ""} trailing'
    assert se.parse_reader(raw, 2) == {"answers": ["a", "b"], "aiSounding": False, "quote": ""}
    assert se.parse_reader('{"answers": ["a"], "aiSounding": false}', 2) is None
    assert se.parse_reader('{"answers": ["a", "b"], "aiSounding": "no"}', 2) is None
    assert se.parse_reader("{not json}", 2) is None


def test_parse_judge_needs_one_boolean_per_question_and_a_list_of_lost_facts():
    assert se.parse_judge('{"correct": [true, false], "lost": []}', 2) == {
        "correct": [True, False], "lost": []}  # fmt: skip
    assert se.parse_judge('{"correct": [true], "lost": []}', 2) is None
    assert se.parse_judge('{"correct": [true, 1], "lost": []}', 2) is None
    assert se.parse_judge('{"correct": [true, true], "lost": [3]}', 2) is None
    assert se.parse_judge("nothing", 2) is None


def test_a_stream_without_an_init_event_lists_no_skills():
    assert se.init_skills([{"type": "result", "result": "x"}]) is None
    assert se.result_text([{"type": "system", "subtype": "init", "skills": []}]) == ""


def test_only_skill_calls_for_the_skill_are_counted_and_stray_lines_are_ignored():
    lines = [
        "[1, 2]",
        "not json",
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "x"}},
                        {"type": "tool_use", "name": "Skill", "input": {"skill": "other"}},
                        {"type": "text", "text": "hi"},
                        {"type": "tool_use", "name": "Skill", "input": {"skill": SKILL}},
                    ]
                },
            }
        ),  # fmt: skip
    ]
    evs = se.events("\n".join(lines))
    assert len(evs) == 1
    assert se.skill_calls(evs, SKILL) == [None]


def test_an_isolation_failure_cancels_the_runs_still_queued(make_ctx):
    fake = TriggerFake({f"q{i}": ["top"] * 3 for i in range(20)}, dup=True)
    ctx = make_ctx(fake, trigger=[trig(f"q{i}", True) for i in range(20)])
    ctx.workers = 1
    with pytest.raises(re_.IsolationError):
        se.run_trigger(ctx, suite("trigger"))
    assert len(fake.calls) < 60
