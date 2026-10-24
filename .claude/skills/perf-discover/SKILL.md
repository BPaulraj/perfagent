---
name: perf-discover
description: Explore an application (web URL and/or Swagger/OpenAPI) with perfagent, infer business scenarios, rank them with the risk-based scoring model and present them for human approval. Use when the user gives an app URL or API spec for performance testing, or asks to find, analyze, rank or review perf test scenarios.
argument-hint: "<project> <web-url> <openapi-url>"
---

# perf-discover: app → ranked scenario catalog → human approval

You are acting as the **performance test analyst/manager**. The output of this skill is `projects/<name>/catalog.yaml` plus a review page, then a **stop for human confirmation**. Never design or run load before scenarios are approved.

## 1. Set up the project
- If `projects/<name>/project.yaml` doesn't exist: `python -m perfagent init <name> --web-url <url> --api-url <api base> --openapi <spec>`.
- Ask the user (once, briefly) for anything you cannot infer and that changes the result: auth method and credentials as **env var names** (never put secrets in files), peak concurrent users, and any known SLAs. Use defaults if they don't know, and record them as assumptions.
- Confirm `safety.allowed_hosts` lists only hosts the user is authorised to test.

## 2. Discover
If access logs, APM or analytics exist, also follow **perf-workload-from-logs** so frequency and share come from real traffic.
`python -m perfagent discover <name>`, which writes `model/api_model.json` (OpenAPI) and `model/web_model.json` (crawl).
If the crawl needs login, configure `crawl.login` in project.yaml. If Playwright is missing, run `pip install playwright && python -m playwright install chromium`.

## 3. Analyze (your reasoning; this is the agent's core job)
Read both models. Work out the **business journeys**, not single endpoints:
- Use resources, tags and operation summaries to identify the domain (e-commerce, banking, booking, CMS...).
- Chain endpoints into flows using `id_providers` and `response_fields`, e.g. list → view detail → create → pay.
- Cross-reference `web_model.api_calls` (the XHR calls each page makes) with OpenAPI endpoints. This shows what a real page load costs in backend calls.
- Typical candidates: login/session, landing/home, browse/search/filter, view detail, create/submit (cart, order, booking, form), payment/checkout, profile/account, reporting/export, file upload, admin bulk operations.
- Use one **web** scenario (real browser) for each critical UI journey, to measure Core Web Vitals. Put most load on **api** scenarios.
- Flag destructive operations (DELETE, payments to real gateways, emails/SMS). Exclude them or mark them as needing stubs.

### Scoring rubric (each factor 1-5)
| Score | business_criticality | frequency (share of traffic) | technical_risk |
|---|---|---|---|
| 5 | Revenue/regulatory; outage = incident (login, checkout, payment) | Every session, very high volume (home, search, list) | Heavy queries, writes with locks, 3rd-party calls, file/report generation, no caching |
| 4 | Core user value | Most sessions | Multi-step writes, large payloads, complex joins |
| 3 | Important, has workaround | Regular | Moderate compute, some writes |
| 2 | Secondary | Occasional | Simple reads with caching |
| 1 | Nice to have / admin | Rare | Static / trivial |

Score = 40% criticality + 35% frequency + 25% risk, scaled to 100. P1 ≥ 75, P2 ≥ 50, P3 < 50 (computed by `catalog score`; don't hand-compute).
Set `workload_share` (%) across api scenarios to reflect the realistic traffic mix (it should sum to 100). Base it on analytics if the user has them. Otherwise use the classic read-heavy pattern (about 60-80% browse/search, 10-30% transactional).

## 4. Write `projects/<name>/catalog.yaml`
```yaml
project: <name>
assumptions:
  - "No production analytics provided; traffic mix estimated from domain norms"
scenarios:
  - id: SC-001                      # stable ids; never renumber after review
    name: Browse and search catalogue
    type: api                       # api | web  (web = real browser; may include api steps too)
    description: Anonymous user lists items by category, searches, opens a detail page
    rationale: Highest-volume journey; search hits the DB without cache → high risk
    business_criticality: 4
    frequency: 5
    technical_risk: 4
    workload_share: 45              # % of api load (approved api scenarios should total 100)
    sla: { p95_ms: 800, p99_ms: 1500, error_rate: 0.01 }   # omit to use project defaults
    data_requirements: ["search terms CSV"]
    data_file: search_terms.csv     # optional: projects/<name>/data/<file>, columns become {{vars}}
    risks: ["search endpoint has no pagination limit"]
    status: proposed
    steps:
      - name: List items             # becomes the transaction name in reports
        kind: api
        method: GET
        path: /items
        query: { category: "{{category}}", limit: 20 }
        extract: { itemId: "0.id" }  # dot path into JSON response → ctx var (correlation)
        expect: [200]
        think_time: 3                # seconds, randomised ±30%
      - name: View item
        method: GET
        path: /items/{itemId}        # OpenAPI-style path param filled from vars
        think_time: 5
      - name: Create order
        method: POST
        path: /orders
        body: { itemId: "{{itemId}}", qty: 1, ref: "{{$uuid}}" }
        expect: [200, 201]
  - id: SC-005
    name: Home → search → product page (browser)
    type: web
    ...
    steps:
      - { name: Open home, kind: web, action: goto, url: /, think_time: 2 }
      - { name: Type search, kind: web, action: fill, selector: "#search", value: "{{term}}" }
      - { name: Submit search, kind: web, action: press, selector: "#search", key: Enter, think_time: 3 }
      - { name: Results visible, kind: web, action: waitFor, selector: ".result-item" }
```
### Challenge-based logins and dynamic values (partial PIN, CSRF, OTP)
- **extract** sources: `"a.b.0"` (JSON path) · `{ from: json, path: items, random: true, field: id }` · `{ from: header, name: Location }` · `{ from: cookie, name: sid }` · `{ from: regex, pattern: 'name="csrf" value="([^"]+)"', all: false }` (group 1; `all: true` returns a list).
- **compute** (on any step, or a `kind: compute` step): `pinDigits: { fn: partialPin, args: ["{{pin}}", "{{positions}}"] }`. Built-ins: `partialPin` (positions may be a list or text like "2nd, 5th and 7th"), `chars`, `pick`, `randomItem`, `join`, `upper`, `lower`, `concat`. Project-specific helpers go in `projects/<name>/hooks.js` (`export function x(...)`).
- **Dotted variables**: `{{pinDigits.0}}`, `{{user.id}}`.
- **Form posts**: `form: { field: value }` sends `application/x-www-form-urlencoded`. Redirects are followed (set `follow_redirects: false` to stop). `assert_text: Welcome` checks the body.
- **Browser** `action: extract` with `selector`, `var`, optional `attr`, `pattern`, `all`: reads on-page text, such as which PIN digits are requested.
- **`data_mode: unique-per-vu`** keeps one data row (account) per virtual user. Use it for logins that lock out, or that kick out concurrent sessions. You need at least as many rows as peak VUs.
- **Secret test data** (PINs, passwords) goes in `data/*.secret.csv`, which git ignores.
- Worked example: `projects/partial-pin-example` (API, HTML-form and browser variants, with a mock bank in `setup/mock_bank.py`).

Templating: `{{var}}` (data/extracted), `{{env.NAME}}` (secrets), `{{$uuid}} {{$randomInt}} {{$timestamp}} {{$randomString}} {{$vu}} {{$iter}}`.
Web actions: goto, click (`navigates: true` if it loads a page), fill, select, press, waitFor, assertText.
Prefer selectors found in `web_model.json` (ids, names, data-testid).

## 5. Score, render, present
1. `python -m perfagent catalog <name> review` scores, ranks, validates and writes `review/scenario_review.html`. Fix any validation messages.
2. Point the user to the review page so they can open it in a browser. If your tool can publish pages (for example Claude Artifacts), offer to share it with stakeholders.
3. In chat, present a **compact ranked table** (rank, id, name, priority, score, type, share, one-line rationale), the key assumptions, endpoint coverage, and what you deliberately excluded and why.
4. Give your recommendation (normally: approve all P1, plus P2s that cover distinct risk), then **stop and ask the user to confirm**. They can approve, reject, re-rate, change shares or SLAs, or add scenarios.
5. Apply their decisions: `python -m perfagent catalog <name> approve SC-001 SC-003 --note "..."`, `reject`, `defer`, or edit the YAML and run `catalog score`.

After approval, continue with the **perf-design** skill.
