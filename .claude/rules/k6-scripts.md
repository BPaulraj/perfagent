---
description: Conventions for generated and hand-edited k6 performance scripts and the shared k6 runtime library.
paths:
- projects/**/scripts/**/*.js
- perfagent/k6lib/**/*.js
- perfagent/templates/*.js.j2
---

<!-- GENERATED from .github/instructions/k6-scripts.instructions.md by `python -m perfagent agents sync` - edit the source, not this file -->
# k6 script conventions

- `projects/*/scripts/` is **generated** by `python -m perfagent generate`. Prefer changing `catalog.yaml` and regenerating. `main.js` and `lib/common.js` are always overwritten. A scenario file is kept only if its first line (`// GENERATED ...`) was deleted.
- To change runtime behaviour for all projects, edit `perfagent/k6lib/common.js` (copied into each project on generate), not the copies.
- k6 is **not Node.js**: no npm modules and no Node APIs. Use only `k6/*` modules (`k6/http`, `k6/browser`, `k6/data`, `k6/metrics`, `k6/encoding`). `open()` only works in init context.
- Browser scenarios are `async`. Wait with `await page.waitForTimeout()`, never `sleep()`. Locators are strict: use `.first()` when several elements are expected.
- Every request carries `tags: { name: '<transaction>' }` so dynamic URLs group under one metric. Never tag with raw URLs or IDs (that explodes metric cardinality).
- Every request is checked (`check`). Iterations stop when a prerequisite step fails (`if (!apiStep(...)) return;`) so correlated steps never fire with missing data.
- SLAs are thresholds built in `buildOptions()`. Do not weaken thresholds to make a run pass. Report the breach instead.
- Load shape comes from `workload.json` (arrival-rate executors, open model). Don't hard-code VUs or durations in scenario files.
- Secrets only via `__ENV` / `{{env.NAME}}`.
