"""Vendored third-party files match their provenance record and still run."""

import hashlib
import json
import subprocess
import sys

from tests.checker_helpers import REPO_ROOT

VENDOR = REPO_ROOT / "tools" / "vendor" / "claude-skills"
SKILLS = REPO_ROOT / "config" / "skills"
RECORD = json.loads((VENDOR / "VENDORED.json").read_text())


def test_every_vendored_file_matches_its_recorded_hash_and_licence():
    assert RECORD, "VENDORED.json lists no files"
    for entry in RECORD:
        data = (VENDOR / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]
        assert entry["licence"] == "MIT"
        assert (VENDOR / entry["licenceFile"]).read_text().startswith("MIT License")
        assert set(entry["upstream"]) == {"repo", "commit", "path"}
        assert len(entry["upstream"]["commit"]) == 40


def test_the_record_points_at_the_marketplace_clone_not_an_installed_plugin():
    # The plugin it came from was removed (#62); the marketplace stays known, so
    # lock.py --check can still compare the file with upstream.
    for entry in RECORD:
        assert "plugin" not in entry and "installedPath" not in entry
        assert entry["marketplace"] == "claude-code-skills"
        assert entry["marketplacePath"] == entry["upstream"]["path"]


def test_the_spec_validator_is_the_same_file_the_spec_driven_workflow_skill_ships():
    skill_copy = SKILLS / "spec-driven-workflow" / "scripts" / "spec_validator.py"
    assert (VENDOR / "spec_validator.py").read_bytes() == skill_copy.read_bytes()


def test_every_file_in_the_vendor_directory_is_recorded():
    recorded = {e["path"] for e in RECORD} | {e["licenceFile"] for e in RECORD} | {"VENDORED.json"}
    assert {p.name for p in VENDOR.iterdir() if p.is_file()} == recorded


def test_the_spec_validator_runs_isolated_on_a_repo_spec():
    r = subprocess.run(
        [sys.executable, "-I", str(VENDOR / "spec_validator.py"), "--file",
         str(REPO_ROOT / "config" / "SPEC.md"), "--strict"],
        capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert "Spec Completeness Score:" in r.stdout, r.stdout + r.stderr
