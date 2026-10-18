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
    """Per query, run outcomes: 'top', 'sub', 'none', 'companion' (plain-prose only) or 'error'."""

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
                "none": [], "companion": [(COMPANION, None)]}[outcome]  # fmt: skip
        return 0, stream(self.listed if self.listed is not None else installed(env), uses)


def trig(query, should, delegated=False):
    return {"query": query, "should_trigger": should, **({"delegated": True} if delegated else {})}


def run_trigger(make_ctx, cases, plan, **kw):
    fake = TriggerFake(plan, **kw)
    ctx = make_ctx(fake, trigger=cases)
    return de.run_doc_trigger(ctx, suite("doc-trigger")), fake, ctx


def test_trigger_runs_install_the_skill_and_its_companions_and_no_github(make_ctx):
    result, fake, ctx = run_trigger(make_ctx, [trig("q", True)], {"q": ["top"] * 3})
    assert result["pass"] is True
    for call in fake.calls:
        cmd = call["cmd"]
        assert call["skills"] == [COMPANION, SKILL]
        assert cmd[cmd.index("--tools") + 1] == "Skill,Read,Agent"
        assert cmd[cmd.index("--max-turns") + 1] == "4"
        assert call["stub_gh"] and call["gh_config_empty"]
    assert leftovers(ctx.root) == []


def test_loading_only_the_companion_is_a_miss(make_ctx):
    result, _, _ = run_trigger(make_ctx, [trig("q", True)], {"q": ["companion"] * 3})
    assert result["cases"][0]["triggered"] == 0
    assert result["pass"] is False


def test_a_delegated_case_counts_only_subagent_loads(make_ctx):
    cases = [trig("own", True, delegated=True), trig("sub", True, delegated=True)]
    result, _, _ = run_trigger(make_ctx, cases, {"own": ["top"] * 3, "sub": ["sub"] * 3})
    assert [c["result"] for c in result["cases"]] == ["fail", "pass"]
    assert result["pass"] is False


def positives(n, misses=0):
    cases = [trig(f"p{i}", True) for i in range(n)]
    plan = {f"p{i}": (["companion"] if i >= n - misses else ["top"]) * 3 for i in range(n)}
    return cases, plan


def test_thirteen_of_fifteen_with_clean_near_misses_passes(make_ctx):
    cases, plan = positives(15, misses=2)
    near = [trig(f"n{i}", False) for i in range(10)]
    result, _, _ = run_trigger(make_ctx, cases + near,
                               plan | {f"n{i}": ["companion"] * 3 for i in range(10)})  # fmt: skip
    assert result["recall"] == round(13 / 15, 3)
    assert result["falseTriggers"] == 0
    assert result["accuracy"] == round(23 / 25, 3)
    assert result["pass"] is True


def test_twelve_of_fifteen_fails(make_ctx):
    cases, plan = positives(15, misses=3)
    result, _, _ = run_trigger(make_ctx, cases, plan)
    assert result["pass"] is False


def test_a_near_miss_that_triggers_fails_the_suite(make_ctx):
    cases, plan = positives(15)
    result, _, _ = run_trigger(make_ctx, cases + [trig("n", False)],
                               plan | {"n": ["top", "top", "none"]})  # fmt: skip
    assert result["falseTriggers"] == 1 and result["pass"] is False


def test_a_run_error_fails_its_query(make_ctx):
    result, _, _ = run_trigger(make_ctx, [trig("n", False)], {"n": ["none", "error", "none"]})
    case = result["cases"][0]
    assert case["result"] == "fail" and case["errors"] == 1 and case["triggered"] == 0


@pytest.mark.parametrize("listed", [[SKILL], [SKILL, COMPANION, COMPANION]])
def test_a_companion_listed_twice_or_not_at_all_stops_the_suite(make_ctx, listed):
    with pytest.raises(re_.IsolationError):
        run_trigger(make_ctx, [trig("q", True)], {"q": ["top"] * 3}, listed=listed)


# doc-reader


WRITE = {
    "id": "w", "kind": "write", "task": "Write the README.", "facts": ["F1", "F2"],
    "questions": ["Q1?", "Q2?", "Q3?"], "mustKeep": ["F1"],
}  # fmt: skip
REWRITE = {
    "id": "r", "kind": "rewrite", "task": "Rewrite it.", "facts": ["F1"],
    "draft": "DRAFT TEXT about F1.", "questions": ["Q1?", "Q2?", "Q3?"], "mustKeep": ["F1"],
}  # fmt: skip
DOC = "# Title\n\nThis document explains the thing to a reader with no context.\n"


class ReaderFake(Recorder):
    """Writers answer a document tagged TEXT<case><arm>; readers and judges follow `plan`.

    A plan entry is a list of attempts, each a dict with `correct`, `lost` and `ai`, and
    optionally `loads` (the skills the writer loads), `text` (the document body) or `raw`.
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
        if stdin.startswith("You are a reader"):
            a = self.attempt(*re.search(r"TEXT<(\w+)><(\w+)>", stdin).groups())
            if "raw" in a:
                return 0, stream(skills, result=a["raw"])
            reply = {"answers": ["x", "y", "z"], "aiSounding": a["ai"], "quote": "q"}
            return 0, stream(skills, result=json.dumps(reply))
        if stdin.startswith("You are checking"):
            a = self.attempt(*re.search(r"TEXT<(\w+)><(\w+)>", stdin).groups())
            correct = [i < a["correct"] for i in range(3)]
            return 0, stream(skills, result=json.dumps({"correct": correct, "lost": a["lost"]}))
        case = "w" if "Write the README" in stdin else "r"
        arm = "skill" if f"Load the {SKILL} skill" in stdin else "base"
        if arm == "base" and self.baseline_has_skill:
            skills = [*skills, SKILL]
        a = self.attempt(case, arm, take=True)
        default = [SKILL, COMPANION] if arm == "skill" else [COMPANION]
        uses = [(name, None) for name in a.get("loads", default)]
        body = a.get("text", DOC).replace("Title", f"TEXT<{case}><{arm}>")
        return 0, stream(skills, uses, result=body)


GOOD = {"correct": 3, "lost": [], "ai": False}
BASE = {"correct": 2, "lost": [], "ai": True}


def run_reader(make_ctx, cases, plan, **kw):
    fake = ReaderFake(plan, **kw)
    ctx = make_ctx(fake, behaviour=cases)
    return de.run_doc_reader(ctx, suite("doc-reader")), fake, ctx


def role(call):
    if call["stdin"].startswith("You are"):
        return "reader"
    return "skill" if f"Load the {SKILL} skill" in call["stdin"] else "base"


def test_a_case_passes_when_the_skill_arm_does_at_least_as_well_on_every_count(make_ctx):
    result, fake, ctx = run_reader(make_ctx, [WRITE], {("w", "skill"): [GOOD],
                                                       ("w", "base"): [BASE]})  # fmt: skip
    assert result["pass"] is True and result["rerunsUsed"] == 0
    case = result["cases"][0]
    assert case["result"] == "pass"
    assert case["skill"]["lint"] == case["baseline"]["lint"] == 0
    assert "detectability" in case["skill"]["diagnostics"]
    assert leftovers(ctx.root) == []


def test_each_arm_gets_its_skills_tools_and_pre_approvals(make_ctx):
    _, fake, _ = run_reader(make_ctx, [WRITE], {("w", "skill"): [GOOD], ("w", "base"): [BASE]})
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
            continue
        cmd = call["cmd"]
        allowed = cmd[cmd.index("--allowedTools") + 1 :]
        allowed = allowed[: allowed.index("--permission-prompts")]
        scripts = sorted(Path(re.search(r"Bash\(python3 -I (\S+) \*\)", a).group(1)).name
                         for a in allowed)  # fmt: skip
        assert scripts == (["doc_lint.py", "prose_check.py"] if role(call) == "skill"
                           else ["prose_check.py"])  # fmt: skip
    base_prompt = next(c["stdin"] for c in fake.calls if role(c) == "base")
    assert base_prompt.startswith(f"Load the {COMPANION} skill first")


def test_both_arms_rewrite_the_same_draft(make_ctx):
    result, fake, _ = run_reader(make_ctx, [REWRITE], {("r", "skill"): [GOOD],
                                                       ("r", "base"): [BASE]})  # fmt: skip
    writers = [c for c in fake.calls if role(c) != "reader"]
    assert sorted(role(c) for c in writers) == ["base", "skill"]
    assert all("DRAFT TEXT" in c["stdin"] for c in writers)
    assert result["cases"][0]["result"] == "pass"


@pytest.mark.parametrize(
    ("skill_arm", "reason"),
    [
        (GOOD | {"correct": 1}, "fewer answers right"),
        (GOOD | {"lost": ["F1"]}, "lost or distorted"),
        (GOOD | {"ai": True}, "AI-sounding"),
        (GOOD | {"text": DOC + "\n```\ncode\n```\n"}, "more lint findings"),
        (GOOD | {"loads": [COMPANION]}, f"never loaded {SKILL}"),
        (GOOD | {"loads": [SKILL]}, f"never loaded {COMPANION}"),
    ],
)
def test_a_case_fails_for_each_reason(make_ctx, skill_arm, reason):
    plan = {("w", "skill"): [skill_arm], ("w", "base"): [GOOD]}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    case = result["cases"][0]
    assert case["result"] == "fail" and reason in case["reason"], case["reason"]
    assert result["rerunsUsed"] == 1 and result["pass"] is False


def test_a_failing_case_is_rerun_once_and_the_rerun_counted(make_ctx):
    plan = {("w", "skill"): [GOOD | {"correct": 1}, GOOD], ("w", "base"): [BASE, BASE]}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    assert result["cases"][0]["result"] == "pass"
    assert result["rerunsUsed"] == 1 and result["pass"] is True


def test_an_unparsable_reply_fails_the_case_with_its_reason(make_ctx):
    plan = {("w", "skill"): [{"raw": "no json"}], ("w", "base"): [BASE]}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    assert "unparsable" in result["cases"][0]["reason"]


def test_a_reply_wrapped_in_one_markdown_fence_is_unwrapped_for_both_arms(make_ctx):
    wrapped = "```markdown\n" + DOC + "\n```text\nkept\n```\n```\n"
    plan = {("w", "skill"): [GOOD | {"text": wrapped}], ("w", "base"): [GOOD]}
    result, _, _ = run_reader(make_ctx, [WRITE], plan)
    case = result["cases"][0]
    assert case["result"] == "pass", case["reason"]
    assert case["skill"]["lint"] == 0


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
        run_reader(make_ctx, [WRITE], {("w", "skill"): [GOOD], ("w", "base"): [BASE]},
                   baseline_has_skill=True)  # fmt: skip
