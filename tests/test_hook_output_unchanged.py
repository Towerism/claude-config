"""The hooks print exactly what they printed before #20 cleaned them up.

The expected bytes were recorded once from the hooks at commit 50737ec, with
the temporary directory written as {ROOT}. Comparing with recorded bytes,
rather than with the hooks on origin/main at test time, keeps the test
meaningful after the merge and in a shallow clone. cfg's deploy smoke test,
the eval harness and the symlink check all read this output.

One recorded line changed on purpose since then (#95): the message for an
overlay that can't be loaded used to name ~/.claude whatever home the hook
read. It now names the home it read, with $HOME written as ~. run_script sets
HOME to the home's parent, so the test's home shows as ~/claude-home.
"""

import pytest

from tests.hook_helpers import make_home, payload, run_script

OVERLAY = "Policy.\n- Read `references/testing_strategies.md` first.\n- Run `scripts/missing.py`.\n"

PLUGIN_CONTEXT = (
    "Skill overlay for engineering-skills:senior-qa:\\nPolicy.\\n- Read "
    "`{ROOT}/plugin-cache/engineering-skills-claude-code-skills/skills/senior-qa/"
    "references/testing_strategies.md` first.\\n- Run "
    "`{ROOT}/plugin-cache/engineering-skills-claude-code-skills/skills/senior-qa/"
    "scripts/missing.py`.\\n\\nmissing: scripts/missing.py"
)


def overlay_output(event: str, context: str) -> str:
    return (
        '{"hookSpecificOutput": {"hookEventName": "' + event + '", '
        '"additionalContext": "' + context + '"}}\n'
    )


ASK = (
    '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", '
    '"permissionDecisionReason": "Merging needs your confirmation '
    '(global rule: merge only with your authorization)"}}\n'
)

OVERLAY_CASES = {
    "plugin skill after a Skill call": (
        payload(
            hook_event_name="PostToolUse",
            tool_name="Skill",
            tool_input={"skill": "engineering-skills:senior-qa"},
        ),
        overlay_output("PostToolUse", PLUGIN_CONTEXT),
    ),
    "typed slash command": (
        payload(hook_event_name="UserPromptExpansion", command_name="engineering-skills:senior-qa"),
        overlay_output("UserPromptExpansion", PLUGIN_CONTEXT),
    ),
    "slash command in a prompt": (
        payload(
            hook_event_name="UserPromptSubmit", prompt="/engineering-skills:senior-qa plan tests"
        ),
        overlay_output("UserPromptSubmit", PLUGIN_CONTEXT),
    ),
    "user skill": (
        payload(hook_event_name="PostToolUse", tool_name="Skill", tool_input={"skill": "foo"}),
        overlay_output(
            "PostToolUse",
            "Skill overlay for foo:\\nUse `{ROOT}/claude-home/skills/foo/references/a.md`.\\n",
        ),
    ),
    "bare name matching one plugin overlay": (
        payload(
            hook_event_name="PostToolUse", tool_name="Skill", tool_input={"skill": "senior-qa"}
        ),
        overlay_output("PostToolUse", PLUGIN_CONTEXT),
    ),
    "overlay whose skill directory is missing": (
        payload(hook_event_name="PostToolUse", tool_name="Skill", tool_input={"skill": "bar"}),
        overlay_output(
            "PostToolUse",
            "Skill overlay for bar could not be loaded (user skill directory not found for bar); "
            "read ~/claude-home/skill-overlays/bar.md manually.",
        ),
    ),
    "skill with no overlay": (
        payload(hook_event_name="PostToolUse", tool_name="Skill", tool_input={"skill": "nothing"}),
        "",
    ),
}

MERGE_CASES = {
    "gh pr merge 5": ASK,
    "gh -R o/r pr merge 5": ASK,
    "cd x && gh pr merge 5": ASK,
    'bash -c "gh pr merge 5"': ASK,
    "gh api repos/o/r/pulls/5/merge": ASK,
    "gh pr view 5": "",
    "gh pr list --search merge": "",
    "ls": "",
}


@pytest.fixture
def home(tmp_path):
    return make_home(
        tmp_path,
        overlays={
            "engineering-skills:senior-qa": OVERLAY,
            "foo": "Use `references/a.md`.\n",
            "bar": "No skill dir.\n",
        },
        skills={"foo": ["references/a.md"]},
        plugins={
            "engineering-skills@claude-code-skills": {
                "senior-qa": ["references/testing_strategies.md"]
            }
        },
    )


@pytest.mark.parametrize("case", OVERLAY_CASES, ids=str)
def test_the_overlay_hook_prints_the_recorded_bytes(home, case):
    stdin, expected = OVERLAY_CASES[case]
    done = run_script("skill_overlay", stdin, home)
    assert done.returncode == 0
    assert done.stdout.decode() == expected.replace("{ROOT}", str(home.parent))


@pytest.mark.parametrize("command", MERGE_CASES, ids=str)
def test_the_merge_hook_prints_the_recorded_bytes(home, command):
    stdin = payload(hook_event_name="PreToolUse", tool_name="Bash", tool_input={"command": command})
    done = run_script("merge_confirm", stdin, home)
    assert done.returncode == 0
    assert done.stdout.decode() == MERGE_CASES[command]
