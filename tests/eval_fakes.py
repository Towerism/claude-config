"""A fake Claude home, a tree snapshot and a scripted `claude` runner for the harness tests.

The runner is the only fake: git, the file system and the real overlay hook run
as they do on the owner's machine.
"""

import json
import re
import shutil
import stat
from pathlib import Path

from tests.checker_helpers import REPO_ROOT

SKILL = "senior-qa"
PLUGIN_SKILL = "engineering-skills:senior-qa"
REMOVED_PLUGINS = (
    "engineering-skills@claude-code-skills",
    "engineering-advanced-skills@claude-code-skills",
)


def make_live_home(tmp_path: Path) -> Path:
    """A Claude home with hooks in settings.json, one installed plugin and credentials."""
    live = tmp_path / "live"
    install = tmp_path / "plugin-cache" / "engineering-skills"
    (install / "skills" / "senior-qa" / "references").mkdir(parents=True)
    (install / "skills" / "senior-qa" / "SKILL.md").write_text("---\nname: senior-qa\n---\n")
    (install / "skills" / "senior-qa" / "references" / "a.md").write_text("a\n")
    rubric = tmp_path / "plugin-cache" / "cmm" / "skills" / "claude-md-improver" / "references"
    rubric.mkdir(parents=True)
    (rubric / "quality-criteria.md").write_text("Score it out of 100.\n")
    (live / "plugins").mkdir(parents=True)
    (live / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "engineering-skills@claude-code-skills": [{"installPath": str(install)}],
                    "claude-md-management@claude-plugins-official": [
                        {"installPath": str(tmp_path / "plugin-cache" / "cmm")}
                    ],
                }
            }
        )
    )
    (live / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": []}, "enabledPlugins": {"x@y": True}})
    )
    creds = live / ".credentials.json"
    creds.write_text('{"token": "secret"}')
    creds.chmod(0o600)
    return live


def make_tree(root: Path, md: str = "# Global\n", overlay: str | None = "Read `references/a.md`.\n",
              hook: bool = True, scenarios: list[dict] | None = None) -> Path:  # fmt: skip
    """A snapshot like the one eval.py extracts from a commit: config/ and eval/.

    It holds one repo skill, SKILL, with the file its overlay names.
    """
    (root / "config" / "skill-overlays").mkdir(parents=True)
    (root / "config" / "CLAUDE.global.md").write_text(md)
    if overlay is not None:
        (root / "config" / "skill-overlays" / f"{SKILL}.md").write_text(overlay)
    skill = root / "config" / "skills" / SKILL
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {SKILL}\n---\n")
    (skill / "references" / "a.md").write_text("a\n")
    if hook:
        (root / "config" / "hooks").mkdir()
        shutil.copy(REPO_ROOT / "config" / "hooks" / "skill_overlay.py", root / "config" / "hooks")
        shutil.copy(REPO_ROOT / "config" / "hooks" / "merge_confirm.py", root / "config" / "hooks")
    shutil.copy(REPO_ROOT / "config" / "settings.fragment.json", root / "config")
    (root / "eval").mkdir()
    (root / "eval" / "scenarios.json").write_text(json.dumps(scenarios or []))
    return root


def scenario(sid: str, **extra) -> dict:
    return {
        "id": sid,
        "prompt": f"prompt {sid}",
        "covers": [],
        "loadsSkills": [],
        "mustInclude": ["does the thing"],
        "mustNotInclude": ["does the bad thing"],
        "tags": [],
        **extra,
    }


class FakeClaude:
    """Answers like `claude -p --output-format json`, from a script keyed by arm and scenario.

    `verdicts[(arm, scenario_id)]` is a list of booleans, one per attempt; the
    grader passes or fails that attempt accordingly. Probes echo the canary
    lines found in the arm's CLAUDE.md, unless `leak` names an arm whose probe
    should also show the other arm's canary.
    """

    def __init__(self, verdicts=None, leak=None, rubric_scores=(85,), raise_on=None):
        self.verdicts = {k: list(v) for k, v in (verdicts or {}).items()}
        self.leak = leak
        self.rubric_scores = list(rubric_scores)
        self.raise_on = raise_on
        self.calls: list[dict] = []
        self.creds_modes: list[int] = []

    def __call__(self, cmd, stdin, env, cwd, timeout):
        cfg = Path(env["CLAUDE_CONFIG_DIR"])
        creds = cfg / ".credentials.json"
        if creds.exists():
            self.creds_modes.append(stat.S_IMODE(creds.stat().st_mode))
        self.calls.append(
            {"cmd": cmd, "stdin": stdin, "cfg": cfg, "cwd": Path(cwd),
             "settings": json.loads((cfg / "settings.json").read_text())}
        )  # fmt: skip
        if self.raise_on and self.raise_on in stdin:
            raise KeyboardInterrupt
        md = (cfg / "CLAUDE.md").read_text()
        if "Eval arm marker" in stdin:
            lines = [ln for ln in md.splitlines() if "Eval arm marker" in ln]
            if self.leak and self.leak in md:
                lines.append("Eval arm marker: CANARY-OLD-5150 CANARY-NEW-6260")
            return 0, json.dumps({"result": "\n".join(lines)})
        if stdin.startswith("Score this global CLAUDE.md"):
            score = self.rubric_scores.pop(0)
            return 0, json.dumps({"result": json.dumps({"total": score, "issues": ["x"]})})
        if stdin.startswith("You are grading"):
            arm, sid = re.search(r"ANSWER:\nanswer (\w+) (\S+) ", stdin).groups()
            crit = json.loads(stdin.split("CRITERIA:\n", 1)[1])
            attempts = self.verdicts.get((arm, sid), [True])
            ok = attempts.pop(0) if len(attempts) > 1 else attempts[0]
            grade = {
                "mustInclude": [ok] * len(crit["mustInclude"]),
                "mustNotInclude": [False] * len(crit["mustNotInclude"]),
                "reason": f"{arm} {sid} {'ok' if ok else 'missed'} " + "r" * 300,
            }
            return 0, json.dumps({"result": "Here: " + json.dumps(grade)})
        arm = "old" if "CANARY-OLD" in md else "new"
        sid = re.search(r"prompt (\S+)$", stdin).group(1)
        return 0, json.dumps({"result": f"answer {arm} {sid} with context {stdin[:400]!r}"})


def leftovers(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir()) if root.exists() else []
