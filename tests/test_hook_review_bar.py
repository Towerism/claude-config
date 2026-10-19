"""The machine-checked rules of ADR-0007's hook review bar.

H1: a hook uses only the standard library and never reaches the network,
starts a program or runs dynamic code. H6: Claude Code invokes every hook as
`python3 -I <absolute path>`. Each rule is checked on the real files and on a
planted copy that breaks it, so a rule that silently stopped matching would
fail here.
"""

import json

import pytest

from tests.checker_helpers import REPO_ROOT
from tests.hook_helpers import HOOKS
from tests.hook_rules import fragment_problems, hook_problems

FRAGMENT = REPO_ROOT / "config" / "settings.fragment.json"


@pytest.mark.parametrize("hook", sorted(HOOKS.glob("*.py")), ids=lambda p: p.name)
def test_each_hook_uses_only_the_standard_library_and_runs_nothing(hook):
    assert hook_problems(hook.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("import subprocess", "imports subprocess"),
        ("import socket", "imports socket"),
        ("from urllib.request import urlopen", "imports urllib"),
        ("import http.client", "imports http"),
        ("import ctypes", "imports ctypes"),
        ("import importlib", "imports importlib"),
        ("import yaml", "imports yaml, which isn't in the standard library"),
        ("eval('1')", "calls eval"),
        ("exec('x = 1')", "calls exec"),
        ("compile('1', 'f', 'eval')", "calls compile"),
        ("__import__('os')", "calls __import__"),
        ("os.system('ls')", "calls os.system"),
        ("os.popen('ls')", "calls os.popen"),
        ("os.execv('/bin/ls', ['ls'])", "calls os.execv"),
        ("os.spawnl(os.P_WAIT, '/bin/ls')", "calls os.spawnl"),
    ],
)
def test_a_planted_violation_is_caught(line, problem):
    source = (HOOKS / "merge_confirm.py").read_text(encoding="utf-8") + f"\n{line}\n"
    assert hook_problems(source) == [problem]


def test_every_hook_command_is_python3_isolated_on_a_hook_file():
    fragment = json.loads(FRAGMENT.read_text(encoding="utf-8"))
    assert fragment_problems(fragment, HOOKS) == []


@pytest.mark.parametrize(
    ("command", "problem"),
    [
        (
            "python3 {claude_home}/hooks/merge_confirm.py",
            "PreToolUse runs 'python3 {claude_home}/hooks/merge_confirm.py', "
            "not 'python3 -I {claude_home}/hooks/<name>.py'",
        ),
        (
            "python3 -I {claude_home}/hooks/missing.py",
            "PreToolUse runs missing.py, which isn't in config/hooks",
        ),
        (
            "bash {claude_home}/hooks/run.sh",
            "PreToolUse runs 'bash {claude_home}/hooks/run.sh', "
            "not 'python3 -I {claude_home}/hooks/<name>.py'",
        ),
    ],
    ids=["no -I", "no such hook", "not python"],
)
def test_a_bad_hook_command_is_caught(command, problem):
    fragment = {
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}]
        }
    }
    assert fragment_problems(fragment, HOOKS) == [problem]
