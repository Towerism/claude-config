"""The settings fragment must be valid Claude Code settings (ADR-0005, ADR-0007 H6).

schemas/claude-code-settings.json is SchemaStore's schema, vendored byte for byte; its
source and hash are in schemas/VENDORED.json. The fragment is rendered for a sample home
first, because it holds {claude_home} placeholders.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tools.cfg import render

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPO_ROOT / "schemas"
RECORD: list[dict[str, Any]] = json.loads((SCHEMAS / "VENDORED.json").read_text())


def settings_validator() -> jsonschema.protocols.Validator:
    schema = json.loads((SCHEMAS / "claude-code-settings.json").read_text())
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def rendered_fragment() -> dict[str, Any]:
    return render.render_fragment(REPO_ROOT, Path("/home/someone/.claude"))


def test_each_vendored_schema_matches_its_recorded_hash_and_has_its_licence() -> None:
    assert [entry["path"] for entry in RECORD] == ["claude-code-settings.json"]
    for entry in RECORD:
        data = (SCHEMAS / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert entry["upstream"]["repo"] == "SchemaStore/schemastore"
        assert len(entry["upstream"]["commit"]) == 40
        assert entry["licence"] == "Apache-2.0"
        assert (SCHEMAS / entry["licenceFile"]).read_text().lstrip().startswith("Apache License")


def test_the_rendered_fragment_is_valid_settings() -> None:
    errors = [e.message for e in settings_validator().iter_errors(rendered_fragment())]
    assert errors == []


@pytest.mark.parametrize(
    "breakage",
    ["misspelled hook type", "hook command not a string", "plugin flag not a boolean"],
)
def test_a_broken_fragment_is_rejected(breakage: str) -> None:
    fragment = copy.deepcopy(rendered_fragment())
    hook = fragment["hooks"]["PostToolUse"][0]["hooks"][0]
    if breakage == "misspelled hook type":
        hook["type"] = "commnd"
    elif breakage == "hook command not a string":
        hook["command"] = ["python3", "-I"]
    else:
        plugin = next(iter(fragment["enabledPlugins"]))
        fragment["enabledPlugins"][plugin] = "yes"
    assert list(settings_validator().iter_errors(fragment)) != []
