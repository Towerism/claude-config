User policy for the reviewer (the caller's handling rules live in the global CLAUDE.md).

- CRITICAL for a spec means: following it as written would cause data loss, a security hole or an outage; it contradicts the accepted architecture/ADRs or a user decision; or an acceptance criterion can't be tested or implemented as written. "Could go wrong if implemented badly" is not CRITICAL.
- A finding raised only to meet this skill's at-least-one-per-persona rule (e.g. "the most fragile assumption" on sound work) is at most a WARNING.
- The 2+-persona promotion rule never promotes a finding to CRITICAL unless it would be CRITICAL on its own.
- Flag only gaps that affect correctness, the stated requirements or the user's rules; don't pad.
