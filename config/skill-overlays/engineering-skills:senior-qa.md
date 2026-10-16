User policy for this skill. Apply it; loading the skill isn't enough.

Before writing any test plan, read all three guides:
- `references/testing_strategies.md`
- `references/qa_best_practices.md`
- `references/test_automation_patterns.md`

The plan must visibly state:
- pyramid placement per acceptance criterion (push logic down; keep E2E for critical journeys);
- coverage targets set by risk;
- each flake hazard and how it's made deterministic (gates, fake clocks, no wall-clock assertions);
- isolation and fixtures/factories;
- behaviour over mocks;
- which existing tests get rewritten or deleted;
- which criteria are review checks rather than tests.

Load this skill again before writing the tests themselves, and aim them at the riskiest behaviour, not line coverage.

Stack: the React/Jest examples are illustrations. Translate them to the repo's test stack (for example, when the repo uses Nuxt/Vitest: `@nuxt/test-utils`, `@vue/test-utils`, MSW, Playwright). Don't run this skill's React generator scripts on a non-React repo.
