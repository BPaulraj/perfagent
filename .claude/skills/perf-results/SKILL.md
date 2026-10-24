---
name: perf-results
description: Analyze a perfagent/k6 run - SLA verdict, bottleneck hypotheses, baseline regression, and a go/no-go summary for stakeholders. Use after a perf test run or when the user asks how a test went.
argument-hint: "<project> [run-id]"
---

# perf-results: run → analysis → recommendation

Inputs in `projects/<name>/runs/<run_id>/`: `report.json` (normalised), `summary.json` (raw k6), `k6.log`, `meta.json`, `dashboard.html` (time series), `report.html`.
Rebuild with `python -m perfagent report <name> [run_id]`.

## Analyse like a perf engineer
1. **Validity first.** Did the run reach the target load? `dropped_iterations > 0` or flat throughput while VUs climb means the target rate was not reached (SUT saturated or generator limited). Check the smoke and the error types before trusting the latency numbers.
2. **Verdict per SLA.** Per scenario, report p95/p99 against SLA and error rate against budget. Use percentiles, never averages alone.
3. **Locate.** Find the slowest transactions (steps) and how far they exceed their SLA. Use the network breakdown: waiting ≫ others means server-side; blocked/connecting means connection handling.
4. **Shape.** Use the dashboard time series: does latency climb with load (capacity), step up suddenly (resource limit or pool exhaustion), or drift up during soak (leak)? Do errors start at a specific load level (the knee point)?
5. **Compare.** Check against the baseline: regressions above 10% p95 or +0.5pp errors.
6. **Hypothesise.** Name concrete suspects and the server-side evidence needed: DB slow-query log, connection-pool metrics, GC logs, CPU/memory by tier, downstream latency. Mark these as hypotheses until correlated with server metrics.

## Record findings in the report (always)
Write `projects/<name>/runs/<run_id>/findings.yaml`, then rebuild with `python -m perfagent report <name> <run_id>`. The findings appear as the **Analyst findings** table at the top of report.html and in report.md:
```yaml
findings:
  - title: Server resets idle keep-alive connections
    severity: Medium            # Critical | High | Medium | Low | Info (docs/PERF_STRATEGY.md §9)
    status: Open - fix proposed # or: Hypothesis - needs confirmation / Expected behaviour / Fixed in <run>
    detail: One or two sentences on what happens and why
    evidence:                   # point at report sections, the Errors table, dashboard charts, logs
      - "Errors table: 4x POST /api/auth/logout - Connection reset by server"
    recommendation: Concrete fix or next diagnostic step
```
The **Errors** table (transport and script errors parsed from k6.log) and the **Failed checks** table (HTTP status failures) are the evidence for error findings.

## Communicate
- To the user: verdict, 3-5 key findings, the bottleneck hypothesis, and the recommended next action (tune, retest, proceed to the next test type, or go/no-go).
- For stakeholders: share `report.html` or write `runs/<run_id>/summary_for_stakeholders.md` (publish as a Claude Artifact if available). Use defect-style entries for breaches: scenario, observed vs expected, load level, evidence, severity.
- Recommend a re-run to confirm repeatability before declaring a pass or fail on a borderline result (within ±10% of the SLA).
