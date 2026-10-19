"""The checker, lock writer and eval tools stay standard-library only, with no network.

CI runs them with python -I and no installed packages, and they read
third-party plugin files, so they must not reach the network or run other
programs.
"""

import ast
import sys

import pytest

from tests.checker_helpers import REPO_ROOT

SCRIPTS = [REPO_ROOT / "tools" / "check_config.py", REPO_ROOT / "tools" / "cfg" / "lock.py"]
BANNED = {"socket", "urllib", "http", "subprocess", "ftplib", "smtplib", "ssl"}


def top_level_imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_only_standard_library_and_repo_modules_are_imported(script):
    imports = top_level_imports(script)
    assert imports - set(sys.stdlib_module_names) <= {"tools"}
    assert not imports & BANNED


# The eval record tools run git (and, for eval.py, claude) as subprocesses, so
# subprocess is allowed there; nothing may reach the network directly.
EVAL_SCRIPTS = [
    REPO_ROOT / "tools" / "cfg" / "eval_records.py",
    REPO_ROOT / "tools" / "cfg" / "eval.py",
    REPO_ROOT / "eval" / "run_eval.py",
    REPO_ROOT / "eval" / "hook_harness.py",
]


@pytest.mark.parametrize("script", EVAL_SCRIPTS, ids=lambda p: p.name)
def test_eval_tools_use_only_the_standard_library_and_no_network(script):
    imports = top_level_imports(script)
    assert imports - set(sys.stdlib_module_names) <= {"tools", "eval"}
    assert not imports & (BANNED - {"subprocess"})


def test_the_record_verifier_imports_nothing_from_the_harness():
    assert top_level_imports(REPO_ROOT / "tools" / "cfg" / "eval_records.py") <= set(
        sys.stdlib_module_names
    )


# cfg's core modules read and write only local files. Only release.py runs a
# program, and only git.
CFG_MODULES = [
    REPO_ROOT / "tools" / "cfg" / name
    for name in (
        "errors.py",
        "state.py",
        "render.py",
        "settings_merge.py",
        "link.py",
        "drift.py",
        "restore.py",
    )
]


@pytest.mark.parametrize("module", CFG_MODULES, ids=lambda p: p.name)
def test_cfg_modules_use_only_the_standard_library_and_run_nothing(module):
    imports = top_level_imports(module)
    assert imports - set(sys.stdlib_module_names) <= {"tools"}
    assert not imports & BANNED


def test_cfg_release_runs_only_git():
    path = REPO_ROOT / "tools" / "cfg" / "release.py"
    imports = top_level_imports(path)
    assert imports - set(sys.stdlib_module_names) <= {"tools"}
    assert not imports & (BANNED - {"subprocess"})
    calls = [
        node
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    ]
    assert calls
    for call in calls:
        first = call.args[0]
        assert isinstance(first, ast.List)
        assert isinstance(first.elts[0], ast.Constant) and first.elts[0].value == "git"


# deploy runs git, the release's checker and the hook commands; cli runs a backup's
# restore.py; capture runs git. Each uses subprocess with an argument list, and
# none may reach the network directly.
CFG_RUNNERS = [
    REPO_ROOT / "tools" / "cfg" / name for name in ("deploy.py", "capture.py", "cli.py")
]


@pytest.mark.parametrize("module", CFG_RUNNERS, ids=lambda p: p.name)
def test_cfg_commands_that_run_programs_reach_no_network(module):
    imports = top_level_imports(module)
    assert imports - set(sys.stdlib_module_names) <= {"tools"}
    assert not imports & (BANNED - {"subprocess"})
