User policy: who runs a pass, then reviewer rules. Handling rules: global CLAUDE.md.

Every pass runs in a fresh agent, not the author's context. Find your role:

- Another agent started you and your task opens with `You are the fresh adversarial reviewer for this pass.` (after harness context): you are the reviewer. Review here and return findings with severities. Start no agent, edit and post nothing, ask no one else to review.
- Another agent started you (a fork or subagent), with no such line: don't review and don't start a reviewer. End your run with a report whose first line is `Adversarial pass requested.`, telling the coordinator to load `adversarial-reviewer` first, then items 2 to 4 below, without your reasoning or view of the risks. Do nothing the review gates until it resumes you with the findings. If one comes from an agent you started, pass it up, then resume that agent with the findings.
- The user started you: don't review here. Start a `general-purpose` agent, never a fork, whose prompt has:
  1. first line `You are the fresh adversarial reviewer for this pass.`
  2. what to review and where (issue or ticket and spec location, or branch and base);
  3. architecture doc and ADR paths, or "none";
  4. the owner's recorded decisions, quoted, with their source;
  5. load `adversarial-reviewer` with the Skill tool first;
  6. return findings with severities; edit no files, post nothing, start no agents.

  Leave out your reasoning and view of the risks. If a fork's `Adversarial pass requested.` report brought you here, take only items 2 to 4 from it, then resume that same agent, not a new one, with the findings, unedited.

A pass happened once a reviewer returned findings with severities, or said it found none. Otherwise you may start one more with the same prompt. If that fails too, or you can't start a fresh agent, don't review yourself: report the pass as blocked, say why, and wait.

Rules for the reviewer:

- CRITICAL for a spec means: following it as written would cause data loss, a security hole or an outage; it contradicts the accepted architecture/ADRs or a user decision; or an acceptance criterion can't be tested or implemented as written. "Could go wrong if implemented badly" is not CRITICAL.
- A finding raised only to meet this skill's at-least-one-per-persona rule (e.g. "the most fragile assumption" on sound work) is at most a WARNING.
- The 2+-persona promotion rule never promotes a finding to CRITICAL unless it would be CRITICAL on its own.
- Flag only gaps that affect correctness, the stated requirements or the user's rules; don't pad.
- Review notes and the findings written into PR descriptions follow `plain-prose`: a reader with no context must understand each finding, what goes wrong, and why.
