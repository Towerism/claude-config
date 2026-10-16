User policy for this skill.

- Before drafting, confirm the approval mode is settled (global CLAUDE.md "Approval modes"): stated in the prompt, relayed by the coordinator, or asked.
- In a GitHub repo the spec lives in the issue body, as `github-issue-lifecycle` describes, not in `specs/*.md`.
- For a skill, the spec is `SPEC.md` beside its `SKILL.md`.
- Splitting: one request is one spec by default. Use this skill only to judge whether a big request fits one spec (one coherent set of FRs/ACs one PR can deliver); if it clearly doesn't, recommend a split and ask.
- Validate with `scripts/spec_validator.py --file <spec> --strict` (score ≥ 80), run as `python3 -I` plus its literal absolute path.
