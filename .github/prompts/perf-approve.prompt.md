---
name: perf-approve
description: Record the human review decisions on proposed perf scenarios, then design the test.
argument-hint: "<project-name> <decisions e.g. approve P1 and SC-006, reject SC-004>"
agent: agent
---

Apply my scenario review decisions for project **${input:project:project name}**.

My decisions: ${input:decisions:e.g. approve all P1 and SC-006; reject SC-004 (admin only); set SC-002 p95 to 1500ms}

1. Apply exactly these decisions and nothing more. Use `python -m perfagent catalog <project> approve|reject|defer <IDs> --note "<my reason>"`. Make rating, share or SLA changes in `catalog.yaml`, then run `catalog <project> score`.
2. Show the updated ranked table and the approved workload share total (it should be 100% for api scenarios). Point out anything inconsistent.
3. Continue with the **perf-design** skill: `design`, then summarise the workload model and test plan, then `generate`, then `run --type smoke`. Fix script problems using the **perf-smoke-debug** skill.
4. Stop after a passing smoke test and ask before any heavier run.
