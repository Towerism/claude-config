"""Which eval tier a change needs, and how verify checks a record's tier (#77).

The smoke tier is enough only when nothing a suite reads could have changed:
its definition, its harness, eval.py, its selectors, and its inputs apart from
moves between paths eval/gates.json declares equivalent.
"""

import json

import pytest

from tests.checker_helpers import REPO_ROOT
from tests.eval_helpers import commit, git
from tools.cfg import eval_records as er

CASES = "config/skills/prose/evals/trigger-evals.json"
SKILL = "config/skills/prose/SKILL.md"
OTHER = "config/skills/other/SKILL.md"
MOVED = "config/vendored-skills/other/SKILL.md"
META = {"ranAt": "2026-01-02T00:00:00Z", "claudeVersion": "9.9.9", "models": {"arm": "default"}}

GATES = {
    "equivalentPaths": [["config/skills/", "config/vendored-skills/"]],
    "suites": {
        "trig": {
            "runner": "trigger",
            "skill": "prose",
            "cases": CASES,
            "paths": [CASES, "config/skills/*/SKILL.md", "config/vendored-skills/*/SKILL.md"],
            "selectors": [{"file": SKILL, "part": "description"}],
            "harness": ["eval/skill_evals.py"],
            "smoke": [0, 2],
        },
        "life": {
            "runner": "script",
            "script": "config/skills/life/evals/run.py",
            "paths": ["config/skills/*/SKILL.md", "config/vendored-skills/*/SKILL.md"],
            "harness": ["config/skills/life/evals/run.py"],
        },
    },
}


def files(**changes):
    out = {
        "eval/gates.json": json.dumps(GATES),
        "eval/skill_evals.py": "print(1)\n",
        "tools/cfg/eval.py": "print(1)\n",
        "config/skills/life/evals/run.py": "print(1)\n",
        CASES: json.dumps([{"query": q, "should_trigger": True} for q in "abc"]),
        SKILL: "---\nname: prose\ndescription: d\n---\n# Body\n",
        OTHER: "---\nname: other\n---\nother\n",
    }
    out.update(changes)
    return out


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    base = commit(path, files(), "base")
    git(path, "update-ref", "refs/remotes/origin/main", base)
    git(path, "checkout", "-q", "-b", "work")
    return path, base


def enough(repo, base, name="trig"):
    head = git(repo, "rev-parse", "HEAD")
    return er.smoke_enough(repo, name, base, head)


def test_a_declared_move_with_the_same_content_needs_only_smoke(repo):
    path, base = repo
    commit(path, {OTHER: None, MOVED: "---\nname: other\n---\nother\n"})
    assert enough(path, base) == (True, None)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({OTHER: "changed\n"}, f"{OTHER} changed"),
        ({OTHER: None, MOVED: "changed\n"}, f"{OTHER} moved to {MOVED} and changed"),
        ({OTHER: None, "config/skills/renamed/SKILL.md": "---\nname: other\n---\nother\n"},
         f"{OTHER} was removed or renamed"),
        ({"config/skills/new/SKILL.md": "x\n"}, "config/skills/new/SKILL.md was added"),
        ({OTHER: None}, f"{OTHER} was removed or renamed"),
        ({SKILL: "---\nname: prose\ndescription: new\n---\n# Body\n"},
         f"the selector {SKILL}#description changed"),
    ],
)  # fmt: skip
def test_any_other_change_needs_the_full_tier_and_says_why(repo, change, reason):
    path, base = repo
    commit(path, change)
    assert enough(path, base) == (False, reason)


def test_an_equivalence_the_branch_adds_itself_does_not_count(repo):
    path, _ = repo
    gates = json.loads(json.dumps(GATES))
    del gates["equivalentPaths"]
    base = commit(path, {"eval/gates.json": json.dumps(gates)}, "no pairs")
    commit(path, {"eval/gates.json": json.dumps(GATES), OTHER: None,
                  MOVED: "---\nname: other\n---\nother\n"})  # fmt: skip
    assert enough(path, base) == (False, f"{OTHER} was removed or renamed")


@pytest.mark.parametrize("harness", ["eval/skill_evals.py", "tools/cfg/eval.py"])
def test_a_harness_edit_alone_needs_only_the_smoke_tier(repo, harness):
    path, base = repo
    commit(path, {harness: "print(2)\n"})
    assert enough(path, base) == (True, None)


def test_a_record_stays_valid_when_its_inputs_only_moved(repo):
    path, base = repo
    write_record(path, base, "trig")
    commit(path, {OTHER: None, MOVED: "---\nname: other\n---\nother\n"})
    _, failures, _ = er.verify(path, "origin/main")
    assert [f for f in failures if f.startswith("trig")] == []


def test_a_record_goes_stale_when_a_moved_input_changed(repo):
    path, base = repo
    write_record(path, base, "trig")
    commit(path, {OTHER: None, MOVED: "changed\n"})
    _, failures, _ = er.verify(path, "origin/main")
    assert f"trig: inputs: {OTHER} changed since the record" in failures


def test_two_inputs_that_swap_contents_need_the_full_tier(repo):
    path, base = repo
    commit(path, {SKILL: "---\nname: other\n---\nother\n",
                  OTHER: "---\nname: prose\ndescription: d\n---\n# Body\n"})  # fmt: skip
    assert enough(path, base)[0] is False


def test_a_changed_suite_definition_needs_the_full_tier_but_a_new_smoke_list_does_not(repo):
    path, base = repo
    gates = json.loads(json.dumps(GATES))
    gates["suites"]["trig"]["smoke"] = [1]
    commit(path, {"eval/gates.json": json.dumps(gates)})
    assert enough(path, base) == (True, None)
    gates["suites"]["trig"]["harness"] = ["eval/skill_evals.py", "eval/other.py"]
    commit(path, {"eval/gates.json": json.dumps(gates)})
    assert enough(path, base) == (False, "the suite's definition in eval/gates.json changed")


def test_a_suite_new_in_this_branch_needs_the_full_tier(repo):
    path, _ = repo
    no_gates = commit(path, {"eval/gates.json": None})
    commit(path, {"eval/gates.json": json.dumps(GATES)})
    assert enough(path, no_gates) == (False, "the suite is new in this branch")


def test_a_script_suite_always_needs_the_full_tier(repo):
    path, base = repo
    commit(path, {OTHER: None, MOVED: "---\nname: other\n---\nother\n"})
    assert enough(path, base, "life") == (False, "the script runner has no smoke tier")


# The smoke list and the equivalences in eval/gates.json


@pytest.mark.parametrize(
    ("runner", "smoke", "ok"),
    [
        ("trigger", [0, 2], True),
        ("trigger", ["a"], False),
        ("trigger", [], False),
        ("trigger", [0, 1, 2, 3], False),
        ("trigger", [True], False),
        ("trigger", [-1], False),
        ("cold-reader", ["W-1"], True),
        ("cold-reader", [0], False),
        ("scenario", ["S-01", "S-02", "S-03"], True),
        ("rubric", ["x"], False),
        ("hook-merge", ["x"], False),
        ("script", ["x"], False),
    ],
)
def test_parse_gates_checks_the_smoke_list_against_its_runner(runner, smoke, ok):
    spec = {"runner": runner, "paths": [], "harness": [], "smoke": smoke}
    if runner in er.SKILL_RUNNERS:
        spec |= {"skill": "s", "cases": "c.json"}
    if runner == "script":
        spec["script"] = "run.py"
    text = json.dumps({"suites": {"s1": spec}})
    if ok:
        assert er.parse_gates(text)["s1"].smoke == tuple(smoke)
    else:
        with pytest.raises(er.GatesError, match="s1 smoke"):
            er.parse_gates(text)


@pytest.mark.parametrize(
    "value",
    [{"a": "b"}, [["a"]], [["a", 1]], [["a", "b", "c"]], "x"],
)
def test_parse_equivalences_wants_pairs_of_prefixes(value):
    with pytest.raises(er.GatesError, match="equivalentPaths"):
        er.parse_equivalences(json.dumps({"suites": {}, "equivalentPaths": value}))


def test_no_equivalences_is_an_empty_list():
    assert er.parse_equivalences(json.dumps({"suites": {}})) == ()


def test_a_suite_that_runs_without_a_list_or_has_one_can_run_smoke():
    suites = er.parse_gates(json.dumps({"suites": {
        "r": {"runner": "rubric", "paths": [], "harness": []},
        "t": {"runner": "trigger", "skill": "s", "cases": "c", "paths": [], "harness": []},
    }}))  # fmt: skip
    assert er.smoke_capable(suites["r"]) is True
    assert er.smoke_capable(suites["t"]) is False


# Records and verify


def write_record(path, base, name, tier="full", cases=3, result_pass=True, smoke_cases=None):
    head = git(path, "rev-parse", "HEAD")
    suite = er.gates_at(path, head)[name]
    result = {"pass": result_pass, "cases": [{"id": str(i)} for i in range(cases)]}
    record = er.build_record(path, name, suite, head, base, result, META, tier=tier,
                             duration=12.34, smoke_cases=smoke_cases)  # fmt: skip
    commit(path, {f"eval/records/{name}.json": er.dump_record(record)}, "record")
    return record


def test_a_record_carries_its_tier_and_duration(repo):
    path, base = repo
    commit(path, {OTHER: "changed\n"})
    record = write_record(path, base, "trig")
    assert record["tier"] == "full" and record["durationSeconds"] == 12.3
    assert "smokeCases" not in record
    smoke = write_record(path, base, "trig", tier="smoke", smoke_cases=[0, 2])
    assert smoke["smokeCases"] == [0, 2]


def test_verify_accepts_a_smoke_record_for_a_declared_move(repo):
    path, base = repo
    commit(path, {OTHER: None, MOVED: "---\nname: other\n---\nother\n"})
    write_record(path, base, "trig", tier="smoke", smoke_cases=[0, 2])
    names, failures, tiers = er.verify(path, "origin/main")
    assert "trig" in names and failures == [f for f in failures if f.startswith("life")]
    assert tiers["trig"] == ("smoke", "smoke")


def test_verify_fails_a_smoke_record_where_the_full_tier_is_needed(repo):
    path, base = repo
    commit(path, {OTHER: "changed\n"})
    write_record(path, base, "trig", tier="smoke", smoke_cases=[0, 2])
    _, failures, tiers = er.verify(path, "origin/main")
    assert f"trig: tier: this change needs the full tier ({OTHER} changed)" in failures
    assert tiers["trig"] == ("full", "smoke")


def test_verify_fails_a_smoke_record_whose_cases_are_not_the_smoke_list(repo):
    path, base = repo
    commit(path, {OTHER: None, MOVED: "---\nname: other\n---\nother\n"})
    write_record(path, base, "trig", tier="smoke", smoke_cases=[1])
    _, failures, _ = er.verify(path, "origin/main")
    assert "trig: tier: the smoke record ran [1], not the smoke list [0, 2]" in failures


def test_verify_accepts_a_record_without_a_tier_as_full(repo):
    path, base = repo
    commit(path, {OTHER: None, MOVED: "---\nname: other\n---\nother\n"})
    head = git(path, "rev-parse", "HEAD")
    suite = er.gates_at(path, head)["trig"]
    record = er.build_record(path, "trig", suite, head, base,
                             {"pass": True, "cases": [{}, {}, {}]}, META)  # fmt: skip
    del record["tier"]
    commit(path, {"eval/records/trig.json": er.dump_record(record)}, "record")
    _, failures, tiers = er.verify(path, "origin/main")
    assert not [f for f in failures if f.startswith("trig")]
    assert tiers["trig"] == ("smoke", "full")


def test_verify_fails_a_full_record_with_fewer_cases_than_its_case_file(repo):
    path, base = repo
    commit(path, {OTHER: "changed\n"})
    write_record(path, base, "trig", cases=2)
    _, failures, _ = er.verify(path, "origin/main")
    assert f"trig: cases: the full record has 2 cases, {CASES} has 3" in failures


def test_a_case_file_that_does_not_parse_skips_the_case_count(repo):
    path, base = repo
    commit(path, {CASES: "{"})
    write_record(path, base, "trig", cases=2)
    _, failures, _ = er.verify(path, "origin/main")
    assert not [f for f in failures if f.startswith("trig: cases")]


def test_the_verify_command_prints_the_tier_each_suite_needs_and_has(repo, capsys, monkeypatch):
    path, base = repo
    commit(path, {OTHER: None, MOVED: "---\nname: other\n---\nother\n"})
    write_record(path, base, "trig", tier="smoke", smoke_cases=[0, 2])
    write_record(path, base, "life")
    monkeypatch.chdir(path)
    assert er.main(["verify"]) == 0
    out = capsys.readouterr().out
    assert "life: needs full, found full" in out
    assert "trig: needs smoke, found smoke" in out


# The repo's own smoke lists


def clean_case(runner, case, entry):
    """Whether a full record's entry for `case` passed every one of its runs."""
    if runner in er.SMOKE_BY_INDEX:
        loads = entry["triggered"]
        if not case["should_trigger"]:
            clean = loads == 0
        elif "runs" in entry:
            clean = loads == entry["runs"]
        else:
            # plain-prose-trigger records don't say how many runs a query got (two, or
            # three on a split), so the best evidence is that it reached the load bar.
            clean = loads >= 2
        return clean and entry["result"] == "pass" and not entry.get("errors")
    if runner == "scenario":
        return entry["old"] == "pass" and entry["new"] == "pass"
    if runner == "doc-reader":
        skill, base = entry["skill"], entry["baseline"]
        return (not entry["writerError"] and not entry["notLoaded"] and not skill["missing"]
                and all(skill["right"]) and skill["lint"] <= base["lint"])  # fmt: skip
    return entry["result"] == "pass"


SUITES = er.parse_gates((REPO_ROOT / er.GATES_PATH).read_text())


def latest_full_record(name):
    """The suite's newest full record: the committed one, or the last full one in git history."""
    path = f"{er.RECORDS_DIR}/{name}.json"
    commits = git(REPO_ROOT, "log", "--format=%H", "--", path).split()
    for sha in ["HEAD", *commits]:
        blob = er.read_blob(REPO_ROOT, sha, path)
        record = json.loads(blob) if blob else None
        if record and record.get("tier", "full") == "full":
            return record
    raise AssertionError(f"{name} has no full record in its history")


@pytest.mark.parametrize("name", sorted(n for n, s in SUITES.items() if s.smoke))
def test_each_smoke_case_passed_every_run_of_its_suites_latest_full_record(name):
    suite = SUITES[name]
    record = latest_full_record(name)
    path = suite.cases or "eval/scenarios.json"
    cases = json.loads((REPO_ROOT / path).read_text())
    entries = record["result"]["cases"]
    for item in suite.smoke:
        if isinstance(item, int):
            case, entry = cases[item], entries[item]
        else:
            case = next(c for c in cases if c["id"] == item)
            entry = next(e for e in entries if e["id"] == item)
        assert clean_case(suite.runner, case, entry), f"{name}: smoke case {item!r} isn't clean"


def test_the_repos_hook_suites_and_only_they_follow_claude_codes_version():
    gated = {n for n, s in SUITES.items() if s.version_gated}
    assert gated == {"hook-overlay", "hook-merge", "hook-session-start"}


def test_the_repo_declares_its_two_skill_trees_equivalent():
    pairs = er.parse_equivalences((REPO_ROOT / er.GATES_PATH).read_text())
    assert pairs == (("config/skills/", "config/vendored-skills/"),)


# Choosing the scenarios a change can affect (#77's cost cuts)

SCENARIOS = [
    {"id": "S-1", "loadsSkills": ["a"], "prompt": "one"},
    {"id": "S-2", "loadsSkills": ["b"], "prompt": "two"},
    {"id": "S-3", "loadsSkills": [], "prompt": "three"},
]
CRITERIA = {"graderModel": "sonnet", "rubric": {"pass": 80}, "scenario": {"tries": 2}}
SCENARIO_GATES = {"suites": {"scenario": {
    "runner": "scenario",
    "paths": ["config/CLAUDE.global.md", "config/skill-overlays/**",
              "config/hooks/skill_overlay.py", "eval/scenarios.json", "eval/clauses.json"],
    "selectors": [{"file": "eval/criteria.json", "pointer": "/graderModel"},
                  {"file": "eval/criteria.json", "pointer": "/scenario/tries"}],
    "harness": ["eval/run_eval.py"],
    "compareBase": True,
}}}  # fmt: skip


@pytest.fixture
def scen(tmp_path):
    path = tmp_path / "scen"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    base = commit(path, {
        "eval/gates.json": json.dumps(SCENARIO_GATES),
        "eval/scenarios.json": json.dumps(SCENARIOS),
        "eval/clauses.json": "[]\n",
        "eval/criteria.json": json.dumps(CRITERIA),
        "config/CLAUDE.global.md": "# Global\n",
        "config/skill-overlays/a.md": "overlay a\n",
        "config/skill-overlays/b.md": "overlay b\n",
        "config/hooks/skill_overlay.py": "print(1)\n",
    }, "base")  # fmt: skip
    git(path, "update-ref", "refs/remotes/origin/main", base)
    return path, base


def selection(path, base):
    head = git(path, "rev-parse", "HEAD").strip()
    suite = er.gates_at(path, head)["scenario"]
    return er.scenario_selection(path, "scenario", suite, base, head)


@pytest.mark.parametrize(
    ("change", "picked", "unloaded"),
    [
        ({"config/skill-overlays/a.md": "new a\n"}, ["S-1"], []),
        ({"config/skill-overlays/a.md": None, "config/skill-overlays/d.md": "overlay a\n"},
         ["S-1"], ["d"]),
        ({"config/skill-overlays/c.md": "new c\n"}, [], ["c"]),
        ({"eval/scenarios.json": json.dumps(SCENARIOS[:2] + [{**SCENARIOS[2], "prompt": "3"}])},
         ["S-3"], []),
        ({"eval/scenarios.json": json.dumps(SCENARIOS + [{"id": "S-4", "loadsSkills": []}])},
         ["S-4"], []),
    ],
)  # fmt: skip
def test_a_change_picks_the_scenarios_it_can_affect(scen, change, picked, unloaded):
    path, base = scen
    commit(path, change)
    assert selection(path, base) == (picked, unloaded)


@pytest.mark.parametrize(
    "change",
    [{"config/CLAUDE.global.md": "# New\n"}, {"config/hooks/skill_overlay.py": "print(2)\n"},
     {"eval/clauses.json": "[1]\n"}],
)  # fmt: skip
def test_a_change_that_reaches_every_scenario_runs_them_all(scen, change):
    path, base = scen
    commit(path, change)
    assert selection(path, base) == (None, [])


@pytest.mark.parametrize(
    "criteria",
    [{**CRITERIA, "graderModel": "haiku"}, {**CRITERIA, "scenario": {"tries": 3}}],
)
def test_a_change_to_a_criteria_key_the_suite_reads_runs_every_scenario(scen, criteria):
    path, base = scen
    commit(path, {"eval/criteria.json": json.dumps(criteria)})
    assert selection(path, base) == (None, [])


def test_a_change_to_a_criteria_key_the_suite_does_not_read_selects_nothing_by_itself(scen):
    path, base = scen
    commit(path, {"eval/criteria.json": json.dumps({**CRITERIA, "rubric": {"pass": 90}})})
    assert selection(path, base) == ([], [])


def test_a_suite_listing_the_whole_criteria_file_as_a_path_still_runs_every_scenario(scen):
    path, _ = scen
    gates = json.loads(json.dumps(SCENARIO_GATES))
    gates["suites"]["scenario"]["paths"].append("eval/criteria.json")
    del gates["suites"]["scenario"]["selectors"]
    base = commit(path, {"eval/gates.json": json.dumps(gates)})
    git(path, "update-ref", "refs/remotes/origin/main", base)
    commit(path, {"eval/criteria.json": json.dumps({**CRITERIA, "rubric": {"pass": 90}})})
    assert selection(path, base) == (None, [])


def test_dropping_the_criteria_selectors_in_the_same_change_as_a_key_edit_still_runs_everything(
    scen,
):
    # The merge-base's definition still requires the suite for the key edit, so the selection
    # must read that definition's selectors too, or the record would pass with nothing run.
    path, base = scen
    gates = json.loads(json.dumps(SCENARIO_GATES))
    del gates["suites"]["scenario"]["selectors"]
    commit(path, {"eval/gates.json": json.dumps(gates)})
    commit(path, {"eval/criteria.json": json.dumps({**CRITERIA, "scenario": {"tries": 3}})})
    assert er.required_suites(path, base, git(path, "rev-parse", "HEAD").strip()) == ["scenario"]
    assert selection(path, base) == (None, [])


@pytest.mark.parametrize("text", ["{", '{"a": 1}', "[1, 2]"])
def test_a_scenarios_file_without_scenarios_runs_everything_so_the_run_fails_loudly(scen, text):
    path, base = scen
    commit(path, {"eval/scenarios.json": text})
    assert selection(path, base) == (None, [])


def test_a_new_scenario_suite_runs_every_scenario(scen):
    path, _ = scen
    no_gates = commit(path, {"eval/gates.json": None})
    commit(path, {"eval/gates.json": json.dumps(SCENARIO_GATES)})
    assert selection(path, no_gates) == (None, [])


def scenario_record(path, base, selected):
    head = git(path, "rev-parse", "HEAD").strip()
    suite = er.gates_at(path, head)["scenario"]
    cases = [{"id": i} for i in selected]
    result = {"pass": True, "cases": cases, "selectedCases": selected}
    record = er.build_record(path, "scenario", suite, head, base, result, META)
    commit(path, {"eval/records/scenario.json": er.dump_record(record)}, "record")


def test_verify_accepts_a_scenario_record_that_covers_the_selection(scen):
    path, base = scen
    commit(path, {"config/skill-overlays/a.md": "new a\n"})
    scenario_record(path, base, ["S-1", "S-2"])
    _, failures, _ = er.verify(path, "origin/main")
    assert failures == []


def test_verify_fails_a_scenario_record_that_misses_a_selected_scenario(scen):
    path, base = scen
    commit(path, {"config/skill-overlays/a.md": "new a\n", "config/skill-overlays/b.md": "b2\n"})
    scenario_record(path, base, ["S-1"])
    _, failures, _ = er.verify(path, "origin/main")
    assert failures == ["scenario: cases: the record didn't run S-2, which this change selects"]


def test_a_scenario_record_without_a_selection_is_checked_by_its_count(scen):
    path, base = scen
    commit(path, {"config/CLAUDE.global.md": "# New\n"})
    head = git(path, "rev-parse", "HEAD").strip()
    suite = er.gates_at(path, head)["scenario"]
    record = er.build_record(path, "scenario", suite, head, base,
                             {"pass": True, "cases": [{"id": "S-1"}]}, META)  # fmt: skip
    commit(path, {"eval/records/scenario.json": er.dump_record(record)}, "record")
    _, failures, _ = er.verify(path, "origin/main")
    assert failures == ["scenario: cases: the full record has 1 cases, eval/scenarios.json has 3"]
