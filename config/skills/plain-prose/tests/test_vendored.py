"""The skill's vendored upstream files match their provenance record."""

import hashlib
import json
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
RECORD_PATH = SKILL / "VENDORED.json"
FIELDS = {
    "path", "upstream", "marketplace", "marketplacePath", "sha256",
    "adapted", "licence", "licenceFile",
}  # fmt: skip


def record() -> list[dict[str, object]]:
    return json.loads(RECORD_PATH.read_text())


def test_every_entry_has_its_provenance_fields():
    entries = record()
    assert len(entries) == 4
    for entry in entries:
        assert set(entry) == FIELDS, entry["path"]
        upstream = entry["upstream"]
        assert set(upstream) == {"repo", "commit", "path"}
        assert upstream["repo"] == "alirezarezvani/claude-skills"
        assert len(upstream["commit"]) == 40
        assert entry["marketplace"] == "claude-code-skills"
        assert entry["licence"] == "MIT"
        assert entry["licenceFile"] == "LICENSE-vendored.md"


def test_unadapted_files_are_byte_identical_to_upstream():
    for entry in record():
        data = (SKILL / entry["path"]).read_bytes()
        if not entry["adapted"]:
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]


def test_only_the_checklist_is_adapted():
    adapted = [e["path"] for e in record() if e["adapted"]]
    assert adapted == ["references/vendored/ai-tells-checklist.md"]


def test_every_file_in_a_vendored_directory_is_recorded():
    listed = {e["path"] for e in record()}
    on_disk = {
        str(p.relative_to(SKILL))
        for d in ("scripts/vendored", "references/vendored")
        for p in (SKILL / d).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    assert on_disk == listed


def test_the_licence_file_holds_the_mit_text_and_names_the_files():
    text = (SKILL / "LICENSE-vendored.md").read_text()
    assert "MIT License" in text
    assert "Permission is hereby granted, free of charge" in text
    for entry in record():
        assert str(entry["path"]) in text
