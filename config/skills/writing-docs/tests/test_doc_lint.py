"""doc_lint.py reports structure and link problems in a document, plus plain-prose's report."""

import ast
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "doc_lint.py"
PLAIN_PROSE = SKILL.parent / "plain-prose"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPT.parent))
import doc_lint  # noqa: E402

PURPOSE = "This page explains how the nightly backup tool copies the database."

# Fixture name -> (label the finding's text starts with, line it's reported on).
STRUCTURE = {
    "no-title": ("no title", 1),
    "multiple-titles": ("more than one title", 5),
    "no-purpose": ("no purpose", 1),
    "skipped-level": ("skipped heading level", 7),
    "unlabelled-fence": ("code fence without a language", 5),
    "leftover-marker": ("leftover marker", 5),
}


def doc(*parts: str, title: str = "Backup tool", purpose: str = PURPOSE) -> str:
    """A document with a title and purpose paragraph, then `parts` as blocks."""
    blocks = [f"# {title}"] if title else []
    if purpose:
        blocks.append(purpose)
    return "\n\n".join([*blocks, *parts]) + "\n"


def found(report, category="structure"):
    return [(f["text"].split(":")[0], f["line"]) for f in report["findings"][category]]


def labels(report, category="structure"):
    return {label for label, _ in found(report, category)}


# Structure


@pytest.mark.parametrize("name", sorted(STRUCTURE))
def test_each_structure_finding_is_reported_on_its_line(name):
    label, line = STRUCTURE[name]
    report = doc_lint.lint((FIXTURES / f"{name}.md").read_text(), FIXTURES)
    assert (label, line) in found(report)


@pytest.mark.parametrize("name", sorted(STRUCTURE))
def test_the_clean_twin_has_no_finding_of_that_kind(name):
    label, _ = STRUCTURE[name]
    report = doc_lint.lint((FIXTURES / f"{name}.clean.md").read_text(), FIXTURES)
    assert label not in labels(report)


def test_a_document_with_no_heading_has_no_title():
    report = doc_lint.lint("Just a paragraph of text with no heading at all here.\n", None)
    assert ("no title", 0) in found(report)


def test_a_setext_title_is_reported_as_unread():
    report = doc_lint.lint("Backup tool\n===========\n\n" + PURPOSE + "\n", None)
    (finding,) = report["findings"]["structure"]
    assert finding["text"].startswith("no title")
    assert "setext" in finding["text"]


def test_every_extra_title_is_reported():
    report = doc_lint.lint(doc("# Second", "# Third"), None)
    assert [x for x in found(report) if x[0] == "more than one title"] == [
        ("more than one title", 5),
        ("more than one title", 7),
    ]


def test_an_adr_status_line_is_not_the_purpose():
    text = "# ADR-0001: Use links\n\n**Status:** Accepted on 2026-10-16 by the owner of the repo.\n"
    assert "no purpose" in labels(doc_lint.lint(text, None))


def test_the_purpose_may_follow_the_metadata_lines():
    text = "# ADR-0001: Use links\n\n**Status:** Accepted.\n\n" + PURPOSE + "\n\n## Context\n"
    assert "no purpose" not in labels(doc_lint.lint(text, None))


@pytest.mark.parametrize(
    "block",
    [
        "Short line here.",  # under eight words
        "| a | table | row | with | many | words | in | it |",
        "> a quoted line that has more than eight words in it",
        "```text\nthis code block has more than eight words in it\n```",
        "<p>an html block that has more than eight words in it</p>",
    ],
)
def test_short_lines_tables_quotes_code_and_html_are_not_a_purpose(block):
    assert "no purpose" in labels(doc_lint.lint(doc(block, purpose=""), None))


def test_a_purpose_split_over_two_lines_counts():
    text = "# Backup tool\n\nThis page explains how the nightly\nbackup tool copies the database.\n"
    assert "no purpose" not in labels(doc_lint.lint(text, None))


def test_the_purpose_must_come_before_the_first_section():
    text = doc("## Restore", PURPOSE, purpose="")
    assert "no purpose" in labels(doc_lint.lint(text, None))


def test_the_purpose_search_stops_after_fifteen_lines():
    filler = "\n".join(f"- item {i}" for i in range(15))
    text = doc(filler, PURPOSE, purpose="")
    assert "no purpose" in labels(doc_lint.lint(text, None))


def test_a_level_one_jump_is_not_a_skip():
    text = doc("## A", "### B", "## C", "### D", "#### E")
    assert "skipped heading level" not in labels(doc_lint.lint(text, None))


def test_a_skip_under_a_missing_title_is_still_reported():
    report = doc_lint.lint("## A\n\n#### B\n", None)
    assert ("skipped heading level", 3) in found(report)


def test_a_closing_hash_sequence_is_not_part_of_the_heading():
    report = doc_lint.lint(doc("## Restore ##", "See [it](#restore)."), None)
    assert report["findings"]["links"] == []


@pytest.mark.parametrize("marker", ["TODO", "TBD", "FIXME", "XXX", "Lorem ipsum"])
def test_each_leftover_marker_is_reported(marker):
    report = doc_lint.lint(doc(f"The value is {marker} for now."), None)
    assert ("leftover marker", 5) in found(report)


@pytest.mark.parametrize("text", ["The todolist app and mastodon work.", "Keep a todo list."])
def test_lowercase_todo_is_not_a_marker(text):
    report = doc_lint.lint(doc(text), None)
    assert "leftover marker" not in labels(report)


@pytest.mark.parametrize(
    "block",
    [
        "```bash\n# TODO not a heading\n[x](missing.md)\n```",
        "~~~\nTODO [x](missing.md)\n~~~",
        "Use `TODO` and `[x](missing.md)` as examples.",
        "Use ``a `TODO` b`` here.",
    ],
)
def test_code_blocks_and_inline_code_are_skipped(block):
    report = doc_lint.lint(doc(block), FIXTURES)
    assert "leftover marker" not in labels(report)
    assert report["findings"]["links"] == []
    assert report["counts"]["links"] == 0


def test_a_fence_closes_only_on_its_own_kind_and_length():
    text = doc("````markdown\n```\n# Not a heading\n```\n````", "## Real")
    report = doc_lint.lint(text, None)
    assert "more than one title" not in labels(report)
    assert "code fence without a language" not in labels(report)


def test_front_matter_is_skipped_and_lines_still_match():
    text = "---\ntitle: TODO\n---\n# Backup tool\n\n" + PURPOSE + "\n\nIt is TBD.\n"
    report = doc_lint.lint(text, None)
    assert found(report) == [("leftover marker", 8)]


def test_an_unclosed_fence_swallows_the_rest():
    text = doc("```python\nprint(1)", "# Not a title", "TODO", "[x](missing.md)")
    report = doc_lint.lint(text, FIXTURES)
    assert found(report) == []
    assert report["findings"]["links"] == []


# Links


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "other.md").write_text("# Other\n\n## Setup steps\n\n```\n## Hidden\n```\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "sub dir").mkdir()
    (tmp_path / "a(b).md").write_text("# Paren\n")
    return tmp_path


LINKED = doc(
    "## Setup",
    "## Setup",
    "## What's new? (v2)",
    "Read [other](other.md) and [missing](missing.md).",
    "![logo](image.png) and ![gone](gone.png).",
    "Jump to [setup](#setup), [again](#setup-1), [new](#whats-new-v2) and [nowhere](#nowhere).",
    "See [steps](other.md#setup-steps) and [hidden](other.md#hidden).",
    "Also [web](https://example.com/x.md), [mail](mailto:a@example.com) and [dir](sub%20dir).",
    "And [paren](a(b).md) and [up](../nowhere-else/x.md).",
)


def test_only_broken_links_are_reported(tree):
    report = doc_lint.lint(LINKED, tree)
    assert found(report, "links") == [
        ("missing file", 11),
        ("missing file", 13),
        ("missing anchor", 15),
        ("missing anchor", 17),
        ("missing file", 21),
    ]
    assert report["linksNotChecked"] == 0


def test_from_stdin_relative_files_are_counted_not_checked(tree):
    report = doc_lint.lint(LINKED, None)
    assert found(report, "links") == [("missing anchor", 15)]
    assert report["linksNotChecked"] == 9


def test_a_link_title_is_not_part_of_the_target(tree):
    report = doc_lint.lint(doc('See [other](other.md "The other page").'), tree)
    assert report["findings"]["links"] == []


def test_an_anchor_into_a_non_markdown_file_is_not_checked(tree):
    report = doc_lint.lint(doc("See [logo](image.png#top)."), tree)
    assert report["findings"]["links"] == []


def test_a_root_relative_link_is_not_checked(tree):
    report = doc_lint.lint(doc("See [root](/docs/missing.md)."), tree)
    assert report["findings"]["links"] == []


def test_an_empty_link_target_is_ignored(tree):
    report = doc_lint.lint(doc("See [nothing]() here."), tree)
    assert report["findings"]["links"] == []


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        ("Install `cfg` now", "install-cfg-now"),
        ("Use [links](x.md) here", "use-links-here"),
        ("snake_case name", "snake_case-name"),
        ("Ünïcode Title", "ünïcode-title"),
        ("A -- B", "a----b"),
    ],
)
def test_anchors_follow_githubs_rules(heading, anchor):
    assert doc_lint.slug(heading) == anchor


# The prose report


def test_the_prose_report_comes_from_plain_prose():
    text = doc("As discussed, the QZX flag stays.")
    report = doc_lint.lint(text, None)
    kinds = {f["kind"] for f in report["prose"]["findings"]["cold_reader"]}
    assert {"session-reference", "undefined-acronym"} <= kinds
    assert all("acronym" not in f["text"] for f in report["findings"]["structure"])


def test_a_missing_sibling_puts_an_error_in_the_prose_field(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(doc_lint, "PROSE_CHECK", tmp_path / "absent" / "prose_check.py")
    monkeypatch.setattr(doc_lint, "_prose", None)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(doc().encode())))
    assert doc_lint.main(["-"]) == 0
    assert "plain-prose" in capsys.readouterr().out
    report = doc_lint.lint(doc(), None)
    assert "error" in report["prose"]
    assert report["counts"]["structure"] == 0


# Reports and exit codes


def test_the_json_report_has_its_fields(tree, capsys):
    (tree / "doc.md").write_text(LINKED)
    assert doc_lint.main([str(tree / "doc.md"), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["path"] == str(tree / "doc.md")
    assert report["words"] > 0
    assert set(report["findings"]) == {"structure", "links"}
    assert report["counts"] == {"structure": 0, "links": 5}
    assert report["linksNotChecked"] == 0
    assert "findings" in report["prose"]


def test_the_text_report_lists_findings_and_the_prose_report(monkeypatch, capsys):
    text = doc("```", "x", "```", "[a](#nowhere)", "As discussed it works.")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode())))
    assert doc_lint.main(["-"]) == 0
    out = capsys.readouterr().out
    assert "doc_lint: -" in out
    assert "code fence without a language" in out
    assert "missing anchor" in out
    assert "relative links not checked" not in out
    assert "session-reference" in out


def test_the_text_report_says_when_links_went_unchecked(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(doc("[a](b.md)").encode())))
    assert doc_lint.main(["-"]) == 0
    assert "relative links not checked, because the input came from stdin: 1" in (
        capsys.readouterr().out
    )


def test_a_missing_file_exits_2_with_a_message(tmp_path, capsys):
    assert doc_lint.main([str(tmp_path / "nope.md")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_no_arguments_is_a_usage_error():
    with pytest.raises(SystemExit) as e:
        doc_lint.main([])
    assert e.value.code == 2


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"---\ntitle: x\n---\n",
        b"# Title\r\n\r\nA line with CRLF endings that has enough words in it.\r\n",
        b"```python\nonly code\n```\n",
        b"```\nunclosed fence",
        b"# T\n\n\xff\xfe invalid bytes in the middle of a line of words here\n",
        b"# T\n\n[odd](a%20b%2F(c).md) and [x](%zz) here\n",
    ],
)
def test_odd_input_never_crashes(raw, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
    assert doc_lint.main(["-", "--json"]) == 0
    json.loads(capsys.readouterr().out)


def test_empty_input_has_no_words_and_only_no_title():
    report = doc_lint.lint("", None)
    assert report["words"] == 0
    assert found(report) == [("no title", 0)]
    assert report["prose"]["counts"] == {"cold_reader": 0, "readability": 0, "ai_tells": 0}


# Safety


def test_only_the_standard_library_and_prose_check_are_imported():
    tree = ast.parse(SCRIPT.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names)
    assert not names & {"socket", "urllib", "http", "subprocess"}


def test_a_run_leaves_no_bytecode_in_either_skill(tmp_path):
    skills = tmp_path / "skills"
    for name in ("writing-docs", "plain-prose"):
        shutil.copytree(SKILL.parent / name / "scripts", skills / name / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))  # fmt: skip
    (tmp_path / "doc.md").write_text(doc("As discussed, it works."))
    out = subprocess.run(
        [sys.executable, "-I", str(skills / "writing-docs/scripts/doc_lint.py"),
         str(tmp_path / "doc.md"), "--json"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert "findings" in json.loads(out.stdout)["prose"]
    assert not list(skills.rglob("__pycache__"))


def test_a_copy_without_plain_prose_still_reports(tmp_path):
    scripts = tmp_path / "skills" / "writing-docs" / "scripts"
    shutil.copytree(SCRIPT.parent, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    out = subprocess.run(
        [sys.executable, "-I", str(scripts / "doc_lint.py"), "-"],
        input=doc("```", "x", "```"), capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert out.returncode == 0
    assert "code fence without a language" in out.stdout
    assert "plain-prose" in out.stdout


def test_every_markdown_file_in_docs_gets_a_report():
    """Not a gate on the docs' content: it only shows the lint runs on real documents."""
    docs = sorted((SKILL.parents[2] / "docs").rglob("*.md"))
    assert docs
    for path in docs:
        report = doc_lint.lint(path.read_text(errors="replace"), path.parent)
        assert "words" in report
