---
name: perf-report
description: Analyze a perf test run and write a stakeholder summary with go/no-go recommendation.
argument-hint: "<project-name> [run-id]"
agent: agent
---

Analyze run **${input:runId:run id, or 'latest'}** of project **${input:project:project name}** using the **perf-results** skill.

1. `python -m perfagent report <project> [run_id]` (rebuilds report.html / report.md / report.json).
2. Check validity first (target load reached, dropped iterations, error types). Then give the SLA verdict per scenario, the slowest transactions, the baseline comparison and bottleneck hypotheses with the server-side evidence needed to confirm them.
3. Write the findings to `projects/<project>/runs/<run_id>/findings.yaml` (format in the perf-results skill) and re-run `python -m perfagent report <project> <run_id>` so they appear in report.html.
4. Write `projects/<project>/runs/<run_id>/summary_for_stakeholders.md`: executive summary (3 lines), verdict, findings table, defects in the format of docs/PERF_STRATEGY.md §9, recommendation (go / no-go / retest after fix) and next steps.
