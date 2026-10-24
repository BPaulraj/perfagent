---
name: perf-workload-from-logs
description: Build a realistic perf workload model from production access logs, APM or web analytics - endpoint mix, peak-hour and peak-minute rates, concurrency - and feed it into the perfagent scenario catalog and NFRs. Use when the user has logs/analytics, or asks how to size load or set the traffic mix.
argument-hint: "<project> <path to access log> [combined|json]"
---

# perf-workload-from-logs: real traffic → workload model

A workload model built from guesses is the most common reason perf tests mislead. Use real traffic whenever it exists.

## 1. Get the data (ask the user)
In order of preference:
1. **Access logs** for the busiest period (load balancer, Nginx/Apache, API gateway), covering at least the peak day and ideally a seasonal peak (sale, month end).
2. **APM** export (Datadog, New Relic, Dynatrace, App Insights): throughput per endpoint and transaction.
3. **Web analytics** (GA4 etc.): sessions per hour, page views per session, top journeys, average session duration.
4. **Business volumes**: orders per hour, logins per day, growth forecast.

Logs must be anonymised or trimmed before sharing. Never paste personal data into chat.

## 2. Profile the logs
```
python -m perfagent discover <project> --api          # spec first, so paths match OpenAPI templates
python -m perfagent traffic <project> <access.log> [--format combined|json]
```
This writes `model/traffic_profile.json`: request count, % matched to the spec, peak hour and peak minute rates, and per-endpoint share, error % and p95 (if logged).
A low match % usually means a different base path (`target.api_base_url`) or undocumented endpoints. Report those as a coverage finding.

## 3. Turn it into the model
- **Frequency rating** (1-5) and **workload_share**: sum the endpoint shares belonging to each scenario's steps, then normalise across scenarios. A scenario's share is driven by its *entry* step, not by every request.
- **Peak load**: design for the **peak hour**, and check that the **peak minute** (bursts) is covered by the spike test (spike = 300% by default; raise it if peak minute ÷ peak-hour average is higher).
- **Concurrency** (Little's Law): `concurrent users = sessions per hour × average session duration (h)`. Set `nfr.peak_concurrent_users`.
- **Think time**: average session duration ÷ pages (or calls) per session, per step.
- **Growth headroom**: multiply by the forecast growth (e.g. ×1.3 for 12 months) and state it.
- After `python -m perfagent design <project>`, compare **planned peak req/s** with the observed peak-hour average and peak minute that `design` prints. Explain any intentional difference (headroom, scaled environment).

## 4. Record it
Add to `catalog.yaml` `assumptions:` the data source, the time window, the growth factor and the derived numbers, so the test plan is auditable. Present the derived mix to the user for confirmation, together with the scenario ranking.
