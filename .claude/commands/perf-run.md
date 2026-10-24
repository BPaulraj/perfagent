---
description: Execute a perf test type (load, stress, spike, soak, breakpoint) with pre-flight checks, then analyze results.
argument-hint: <project-name> <test-type> [scale]
---

<!-- GENERATED from .github/prompts/perf-run.prompt.md by `python -m perfagent agents sync` - edit the source, not this file -->
Run a **<testType>** test for project **<project>** at scale **<scale>**.

Pre-flight (report each item, and stop if any fails):
- A smoke run passed for the current scripts (`python -m perfagent runs <project>`).
- Target host is in `safety.allowed_hosts`, and the environment is not production.
- Show `python -m perfagent run <project> --type <type> --scale <scale> --dry-run`, then the planned peak rate per scenario and the total duration from `design/workload.json`.
- Remind me of the entry criteria in `design/test_plan.md` that still need confirming (environment window, monitoring, stakeholders).

Then **ask for my explicit go-ahead** before running. After the run, analyze it with the **perf-results** skill. If this was the first clean load test, offer to set it as the baseline.

Arguments (testType, project, scale): $ARGUMENTS
Ask for any value not supplied.
