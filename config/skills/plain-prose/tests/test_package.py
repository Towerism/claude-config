"""The skill package has what Claude needs when it loads, and stays small."""

import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
BODY_TOKEN_LIMIT = 800  # once loaded, the body stays in context for the whole session


def frontmatter_and_body() -> tuple[dict[str, str], str]:
    text = (SKILL / "SKILL.md").read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, "SKILL.md has no frontmatter"
    fields = dict(line.split(": ", 1) for line in m.group(1).splitlines())
    return fields, m.group(2)


def test_the_package_files_exist():
    for rel in (
        "SKILL.md", "SPEC.md", "REVIEW-NOTE.md", "VENDORED.json", "LICENSE-vendored.md",
        "scripts/prose_check.py", "scripts/vendored/readability_scorer.py",
        "scripts/vendored/ai_content_detector.py", "references/vendored/ai-tells-checklist.md",
        "references/vendored/plain-english-alternatives.md", "evals/trigger-evals.json",
        "evals/behaviour-evals.json",
    ):  # fmt: skip
        assert (SKILL / rel).is_file(), rel


def test_the_frontmatter_names_the_skill_and_its_scope():
    fields, _ = frontmatter_and_body()
    assert set(fields) == {"name", "description"}
    assert fields["name"] == "plain-prose"
    description = fields["description"]
    for covered in ("chat replies", "commit", "review notes", "code comments", "once per session"):
        assert covered in description, covered
    for excluded in ("CLAUDE.md", "SKILL.md"):
        assert excluded in description.split("Not for", 1)[1], excluded


def test_the_body_links_the_references_and_names_the_checker_command():
    _, body = frontmatter_and_body()
    assert "references/vendored/ai-tells-checklist.md" in body
    assert "plain-english-alternatives.md" in body
    command = "python3 -I <this skill's base directory>/scripts/prose_check.py <file or -> [--json]"
    assert command in body
    assert "$" not in body, "the command must not go through a shell variable"


def test_the_body_fits_its_token_budget():
    _, body = frontmatter_and_body()
    assert len(body) / 4 <= BODY_TOKEN_LIMIT, len(body) / 4
