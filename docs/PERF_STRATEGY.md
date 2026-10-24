# Performance Engineering Strategy

The standards perfagent follows and enforces. Use this as the team's reference when reviewing plans and results.

## 1. Lifecycle
1. **Requirements**: NFRs agreed with business owners (SLAs, peak volumes, growth).
2. **Discovery and scenario selection**: risk-based. Not every endpoint, only journeys that matter.
3. **Workload modelling**: traffic mix, arrival rates, think time and data, taken from production analytics where possible.
4. **Scripting**: correlation, parameterisation, checks, transaction naming.
5. **Environment readiness**: production-like, monitored, isolated window.
6. **Execution**: smoke → load (baseline) → stress → spike → soak → breakpoint.
7. **Analysis**: percentiles, saturation, server-side correlation.
8. **Tuning and retest**: one change at a time, compared with the baseline.
9. **Reporting and sign-off**: go/no-go against exit criteria.
10. **Continuous**: smoke and short load tests in CI on every release, with regressions flagged against the baseline.

## 2. Scenario prioritisation (risk-based)
Score = 40% business criticality + 35% frequency + 25% technical risk (each 1-5) → 0-100.
P1 ≥ 75 must be tested; P2 ≥ 50 should be tested; P3 only if capacity allows. Humans approve the final scope.
Aim for the scenarios that together make up about 80% of production load plus every revenue or regulatory path, typically 5-10 scenarios.

## 3. NFR template
| Attribute | Example target |
|---|---|
| Response time | p95 < 1s, p99 < 2s per transaction (UI page: LCP p75 < 2.5s) |
| Throughput | 6,000 orders/hour at peak |
| Concurrency | 1,000 concurrent active users |
| Error rate | < 1% overall, < 0.5% on payment |
| Resource headroom | CPU < 70%, memory stable, no pool exhaustion at peak |
| Scalability | Linear to 2× peak with horizontal scaling |
| Stability | No degradation over 4-8h at 80% peak |

## 4. Test types
| Type | Load | Question answered |
|---|---|---|
| Smoke | 1 VU, few iterations | Do scripts, data and environment work? |
| Load | 100% peak, sustained | Are SLAs met at expected peak? (baseline) |
| Stress | Steps 50→200% | Where does it degrade and how does it fail? |
| Spike | Jump to 300% and back | Does it survive surges and recover? |
| Soak / endurance | 80% for hours | Leaks, creep, pool exhaustion? |
| Breakpoint / capacity | Ramp until failure | What is maximum capacity? |

## 5. Workload modelling
- **Open model** (arrival rate) for internet-facing systems: users arrive whether or not the system is slow. A closed model (fixed VUs) under-reports latency because of coordinated omission.
- **Little's Law**: N = X × (R + Z). Concurrent users = throughput × (response time + think time).
- Think time comes from real user behaviour and is randomised ±30%. Pacing is controlled by the arrival rate.
- **Hybrid**: protocol VUs generate the load, and a few browser VUs measure what users experience.
- Scaled environments: run at `--scale` = environment capacity ÷ production capacity and state the factor in reports.

## 6. Scripting standards
- Name transactions consistently (`SC-001 02 View item`). Group dynamic URLs under those names to avoid metric-cardinality explosion.
- Correlate dynamic values (IDs, tokens); never hard-code them.
- Use parameterised, unique test data per VU for writes and logins.
- Check every request (status plus key content), and stop the iteration when a prerequisite step fails.
- Keep secrets in env vars only.
- Make SLAs into thresholds so the run fails automatically.

## 7. Monitoring and analysis
- Client side: throughput, p90/p95/p99, error rate by type, dropped iterations, load-generator CPU (< 80%).
- Server side: **USE** (Utilisation, Saturation, Errors) for CPU, memory, disk, network, pools; **RED** (Rate, Errors, Duration) for services. Also DB slow queries, locks, GC and cache hit ratio.
- Tag every run (`testid`) so client and server timelines can be correlated.
- Never sign off on averages. Check that the test was valid (target load reached) before judging latency.
- Repeat runs: results within ±10% are repeatable; otherwise investigate environment noise.

## 8. Entry and exit criteria
**Entry:** NFRs signed off · stable build · production-like environment · test data loaded · monitoring live · stakeholders notified · smoke passes.
**Exit:** all planned tests run · P1 SLAs met at 100% load · no open critical or high perf defects (or risk accepted) · soak stable · report reviewed and go/no-go recorded.

## 9. Performance defect template
Scenario/transaction · test type and load level · observed vs expected (p95, errors) · first occurrence time · evidence (report, dashboard, server metrics) · suspected component · severity (Critical: SLA breach at ≤100% load on P1; High: breach on P2 or at <150% stress; Medium: degradation trend; Low: optimisation).

## 10. Governance
- Production testing only with written approval, a change ticket, an agreed window and a rollback/abort plan.
- Third-party endpoints are stubbed unless the provider agrees.
- Results, scripts and plans are versioned with the application release.
