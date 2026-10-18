"""The skill package has what Claude needs when it loads, and stays small."""

import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import doc_lint  # noqa: E402

BODY_TOKEN_LIMIT = 1000  # once loaded, the body stays in context for the whole session
TEMPLATES = ["readme", "how-to", "tutorial", "reference", "explanation", "adr", "changelog"]
GUIDE = SKILL / "references" / "reader-first.md"


def frontmatter_and_body() -> tuple[dict[str, str], str]:
    text = (SKILL / "SKILL.md").read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, "SKILL.md has no frontmatter"
    fields = dict(line.split(": ", 1) for line in m.group(1).splitlines())
    return fields, m.group(2)


def test_the_package_files_exist():
    names = ["SKILL.md", "SPEC.md", "REVIEW-NOTE.md", "scripts/doc_lint.py",
             "references/reader-first.md", "evals/trigger-evals.json",
             "evals/behaviour-evals.json"]  # fmt: skip
    names += [f"references/templates/{t}.md" for t in TEMPLATES]
    for rel in names:
        assert (SKILL / rel).is_file(), rel


def test_the_frontmatter_names_the_skill_and_its_scope():
    fields, _ = frontmatter_and_body()
    assert set(fields) == {"name", "description"}
    assert fields["name"] == "writing-docs"
    covered, excluded = fields["description"].split("Not for", 1)
    for kind in ("README", "ADR", "runbook", "how-to", "changelog"):
        assert kind in covered, kind
    for text in ("commit messages", "PR text", "code comments", "error messages"):
        assert text in covered, text
    # The coding work that usually writes text people read later, in general terms.
    for work in ("tests", "CLI or UI"):
        assert work in covered, work
    assert "plain-prose" in covered
    assert "chat repl" not in covered
    assert "alone" not in fields["description"]
    for phrase in ("chat reply", "CLAUDE.md", "SKILL.md"):
        assert phrase in excluded, phrase


def test_the_description_fits_the_agent_skills_limit():
    fields, _ = frontmatter_and_body()
    assert len(fields["description"]) <= 1024, len(fields["description"])


def test_the_body_names_the_lint_command_and_its_references():
    _, body = frontmatter_and_body()
    command = "python3 -I <this skill's base directory>/scripts/doc_lint.py <file.md or -> [--json]"
    assert command in body
    assert "$" not in body, "the command must not go through a shell variable"
    assert "plain-prose" in body
    assert "references/reader-first.md" in body
    assert "references/templates/" in body
    assert "including chat replies" not in body and "chat replies," not in body


def test_the_body_fits_its_token_budget():
    _, body = frontmatter_and_body()
    assert len(body) / 4 <= BODY_TOKEN_LIMIT, len(body) / 4


@pytest.mark.parametrize("name", TEMPLATES)
def test_each_template_names_its_kind_and_leaves_only_todo_markers(name):
    path = SKILL / "references" / "templates" / f"{name}.md"
    lines = path.read_text().splitlines()
    assert lines[0].startswith("# TODO:"), lines[0]
    report = doc_lint.lint(path.read_text(), path.parent)
    assert report["findings"]["links"] == []
    other = [f for f in report["findings"]["structure"] if "leftover marker" not in f["text"]]
    assert other == [], other


def test_the_guide_lints_clean_and_covers_every_step():
    report = doc_lint.lint(GUIDE.read_text(), GUIDE.parent)
    assert report["findings"] == {"structure": [], "links": []}
    headings = [text for _, level, text in doc_lint.scan(GUIDE.read_text()).headings if level == 2]
    numbered = [h for h in headings if re.match(r"\d\. ", h)]
    assert [h[0] for h in numbered] == [str(i) for i in range(1, 10)], headings
    assert any(h.lower().startswith("short texts") for h in headings), headings
    short = GUIDE.read_text().split("## Short texts", 1)[1]
    for example in ("commit message", "PR description", "code comment"):
        assert example in short, example
    assert len(GUIDE.read_text().splitlines()) < 300
