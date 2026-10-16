"""The checker and lock writer stay standard-library only, with no network or subprocesses.

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
