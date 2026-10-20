"""The doc-trigger and doc-reader runners, which test a skill with its companions installed.

What the model says is faked. What the runners build (config directories with
the skill and its companions, the stub gh, the tool lists) and how they score
the streams are real, and so are doc_lint.py and prose_check.py, which supply
the lint counts and the diagnostics.
"""

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from eval import doc_evals as de
from eval import run_eval as re_
from tests.checker_helpers import REPO_ROOT
from tests.eval_fakes import leftovers, make_live_home
from tools.cfg import eval_records as er

SKILL, COMPANION = "writing-docs", "plain-prose"
# Another repo skill: trigger runs list it beside both, behaviour runs don't.
OTHER = "other-skill"


@pytest.fixture(autouse=True)
def fresh_tempdir(monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)


def make_head(root: Path, trigger=None, behaviour=None) -> Path:
    """A HEAD snapshot with both skills' real scripts and the given case files."""
    skills = root / "config" / "skills"
    src = REPO_ROOT / "config" / "skills"
    for name, rels in (
        (COMPANION, ["prose_check.py", "vendored/readability_scorer.py",
                     "vendored/ai_content_detector.py"]),
        (SKILL, ["doc_lint.py"]),
    ):  # fmt: skip
        (skills / name / "scripts" / "vendored").mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nBody\n")
        for rel in rels:
            (skills / name / "scripts" / rel).write_text((src / name / "scripts" / rel).read_text())
    (skills / SKILL / "evals").mkdir()
    (skills / SKILL / "evals" / "trigger-evals.json").write_text(json.dumps(trigger or []))
    (skills / SKILL / "evals" / "behaviour-evals.json").write_text(json.dumps(behaviour or []))
    (skills / OTHER).mkdir()
    (skills / OTHER / "SKILL.md").write_text(f"---\nname: {OTHER}\ndescription: d\n---\nBody\n")
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
    cases = "trigger-evals.json" if runner == "doc-trigger" else "behaviour-evals.json"
    return er.Suite(runner=runner, paths=(), harness=(), skill=SKILL,
                    cases=f"config/skills/{SKILL}/evals/{cases}",
                    companions=(COMPANION,))  # fmt: skip


def stream(skills, uses=(), result="done"):
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


# doc-trigger


class TriggerFake(Recorder):
    """Per query, run outcomes in order: 'top', 'sub', 'none', 'companion', 'limit' or 'error'.

    'limit' is a run that hit the turn limit before loading anything, and 'companion-limit'
    one that hit it after loading only the companion.
    """

    def __init__(self, plan, listed=None):
        super().__init__()
        self.plan = {q: list(v) for q, v in plan.items()}
        self.listed = listed

    def __call__(self, cmd, stdin, env, cwd, timeout):
        self.note(cmd, stdin, env)
        outcome = self.plan[stdin].pop(0)
        if outcome == "error":
            return 1, "not json at all"
        uses = {"top": [(COMPANION, None), (SKILL, None)], "sub": [(SKILL, "toolu_1")],
                "none": [], "limit": [], "companion": [(COMPANION, None)],
                "companion-limit": [(COMPANION, None)]}[outcome]  # fmt: skip
        skills = self.listed if self.listed is not None else installed(env)
        out = stream(skills, uses)
        if outcome.endswith("limit"):
            out = out.replace('"type": "result"', '"type": "result", "subtype": "error_max_turns"')
        return 0, out


def trig(query, should, delegated=False):
    return {"query": query, "should_trigger": should, **({"delegated": True} if delegated else {})}


def run_trigger(make_ctx, cases, plan, **kw):
    fake = TriggerFake(plan, **kw)
    ctx = make_ctx(fake, trigger=cases)
    return de.run_doc_trigger(ctx, suite("doc-trigger")), fake, ctx


def runs_for(fake, query):
    return [c for c in fake.calls if c["stdin"] == query]


def test_trigger_runs_install_every_repo_skill_once_and_no_github(make_ctx):
    result, fake, ctx = run_trigger(make_ctx, [trig("q", True)], {"q": ["top"] * 2})
    assert result["pass"] is True
    for call in fake.calls:
        cmd = call["cmd"]
        assert call["skills"] == sorted([COMPANION, OTHER, SKILL])
        assert cmd[cmd.index("--tools") + 1] == "Skill,Read,Agent"
        assert call["stub_gh"] and call["gh_config_empty"]
    assert leftovers(ctx.root) == []


def test_ordinary_queries_get_three_turns_and_delegated_ones_four(make_ctx):
    cases = [trig("own", True), trig("sub", True, delegated=True)]
    _, fake, _ = run_trigger(make_ctx, cases, {"own": ["top"] * 2, "sub": ["sub"] * 2})
    turns = {c["stdin"]: c["cmd"][c["cmd"].index("--max-turns") + 1] for c in fake.calls}
    assert turns == {"own": "3", "sub": "4"}


@pytest.mark.parametrize(
    ("plan", "calls", "triggered"),
    [
        (["top", "top"], 2, True),
        (["none", "none"], 2, False),
        (["top", "none", "top"], 3, True),
        (["none", "top", "none"], 3, False),
    ],
)
def test_a_third_run_happens_only_when_the_first_two_disagree(make_ctx, plan, calls, triggered):
    result, fake, _ = run_trigger(make_ctx, [trig("q", True)], {"q": plan})
    assert len(runs_for(fake, "q")) == calls
    assert result["cases"][0]["runs"] == calls
    assert (result["cases"][0]["result"] == "pass") is triggered


def test_loading_only_the_companion_is_a_miss(make_ctx):
    result, _, _ = run_trigger(make_ctx, [trig("q", True)], {"q": ["companion"] * 2})
    assert result["cases"][0]["triggered"] == 0
    assert result["pass"] is False


def test_a_delegated_query_counts_only_subagent_loads(make_ctx):
    result, _, _ = run_trigger(make_ctx, [trig("d", True, delegated=True)], {"d": ["top"] * 2})
    assert result["cases"][0]["result"] == "fail"


def positives(n, misses=0, delegated_misses=0):
    """`n` should-trigger queries; the last `misses` never trigger, and the first
    `delegated_misses` are delegated queries that never load inside a subagent."""
    cases, plan = [], {}
    for i in range(n):
        delegated = i < delegated_misses
        cases.append(trig(f"p{i}", True, delegated=delegated))
        plan[f"p{i}"] = ["none"] * 2 if (i >= n - misses or delegated) else ["top"] * 2
    return cases, plan


NEAR = [trig(f"n{i}", False) for i in range(10)]
QUIET = {f"n{i}": ["companion"] * 2 for i in range(10)}


def test_thirteen_of_fifteen_with_clean_near_misses_passes(make_ctx):
    cases, plan = positives(15, misses=2)
    result, _, _ = run_trigger(make_ctx, cases + NEAR, plan | QUIET)
    assert result["recall"] == round(13 / 15, 3)
    assert result["falseTriggers"] == 0
    assert result["accuracy"] == round(23 / 25, 3)
    assert result["pass"] is True


def test_eleven_of_fifteen_passes_the_interim_seventy_percent_gate(make_ctx):
    cases, plan = positives(15, misses=4)
    result, _, _ = run_trigger(make_ctx, cases + NEAR, plan | QUIET)
    assert result["recall"] == round(11 / 15, 3) and result["pass"] is True


def test_ten_of_fifteen_fails(make_ctx):
    cases, plan = positives(15, misses=5)
    result, _, _ = run_trigger(make_ctx, cases + NEAR, plan | QUIET)
    assert result["pass"] is False


def test_a_missed_delegated_query_counts_toward_recall_like_any_other(make_ctx):
    cases, plan = positives(15, misses=1, delegated_misses=1)
    result, _, _ = run_trigger(make_ctx, cases + NEAR, plan | QUIET)
    assert result["recall"] == round(13 / 15, 3)
    assert result["delegatedRate"] == 0.0
    assert result["pass"] is True


def test_a_near_miss_that_triggers_fails_the_suite(make_ctx):
    cases, plan = positives(15)
    result, _, _ = run_trigger(make_ctx, cases + [trig("n", False)],
                               plan | {"n": ["top", "none", "top"]})  # fmt: skip
    assert result["falseTriggers"] == 1 and result["pass"] is False


def test_an_errored_run_counts_against_its_class(make_ctx):
    cases = [trig("p", True), trig("n", False)]
    plan = {"p": ["top", "error", "none"], "n": ["none", "error", "top"]}
    result, _, _ = run_trigger(make_ctx, cases, plan)
    by_id = {c["id"]: c for c in result["cases"]}
    assert by_id["p"]["result"] == "fail" and by_id["p"]["errors"] == 1
    assert by_id["n"]["result"] == "fail" and by_id["n"]["errors"] == 1


def test_a_run_that_hits_the_turn_limit_after_loading_only_the_companion_counts(make_ctx):
    plan = {"q": ["companion-limit", "top", "top"]}
    result, _, _ = run_trigger(make_ctx, [trig("q", True)], plan)
    assert result["turnLimitRuns"] == 1


def test_runs_that_hit_the_turn_limit_before_loading_are_counted(make_ctx):
    result, _, _ = run_trigger(make_ctx, [trig("q", True)], {"q": ["limit", "top", "limit"]})
    assert result["turnLimitRuns"] == 2
    assert result["runs"] == 3


@pytest.mark.parametrize("listed", [[SKILL], [SKILL, COMPANION, COMPANION]])
def test_a_companion_listed_twice_or_not_at_all_stops_the_suite(make_ctx, listed):
    with pytest.raises(re_.IsolationError):
        run_trigger(make_ctx, [trig("q", True)], {"q": ["top"] * 2}, listed=listed)


# doc-reader


QUESTIONS = ["How do you install it?", "What error does a bad key give?", "Which row is kept?"]
EXPECT = [["pipx install csv-dedupe"], ["no such column"], ["first"]]
WRITE = {
    "id": "w", "kind": "write", "task": "Write the README.", "facts": ["F1", "F2"],
    "questions": QUESTIONS, "expect": EXPECT, "mustContain": ["pipx install", "no such column"],
}  # fmt: skip
REWRITE = {
    "id": "r", "kind": "rewrite", "task": "Rewrite it.", "facts": ["F1"],
    "draft": "DRAFT TEXT about F1.", "questions": QUESTIONS, "expect": EXPECT,
    "mustContain": ["pipx install", "no such column"],
}  # fmt: skip
DOC = (
    "# Title\n\nThis document explains the thing to a reader with no context.\n\n"
    "Install with `pipx install csv-dedupe`. A bad key prints no such column.\n"
)
RIGHT = ["Run pipx install csv-dedupe", "It prints no such column", "The first one"]


class ReaderFake(Recorder):
    """Writers answer DOC tagged TEXT<case><arm>; readers answer from `plan[(case, arm)]`.

    A plan entry is a dict with optional `answers` (default RIGHT), `loads` (the skills the
    writer loads), `text` (the document) or `raw` (a reader reply to send as it is).
    """

    def __init__(self, plan, baseline_has_skill=False):
        super().__init__()
        self.plan = plan
        self.baseline_has_skill = baseline_has_skill

    def __call__(self, cmd, stdin, env, cwd, timeout):
        self.note(cmd, stdin, env)
        skills = installed(env)
        if stdin.startswith("You are a reader"):
            a = self.plan[re.search(r"TEXT<(\w+)><(\w+)>", stdin).groups()]
            if "raw" in a:
                return 0, stream(skills, result=a["raw"])
            return 0, stream(skills, result=json.dumps({"answers": a.get("answers", RIGHT)}))
        case = "w" if "Write the README" in stdin else "r"
        arm = "skill" if f"Load the {SKILL} skill" in stdin else "base"
        if arm == "base" and self.baseline_has_skill:
            skills = [*skills, SKILL]
        a = self.plan[(case, arm)]
        default = [SKILL, COMPANION] if arm == "skill" else [COMPANION]
        uses = [(name, None) for name in a.get("loads", default)]
        body = a.get("text", DOC).replace("Title", f"TEXT<{case}><{arm}>")
        return 0, stream(skills, uses, result=body)


def run_reader(make_ctx, cases, plan, **kw):
    fake = ReaderFake(plan, **kw)
    ctx = make_ctx(fake, behaviour=cases)
    return de.run_doc_reader(ctx, suite("doc-reader")), fake, ctx


def role(call):
    if call["stdin"].startswith("You are"):
        return "reader"
    return "skill" if f"Load the {SKILL} skill" in call["stdin"] else "base"


FINE = {("w", "skill"): {}, ("w", "base"): {}, ("r", "skill"): {}, ("r", "base"): {}}


def test_the_suite_passes_and_makes_four_calls_per_case(make_ctx):
    result, fake, ctx = run_reader(make_ctx, [WRITE, REWRITE], FINE)
    assert result["pass"] is True, result["reasons"]
    assert result["rightShare"] == 1.0
    assert result["lint"] == {"skill": 0, "baseline": 0}
    assert len(fake.calls) == 8
    assert sorted(role(c) for c in fake.calls).count("reader") == 4
    assert "rerunsUsed" not in result
    assert leftovers(ctx.root) == []


def test_each_arm_gets_its_skills_tools_and_pre_approvals(make_ctx):
    _, fake, _ = run_reader(make_ctx, [WRITE], FINE)
    seen = {}
    for call in fake.calls:
        cmd = call["cmd"]
        seen.setdefault(role(call), set()).add(
            (tuple(call["skills"]), cmd[cmd.index("--tools") + 1]))  # fmt: skip
        assert call["stub_gh"] and call["gh_config_empty"]
    assert seen == {"skill": {((COMPANION, SKILL), "Skill,Read,Bash")},
                    "base": {((COMPANION,), "Skill,Read,Bash")},
                    "reader": {((), "")}}  # fmt: skip
    for call in fake.calls:
        if role(call) == "reader":
            assert "AI" not in call["stdin"]
            continue
        cmd = call["cmd"]
        allowed = cmd[cmd.index("--allowedTools") + 1 :]
        allowed = allowed[: allowed.index("--permission-prompts")]
        scripts = sorted(Path(re.search(r"Bash\(python3 -I (\S+) \*\)", a).group(1)).name
                         for a in allowed)  # fmt: skip
        assert scripts == (["doc_lint.py", "prose_check.py"] if role(call) == "skill"
                           else ["prose_check.py"])  # fmt: skip


def test_both_arms_rewrite_the_same_draft(make_ctx):
    _, fake, _ = run_reader(make_ctx, [REWRITE], FINE)
    writers = [c for c in fake.calls if role(c) != "reader"]
    assert sorted(role(c) for c in writers) == ["base", "skill"]
    assert all("DRAFT TEXT" in c["stdin"] for c in writers)


def test_answers_are_graded_by_their_expect_patterns_in_code(make_ctx):
    plan = FINE | {("w", "skill"): {"answers": ["use PIPX INSTALL CSV-DEDUPE", "not stated",
                                                "the first"]}}  # fmt: skip
    result, _, _ = run_reader(make_ctx, [WRITE, REWRITE], plan)
    case = next(c for c in result["cases"] if c["id"] == "w")
    assert case["skill"]["right"] == [True, False, True]
    assert result["rightShare"] == round(5 / 6, 3)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({("w", "skill"): {"loads": [COMPANION]}}, f"never loaded {SKILL}"),
        ({("w", "skill"): {"loads": [SKILL]}}, f"never loaded {COMPANION}"),
        ({("w", "skill"): {"text": "# Title\n\nThis text leaves out the install command."}},
         "missing pipx install"),
        ({("w", "skill"): {"answers": ["x", "y", "z"]}}, "right answers"),
        ({("w", "skill"): {"text": DOC + "\n```\ncode\n```\n"}}, "lint findings"),
    ],
)  # fmt: skip
def test_the_suite_fails_for_each_reason(make_ctx, change, reason):
    result, _, _ = run_reader(make_ctx, [WRITE, REWRITE], FINE | change)
    assert result["pass"] is False
    assert any(reason in r for r in result["reasons"]), result["reasons"]


FIVE = [WRITE] + [REWRITE | {"id": f"r{i}"} for i in range(4)]


def test_one_case_below_the_bar_does_not_fail_the_suite_when_the_totals_hold(make_ctx):
    plan = FINE | {("w", "skill"): {"answers": ["x", *RIGHT[1:]]}}
    result, _, _ = run_reader(make_ctx, FIVE, plan)
    assert result["rightShare"] == round(14 / 15, 3) and result["pass"] is True


def test_totals_under_ninety_percent_fail_the_suite(make_ctx):
    plan = FINE | {("w", "skill"): {"answers": ["x", "y", "the first"]}}
    result, _, _ = run_reader(make_ctx, FIVE, plan)
    assert result["rightShare"] == round(13 / 15, 3) and result["pass"] is False


class ErroringWriter(ReaderFake):
    def __call__(self, cmd, stdin, env, cwd, timeout):
        if f"Load the {SKILL} skill" in stdin:
            self.note(cmd, stdin, env)
            return 1, "not json at all"
        if stdin.startswith("You are a reader") and "TEXT<" not in stdin:
            self.note(cmd, stdin, env)  # the reader of the skill arm's empty text
            return 0, stream([], result=json.dumps({"answers": RIGHT}))
        return super().__call__(cmd, stdin, env, cwd, timeout)


def test_an_errored_writing_run_is_reported_as_an_error(make_ctx):
    ctx = make_ctx(ErroringWriter(FINE), behaviour=[WRITE])
    result = de.run_doc_reader(ctx, suite("doc-reader"))
    assert result["pass"] is False
    assert any("writing run errored" in r for r in result["reasons"]), result["reasons"]
    assert not any("never loaded" in r for r in result["reasons"])


def test_an_unparsable_reply_counts_its_answers_as_wrong_and_is_reported(make_ctx):
    plan = FINE | {("w", "skill"): {"raw": "no json"}}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    assert result["unparsable"] == ["w/skill"]
    assert result["cases"][0]["skill"]["right"] == [False] * 3
    assert result["pass"] is False


def test_a_reply_wrapped_in_one_markdown_fence_is_unwrapped_for_both_arms(make_ctx):
    wrapped = "```markdown\n" + DOC + "\n```text\nkept\n```\n```\n"
    result, _, _ = run_reader(make_ctx, [WRITE], FINE | {("w", "skill"): {"text": wrapped}})
    assert result["pass"] is True, result["reasons"]
    assert result["lint"]["skill"] == 0


@pytest.mark.parametrize(
    ("text", "unwrapped"),
    [
        ("```markdown\n# T\n```", "# T"),
        ("\n```md\n# T\n```\n\n", "# T"),
        ("```\n# T\n```", "# T"),
        ("```python\nx = 1\n```", "```python\nx = 1\n```"),
        ("# T\n\n```\nx\n```", "# T\n\n```\nx\n```"),
        ("```markdown\n# T", "```markdown\n# T"),
        # A bare fence wraps the reply only when nothing inside looks like a fence.
        ("```\ncode\n```\n\nText\n\n```\nmore\n```", "```\ncode\n```\n\nText\n\n```\nmore\n```"),
        ("````\n# T\n```bash\nx\n```\n````", "````\n# T\n```bash\nx\n```\n````"),
        ("", ""),
    ],
)
def test_unwrap_removes_only_one_outer_markdown_fence(text, unwrapped):
    assert de.unwrap(text).strip() == unwrapped.strip()


def test_the_skill_in_the_baseline_stops_the_suite(make_ctx):
    with pytest.raises(re_.IsolationError):
        run_reader(make_ctx, [WRITE], FINE, baseline_has_skill=True)
