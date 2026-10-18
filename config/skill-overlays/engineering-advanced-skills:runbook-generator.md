User policy for this skill.

- Before writing the runbook file, load the `writing-docs` skill (it loads `plain-prose` itself), unless its `Base directory for this skill:` block is already in context.
- The runbook's own template wins on structure. The `writing-docs` steps still apply: name who runs it and when, give real commands with the output to expect, say how to tell each step worked and what to do when it doesn't, and run its `doc_lint.py` on each version before it goes to a reviewer, a commit or the user.
