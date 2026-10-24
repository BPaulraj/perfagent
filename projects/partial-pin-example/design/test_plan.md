# Performance Test Plan - partial-pin-example

| | |
|---|---|
| Version | 0.1 (draft - generated 2026-10-24T12:35:23+05:30) |
| Environment | test |
| Web URL | http://localhost:5180 |
| API base URL | http://localhost:5180 |
| Status | **Awaiting sign-off** |

## 1. Objectives
1. Verify the approved business scenarios meet their SLAs at the expected peak load (10 concurrent users).
2. Find the point where the system degrades and how it fails (stress / breakpoint).
3. Check that it recovers from sudden surges (spike) and stays stable over time (soak).
4. Record a baseline so later releases can be checked for regressions.

## 2. Scope
### In scope - approved scenarios
| Rank | ID | Scenario | Priority | Type | Share | Rationale |
|---:|---|---|---|---|---:|---|
| 1 | SC-001 | Partial-PIN login via API then view accounts | P1 (95) | api | 70.0% | Login gate for every customer session; challenge generation and PIN verification (HSM-style) are classic banking bottlenecks |
| 2 | SC-002 | Partial-PIN login via HTML forms (no browser) | P1 (88) | api | 30.0% | Same journey at protocol level for apps without a JSON login API - cheap to scale to many users |
| 3 | SC-003 | Partial-PIN login in a real browser | P1 (83) | web | browser | End-user experience of the login journey (Core Web Vitals, front-end timings) |

### Out of scope
- (none recorded)
- Third-party systems are stubbed or excluded unless the provider has agreed to the load.

## 3. Non-functional requirements (acceptance criteria)
| ID | Scenario | p95 (ms) | p99 (ms) | Error rate | LCP p75 (ms) |
|---|---|---:|---:|---:|---:|
| SC-001 | Partial-PIN login via API then view accounts | 1000 | 2000 | 1.0% | - |
| SC-002 | Partial-PIN login via HTML forms (no browser) | 1000 | 2000 | 1.0% | - |
| SC-003 | Partial-PIN login in a real browser | 1000 | 2000 | 1.0% | 2500 |

Global: overall error rate < 1.0%, functional checks > 99%. These are coded into the k6 thresholds, so a breach fails the run (exit code 99) and fails CI.

## 4. Workload model
Open model (arrival-rate executors): new iterations start at the planned rate whether or not the system slows down. A closed model would hide saturation through coordinated omission. Sizing uses Little's Law: **N = X × (R + Z)**.

| ID | Users at peak | Iteration time (s) (R+Z) | Arrival rate (iter/min) | Data file |
|---|---:|---:|---:|---|
| SC-001 | 7 | 18.5 | 22.7 | users.secret.csv |
| SC-002 | 3 | 12.9 | 13.95 | users.secret.csv |
| SC-003 | 1 | 26.0 | constant browser VUs | users.secret.csv |

Assumptions: server time 0.3s per request until a baseline exists; think time is randomised ±30%. **Replace these estimates with production analytics or access-log data where available**, then re-run `design`.
Hybrid model: protocol-level VUs generate the load, and 1 real-browser VU(s) measure what end users experience (Core Web Vitals).

## 5. Test types and schedule
| Order | Type | Purpose | Load profile | Duration |
|---:|---|---|---|---|
| 1 | smoke | Verify scripts, data and environment work (1 VU, few iterations). Gate before every other test. | 1 VU × 2 iterations | ~1m |
| 2 | load | Validate SLAs at expected peak load (100%) for a sustained period. | 100%/5m → 100%/20m → 0%/2m | 27m |
| 3 | stress | Step beyond peak (50% -> 200%) to find the degradation point and how the system fails. | 50%/2m → 50%/5m → 100%/2m → 100%/5m → 150%/2m → 150%/5m → 200%/2m → 200%/5m → 0%/3m | 31m |
| 4 | spike | Sudden surge to 300% of peak (sale launch, batch release) and recovery back to normal. | 30%/2m → 30%/2m → 300%/30s → 300%/3m → 30%/30s → 30%/3m → 0%/1m | 12m |
| 5 | soak | 80% of peak for hours to expose memory leaks, connection-pool exhaustion and resource creep. | 80%/5m → 80%/4h → 0%/5m | 4h10m |
| 6 | breakpoint | Linear ramp to 500% until the error-rate guard aborts - establishes maximum capacity. | 500%/30m | 30m |
| 7 | quick | Short check at 100% of peak - framework demos, not a sign-off test | 100%/20s → 100%/80s → 0%/10s | 1m50s |

Run order: smoke → load (record the baseline) → stress → spike → soak → breakpoint. Repeat each failed test after tuning. Run each test at least twice to confirm results are repeatable (within ±10%).

## 6. Entry criteria
- [ ] NFRs in section 3 agreed by business and engineering owners
- [ ] Test environment is production-like (topology, data volumes, configuration). Any scaling factor is documented (use `--scale`)
- [ ] Build under test is functionally stable and its version is recorded
- [ ] Test data is loaded and enough for the run (unique users and records)
- [ ] Monitoring is in place for app servers, DB, cache and queues (CPU, memory, GC, connection pools, slow queries)
- [ ] Stakeholders (ops, DBA, owners of third-party systems) notified of the test window
- [ ] Smoke test passes with 0 errors

## 7. Exit criteria
- [ ] All planned test types executed, or deviations documented
- [ ] All P1 scenarios meet their SLAs at 100% load
- [ ] No open critical or high performance defects, or they are risk-accepted by the product owner
- [ ] Soak shows no upward trend in memory, connections or response time
- [ ] Results report reviewed and go/no-go recorded

## 8. Monitoring and analysis
- Client side: k6 web dashboard (live), HTML report per run, Grafana + InfluxDB for trends across runs
- Server side: the **USE** method (Utilisation, Saturation, Errors) for resources and the **RED** method (Rate, Errors, Duration) for services. Correlate time windows using the run's `testid` tag
- Sign off on percentiles (p95/p99), not averages

## 9. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Environment differs from production | Document the differences and scale the load. Treat results as relative, not absolute |
| Test data runs out or collides | Unique data per VU via CSV. Reset data between runs |
| Load generator becomes the bottleneck | Watch k6 CPU (<80%) and dropped_iterations. Distribute the load if needed |
| Shared environment disturbs results | Book an exclusive test window |
| Third-party rate limits or cost | Stub or exclude, or get the provider's approval |

## 10. Roles (RACI)
| Activity | Perf engineer | Dev lead | Ops/SRE | Product owner |
|---|---|---|---|---|
| NFR definition | C | C | C | A/R |
| Scripting and execution | A/R | C | I | I |
| Environment and monitoring | C | C | A/R | I |
| Analysis and tuning | R | A/R | R | I |
| Go/no-go | R | C | C | A |

## 11. Deliverables
Approved scenario catalog · this plan · k6 scripts · per-run HTML reports and dashboards · final summary report with go/no-go recommendation.
