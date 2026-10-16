"""Factories for the eval tests: small git repos with fixed identities and dates."""

import json
import os
import subprocess
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@users.noreply.github.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

FRAGMENT = {
    "enabledPlugins": {"a@b": True},
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Skill",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -I {claude_home}/hooks/skill_overlay.py",
                    }
                ],
            }
        ],
        "UserPromptExpansion": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -I {claude_home}/hooks/skill_overlay.py",
                    }
                ]
            }
        ],
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -I {claude_home}/hooks/merge_confirm.py",
                    }
                ],
            }
        ],
    },
}

SKILL_MD = "---\nname: lc\ndescription: Runs the issue lifecycle.\n  Second line.\n---\n# Body\n"

LIFECYCLE = "config/skills/github-issue-lifecycle"

GATES = {
    "suites": {
        "scenario": {
            "runner": "scenario",
            "paths": [
                "config/CLAUDE.global.md",
                "config/skill-overlays/**",
                "config/hooks/skill_overlay.py",
                "eval/scenarios.json",
                "eval/clauses.json",
            ],
            "harness": ["eval/run_eval.py"],
            "compareBase": True,
        },
        "rubric": {
            "runner": "rubric",
            "paths": ["config/CLAUDE.global.md"],
            "harness": ["eval/run_eval.py"],
        },
        "hook-overlay": {
            "runner": "hook-overlay",
            "paths": ["config/hooks/skill_overlay.py"],
            "selectors": [
                {"file": "config/settings.fragment.json", "pointer": "/hooks/PostToolUse"},
                {"file": "config/settings.fragment.json", "pointer": "/hooks/UserPromptExpansion"},
            ],
            "harness": ["eval/hook_harness.py"],
        },
        "hook-merge": {
            "runner": "hook-merge",
            "paths": ["config/hooks/merge_confirm.py"],
            "selectors": [
                {"file": "config/settings.fragment.json", "pointer": "/hooks/PreToolUse"}
            ],
            "harness": ["eval/hook_harness.py"],
        },
        "lifecycle-trigger": {
            "runner": "script",
            "script": f"{LIFECYCLE}/evals/run_trigger_evals.py",
            "paths": [f"{LIFECYCLE}/evals/trigger-evals.json"],
            "selectors": [{"file": f"{LIFECYCLE}/SKILL.md", "part": "description"}],
            "harness": [f"{LIFECYCLE}/evals/run_trigger_evals.py"],
        },
        "lifecycle-behaviour": {
            "runner": "script",
            "script": f"{LIFECYCLE}/evals/run_behaviour_evals.py",
            "paths": [f"{LIFECYCLE}/evals/evals.json"],
            "selectors": [{"file": f"{LIFECYCLE}/SKILL.md", "part": "body"}],
            "harness": [f"{LIFECYCLE}/evals/run_behaviour_evals.py"],
        },
    }
}


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def write(repo: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def commit(repo: Path, files: dict[str, str | None], message: str = "change") -> str:
    write(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def base_files() -> dict[str, str | None]:
    return {
        "config/CLAUDE.global.md": "# Global\nrule one\n",
        "config/skill-overlays/engineering-skills:senior-qa.md": "Read `references/a.md`.\n",
        "config/hooks/skill_overlay.py": "print('hook')\n",
        "config/hooks/merge_confirm.py": "print('merge')\n",
        "config/settings.fragment.json": json.dumps(FRAGMENT, indent=2),
        f"{LIFECYCLE}/SKILL.md": SKILL_MD,
        f"{LIFECYCLE}/evals/trigger-evals.json": "[]\n",
        f"{LIFECYCLE}/evals/evals.json": '{"evals": []}\n',
        f"{LIFECYCLE}/evals/run_trigger_evals.py": "print('t')\n",
        f"{LIFECYCLE}/evals/run_behaviour_evals.py": "print('b')\n",
        "eval/scenarios.json": "[]\n",
        "eval/clauses.json": "[]\n",
        "eval/run_eval.py": "print('run')\n",
        "eval/hook_harness.py": "print('hooks')\n",
        "eval/gates.json": json.dumps(GATES, indent=2),
        "docs/readme.md": "docs\n",
        "plugins.lock.json": "{}\n",
    }


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repo whose `main` and `origin/main` hold every gated file, with `work` checked out."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    base = commit(repo, base_files(), "base")
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    git(repo, "checkout", "-q", "-b", "work")
    return repo, base
