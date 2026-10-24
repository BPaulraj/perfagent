---
name: perf-design
description: Turn approved perfagent scenarios into a workload model, test plan and runnable k6 scripts, then validate with a smoke test. Use after scenarios are approved, or when the user asks to design, script, or execute a perf test.
argument-hint: "<project>"
---

# perf-design: approved scenarios → workload → plan → scripts → smoke → test runs

Precondition: `catalog.yaml` has `status: approved` scenarios. If it doesn't, go back to **perf-discover**. Do not approve on the user's behalf.

## 1. Workload model and plan
`python -m perfagent design <name>` writes `design/workload.json` and `design/test_plan.md`.
- Sanity-check the numbers: users per scenario, iteration time, arrival rate/min. If the user has real figures (peak RPS, orders/hour, analytics), adjust `nfr.peak_concurrent_users`, `workload_share`, `think_time` and re-run.
- Business-volume cross-check: e.g. 6,000 orders/hour = 100/min, so the order scenario's rate_per_min should be about 100 at 1.0.
- Summarise the plan for the user (scope, SLAs, workload table, test sequence, entry criteria still open). The plan is a **sign-off document**, so ask them to confirm NFRs if they were assumed.

## 2. Generate scripts
`python -m perfagent generate <name>` writes `scripts/main.js`, `scripts/scenarios/*.js` and `scripts/lib/common.js`.
- Review each scenario file. Fix correlation (extract paths), request bodies (the OpenAPI examples are placeholders) and selectors.
- Put test data in `projects/<name>/data/*.csv` (header row = var names) and reference it via `data_file`. Use unique rows for create/login flows.
- Hand edits: delete the `// GENERATED` first line so `generate` keeps the file. Prefer fixing `catalog.yaml` so the change survives regeneration.

## 3. Execute in order (stop at each failure)
1. `python -m perfagent run <name> --type smoke`: must show 0 errors and all checks passing. Debug with `k6.log` before moving on.
2. `--type load`, then on a clean pass: `python -m perfagent baseline <name> <run_id>`.
3. `--type stress`, `--type spike`, `--type soak`, `--type breakpoint` as the plan requires.
- `--scale 0.25` for scaled-down environments (state this in the report). `--dry-run` shows the command. `--influx` streams to Grafana (`monitoring/`).
- Before any non-smoke run: confirm with the user that the environment window is agreed. Load affects shared systems.
- Never use `--allow-production` unless the user explicitly instructs it with approval.

If smoke fails, use **perf-smoke-debug**. After each run, use **perf-results** to interpret it.
