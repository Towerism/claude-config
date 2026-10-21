"""tools/cfg/eval.py, the interim `cfg eval`: refusals, records and the PR summary.

Suite runners are faked where the test is about eval.py itself; one test runs
the real scenario suite end to end against a scripted claude, and the
lifecycle adapter runs real stand-in scripts.
"""

import json
import shutil
import tempfile

import pytest

from eval import hook_harness
from tests.checker_helpers import REPO_ROOT
from tests.eval_fakes import FakeClaude, leftovers, make_live_home, scenario
from tests.eval_helpers import LIFECYCLE, commit, git, make_repo
from tools.cfg import eval as eval_cmd
from tools.cfg import eval_records as er

PASS = {"pass": True, "rerunsUsed": 0, "cases": [{"id": "hook", "result": "pass"}]}
FIXED = {
    "version": lambda: "2.1.299 (Claude Code)",
    "clock": lambda: "2026-10-16T12:00:00Z",
    "which": lambda name: "/usr/bin/claude",
}


@pytest.fixture(autouse=True)
def fresh_tempdir(monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A repo on a branch, a fake live home via CLAUDE_CONFIG_DIR, and a temp root outside HOME."""
    repo, base = make_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    live = make_live_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(live))
    root = tmp_path / "tmproot"
    root.mkdir()
    monkeypatch.setenv("TMPDIR", str(root))
    monkeypatch.chdir(repo)
    return {"repo": repo, "base": base, "live": live, "root": root, "tmp": tmp_path}


def run(argv, runners=None, **deps):
    return eval_cmd.main(argv, runners=runners or {}, **{**FIXED, **deps})


def fake_runners(results=None, seen=None):
    def make(kind):
        def runner(ctx, suite):
            if seen is not None:
                seen.append((kind, ctx))
            return (results or {}).get(kind, PASS)

        return runner

    return {k: make(k) for k in ("scenario", "rubric", "hook-overlay", "hook-merge", "script")}


# Refusals


def test_a_modified_file_is_refused_before_anything_runs(world, capsys):
    (world["repo"] / "docs" / "readme.md").write_text("edited\n")
    seen = []
    assert run([], fake_runners(seen=seen)) == 2
    assert seen == []
    assert "working tree" in capsys.readouterr().err


def test_an_untracked_file_is_refused(world):
    (world["repo"] / "new.txt").write_text("x\n")
    assert run([], fake_runners()) == 2


def test_claude_missing_from_path_is_refused_before_any_temporary_directory(world, capsys):
    commit(world["repo"], {"config/hooks/merge_confirm.py": "print(2)\n"})
    assert run([], fake_runners(), which=lambda name: None) == 2
    assert "claude" in capsys.readouterr().err
    assert leftovers(world["root"]) == []


def test_missing_credentials_are_refused(world, capsys):
    commit(world["repo"], {"config/hooks/merge_confirm.py": "print(2)\n"})
    (world["live"] / ".credentials.json").unlink()
    assert run([], fake_runners()) == 2
    assert "log in" in capsys.readouterr().err


def test_a_temp_root_inside_home_is_refused(world, monkeypatch):
    commit(world["repo"], {"config/hooks/merge_confirm.py": "print(2)\n"})
    inside = world["tmp"] / "home" / "t"
    inside.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(inside))
    assert run([], fake_runners()) == 2


def test_no_base_branch_is_refused(world, capsys):
    git(world["repo"], "update-ref", "-d", "refs/remotes/origin/main")
    assert run([], fake_runners()) == 2
    assert "origin/main" in capsys.readouterr().err


def test_an_unknown_suite_name_is_refused(world, capsys):
    assert run(["--suite", "nope"], fake_runners()) == 2
    assert "nope" in capsys.readouterr().err


def test_a_malformed_gates_file_is_refused(world):
    commit(world["repo"], {"eval/gates.json": "{"})
    assert run([], fake_runners()) == 2


def test_nothing_required_runs_nothing(world, capsys):
    seen = []
    assert run([], fake_runners(seen=seen)) == 0
    assert seen == []
    assert capsys.readouterr().out == "no eval suites required\n"


# Records


def test_required_suites_run_and_their_records_verify(world, capsys):
    commit(world["repo"], {"config/hooks/merge_confirm.py": "print(2)\n"})
    assert run([], fake_runners()) == 0
    record = json.loads((world["repo"] / "eval" / "records" / "hook-merge.json").read_text())
    assert record["claudeVersion"] == "2.1.299 (Claude Code)"
    assert record["ranAt"] == "2026-10-16T12:00:00Z"
    assert record["models"] == {"arm": "default", "grader": "none"}
    assert record["result"] == PASS
    commit(world["repo"], {}, "records")
    assert er.verify(world["repo"], "origin/main")[1] == []
    assert "hook-merge: pass" in capsys.readouterr().out


def test_a_failing_suite_still_gets_a_record_and_exits_1(world):
    commit(world["repo"], {"config/hooks/merge_confirm.py": "print(2)\n"})
    failing = {"hook-merge": {"pass": False, "rerunsUsed": 1, "cases": []}}
    assert run([], fake_runners(failing)) == 1
    record = json.loads((world["repo"] / "eval" / "records" / "hook-merge.json").read_text())
    assert record["result"]["pass"] is False


def test_named_suites_run_even_when_not_required(world):
    seen = []
    assert run(["--suite", "hook-merge,rubric", "--model", "sonnet"], fake_runners(seen=seen)) == 0
    assert [k for k, _ in seen] == ["hook-merge", "rubric"]
    assert all(ctx.model == "sonnet" for _, ctx in seen)
    record = json.loads((world["repo"] / "eval" / "records" / "rubric.json").read_text())
    assert record["models"]["arm"] == "sonnet"


def test_no_record_mode_writes_nothing(world):
    assert run(["--suite", "hook-merge", "--no-record"], fake_runners()) == 0
    assert not (world["repo"] / "eval" / "records").exists()


def test_suites_get_trees_from_the_commits_and_an_out_directory_each(world):
    commit(world["repo"], {"config/CLAUDE.global.md": "# Head\n"})
    (world["repo"] / ".gitignore").write_text("eval/out/\n")
    git(world["repo"], "add", ".gitignore")
    git(world["repo"], "commit", "-q", "-m", "ignore out")
    seen, trees = [], {}

    def scenario_runner(ctx, suite):
        trees["head"] = (ctx.head_tree / "config" / "CLAUDE.global.md").read_text()
        trees["base"] = (ctx.base_tree / "config" / "CLAUDE.global.md").read_text()
        trees["scenarios"] = (ctx.head_tree / "eval" / "scenarios.json").is_file()
        trees["root"], trees["out"] = ctx.root, ctx.out
        return PASS

    runners = {**fake_runners(seen=seen), "scenario": scenario_runner}
    assert run(["--suite", "scenario"], runners) == 0
    assert trees["head"] == "# Head\n" and trees["base"] == "# Global\nrule one\n"
    assert trees["scenarios"] is True
    assert trees["root"] == world["root"].resolve()
    assert trees["out"] == world["repo"] / "eval" / "out" / "scenario"
    assert leftovers(world["root"]) == []


def test_a_merge_base_without_config_gives_the_suite_no_base_tree(world):
    empty_tree = git(world["repo"], "hash-object", "-t", "tree", "/dev/null")
    bare = git(world["repo"], "commit-tree", empty_tree, "-m", "bare")
    git(world["repo"], "checkout", "-q", "-b", "on-bare", bare)
    commit(world["repo"], {"eval/gates.json": (REPO_ROOT / "eval" / "gates.json").read_text()})
    trees = {}

    def scenario_runner(ctx, suite):
        trees["base"] = ctx.base_tree
        return PASS

    runners = {**fake_runners(), "scenario": scenario_runner}
    assert run(["--suite", "scenario", "--base", bare, "--no-record"], runners) == 0
    assert trees["base"] is None


def test_head_moving_during_a_suite_writes_no_record(world, capsys):
    commit(world["repo"], {"config/hooks/merge_confirm.py": "print(2)\n"})

    def moving(ctx, suite):
        git(world["repo"], "commit", "-q", "--allow-empty", "-m", "moved")
        return PASS

    assert run([], {**fake_runners(), "hook-merge": moving}) == 1
    assert not (world["repo"] / "eval" / "records" / "hook-merge.json").exists()
    assert "HEAD moved" in capsys.readouterr().out


def test_an_isolation_failure_writes_no_record_and_exits_1(world, capsys):
    from eval import run_eval

    commit(world["repo"], {"config/hooks/merge_confirm.py": "print(2)\n"})

    def leaky(ctx, suite):
        raise run_eval.IsolationError("arm isolation failed")

    assert run([], {**fake_runners(), "hook-merge": leaky}) == 1
    assert not (world["repo"] / "eval" / "records").exists()
    assert "isolation failed" in capsys.readouterr().out


def test_a_refusal_inside_a_suite_exits_2(world):
    from eval import run_eval

    def refuse(ctx, suite):
        raise run_eval.Refusal("plugin missing")

    assert run(["--suite", "rubric"], {**fake_runners(), "rubric": refuse}) == 2
    assert leftovers(world["root"]) == []


def test_an_interrupt_exits_130_and_cleans_up(world):
    def interrupted(ctx, suite):
        raise KeyboardInterrupt

    assert run(["--suite", "rubric"], {**fake_runners(), "rubric": interrupted}) == 130
    assert not (world["repo"] / "eval" / "records").exists()
    assert leftovers(world["root"]) == []


# The summary for the PR body


def test_the_summary_tabulates_scenario_cases():
    result = {
        "pass": False,
        "regressions": ["S-02"],
        "newRuleFailures": [],
        "rerunsUsed": 1,
        "cases": [
            {"id": "S-01", "old": "pass", "new": "pass", "graderReason": "fine | ok"},
            {"id": "S-02", "old": "pass", "new": "fail", "graderReason": "missed"},
        ],
    }
    assert eval_cmd.summary("scenario", result) == (
        "### scenario: FAIL\n\n"
        "- regressions: S-02\n"
        "- newRuleFailures: none\n"
        "- rerunsUsed: 1\n\n"
        "| Case | Old | New | Grader's reason |\n"
        "|---|---|---|---|\n"
        "| S-01 | pass | pass | fine \\| ok |\n"
        "| S-02 | pass | fail | missed |\n"
    )


def test_the_summary_tabulates_other_cases_and_skips_an_empty_table():
    assert eval_cmd.summary(
        "hook-overlay", {"pass": True, "p95Ms": 30.1, "cases": [{"id": "main", "result": "pass"}]}
    ) == (  # fmt: skip
        "### hook-overlay: pass\n\n- p95Ms: 30.1\n\n| Case | Result |\n|---|---|\n| main | pass |\n"
    )
    assert eval_cmd.summary("rubric", {"pass": True, "scores": [85], "cases": []}) == (
        "### rubric: pass\n\n- scores: 85\n"
    )


def test_the_claude_version_comes_from_the_cli(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\necho '2.1.299 (Claude Code)'\n")
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    assert eval_cmd.claude_version() == "2.1.299 (Claude Code)"
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert eval_cmd.claude_version() == "unknown"


def test_the_clock_is_utc_iso_8601():
    stamp = eval_cmd.utc_now()
    assert len(stamp) == 20 and stamp.endswith("Z") and stamp[10] == "T"


# Lifecycle suites run the skill's own script


SCRIPT = """import json, sys
out = sys.argv[sys.argv.index("--out") + 1]
{body}
"""


def script_world(world, body):
    commit(
        world["repo"],
        {
            f"{LIFECYCLE}/evals/run_trigger_evals.py": SCRIPT.format(body=body),
            f"{LIFECYCLE}/SKILL.md": "---\ndescription: changed\n---\nbody\n",
        },
    )


def test_a_script_suite_passes_on_exit_0_and_reads_its_cases(world):
    script_world(world, 'json.dump({"results": [{"query": "q1", "pass": True}, '
                        '{"id": 7, "passed": False}]}, open(out, "w")); sys.exit(0)')  # fmt: skip
    assert run(["--suite", "lifecycle-trigger"], {"script": eval_cmd.run_script_suite}) == 0
    record = json.loads((world["repo"] / "eval" / "records" / "lifecycle-trigger.json").read_text())
    assert record["result"] == {
        "pass": True,
        "exitCode": 0,
        "cases": [{"id": "q1", "result": "pass"}, {"id": "7", "result": "fail"}],
    }


def test_a_script_suite_fails_on_a_nonzero_exit_and_tolerates_bad_output(world):
    script_world(world, 'open(out, "w").write("not json"); sys.exit(1)')
    assert run(["--suite", "lifecycle-trigger"], {"script": eval_cmd.run_script_suite}) == 1
    record = json.loads((world["repo"] / "eval" / "records" / "lifecycle-trigger.json").read_text())
    assert record["result"] == {"pass": False, "exitCode": 1, "cases": []}


@pytest.mark.parametrize("payload", ["[]", '[{"id": "a", "pass": true}, 5]', '{"results": 3}'])
def test_script_cases_accept_a_list_or_results_and_skip_odd_entries(tmp_path, payload):
    out = tmp_path / "r.json"
    out.write_text(payload)
    cases = eval_cmd.script_cases(out)
    assert cases in ([], [{"id": "a", "result": "pass"}])


def test_script_cases_from_a_missing_file_are_empty(tmp_path):
    assert eval_cmd.script_cases(tmp_path / "none.json") == []


def test_a_script_suite_runs_the_head_commits_script_with_the_temp_root(world):
    script_world(world, 'import os; json.dump({"tmp": os.environ["TMPDIR"], "argv": sys.argv[1:]},'
                        ' open(out, "w"))')  # fmt: skip
    assert run(["--suite", "lifecycle-trigger", "--model", "m", "--no-record"],
               {"script": eval_cmd.run_script_suite}) == 0  # fmt: skip
    raw = json.loads(
        (world["repo"] / "eval" / "out" / "lifecycle-trigger" / "results.json").read_text()
    )
    assert raw["tmp"] == str(world["root"].resolve())
    assert raw["argv"][-2:] == ["--model", "m"]


# The real scenario suite end to end, with a scripted claude


def test_the_scenario_suite_runs_end_to_end_and_its_record_verifies(world, capsys):
    repo = world["repo"]
    hook = (REPO_ROOT / "config" / "hooks" / "skill_overlay.py").read_text()
    commit(repo, {"config/hooks/skill_overlay.py": hook, ".gitignore": "eval/out/\n"}, "real hook")
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "HEAD"))
    commit(
        repo,
        {
            "config/CLAUDE.global.md": "# Head rules\n",
            "eval/scenarios.json": json.dumps([scenario("S-01")]),
        },
    )
    assert run(["--suite", "scenario,rubric", "--workers", "1"], runner=FakeClaude()) == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "scenario record")
    assert er.verify(repo, "origin/main")[1] == []
    out = capsys.readouterr().out
    assert "### scenario: pass" in out and "| S-01 | pass | pass |" in out
    assert leftovers(world["root"]) == []
    shutil.rmtree(repo / "eval" / "out")


# The hook harness command line delegates here


def test_the_hook_harness_command_line_needs_overlay_or_merge(capsys):
    assert hook_harness.main(["both"]) == 2
    assert "overlay|merge" in capsys.readouterr().err


def test_the_hook_harness_command_line_runs_one_suite_without_a_record(monkeypatch):
    calls = []
    monkeypatch.setattr(eval_cmd, "main", lambda argv: calls.append(argv) or 0)
    assert hook_harness.main(["merge"]) == 0
    assert calls == [["--suite", "hook-merge", "--no-record"]]


def test_the_doc_runners_are_registered():
    from eval import doc_evals

    assert eval_cmd.RUNNERS["doc-trigger"] is doc_evals.run_doc_trigger
    assert eval_cmd.RUNNERS["doc-reader"] is doc_evals.run_doc_reader


def test_the_summary_shows_the_doc_runners_figures():
    text = eval_cmd.summary("writing-docs-behaviour", {
        "pass": False, "rightShare": 0.867, "lint": {"skill": 1, "baseline": 3},
        "reasons": ["right answers 87% of all questions, under 90%"], "cases": [],
    })  # fmt: skip
    assert "- rightShare: 0.867" in text
    assert "- lint: {'skill': 1, 'baseline': 3}" in text
    assert "- reasons: right answers 87% of all questions, under 90%" in text
    text = eval_cmd.summary("writing-docs-trigger", {
        "pass": True, "recall": 0.867, "delegatedRate": 0.5, "runs": 55, "turnLimitRuns": 0,
        "cases": [],
    })  # fmt: skip
    for line in ("- delegatedRate: 0.5", "- runs: 55", "- turnLimitRuns: 0"):
        assert line in text


def test_the_summary_skips_the_case_table_when_cases_have_no_result():
    text = eval_cmd.summary("writing-docs-behaviour", {
        "pass": True, "rightShare": 1.0, "cases": [{"id": "w", "skill": {"right": [True]}}],
    })  # fmt: skip
    assert "- rightShare: 1.0" in text
    assert "| Case |" not in text


def test_the_session_runner_is_registered(monkeypatch):
    from eval import session_harness

    monkeypatch.setattr(session_harness, "run_session", lambda ctx: {"ctx": ctx})
    assert eval_cmd.RUNNERS["hook-session-start"]("c", None) == {"ctx": "c"}
