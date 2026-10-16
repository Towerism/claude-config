"""Vendored third-party files match their provenance record and still run."""

import hashlib
import json
import subprocess
import sys

from tests.checker_helpers import REPO_ROOT

VENDOR = REPO_ROOT / "tools" / "vendor" / "claude-skills"
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
