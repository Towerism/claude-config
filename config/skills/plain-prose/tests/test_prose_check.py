"""prose_check.py reports what makes prose hard for a cold reader, and never blocks."""

import ast
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "prose_check.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPT.parent))
import prose_check  # noqa: E402

# Finding kind -> (category, line it should be reported on; 0 means the whole text).
EXPECTED = {
    "session-reference": ("cold_reader", 1),
    "bare-id": ("cold_reader", 1),
    "undefined-acronym": ("cold_reader", 1),
    "long-sentence": ("readability", 1),
    "passive": ("readability", 1),
    "filler": ("readability", 1),
    "tell-word": ("ai_tells", 1),
    "tell-phrase": ("ai_tells", 1),
    "em-dash": ("ai_tells", 1),
    "tricolon": ("ai_tells", 1),
    "uniform-rhythm": ("ai_tells", 1),
    "repeated-opening": ("ai_tells", 1),
    "over-structured": ("ai_tells", 0),
    "bold-overuse": ("ai_tells", 0),
    "heading-overuse": ("ai_tells", 0),
}


def kinds(report, category):
    return {(f["kind"], f["line"]) for f in report["findings"][category]}


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_each_finding_kind_is_reported_where_it_occurs(kind):
    category, line = EXPECTED[kind]
    report = prose_check.check((FIXTURES / f"{kind}.md").read_text())
    assert (kind, line) in kinds(report, category)


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_the_clean_twin_has_no_finding_of_that_kind(kind):
    category, _ = EXPECTED[kind]
    report = prose_check.check((FIXTURES / f"{kind}.clean.md").read_text())
    assert kind not in {k for k, _ in kinds(report, category)}


def test_code_tables_front_matter_and_html_produce_no_findings():
    text = (
        "---\ntitle: leverage\n---\n"
        "```\nWe leverage it as discussed.\n```\n"
        "| leverage | FR-1 |\n|---|---|\n"
        "<div>leverage as discussed</div>\n"
        "Use `leverage` here.\n"
        "We leverage the cache.\n"
    )
    report = prose_check.check(text)
    found = [f for cat in report["findings"].values() for f in cat]
    assert [(f["kind"], f["line"]) for f in found] == [("tell-word", 11)]


def test_an_unclosed_fence_hides_everything_after_it():
    report = prose_check.check("Plain start.\n```\nWe leverage it as discussed.\n")
    assert all(not v for v in report["findings"].values())


def test_crlf_text_keeps_its_line_numbers():
    report = prose_check.check("First line.\r\n\r\nWe leverage it.\r\n")
    assert ("tell-word", 3) in kinds(report, "ai_tells")


def test_an_acronym_defined_on_the_same_line_is_not_reported():
    report = prose_check.check("The QPS (queries per second) limit applies. QPS stays at 10.\n")
    assert not report["findings"]["cold_reader"]


def test_an_acronym_is_reported_only_the_first_time():
    report = prose_check.check("The QPS limit applies.\n\nThe QPS limit is 10.\n")
    assert kinds(report, "cold_reader") == {("undefined-acronym", 1)}


def test_common_acronyms_plurals_and_hyphenated_words_are_not_reported():
    report = prose_check.check("The CLI calls two APIs. PRs merge. A UTF-8 file.\n")
    assert not report["findings"]["cold_reader"]


def test_the_json_report_has_every_field():
    report = prose_check.check((FIXTURES / "tell-word.md").read_text())
    assert set(report) == {
        "words", "findings", "counts", "per_1000_words", "structure",
        "readability_info", "detectability", "sentence_length_stdev",
    }  # fmt: skip
    assert set(report["findings"]) == {"cold_reader", "readability", "ai_tells"}
    assert report["counts"]["ai_tells"] == len(report["findings"]["ai_tells"])
    assert report["per_1000_words"]["ai_tells"] > 0
    assert set(report["structure"]) == {
        "list_line_share", "bold_per_100_words", "headings_per_100_words",
    }  # fmt: skip
    assert isinstance(report["readability_info"]["flesch_kincaid_grade"], float)
    assert "never a gate" in report["detectability"]["note"]
    assert isinstance(report["detectability"]["score"], (int, float))


def test_the_sentence_length_spread_is_reported_for_several_sentences():
    report = prose_check.check("One two three four. Five six seven eight nine ten eleven.\n")
    assert report["sentence_length_stdev"] == 1.5


@pytest.mark.parametrize(
    "text",
    ["", "word", "no punctuation at all here", "```\nonly code\n```\n", "\n\n\n"],
)
def test_odd_input_gives_a_report_and_no_error(text):
    report = prose_check.check(text)
    assert report["counts"].keys() == {"cold_reader", "readability", "ai_tells"}


def test_text_with_no_prose_has_null_readability_and_detectability():
    report = prose_check.check("```\nonly code\n```\n")
    assert report["words"] == 0
    assert report["readability_info"]["flesch_kincaid_grade"] is None
    assert report["detectability"]["score"] is None
    assert report["sentence_length_stdev"] is None


def test_a_file_with_findings_exits_zero_and_prints_the_report(tmp_path, capsys):
    f = tmp_path / "t.md"
    f.write_text((FIXTURES / "tell-word.md").read_text())
    assert prose_check.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "AI tells (1)" in out
    assert "tell-word: leverage" in out


def test_json_output_parses(tmp_path, capsys):
    f = tmp_path / "t.md"
    f.write_text("We leverage it.\n")
    assert prose_check.main([str(f), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["ai_tells"] == 1


def test_stdin_is_read_with_a_dash(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"We leverage it.\n")))
    assert prose_check.main(["-"]) == 0
    assert "tell-word" in capsys.readouterr().out


def test_invalid_utf8_is_replaced_not_fatal(tmp_path, capsys):
    f = tmp_path / "t.md"
    f.write_bytes(b"We leverage it \xff\xfe here.\n")
    assert prose_check.main([str(f)]) == 0
    assert "tell-word" in capsys.readouterr().out


def test_a_missing_file_exits_two_with_a_message(tmp_path, capsys):
    assert prose_check.main([str(tmp_path / "nope.md")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_no_arguments_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as e:
        prose_check.main([])
    assert e.value.code == 2


def test_long_finding_lists_are_cut_off_in_the_text_report(capsys):
    prose_check.print_report(prose_check.check("We leverage it.\n" * 45), as_json=False)
    out = capsys.readouterr().out
    assert "… 6 more" in out


def test_the_script_runs_isolated_from_its_own_path(tmp_path):
    f = tmp_path / "t.md"
    f.write_text("We leverage it.\n")
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), str(f)], capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, r.stderr
    assert "tell-word" in r.stdout


@pytest.mark.parametrize(
    "path",
    [
        SCRIPT,
        SKILL / "scripts" / "vendored" / "readability_scorer.py",
        SKILL / "scripts" / "vendored" / "ai_content_detector.py",
    ],
)
def test_scripts_import_only_the_standard_library_and_no_network_or_processes(path):
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    assert modules <= allowed, modules - allowed
    assert not modules & {"socket", "urllib", "http", "subprocess"}
