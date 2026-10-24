---
name: perf-frontend-audit
description: Run and interpret Lighthouse front-end audits with perfagent (Playwright logs in, Lighthouse audits pages for a single user, no load) - lab Core Web Vitals, performance budgets, concrete fixes, and comparison with load-test browser vitals. Use when the user asks about page speed, Lighthouse, Core Web Vitals, front-end performance, performance budgets, or why a page is slow for one user.
argument-hint: "<project> [--page NAME] [--form-factor mobile|desktop|both]"
---

# perf-frontend-audit: why is this page slow for one user?

Complements load testing. Lighthouse measures a single user on a quiet system with fixed emulation and explains *why* a page is slow (unused or unminified JavaScript, image weight, render-blocking resources, layout shift). It says nothing about capacity: use load tests for that.

## 1. Configure (`projects/<name>/project.yaml`)
```yaml
audit:
  login_scenario: SC-010        # catalog scenario whose steps log in (web/compute/api steps; partial PIN works)
  pages:
    - { name: Login page, url: /login, auth: false }
    - { name: Dashboard, url: /dashboard, auth: true }   # auth: true = audited with the saved login session
  form_factors: [mobile, desktop]
  runs: 3                       # median of N - Lighthouse varies 5-10 points run to run
  budgets: { performance: 80, lcp_ms: 2500, fcp_ms: 1800, tbt_ms: 200, cls: 0.1 }   # Google "good" thresholds
```
Pick the pages users actually land on (entry, key journey pages, the heaviest page). Prerequisites: `npm install` once (Lighthouse, Node 22.19+) and the Playwright Chromium.

## 2. Run
`python -m perfagent audit <name>` (options: `--runs N`, `--form-factor mobile|desktop|both`, `--page <text>`, `--headed`).
- It's single-user traffic, but still only against hosts in `safety.allowed_hosts`.
- Exit code 0 = all budgets met, 1 = a budget failed or pages couldn't be audited.
- Output: `runs/<id>_audit/report.html`, plus a full Lighthouse HTML report per page and device in `lighthouse/`.

## 3. Interpret
1. **Validity first:** a "Redirected to login" flag or errors mean the login replay failed. Fix `login_scenario` (smoke-test it with k6 first).
2. **Gate on metrics, not the score:** LCP, FCP, TBT and CLS against budgets. The score's weighting changes between Lighthouse versions.
3. **Mobile vs desktop:** a big gap usually means heavy JavaScript (CPU throttling on mobile). Check TBT and "Reduce unused JavaScript".
4. **Top opportunities** give estimated ms and KB savings. Lead with the largest, and name the concrete fix (code-splitting, minification, image formats and sizes, preloading the LCP image, reserving space to stop layout shift).
5. **Dev vs production builds:** dev servers (Vite, webpack dev) serve unbundled, unminified code. Audit a production build before raising findings about bundle size.
6. **Compare with load:** the report shows the latest load run's browser vitals. Similar values in both = front-end issue. Fine in the audit but poor under load = server-side degradation.
7. Record findings like the perf-results skill does (severity, evidence, recommendation) and share the report.
