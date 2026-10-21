"""tools/cfg/eval.py with tiers (#77): each suite runs in the tier the diff needs, all at once."""

import json
import threading

from tests import test_eval_cmd as cmd_tests
from tests.eval_helpers import GATES, commit, git
from tests.test_eval_cmd import PASS, fake_runners, run
from tools.cfg import eval as eval_cmd

# The same fixtures as the eval command's own tests.
world = cmd_tests.world
fresh_tempdir = cmd_tests.fresh_tempdir

MERGE = "config/hooks/merge_confirm.py"


def tiered_base(world, scenario_smoke=None):
    """A new origin/main whose gates declare config/hooks/ and config/hooks2/ the same."""
    gates = json.loads(json.dumps(GATES))
    gates["equivalentPaths"] = [["config/hooks/", "config/hooks2/"]]
    gates["suites"]["hook-merge"]["paths"].append("config/hooks2/merge_confirm.py")
    gates["suites"]["scenario"]["paths"].append("config/hooks2/skill_overlay.py")
    if scenario_smoke:
        gates["suites"]["scenario"]["smoke"] = scenario_smoke
    base = commit(world["repo"], {"eval/gates.json": json.dumps(gates)}, "tiered gates")
    git(world["repo"], "update-ref", "refs/remotes/origin/main", base)
    return base


def move(world, path):
    text = (world["repo"] / path).read_text()
    commit(world["repo"], {path: None, path.replace("hooks/", "hooks2/"): text}, "move")


def record(world, name):
    return json.loads((world["repo"] / "eval" / "records" / f"{name}.json").read_text())


def test_a_declared_move_runs_smoke_and_a_content_change_runs_full(world):
    tiered_base(world)
    move(world, MERGE)
    commit(world["repo"], {"config/hooks/skill_overlay.py": "print(9)\n"})
    seen = []
    assert run([], fake_runners(seen=seen)) == 0
    tiers = {kind: ctx.tier for kind, ctx in seen}
    assert tiers == {"hook-merge": "smoke", "scenario": "full", "hook-overlay": "full"}
    assert record(world, "hook-merge")["tier"] == "smoke"
    assert record(world, "hook-merge")["smokeCases"] == []
    assert record(world, "scenario")["tier"] == "full"


def test_tier_full_runs_every_suite_in_the_full_tier(world):
    tiered_base(world)
    move(world, MERGE)
    seen = []
    assert run(["--tier", "full"], fake_runners(seen=seen)) == 0
    assert {ctx.tier for _, ctx in seen} == {"full"}


def test_a_smoke_case_missing_from_its_case_file_stops_the_run_before_any_model(world, capsys):
    tiered_base(world, scenario_smoke=["S-99"])
    move(world, "config/hooks/skill_overlay.py")
    seen = []
    assert run(["--suite", "scenario"], fake_runners(seen=seen)) == 2
    assert seen == []
    assert "smoke case 'S-99' is not in eval/scenarios.json" in capsys.readouterr().err


def test_smoke_problems_checks_positions_and_ids(world):
    tiered_base(world)
    head = git(world["repo"], "rev-parse", "HEAD").strip()
    from tools.cfg import eval_records as er

    trig = er.Suite(runner="trigger", paths=(), harness=(), skill="s", cases="cases.json",
                    smoke=(0, 5))  # fmt: skip
    commit(world["repo"], {"cases.json": json.dumps([{"query": "a"}])})
    head = git(world["repo"], "rev-parse", "HEAD").strip()
    assert eval_cmd.smoke_problems(world["repo"], head, "t", trig) == [
        "t: smoke case 5 is not in cases.json"
    ]
    broken = er.Suite(runner="trigger", paths=(), harness=(), skill="s", cases="none.json",
                      smoke=(0,))  # fmt: skip
    assert eval_cmd.smoke_problems(world["repo"], head, "b", broken) == [
        "b: no case file to take its smoke cases from"
    ]
    commit(world["repo"], {"bad.json": "{"})
    head = git(world["repo"], "rev-parse", "HEAD").strip()
    unparsable = er.Suite(runner="trigger", paths=(), harness=(), skill="s", cases="bad.json",
                          smoke=(0,))  # fmt: skip
    assert eval_cmd.smoke_problems(world["repo"], head, "u", unparsable) == [
        "u: no case file to take its smoke cases from"
    ]
    fixed = er.Suite(runner="rubric", paths=(), harness=())
    assert eval_cmd.smoke_problems(world["repo"], head, "r", fixed) == []


def test_suites_run_at_the_same_time(world):
    commit(world["repo"], {"config/CLAUDE.global.md": "# New\n", MERGE: "print(3)\n"})
    together = threading.Barrier(3, timeout=10)

    def waits(ctx, suite):
        together.wait()
        return PASS

    runners = {k: waits for k in ("scenario", "rubric", "hook-merge")}
    assert run([], runners) == 0


def test_each_record_has_its_time_and_the_run_prints_its_total(world, capsys):
    commit(world["repo"], {MERGE: "print(3)\n"})
    ticks = iter([0.0, 10.0, 22.5, 200.0])
    assert run([], fake_runners(), timer=lambda: next(ticks)) == 0
    assert record(world, "hook-merge")["durationSeconds"] == 12.5
    out = capsys.readouterr().out
    assert "### hook-merge: pass (full, 12.5 s)" in out
    assert "elapsed: 200.0 s (budget 180 s) over budget" in out


def test_a_smoke_run_is_measured_against_the_smoke_budget(world, capsys):
    tiered_base(world)
    move(world, MERGE)
    ticks = iter([0.0, 1.0, 2.0, 30.0])
    assert run([], fake_runners(), timer=lambda: next(ticks)) == 0
    out = capsys.readouterr().out
    assert "elapsed: 30.0 s (budget 60 s)" in out and "over budget" not in out


def test_an_interrupt_stops_every_running_call(world, monkeypatch):
    from eval import run_eval

    stopped = []
    monkeypatch.setattr(run_eval, "stop_all", lambda: stopped.append(True))

    def interrupted(ctx, suite):
        raise KeyboardInterrupt

    assert run(["--suite", "rubric"], {**fake_runners(), "rubric": interrupted}) == 130
    assert stopped == [True]


def test_a_script_suite_gets_its_share_of_the_limit_as_workers(world):
    from tests.test_eval_cmd import script_world

    script_world(world, 'json.dump({"argv": sys.argv[1:]}, open(out, "w"))')
    assert run(["--suite", "lifecycle-trigger", "--workers", "3", "--no-record"],
               {"script": eval_cmd.run_script_suite}) == 0  # fmt: skip
    raw = json.loads(
        (world["repo"] / "eval" / "out" / "lifecycle-trigger" / "results.json").read_text()
    )
    assert raw["argv"][raw["argv"].index("--workers") + 1] == "3"
