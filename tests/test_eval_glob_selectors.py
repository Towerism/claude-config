"""A selector's file may be a glob, so a suite can count every skill through its description (#91).

Each test builds a small real git repo and asks the gate what a change requires, with no
model in the loop. The suite under test, `descs`, lists no SKILL.md in `paths`; it reads
only the `description` of every skill in both skill trees.
"""

import json

import pytest

from tests.eval_helpers import GATES, LIFECYCLE, commit, git, make_repo
from tests.test_eval_records import META, RESULT_PASS, required, sha, write_records
from tools.cfg import eval_records as er

SK = "config/skills"
VEN = "config/vendored-skills"
GLOBS = [
    {"file": f"{SK}/*/SKILL.md", "part": "description"},
    {"file": f"{VEN}/*/SKILL.md", "part": "description"},
]


def skill(name, description="Does the thing.", body="# Body\n"):
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def gates_with(selectors, equivalent=True):
    suite = {
        "runner": "rubric",
        "paths": ["eval/descs-cases.json"],
        "selectors": selectors,
        "harness": ["eval/run_eval.py"],
    }
    gates = {"suites": {**GATES["suites"], "descs": suite}}
    if equivalent:
        gates["equivalentPaths"] = [[f"{SK}/", f"{VEN}/"]]
    return json.dumps(gates, indent=2)


def glob_repo(tmp_path, selectors=None, **kwargs):
    repo, _ = make_repo(tmp_path)
    base = commit(
        repo,
        {
            "eval/gates.json": gates_with(GLOBS if selectors is None else selectors, **kwargs),
            "eval/descs-cases.json": "[]\n",
            f"{SK}/a/SKILL.md": skill("a", "Does a."),
            f"{SK}/b/SKILL.md": skill("b", "Does b."),
            f"{VEN}/c/SKILL.md": skill("c", "Does c."),
        },
        "base",
    )
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    return repo, base


def head_of(repo):
    return git(repo, "rev-parse", "HEAD")


def descs_inputs(repo):
    head = head_of(repo)
    return er.suite_inputs(repo, head, er.gates_at(repo, head)["descs"])


# Parsing


def test_a_selector_file_may_hold_wildcards():
    suites = er.parse_gates(gates_with(GLOBS))
    assert suites["descs"].selectors == tuple(GLOBS)


@pytest.mark.parametrize(
    "selector", [{"file": f"{SK}/*/SKILL.md"}, {"file": f"{SK}/*/SKILL.md", "part": "title"}]
)
def test_a_glob_selector_still_needs_a_pointer_or_a_part(selector):
    with pytest.raises(er.GatesError, match="pointer or a part"):
        er.parse_gates(gates_with([selector]))


# What a change requires


def test_a_body_edit_to_any_skill_requires_nothing(tmp_path):
    repo, base = glob_repo(tmp_path)
    commit(repo, {f"{SK}/a/SKILL.md": skill("a", "Does a.", "# New body\n")})
    commit(repo, {f"{VEN}/c/SKILL.md": skill("c", "Does c.", "# Other body\n")})
    assert required(repo, base) == []


def test_a_description_edit_requires_the_suite_and_the_stale_line_names_the_file(tmp_path):
    repo, base = glob_repo(tmp_path)
    write_records(repo, base, ["descs"])
    commit(repo, {f"{SK}/b/SKILL.md": skill("b", "Does something else.")})
    assert required(repo, base) == ["descs"]
    head = head_of(repo)
    suite = er.gates_at(repo, head)["descs"]
    failures = er.check_record(repo, "descs", suite, head, base, why_full=None)
    assert (
        "descs: inputs: config/skills/b/SKILL.md#description changed since the record" in failures
    )


def test_moving_the_selectors_from_literal_files_to_a_glob_requires_nothing(tmp_path):
    literal = [{"file": f"{SK}/a/SKILL.md", "part": "description"}]
    repo, base = glob_repo(tmp_path, selectors=literal)
    commit(
        repo,
        {
            "eval/gates.json": gates_with(
                [{"file": f"{SK}/a/SKILL.md", "part": "description"}, *GLOBS[:1]]
            )
        },
    )
    assert required(repo, base) == []


def test_a_skill_added_or_removed_requires_the_suite_and_names_it(tmp_path):
    repo, base = glob_repo(tmp_path)
    commit(repo, {f"{SK}/new/SKILL.md": skill("new")})
    assert required(repo, base) == ["descs"]
    assert er.smoke_enough(repo, "descs", base, head_of(repo)) == (
        False,
        "config/skills/new/SKILL.md#description was added",
    )
    (tmp_path / "second").mkdir()
    repo, base = glob_repo(tmp_path / "second")
    commit(repo, {f"{SK}/a/SKILL.md": None})
    assert required(repo, base) == ["descs"]
    reason = er.smoke_enough(repo, "descs", base, head_of(repo))[1]
    assert reason == "config/skills/a/SKILL.md#description was removed or renamed"


def test_a_skill_that_moves_between_the_trees_unchanged_is_not_a_change(tmp_path):
    repo, base = glob_repo(tmp_path)
    commit(repo, {f"{SK}/a/SKILL.md": None, f"{VEN}/a/SKILL.md": skill("a", "Does a.")})
    assert required(repo, base) == []
    assert er.smoke_enough(repo, "descs", base, head_of(repo)) == (True, None)


def test_a_skill_that_moves_between_the_trees_and_changes_its_description_is_a_change(tmp_path):
    repo, base = glob_repo(tmp_path)
    commit(repo, {f"{SK}/a/SKILL.md": None, f"{VEN}/a/SKILL.md": skill("a", "Does more.")})
    assert required(repo, base) == ["descs"]
    reason = er.smoke_enough(repo, "descs", base, head_of(repo))[1]
    assert (
        reason
        == "config/skills/a/SKILL.md#description moved to config/vendored-skills/a/SKILL.md#description and changed"
    )


def test_a_record_from_before_a_move_between_the_trees_still_matches(tmp_path):
    repo, base = glob_repo(tmp_path)
    write_records(repo, base, ["descs"])
    commit(repo, {f"{SK}/a/SKILL.md": None, f"{VEN}/a/SKILL.md": skill("a", "Does a.")})
    head = head_of(repo)
    suite = er.gates_at(repo, head)["descs"]
    failures = er.check_record(repo, "descs", suite, head, base, why_full=None)
    assert [f for f in failures if f.startswith("descs: inputs")] == []


def test_two_skills_that_swap_descriptions_across_the_trees_are_a_change(tmp_path):
    repo, base = glob_repo(
        tmp_path,
    )
    commit(repo, {f"{VEN}/a/SKILL.md": skill("a", "Does something else.")})
    base = commit(repo, {}, "both trees hold a")
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    commit(
        repo,
        {
            f"{SK}/a/SKILL.md": skill("a", "Does something else."),
            f"{VEN}/a/SKILL.md": skill("a", "Does a."),
        },
    )
    assert required(repo, base) == ["descs"]


def test_a_skill_name_that_appears_in_both_trees_is_a_change(tmp_path):
    repo, base = glob_repo(tmp_path)
    commit(repo, {f"{VEN}/a/SKILL.md": skill("a", "Does a.")})
    assert required(repo, base) == ["descs"]


def test_a_glob_that_matches_nothing_is_harmless(tmp_path):
    repo, base = glob_repo(
        tmp_path, selectors=[{"file": "config/none/*/SKILL.md", "part": "description"}]
    )
    assert descs_inputs(repo) == {"eval/descs-cases.json": sha("[]\n")}
    commit(repo, {f"{SK}/a/SKILL.md": skill("a", "Changed.")})
    assert required(repo, base) == []


def test_a_description_in_a_new_skill_with_no_frontmatter_reads_as_empty(tmp_path):
    repo, base = glob_repo(tmp_path)
    commit(repo, {f"{SK}/raw/SKILL.md": "---\nname: raw\ndescription: x"})
    key = "config/skills/raw/SKILL.md#description"
    assert descs_inputs(repo)[key] == sha("")


def test_two_selectors_on_the_same_file_and_part_count_once(tmp_path):
    repo, _ = glob_repo(tmp_path, selectors=[*GLOBS, GLOBS[0]])
    assert sorted(descs_inputs(repo)) == [
        "config/skills/a/SKILL.md#description",
        "config/skills/b/SKILL.md#description",
        f"{LIFECYCLE}/SKILL.md#description",
        "config/vendored-skills/c/SKILL.md#description",
        "eval/descs-cases.json",
    ]


# What a record holds


def test_a_record_holds_one_entry_per_matched_description_and_no_whole_skill_file(tmp_path):
    repo, base = glob_repo(tmp_path)
    head = head_of(repo)
    suite = er.gates_at(repo, head)["descs"]
    record = er.build_record(repo, "descs", suite, head, base, RESULT_PASS, META)
    assert record["inputs"] == {
        "config/skills/a/SKILL.md#description": sha("description: Does a.\n"),
        "config/skills/b/SKILL.md#description": sha("description: Does b.\n"),
        f"{LIFECYCLE}/SKILL.md#description": sha(
            "description: Runs the issue lifecycle.\n  Second line.\n"
        ),
        "config/vendored-skills/c/SKILL.md#description": sha("description: Does c.\n"),
        "eval/descs-cases.json": sha("[]\n"),
    }


def test_a_record_with_a_whole_file_skill_entry_reads_as_stale(tmp_path):
    repo, base = glob_repo(tmp_path)
    head = head_of(repo)
    suite = er.gates_at(repo, head)["descs"]
    record = er.build_record(repo, "descs", suite, head, base, RESULT_PASS, META)
    record["inputs"]["config/skills/a/SKILL.md"] = sha("whole file")
    commit(repo, {"eval/records/descs.json": er.dump_record(record)}, "records")
    failures = er.check_record(repo, "descs", suite, head_of(repo), base, why_full=None)
    assert "descs: inputs: config/skills/a/SKILL.md changed since the record" in failures
