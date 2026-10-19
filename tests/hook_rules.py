"""ADR-0007's H1 and H6 rules for the hooks, as functions the tests call.

CI runs them with the other repo tests (#10), so they need no tool of their own.
"""

import ast
import re
import sys
from pathlib import Path
from typing import Any

# Modules that reach the network, start programs or load code by name.
BANNED_MODULES = {"socket", "urllib", "http", "subprocess", "ctypes", "importlib"}
BANNED_CALLS = {"eval", "exec", "compile", "__import__"}
BANNED_OS_CALLS = re.compile(r"system|popen|exec\w*|spawn\w*")
EXPECTED = "python3 -I {claude_home}/hooks/<name>.py"
HOOK_COMMAND = re.compile(r"python3 -I \{claude_home\}/hooks/(?P<name>[a-z0-9_]+\.py)")


def hook_problems(source: str) -> list[str]:
    """Each way a hook's source breaks H1."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        else:
            modules = []
        for module in modules:
            top = module.split(".")[0]
            if top in BANNED_MODULES:
                problems.append(f"imports {top}")
            elif top not in sys.stdlib_module_names:
                problems.append(f"imports {top}, which isn't in the standard library")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
                problems.append(f"calls {func.id}")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and BANNED_OS_CALLS.fullmatch(func.attr)
            ):
                problems.append(f"calls os.{func.attr}")
    return problems


def fragment_problems(fragment: dict[str, Any], hooks: Path) -> list[str]:
    """Each hook command in a settings fragment that breaks H6."""
    problems = []
    for event, groups in fragment.get("hooks", {}).items():
        for group in groups:
            for handler in group["hooks"]:
                command = handler["command"]
                m = HOOK_COMMAND.fullmatch(command)
                if m is None:
                    problems.append(f"{event} runs {command!r}, not {EXPECTED!r}")
                elif not (hooks / m["name"]).is_file():
                    problems.append(f"{event} runs {m['name']}, which isn't in config/hooks")
    return problems
