User policy for this skill.

- Before writing an architecture doc or ADR file, load the `writing-docs` skill (it loads `plain-prose` itself), unless its `Base directory for this skill:` block is already in context.
- Its steps apply to every document this skill produces: name the reader and their task, put the decision or answer first, be concrete, and run its `doc_lint.py` on each version before it goes to a reviewer, a commit or the user.
- When this skill supplies its own ADR or architecture template, that template wins on structure; the `writing-docs` steps still apply.
