"""The record verifier and the gate rules that decide which eval suites a change needs.

CI trusts these with no model in the loop, so every branch is covered against
real git repos built by tests/eval_helpers.py.
"""

import hashlib
import json

import pytest

from tests.checker_helpers import REPO_ROOT
from tests.eval_helpers import FRAGMENT, GATES, LIFECYCLE, SKILL_MD, commit, git, make_repo
from tools.cfg import eval_records as er

RESULT_PASS = {"pass": True, "cases": []}
META = {"ranAt": "2026-01-02T00:00:00Z", "claudeVersion": "9.9.9", "models": {"arm": "default"}}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def required(repo, base):
    return er.required_suites(repo, base, git(repo, "rev-parse", "HEAD"))


def fragment_with(**hooks):
    frag = json.loads(json.dumps(FRAGMENT))
    frag["hooks"].update(hooks)
    return json.dumps(frag, indent=2)


def write_records(repo, base, suites, result=RESULT_PASS):
    """Commit records for `suites`, built from the current HEAD, the way eval.py does."""
    head = git(repo, "rev-parse", "HEAD")
    gates = er.gates_at(repo, head)
    files = {}
    for name in suites:
        record = er.build_record(repo, name, gates[name], head, base, result, META)
        files[f"eval/records/{name}.json"] = er.dump_record(record)
    return commit(repo, files, "records")


# Which suites a change requires


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"config/CLAUDE.global.md": "# Global\nrule two\n"}, ["rubric", "scenario"]),
        ({"config/skill-overlays/engineering-skills:senior-qa.md": "x\n"}, ["scenario"]),
        ({"config/skill-overlays/new:skill.md": "x\n"}, ["scenario"]),
        ({"config/hooks/skill_overlay.py": "print(2)\n"}, ["hook-overlay", "scenario"]),
        ({"config/hooks/merge_confirm.py": "print(2)\n"}, ["hook-merge"]),
        ({"eval/run_eval.py": "print(2)\n"}, ["rubric", "scenario"]),
        ({"eval/hook_harness.py": "print(2)\n"}, ["hook-merge", "hook-overlay"]),
        ({"eval/clauses.json": "[1]\n"}, ["scenario"]),
        ({"plugins.lock.json": '{"a": 1}\n'}, []),
        ({"docs/readme.md": "new\n"}, []),
        ({f"{LIFECYCLE}/evals/evals.json": "{}\n"}, ["lifecycle-behaviour"]),
    ],
)
def test_changed_paths_require_the_suites_their_gates_name(tmp_path, files, expected):
    repo, base = make_repo(tmp_path)
    commit(repo, files)
    assert required(repo, base) == expected


def test_a_change_to_the_pre_tool_use_hook_entry_requires_only_the_merge_check(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/settings.fragment.json": fragment_with(PreToolUse=[])})
    assert required(repo, base) == ["hook-merge"]


def test_a_change_to_the_overlay_hook_entries_requires_the_overlay_check(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/settings.fragment.json": fragment_with(UserPromptExpansion=[])})
    assert required(repo, base) == ["hook-overlay"]


def test_a_fragment_change_outside_the_hook_entries_requires_nothing(tmp_path):
    repo, base = make_repo(tmp_path)
    frag = json.loads(json.dumps(FRAGMENT))
    frag["enabledPlugins"]["c@d"] = True
    commit(repo, {"config/settings.fragment.json": json.dumps(frag)})
    assert required(repo, base) == []


def test_reformatting_the_fragment_without_changing_values_requires_nothing(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/settings.fragment.json": json.dumps(FRAGMENT, indent=4)})
    assert required(repo, base) == []


def test_skill_description_and_body_changes_require_different_suites(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {f"{LIFECYCLE}/SKILL.md": SKILL_MD.replace("Second line.", "Other.")})
    assert required(repo, base) == ["lifecycle-trigger"]
    commit(repo, {f"{LIFECYCLE}/SKILL.md": SKILL_MD.replace("# Body", "# New body")})
    assert required(repo, base) == ["lifecycle-behaviour"]


def test_removing_a_gate_in_the_same_change_as_the_gated_file_still_requires_the_suite(tmp_path):
    repo, base = make_repo(tmp_path)
    gates = json.loads(json.dumps(GATES))
    gates["suites"]["scenario"]["paths"].remove("config/CLAUDE.global.md")
    commit(
        repo,
        {"config/CLAUDE.global.md": "# Changed\n", "eval/gates.json": json.dumps(gates)},
    )
    assert required(repo, base) == ["rubric", "scenario"]


def test_a_suite_dropped_from_the_gates_is_still_checked_with_its_old_definition(tmp_path):
    repo, base = make_repo(tmp_path)
    gates = json.loads(json.dumps(GATES))
    del gates["suites"]["hook-merge"]
    commit(
        repo,
        {"config/hooks/merge_confirm.py": "print(3)\n", "eval/gates.json": json.dumps(gates)},
    )
    assert required(repo, base) == ["hook-merge"]
    failures = er.verify(repo, "main")[1]
    assert failures == ["hook-merge: record: eval/records/hook-merge.json is missing"]


def test_a_renamed_gated_file_counts_under_its_old_path(tmp_path):
    repo, base = make_repo(tmp_path)
    git(repo, "mv", "config/hooks/merge_confirm.py", "config/hooks/renamed.py")
    git(repo, "commit", "-q", "-m", "rename")
    assert required(repo, base) == ["hook-merge"]


def test_a_selector_file_that_appears_or_disappears_counts_as_a_change(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/settings.fragment.json": None})
    assert required(repo, base) == ["hook-merge", "hook-overlay"]


def test_gates_are_empty_at_a_commit_without_a_gates_file(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"eval/gates.json": None})
    assert er.gates_at(repo, git(repo, "rev-parse", "HEAD")) == {}


# Selector values


def test_a_skill_without_frontmatter_has_an_empty_description_and_the_whole_file_as_body():
    assert er.split_skill_md("# Just a body\n") == ("", "# Just a body\n")


def test_an_unclosed_frontmatter_block_is_treated_as_body():
    assert er.split_skill_md("---\nname: x\n") == ("", "---\nname: x\n")


def test_the_description_keeps_its_continuation_lines_and_stops_at_the_next_key():
    text = "---\nname: x\ndescription: one\n  two\nlicense: MIT\n---\nbody\n"
    assert er.split_skill_md(text) == ("description: one\n  two\n", "body\n")


def test_a_pointer_into_a_list_and_escaped_keys_resolve():
    doc = {"a/b": [{"~k": 1}]}
    assert er.resolve_pointer(doc, "/a~1b/0/~0k") == 1
    assert er.resolve_pointer(doc, "/a~1b/5") is er.MISSING
    assert er.resolve_pointer(doc, "/a~1b/x") is er.MISSING
    assert er.resolve_pointer(doc, "/nope") is er.MISSING
    assert er.resolve_pointer(1, "/x") is er.MISSING
    assert er.resolve_pointer(doc, "") == doc


def test_a_pointer_into_a_file_that_is_not_json_selects_the_raw_bytes(tmp_path):
    repo, _ = make_repo(tmp_path)
    head = commit(repo, {"config/settings.fragment.json": "not json"})
    sel = {"file": "config/settings.fragment.json", "pointer": "/hooks"}
    assert er.selector_value(repo, head, sel) == b"not json"


# Loading gates.json


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "[]",
        '{"suites": []}',
        '{"suites": {"x": []}}',
        '{"suites": {"x": {"runner": "nope", "paths": [], "harness": []}}}',
        '{"suites": {"x": {"runner": "script", "paths": [], "harness": []}}}',
        '{"suites": {"x": {"runner": "scenario", "paths": "a", "harness": []}}}',
        '{"suites": {"x": {"runner": "scenario", "paths": [], "harness": [1]}}}',
        '{"suites": {"x": {"runner": "scenario", "paths": [], "harness": [], "selectors": [{}]}}}',
        '{"suites": {"x": {"runner": "scenario", "paths": [], "harness": [],'
        ' "selectors": [{"file": "a", "part": "middle"}]}}}',
        '{"suites": {"x": {"runner": "scenario", "paths": [], "harness": [], "selectors": "a"}}}',
        '{"suites": {"x": {"runner": "scenario", "paths": [], "harness": [],'
        ' "compareBase": "yes"}}}',
    ],
)
def test_a_malformed_gates_file_is_refused(bad):
    with pytest.raises(er.GatesError):
        er.parse_gates(bad)


def test_verify_exits_2_when_the_gates_file_is_malformed(tmp_path, capsys, monkeypatch):
    repo, _ = make_repo(tmp_path)
    commit(repo, {"eval/gates.json": "{"})
    monkeypatch.chdir(repo)
    assert er.main(["verify"]) == 2
    assert "gates.json" in capsys.readouterr().err


# Records and the verifier


def test_a_record_holds_input_harness_and_base_hashes(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/CLAUDE.global.md": "# New\n"})
    head = git(repo, "rev-parse", "HEAD")
    gates = er.gates_at(repo, head)
    record = er.build_record(repo, "scenario", gates["scenario"], head, base, RESULT_PASS, META)
    assert record["suite"] == "scenario"
    assert record["inputs"]["config/CLAUDE.global.md"] == sha("# New\n")
    assert record["base"] == {
        "ref": base,
        "inputs": {**record["inputs"], "config/CLAUDE.global.md": sha("# Global\nrule one\n")},
    }
    assert record["harness"] == {"eval/run_eval.py": sha("print('run')\n")}
    assert record["result"] == RESULT_PASS
    assert record["claudeVersion"] == "9.9.9"
    rubric = er.build_record(repo, "rubric", gates["rubric"], head, base, RESULT_PASS, META)
    assert "base" not in rubric


def test_selector_inputs_are_keyed_by_file_and_selector(tmp_path):
    repo, base = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    gates = er.gates_at(repo, head)
    record = er.build_record(repo, "hook-merge", gates["hook-merge"], head, base, RESULT_PASS, META)
    entry = json.dumps(FRAGMENT["hooks"]["PreToolUse"], sort_keys=True, separators=(",", ":"))
    assert record["inputs"] == {
        "config/hooks/merge_confirm.py": sha("print('merge')\n"),
        "config/settings.fragment.json#/hooks/PreToolUse": sha(entry),
    }
    trig = er.build_record(
        repo, "lifecycle-trigger", gates["lifecycle-trigger"], head, base, RESULT_PASS, META
    )
    assert trig["inputs"][f"{LIFECYCLE}/SKILL.md#description"] == sha(
        "description: Runs the issue lifecycle.\n  Second line.\n"
    )


def test_records_are_written_with_sorted_keys_and_a_final_newline():
    text = er.dump_record({"b": 1, "a": {"d": 2, "c": 3}})
    assert text == '{\n  "a": {\n    "c": 3,\n    "d": 2\n  },\n  "b": 1\n}\n'


def test_verify_passes_with_matching_records(tmp_path, capsys, monkeypatch):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/CLAUDE.global.md": "# New\n"})
    write_records(repo, base, ["scenario", "rubric"])
    monkeypatch.chdir(repo)
    assert er.main(["verify", "--base", "main"]) == 0
    assert "rubric, scenario" in capsys.readouterr().out


def test_verify_passes_when_no_suite_is_required(tmp_path, capsys, monkeypatch):
    repo, _ = make_repo(tmp_path)
    commit(repo, {"docs/readme.md": "changed\n"})
    monkeypatch.chdir(repo)
    assert er.main(["verify"]) == 0
    assert capsys.readouterr().out == "no eval suites required\n"


def test_verify_reports_a_missing_record(tmp_path, capsys, monkeypatch):
    repo, _ = make_repo(tmp_path)
    commit(repo, {"config/hooks/merge_confirm.py": "print(2)\n"})
    monkeypatch.chdir(repo)
    assert er.main(["verify"]) == 1
    assert capsys.readouterr().out == (
        "hook-merge: needs full, found no record\n"
        "hook-merge: record: eval/records/hook-merge.json is missing\n"
    )


def test_verify_reports_an_unparsable_record(tmp_path):
    repo, _ = make_repo(tmp_path)
    commit(repo, {"config/hooks/merge_confirm.py": "print(2)\n"})
    commit(repo, {"eval/records/hook-merge.json": "{"})
    assert er.verify(repo, "main")[1] == [
        "hook-merge: record: eval/records/hook-merge.json does not parse"
    ]


def test_verify_reports_a_record_that_is_not_an_object(tmp_path):
    repo, _ = make_repo(tmp_path)
    commit(repo, {"config/hooks/merge_confirm.py": "print(2)\n"})
    commit(repo, {"eval/records/hook-merge.json": "[]"})
    assert er.verify(repo, "main")[1] == [
        "hook-merge: record: eval/records/hook-merge.json does not parse"
    ]


def test_an_input_edited_after_the_record_makes_it_stale(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/CLAUDE.global.md": "# New\n"})
    write_records(repo, base, ["scenario", "rubric"])
    commit(repo, {"config/CLAUDE.global.md": "# Newer\n"})
    assert er.verify(repo, "main")[1] == [
        "rubric: inputs: config/CLAUDE.global.md changed since the record",
        "scenario: inputs: config/CLAUDE.global.md changed since the record",
    ]


def test_an_input_added_or_removed_after_the_record_makes_it_stale(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/skill-overlays/engineering-skills:senior-qa.md": "x\n"})
    write_records(repo, base, ["scenario"])
    commit(
        repo,
        {
            "config/skill-overlays/new:one.md": "y\n",
            "config/skill-overlays/engineering-skills:senior-qa.md": None,
        },
    )
    assert er.verify(repo, "main")[1] == [
        "scenario: inputs: config/skill-overlays/engineering-skills:senior-qa.md changed since"
        " the record",
        "scenario: inputs: config/skill-overlays/new:one.md changed since the record",
    ]


def test_a_changed_harness_makes_the_record_stale(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/hooks/merge_confirm.py": "print(2)\n"})
    write_records(repo, base, ["hook-merge"])
    commit(repo, {"eval/hook_harness.py": "print('new')\n"})
    failures = er.verify(repo, "main")[1]
    assert "hook-merge: harness: eval/hook_harness.py changed since the record" in failures


def test_a_record_against_an_old_base_is_stale(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/CLAUDE.global.md": "# New\n"})
    write_records(repo, base, ["scenario", "rubric"])
    git(repo, "checkout", "-q", "main")
    commit(repo, {"config/skill-overlays/engineering-skills:senior-qa.md": "moved on\n"})
    git(repo, "checkout", "-q", "work")
    git(repo, "merge", "-q", "--no-edit", "main")
    failures = er.verify(repo, "main")[1]
    assert failures == [
        "scenario: base: config/skill-overlays/engineering-skills:senior-qa.md differs from the"
        " merge-base",
        "scenario: inputs: config/skill-overlays/engineering-skills:senior-qa.md changed since"
        " the record",
    ]


def test_a_scenario_record_without_base_hashes_fails_the_base_check(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/skill-overlays/engineering-skills:senior-qa.md": "x\n"})
    head = commit(repo, {})
    gates = er.gates_at(repo, head)
    record = er.build_record(repo, "scenario", gates["scenario"], head, base, RESULT_PASS, META)
    del record["base"]
    commit(repo, {"eval/records/scenario.json": er.dump_record(record)})
    assert er.verify(repo, "main")[1][0].startswith("scenario: base: config/")


def test_a_failing_result_fails_verification(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/hooks/merge_confirm.py": "print(2)\n"})
    write_records(repo, base, ["hook-merge"], result={"pass": False, "cases": []})
    assert er.verify(repo, "main")[1] == ["hook-merge: result: the recorded run did not pass"]


def test_records_for_suites_that_are_not_required_are_ignored(tmp_path):
    repo, base = make_repo(tmp_path)
    write_records(repo, base, ["hook-merge"], result={"pass": False})
    commit(repo, {"docs/readme.md": "x\n"})
    assert er.verify(repo, "main") == ([], [], {})


def test_a_record_with_hashes_of_the_wrong_shape_counts_as_stale(tmp_path):
    repo, base = make_repo(tmp_path)
    commit(repo, {"config/hooks/merge_confirm.py": "print(2)\n"})
    head = commit(repo, {})
    gates = er.gates_at(repo, head)
    record = er.build_record(repo, "hook-merge", gates["hook-merge"], head, base, RESULT_PASS, META)
    record["inputs"] = ["x"]
    record["harness"] = None
    commit(repo, {"eval/records/hook-merge.json": er.dump_record(record)})
    failures = er.verify(repo, "main")[1]
    assert "hook-merge: harness: eval/hook_harness.py changed since the record" in failures
    assert "hook-merge: inputs: config/hooks/merge_confirm.py changed since the record" in failures


# Refs and git errors


def test_an_unknown_base_ref_exits_2(tmp_path, capsys, monkeypatch):
    repo, _ = make_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert er.main(["verify", "--base", "no-such-ref"]) == 2
    assert "no-such-ref" in capsys.readouterr().err


def test_a_ref_that_looks_like_an_option_is_not_passed_to_git_as_one(tmp_path):
    repo, _ = make_repo(tmp_path)
    with pytest.raises(er.GitError):
        er.resolve_commit(repo, "--output=/tmp/x")


def test_no_merge_base_exits_2(tmp_path, capsys, monkeypatch):
    repo, _ = make_repo(tmp_path)
    git(repo, "checkout", "-q", "--orphan", "lonely")
    commit(repo, {"x.txt": "x\n"})
    monkeypatch.chdir(repo)
    assert er.main(["verify", "--base", "main"]) == 2
    assert "merge-base" in capsys.readouterr().err


def test_verify_without_a_subcommand_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        er.main([])
    assert exit_info.value.code == 2


def test_a_pointer_to_an_absent_key_selects_nothing(tmp_path):
    repo, _ = make_repo(tmp_path)
    head = commit(repo, {"config/settings.fragment.json": "{}"})
    sel = {"file": "config/settings.fragment.json", "pointer": "/hooks/PreToolUse"}
    assert er.selector_value(repo, head, sel) is None


# The plain-prose suites, as eval/gates.json defines them

PROSE = "config/skills/plain-prose"
PROSE_SKILL_MD = "---\nname: plain-prose\ndescription: Makes prose clear.\n---\n# Body\n"


def prose_repo(tmp_path):
    repo, _ = make_repo(tmp_path)
    base = commit(repo, {
        "eval/gates.json": (REPO_ROOT / "eval" / "gates.json").read_text(),
        f"{PROSE}/SKILL.md": PROSE_SKILL_MD,
        f"{PROSE}/scripts/prose_check.py": "print(1)\n",
        f"{PROSE}/evals/trigger-evals.json": "[]\n",
        f"{PROSE}/evals/behaviour-evals.json": "[]\n",
    })  # fmt: skip
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    return repo, base


@pytest.mark.parametrize(
    "change, suites",
    [
        # writing-docs competes with plain-prose in every trigger run, so its suite runs too.
        ({f"{PROSE}/SKILL.md": PROSE_SKILL_MD.replace("clear", "plain")},
         ["plain-prose-trigger", "writing-docs-trigger"]),
        ({f"{PROSE}/evals/trigger-evals.json": "[1]\n"}, ["plain-prose-trigger"]),
        # Both trigger suites count every repo skill through its description only (#91), so
        # a body edit leaves them alone, plain-prose's own included (D-4).
        ({f"{PROSE}/SKILL.md": PROSE_SKILL_MD.replace("# Body", "# New")},
         ["plain-prose-behaviour"]),
        ({"config/skills/other-skill/SKILL.md": "---\nname: other-skill\n---\n"},
         ["plain-prose-trigger", "writing-docs-trigger"]),
        # The vendored skills count in every trigger run too (#75), by description.
        ({"config/vendored-skills/copied/SKILL.md": "---\nname: copied\n---\n"},
         ["plain-prose-trigger", "writing-docs-trigger"]),
        ({f"{PROSE}/scripts/prose_check.py": "print(2)\n"}, ["plain-prose-behaviour"]),
    ],
)  # fmt: skip
def test_each_plain_prose_change_requires_its_suite(tmp_path, change, suites):
    repo, base = prose_repo(tmp_path)
    commit(repo, change)
    assert required(repo, base) == suites


@pytest.mark.parametrize("missing", ["skill", "cases"])
def test_a_skill_suite_without_its_skill_or_cases_is_refused(missing):
    gates = json.loads((REPO_ROOT / "eval" / "gates.json").read_text())
    del gates["suites"]["plain-prose-trigger"][missing]
    with pytest.raises(er.GatesError, match="needs a skill and a cases file"):
        er.parse_gates(json.dumps(gates))


def test_the_trigger_suites_count_skills_by_description_and_no_selector_repeats():
    suites = er.parse_gates((REPO_ROOT / "eval" / "gates.json").read_text())
    globs = [
        {"file": "config/skills/*/SKILL.md", "part": "description"},
        {"file": "config/vendored-skills/*/SKILL.md", "part": "description"},
    ]
    for name in ("plain-prose-trigger", "writing-docs-trigger"):
        assert not [p for p in suites[name].paths if p.endswith("SKILL.md")]
        assert [s for s in suites[name].selectors if s.get("part")] == globs
    for name, suite in suites.items():
        keys = [er.selector_key(s) for s in suite.selectors]
        assert len(keys) == len(set(keys)), name


@pytest.fixture
def criteria_repo(tmp_path):
    repo, _ = make_repo(tmp_path)
    base = commit(
        repo,
        {
            "eval/gates.json": (REPO_ROOT / "eval" / "gates.json").read_text(),
            "eval/criteria.json": (REPO_ROOT / "eval" / "criteria.json").read_text(),
        },
    )
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    return repo, base


def with_bar(key, sub, value):
    bars = json.loads((REPO_ROOT / "eval" / "criteria.json").read_text())
    bars[key][sub] = value
    return {"eval/criteria.json": json.dumps(bars)}


@pytest.mark.parametrize(
    ("key", "sub", "suites"),
    [
        ("rubric", "pass", ["rubric"]),
        ("scenario", "tries", ["scenario"]),
        ("hooks", "p95LimitMs", ["hook-merge", "hook-overlay", "hook-session-start"]),
        ("trigger", "recall", ["plain-prose-trigger", "writing-docs-trigger"]),
        ("trigger", "maxTurns", ["plain-prose-trigger"]),
        ("coldReader", "tries", ["plain-prose-behaviour"]),
        ("docTrigger", "needed", ["writing-docs-trigger"]),
        ("docReader", "rightShare", ["writing-docs-behaviour"]),
    ],
)
def test_a_criteria_edit_requires_only_the_suites_that_read_the_key(
    criteria_repo, key, sub, suites
):
    repo, base = criteria_repo
    commit(repo, with_bar(key, sub, 7))
    assert required(repo, base) == suites


def test_the_grader_model_requires_the_suites_a_grader_decides():
    suites = er.parse_gates((REPO_ROOT / "eval" / "gates.json").read_text())
    graded = {
        name
        for name, suite in suites.items()
        if any(s.get("pointer") == "/graderModel" for s in suite.selectors)
    }
    assert graded == {"scenario", "plain-prose-behaviour", "writing-docs-behaviour"}


def test_the_skill_suites_parse_with_their_skill_and_cases():
    suites = er.parse_gates((REPO_ROOT / "eval" / "gates.json").read_text())
    assert suites["plain-prose-behaviour"].skill == "plain-prose"
    assert suites["plain-prose-behaviour"].cases == f"{PROSE}/evals/behaviour-evals.json"


# The writing-docs suites, which run the skill beside plain-prose

DOCS = "config/skills/writing-docs"
DOCS_SKILL_MD = "---\nname: writing-docs\ndescription: Guides writing.\n---\n# Body\n"


def docs_repo(tmp_path):
    repo, base = prose_repo(tmp_path)
    base = commit(repo, {
        f"{DOCS}/SKILL.md": DOCS_SKILL_MD,
        f"{DOCS}/scripts/doc_lint.py": "print(1)\n",
        f"{DOCS}/references/templates/readme.md": "# TODO: name\n",
        f"{DOCS}/evals/trigger-evals.json": "[]\n",
        f"{DOCS}/evals/behaviour-evals.json": "[]\n",
    })  # fmt: skip
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    return repo, base


@pytest.mark.parametrize(
    "change, suites",
    [
        ({f"{DOCS}/SKILL.md": DOCS_SKILL_MD.replace("Guides", "Helps")},
         ["plain-prose-trigger", "writing-docs-trigger"]),
        ({f"{DOCS}/evals/trigger-evals.json": "[1]\n"}, ["writing-docs-trigger"]),
        ({f"{DOCS}/SKILL.md": DOCS_SKILL_MD.replace("# Body", "# New")},
         ["writing-docs-behaviour"]),
        ({f"{DOCS}/scripts/doc_lint.py": "print(2)\n"}, ["writing-docs-behaviour"]),
        ({f"{DOCS}/references/templates/readme.md": "# TODO: x\n"}, ["writing-docs-behaviour"]),
        ({"eval/doc_evals.py": "print(2)\n"}, ["writing-docs-behaviour", "writing-docs-trigger"]),
    ],
)  # fmt: skip
def test_each_writing_docs_change_requires_its_suite(tmp_path, change, suites):
    repo, base = docs_repo(tmp_path)
    commit(repo, change)
    assert required(repo, base) == suites


def test_the_writing_docs_suites_parse_with_their_companions():
    suites = er.parse_gates((REPO_ROOT / "eval" / "gates.json").read_text())
    for name in ("writing-docs-trigger", "writing-docs-behaviour"):
        assert suites[name].skill == "writing-docs"
        assert suites[name].companions == ("plain-prose",)
    assert suites["plain-prose-trigger"].companions == ()


@pytest.mark.parametrize("companions", [None, []])
def test_a_companion_suite_without_companions_is_refused(companions):
    gates = json.loads((REPO_ROOT / "eval" / "gates.json").read_text())
    entry = gates["suites"]["writing-docs-behaviour"]
    if companions is None:
        del entry["companions"]
    else:
        entry["companions"] = companions
    with pytest.raises(er.GatesError, match="needs a non-empty companions list"):
        er.parse_gates(json.dumps(gates))


@pytest.mark.parametrize("missing", ["skill", "cases"])
def test_a_companion_suite_without_its_skill_or_cases_is_refused(missing):
    gates = json.loads((REPO_ROOT / "eval" / "gates.json").read_text())
    del gates["suites"]["writing-docs-trigger"][missing]
    with pytest.raises(er.GatesError, match="needs a skill and a cases file"):
        er.parse_gates(json.dumps(gates))


def test_companions_must_be_a_list_of_strings():
    gates = json.loads((REPO_ROOT / "eval" / "gates.json").read_text())
    gates["suites"]["writing-docs-trigger"]["companions"] = "plain-prose"
    with pytest.raises(er.GatesError, match="companions must be a list of strings"):
        er.parse_gates(json.dumps(gates))


# The lifecycle suites, as eval/gates.json defines them

LIFECYCLE = "config/skills/github-issue-lifecycle"


@pytest.mark.parametrize(
    "change, suites",
    [
        # The trigger runner builds its config with the behaviour runner's code.
        ({f"{LIFECYCLE}/evals/run_behaviour_evals.py": "print(2)\n"},
         ["lifecycle-behaviour", "lifecycle-trigger"]),
        ({f"{LIFECYCLE}/scripts/issue_lifecycle.py": "print(2)\n"}, ["lifecycle-behaviour"]),
        ({f"{LIFECYCLE}/evals/fake_gh.py": "print(2)\n"}, ["lifecycle-behaviour"]),
        ({f"{LIFECYCLE}/references/gh-commands.md": "# New\n"}, ["lifecycle-behaviour"]),
        ({f"{LIFECYCLE}/assets/pr-body-template.md": "Closes #N\n"}, ["lifecycle-behaviour"]),
        ({f"{LIFECYCLE}/tests/test_x.py": "def test_x(): pass\n"}, []),
    ],
)  # fmt: skip
def test_each_lifecycle_change_requires_the_suites_it_affects(tmp_path, change, suites):
    repo, _ = make_repo(tmp_path)
    base = commit(repo, {
        "eval/gates.json": (REPO_ROOT / "eval" / "gates.json").read_text(),
        f"{LIFECYCLE}/evals/run_behaviour_evals.py": "print(1)\n",
        f"{LIFECYCLE}/scripts/issue_lifecycle.py": "print(1)\n",
    })  # fmt: skip
    commit(repo, change)
    assert required(repo, base) == suites


# The hook-session-start suite, as eval/gates.json defines it (#15)

LIFECYCLE_HOOK = "config/hooks/lifecycle_default.py"


def session_repo(tmp_path):
    repo, _ = make_repo(tmp_path)
    fragment = json.loads((REPO_ROOT / "config" / "settings.fragment.json").read_text())
    base = commit(repo, {
        "eval/gates.json": (REPO_ROOT / "eval" / "gates.json").read_text(),
        "config/settings.fragment.json": json.dumps(fragment, indent=2),
        LIFECYCLE_HOOK: "print(1)\n",
        "eval/session_harness.py": "print(1)\n",
        "eval/hook_harness.py": "print(1)\n",
    })  # fmt: skip
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    return repo, base, fragment


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        # The scenarios embed the hook's line, so they run again when it changes.
        ({LIFECYCLE_HOOK: "print(2)\n"}, ["hook-session-start", "scenario"]),
        ({"eval/session_harness.py": "print(2)\n"}, ["hook-session-start"]),
        ({"eval/hook_harness.py": "print(2)\n"},
         ["hook-merge", "hook-overlay", "hook-session-start"]),
    ],
)  # fmt: skip
def test_each_lifecycle_hook_change_requires_the_session_suite(tmp_path, files, expected):
    repo, base, _ = session_repo(tmp_path)
    commit(repo, files)
    assert required(repo, base) == expected


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart"])
def test_a_change_to_either_start_hook_entry_requires_only_the_session_suite(tmp_path, event):
    repo, base, fragment = session_repo(tmp_path)
    fragment["hooks"][event] = []
    commit(repo, {"config/settings.fragment.json": json.dumps(fragment, indent=2)})
    assert required(repo, base) == ["hook-session-start"]


def test_the_session_suite_parses_with_its_runner():
    suites = er.parse_gates((REPO_ROOT / "eval" / "gates.json").read_text())
    assert suites["hook-session-start"].runner == "hook-session-start"
    assert LIFECYCLE_HOOK in suites["scenario"].paths
