"""diff-check against a real temp git repo: spec IDs in code and committed spec files."""

import json

from conftest import make_repo, run

CLEAN = {"src/export.py": "def write_header():\n    return 'a,b'\n",
         "tests/test_export.py": "def test_export_writes_header():\n    pass\n"}


def check(repo):
    return run("diff-check", "--base", "main", "--repo-dir", repo, "--json")


def test_spec_id_in_test_name_and_spec_file_both_reported(tmp_path):
    repo = make_repo(tmp_path, {"tests/test_x.py": "def test_ac3_rejects_empty():\n    pass\n",
                                "docs/feature-spec.md": "# Spec\n"})
    r = check(repo)
    assert r.returncode == 1
    codes = {p["code"] for p in json.loads(r.stdout)["problems"]}
    assert codes == {"spec-id", "spec-file"}


def test_clean_branch_passes(tmp_path):
    assert check(make_repo(tmp_path, CLEAN)).returncode == 0


def test_skill_spec_inspection_doc_and_playwright_spec_are_allowed(tmp_path):
    repo = make_repo(tmp_path, {
        ".claude/skills/foo/SKILL.md": "---\nname: foo\n---\n",
        ".claude/skills/foo/SPEC.md": "# Spec\n- FR-1: x\n",
        "docs/inspection.md": "# Inspection\n",
        "e2e/login.spec.ts": "test('login', () => {})\n",
    })
    r = check(repo)
    assert r.returncode == 0, r.stdout


def test_uppercase_hyphenated_id_in_a_comment_is_reported(tmp_path):
    repo = make_repo(tmp_path, {"src/a.ts": "// covers AC-12\nexport const a = 1\n"})
    assert check(repo).returncode == 1


def test_infra_names_like_ec2_are_not_spec_ids(tmp_path):
    repo = make_repo(tmp_path, {"infra/main.tf": 'resource "aws_instance" "ec2 box" {}\n',
                                "src/os2.py": "x = 'os2'\n"})
    assert check(repo).returncode == 0


def test_specs_directory_and_spec_basenames_are_spec_files(tmp_path):
    for i, name in enumerate(["specs/auth.md", "SPEC.md", "spec-auth.md", "auth.spec.md"]):
        (tmp_path / str(i)).mkdir()
        repo = make_repo(tmp_path / str(i), {name: "# Spec\n"})
        problems = json.loads(check(repo).stdout)["problems"]
        assert [p["code"] for p in problems] == ["spec-file"], name


def test_spec_ids_in_markdown_docs_are_not_code(tmp_path):
    repo = make_repo(tmp_path, {"docs/adr/0003-export.md": "Addresses FR-2 of the design.\n"})
    assert check(repo).returncode == 0


def test_bad_base_is_a_usage_error(tmp_path):
    repo = make_repo(tmp_path, CLEAN)
    r = run("diff-check", "--base", "nope", "--repo-dir", repo)
    assert r.returncode == 2
