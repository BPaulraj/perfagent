# perfagent: performance test agent framework

Give it a **web URL** and/or an **OpenAPI/Swagger spec** for an application written in any language. It explores the app, proposes ranked business scenarios for **your approval**, designs a realistic workload, generates **k6** scripts (API and real browser), runs them and reports against your SLAs. It also runs **Lighthouse front-end audits**, which show why a page is slow for a single user, alongside the load tests.

It works with an AI assistant (**GitHub Copilot** or **Claude Code**) doing the analysis, or entirely by hand. The generated test scripts run with **k6 alone**, with no AI and no Python needed.

```
 discover ──► analyze & rank ──► HUMAN REVIEW ──► design ──► generate ──► smoke ──► load/stress/spike/soak ──► report
 (OpenAPI +    (AI assistant      (approve /       (Little's   (k6 API +    (gate)    (open workload model,        (SLA verdict,
  crawler)      or you)            re-rate)         Law plan)   browser)               thresholds = SLAs)           findings, baseline)
```

## Contents
1. [Prerequisites and installation](#1-prerequisites-and-installation)
2. [Verify the install: 5-minute first run](#2-verify-the-install-5-minute-first-run)
3. [Test your own application](#3-test-your-own-application)
4. [Configure a project (`project.yaml`, secrets, test data)](#4-configure-a-project)
5. [Scenarios without AI (`catalog.yaml`)](#5-scenarios-without-ai-catalogyaml)
6. [Test types and running tests](#6-test-types-and-running-tests)
7. [Reading the results](#7-reading-the-results)
8. [Run the scripts standalone (k6 only)](#8-run-the-scripts-standalone-k6-only) · [Front-end audits (Lighthouse)](#front-end-audits-lighthouse)
9. [CI/CD](#9-cicd)
10. [AI assistants: GitHub Copilot and Claude Code](#10-ai-assistants-github-copilot-and-claude-code)
11. [Command reference](#11-command-reference)
12. [Troubleshooting](#12-troubleshooting)
13. [Repository layout and safety](#13-repository-layout-and-safety)

---

## 1. Prerequisites and installation
| Need | Version | Notes |
|---|---|---|
| Python | 3.11+ | Windows, macOS or Linux |
| k6 | 1.x / 2.x | The load test engine, including browser support (Chromium) |
| Chromium for Playwright | - | Used only by web discovery (the crawler) |
| Node.js | 22.19+ | Only for front-end audits (Lighthouse). Install once: `npm install` |
| Docker | optional | Only for the Grafana dashboards |
| GitHub Copilot or Claude Code | optional | For AI-driven analysis (see §10) |

```powershell
pip install -r requirements.txt
python -m playwright install chromium
npm install                                # Lighthouse, for `audit` (optional)

# k6 - pick one:
winget install k6 --source winget          # Windows (system-wide)
brew install k6                            # macOS
# or download a portable binary from https://github.com/grafana/k6/releases into tools/ (tools/k6.exe or tools/k6)
k6 version
```
perfagent looks for k6 in `K6_PATH`, then on your `PATH`, then in `tools/` (git-ignored, so a fresh clone doesn't include it).

## 2. Verify the install: 5-minute first run
`projects/petstore-demo` is a ready-made example against the public Swagger Petstore demo. It's a shared public service, so run **smoke only** against it.
```powershell
python -m perfagent generate petstore-demo
python -m perfagent run petstore-demo --type smoke
```
You should see `SMOKE test - PASS` and a path to `projects/petstore-demo/runs/<run_id>/report.html`. Open that file in a browser.

`projects/demo-app` is a second worked example for an app with **no published spec**, cookie login, seeded test data and a full load test with analyst findings.

## 3. Test your own application
**Before you start:** only test systems you're authorised to load test, and agree a time window for shared environments.

### Option A: with an AI assistant (recommended)
Open the repo in VS Code with Copilot (Agent mode) or in Claude Code, and say:
> *Performance test https://test.my.app with spec https://test.my.app/v3/api-docs*

or use `/perf-start`. The assistant creates the project, discovers the app, proposes ranked scenarios and **stops for your approval**. Then:
`/perf-approve approve all P1 and SC-006` → `/perf-run load` → `/perf-report`.

### Option B: by hand
```powershell
python -m perfagent init shop --web-url https://test.shop.local --api-url https://test.shop.local/api --openapi https://test.shop.local/v3/api-docs
#   edit projects/shop/project.yaml (auth, nfr, allowed hosts) - see §4
python -m perfagent discover shop                 # -> model/api_model.json, model/web_model.json
python -m perfagent traffic shop access.log       # optional: real endpoint mix + peak rates from logs
#   write projects/shop/catalog.yaml - see §5 (or let the AI do it)
python -m perfagent catalog shop review           # ranked table + review/scenario_review.html
python -m perfagent catalog shop approve --priority P1
python -m perfagent design shop                   # -> design/workload.json + design/test_plan.md
python -m perfagent generate shop                 # -> scripts/ (k6)
python -m perfagent run shop --type smoke         # must PASS before anything heavier
python -m perfagent run shop --type load
python -m perfagent baseline shop <run_id>        # mark a good run as the comparison baseline
```

### No Swagger/OpenAPI spec?
- Run discovery with the web URL only (`--web-url`). The crawler records the API calls each page makes.
- Or write a small OpenAPI file for the endpoints you care about (the AI can reverse-engineer one from the source code, as in `projects/demo-app/model/server-openapi.yaml`) and set `target.openapi` to that file.

## 4. Configure a project
Everything for one application lives in `projects/<name>/`. The main file is **`project.yaml`**:

| Section | What to set |
|---|---|
| `environment` | `test`/`staging`/`uat`. `production` refuses to run without `--allow-production` |
| `target` | `web_url`, `api_base_url` (what the scripts call), `openapi` (URL or file) |
| `safety.allowed_hosts` | **Required.** Hosts you may send load to. Every other host is refused |
| `auth.type` | Multi-step or challenge logins (partial PIN, CSRF, OTP) are written as scenario steps - see §5. `none` · `bearer` (token from `token_env`) · `apikey` (header + `apikey_env`) · `basic` · `login` (done once in setup, token extracted from the JSON response) · cookie sessions work automatically (k6 keeps a cookie jar per virtual user; put a login step in each scenario) |
| `crawl` | Page limits, exclude patterns, optional form `login` for crawling behind authentication |
| `nfr` | `peak_concurrent_users`, `browser_vus`, `default_sla` (p95, p99, error rate, LCP), `est_response_s` |
| `test_profiles` | Optional overrides of the load shapes (see §6) |
| `monitoring.influxdb_url` | Used with `run --influx` |

**Secrets (passwords, tokens):** never put them in YAML, CSV or scripts. Reference them as `{{env.NAME}}` and supply them as environment variables, or in a git-ignored `projects/<name>/.env` file:
```
PERF_PASSWORD=...
PERF_TOKEN=...
```
The runner loads `.env` automatically. Real environment variables take precedence.

**Test data:** put CSV files in `projects/<name>/data/` (header row = variable names, e.g. `email,search_term`) and reference them from a scenario with `data_file: users.csv`. Columns become `{{email}}` etc. For apps that need pre-existing users or records, write a seed script (example: `projects/demo-app/setup/seed_test_data.py`) and run it once before testing.

## 5. Scenarios without AI (`catalog.yaml`)
`projects/<name>/catalog.yaml` lists the business scenarios. The AI normally writes it, but it's plain YAML:
```yaml
scenarios:
  - id: SC-001
    name: Browse and search catalogue
    type: api                       # api | web (real browser)
    business_criticality: 4         # 1-5  } priority score = 40% criticality
    frequency: 5                    # 1-5  }   + 35% frequency + 25% risk -> P1/P2/P3
    technical_risk: 4               # 1-5  }
    workload_share: 45              # % of API load (approved api scenarios total 100)
    sla: { p95_ms: 800, p99_ms: 1500, error_rate: 0.01 }   # optional, defaults from project.yaml
    data_file: users.csv            # optional
    rationale: Why it matters
    steps:
      - { name: Login, method: POST, path: /auth/login, body: { email: "{{email}}", password: "{{env.PERF_PASSWORD}}" }, expect: [200] }
      - { name: List items, method: GET, path: /items, query: { q: "{{search_term}}" }, extract: { itemId: "0.id" }, think_time: 3 }
      - { name: View item, method: GET, path: "/items/{itemId}", think_time: 5 }
  - id: SC-002
    name: Home page (browser)
    type: web
    business_criticality: 4
    frequency: 4
    technical_risk: 2
    steps:
      - { name: Open home, kind: web, action: goto, url: / }
      - { name: Results visible, kind: web, action: waitFor, selector: ".result" }
```
- **Placeholders:** `{{var}}` (from CSV or `extract`), `{{env.NAME}}`, `{{$uuid}} {{$randomInt}} {{$timestamp}} {{$randomString}} {{$vu}} {{$iter}}`.
- **Web actions:** `goto`, `click`, `fill`, `select`, `press`, `waitFor`, `assertText`.

Run `python -m perfagent catalog <name> score` after editing. Full schema and rubric: `.claude/skills/perf-discover/SKILL.md`.

#### Challenge-based logins and dynamic values (partial PIN, CSRF, OTP)
- **extract** sources: `"a.b.0"` (JSON path) · `{ from: json, path: items, random: true, field: id }` · `{ from: header, name: Location }` · `{ from: cookie, name: sid }` · `{ from: regex, pattern: 'name="csrf" value="([^"]+)"', all: false }` (group 1; `all: true` returns a list).
- **compute** (on any step, or a `kind: compute` step): `pinDigits: { fn: partialPin, args: ["{{pin}}", "{{positions}}"] }`. Built-ins: `partialPin` (positions may be a list or text like "2nd, 5th and 7th"), `chars`, `pick`, `randomItem`, `join`, `upper`, `lower`, `concat`. Project-specific helpers go in `projects/<name>/hooks.js` (`export function x(...)`).
- **Dotted variables**: `{{pinDigits.0}}`, `{{user.id}}`.
- **Form posts**: `form: { field: value }` sends `application/x-www-form-urlencoded`. Redirects are followed (set `follow_redirects: false` to stop). `assert_text: Welcome` checks the body.
- **Browser** `action: extract` with `selector`, `var`, optional `attr`, `pattern`, `all`: reads on-page text, such as which PIN digits are requested.
- **`data_mode: unique-per-vu`** keeps one data row (account) per virtual user. Use it for logins that lock out, or that kick out concurrent sessions. You need at least as many rows as peak VUs.
- **Secret test data** (PINs, passwords) goes in `data/*.secret.csv`, which git ignores.
- Worked example: `projects/partial-pin-example` (API, HTML-form and browser variants, with a mock bank in `setup/mock_bank.py`).


## 6. Test types and running tests
| Type | Load (× designed peak) | Default duration | Purpose |
|---|---|---|---|
| `smoke` | 1 user, 2 iterations | ~1 min | Scripts, data and environment work. **Run first, always** |
| `load` | ramp to 100%, hold | 27 min | SLAs at expected peak. Becomes the baseline |
| `stress` | 50% → 100% → 150% → 200% | 31 min | Where it degrades and how it fails |
| `spike` | 30% → 300% → 30% | 12 min | Surge survival and recovery |
| `soak` | 80% held | 4 h 10 min | Leaks, resource creep |
| `breakpoint` | ramp to 500%, stops on errors | 30 min | Maximum capacity |

Useful options:
- `--scale 0.25`: run at a quarter of the designed load (smaller environment).
- `--dry-run`: show the k6 command without running it.
- `--headed`: watch the browser scenarios.
- `--influx`: stream metrics to Grafana.
- `-e KEY=VALUE`: pass extra variables to k6.

Change the user count in `project.yaml` (`nfr.peak_concurrent_users`) or durations in `test_profiles`, then run `design` and `generate` again.

**While a test runs**, the live dashboard is at http://localhost:5665. **Close that tab when the test ends**: k6 waits for connected dashboard clients before writing the summary and exiting.

## 7. Reading the results
Each run writes `projects/<name>/runs/<run_id>/`:

| File | What it is |
|---|---|
| **`report.html`** | Main report: open it in a browser |
| `dashboard.html` | k6 time-series charts (latency, throughput, users, errors over time) |
| `report.md` | Short text version (good for chat, PRs and CI summaries) |
| `findings.yaml` | Analyst findings (written by the AI's `perf-results` skill or by you) |
| `summary.json` · `k6.log` · `meta.json` | Raw metrics · k6 console log · run details |

**Sections of `report.html`, top to bottom:**
1. **Verdict and KPI tiles.** PASS only if every SLA threshold held. Tiles show throughput, p95/p99, error rate, peak virtual users and **dropped iterations** (above 0 means the planned load wasn't fully reached).
2. **Analyst findings.** Severity, status, evidence and recommendation for each issue. Rebuild the report after editing `findings.yaml`: `python -m perfagent report <name> <run_id>`.
3. **Observations.** Automatic checks: dropped iterations, long-tail latency, server-side versus connection time, errors.
4. **Scenarios vs SLA.** Per scenario: iterations, p90/p95/p99, errors, met or breached. Browser scenarios show LCP, FCP, TTFB and CLS.
5. **Errors.** Connection and script errors grouped from `k6.log`, such as "Connection reset by server".
6. **Transactions (steps).** Every step's timings, with a bar against its SLA. Shows *which* step is slow.
7. **Baseline comparison.** Shown once a baseline is set. Flags p95 regressions above 10% and error-rate increases above 0.5 percentage points.
8. **Thresholds**, **Network breakdown** (server wait versus connection and TLS) and **Failed checks** (HTTP status failures).

Sign off on **percentiles (p95/p99), not averages**. For a stakeholder summary, ask the assistant for `/perf-report`, which writes `summary_for_stakeholders.md`.

**Grafana (optional, for trends across runs):**
```powershell
docker compose -f monitoring/docker-compose.yml up -d     # http://localhost:3000
python -m perfagent run <name> --type load --influx
```

## 8. Run the scripts standalone (k6 only)
`projects/<name>/scripts/` is self-contained. Copy it to any machine or CI agent that has k6:
```powershell
k6 run main.js -e TEST_TYPE=load -e BASE_URL=https://test.my.app/api -e WEB_URL=https://test.my.app -e PERF_PASSWORD=<secret>
```
It prints a PASS/FAIL table per scenario and writes `summary.json`.
- **Exit code 0** means all SLAs were met; **99** means an SLA was breached.
- To tune the load without code changes, edit `workload.json`.

Each scripts folder has its own generated `README.md` listing the variables it needs.

### Front-end audits (Lighthouse)
Load tests answer *"does it cope with many users?"*. Audits answer *"why is this page slow for one user?"* Use both.

`python -m perfagent audit <name>` logs in with Playwright by **replaying a catalog browser scenario**, so partial PIN and other multi-step logins work. It saves the session, then runs **Lighthouse** on each configured page in a fresh browser with that session. Each page is run several times on mobile and desktop emulation, and the median is reported.
```yaml
# project.yaml
audit:
  login_scenario: SC-010
  pages:
    - { name: Login page, url: /login, auth: false }
    - { name: Dashboard, url: /dashboard, auth: true }
  form_factors: [mobile, desktop]
  runs: 3
  budgets: { performance: 80, lcp_ms: 2500, fcp_ms: 1800, tbt_ms: 200, cls: 0.1 }
```
`runs/<id>_audit/report.html` shows, per page and device:
- scores (performance, accessibility, best practices, SEO)
- LCP, FCP, TBT, CLS, Speed Index, TTFB and page weight
- budget pass/fail
- the **top improvement opportunities** with estimated savings
- a link to the full Lighthouse report
- the latest load run's browser vitals, for comparison

Exit code 1 if a budget fails. Options: `--runs`, `--form-factor mobile|desktop|both`, `--page`, `--headed`. Note that dev servers (Vite, webpack) serve unbundled code, so audit a production build before raising bundle-size findings.

## 9. CI/CD
No CI workflows are included, so you can add a pipeline that fits your organisation. The scripts are CI-ready:
- **Exit code 0** means all SLAs were met; **99** means an SLA was breached. Gate the pipeline on it.
- A minimal job: install k6, run `python -m perfagent generate <name>` and `python -m perfagent run <name> --type smoke` (or run the standalone scripts with k6 alone, §8), then publish `projects/<name>/runs/` as an artifact.
- Pass secrets (`PERF_PASSWORD`, `PERF_TOKEN`, ...) from your CI secret store as environment variables.
- Put heavier test types behind a manual approval step, the CI version of the human go-ahead.
- Runners must be able to reach the target. `localhost` and internal test environments need a self-hosted runner or agent inside your network.
- Run `python -m perfagent agents check` in a PR check to keep the Copilot and Claude files in step.
- GitHub note: pushing files under `.github/workflows/` with the GitHub CLI needs the `workflow` token scope (`gh auth refresh -s workflow`).

## 10. AI assistants: GitHub Copilot and Claude Code
Both get the same skills and rules, and each customisation has a single source of truth:

| Purpose | File(s) | Copilot | Claude Code |
|---|---|:-:|:-:|
| Shared operating guide | `AGENTS.md` | ✓ | ✓ via `CLAUDE.md` → `@AGENTS.md` |
| Copilot summary | `.github/copilot-instructions.md` | ✓ | - |
| **Skills** | `.claude/skills/<name>/SKILL.md` | ✓ | ✓ |
| Path-specific rules | `.github/instructions/*.instructions.md` → generated `.claude/rules/` | ✓ | ✓ |
| Slash prompts | `.github/prompts/*.prompt.md` → generated `.claude/commands/` | ✓ | ✓ |
| Role agents with handoffs | `.github/agents/*.agent.md` | ✓ | - |
| Terminal auto-approve (read-only commands) | `.vscode/settings.json` | ✓ | optional `.claude/settings.json` |

- **Skills:** `perf-discover` · `perf-workload-from-logs` · `perf-design` · `perf-smoke-debug` · `perf-results` · `perf-frontend-audit`
- **Prompts:** `/perf-start` · `/perf-approve` · `/perf-run` · `/perf-report`
- **Copilot agents:** `perf-analyst` → `perf-engineer` → `perf-results-analyst`

The assistant always **stops for your approval** of scenarios and **asks before every non-smoke run**.

Maintainers: after editing `.github/instructions` or `.github/prompts`, run `python -m perfagent agents sync`. Don't copy skills to `.github/skills`: Copilot reads `.claude/skills` too and would load them twice.

## 11. Command reference
| Command | Purpose |
|---|---|
| `init <name> [--web-url] [--api-url] [--openapi]` | Create `projects/<name>/` |
| `discover <name> [--api\|--web]` | Build the app model from OpenAPI and/or the crawler |
| `traffic <name> <log> [--format combined\|json]` | Endpoint mix and peak rates from access logs |
| `catalog <name> score\|status\|review` | Score, list, or render the review page |
| `catalog <name> approve\|reject\|defer <IDs\|all> [--priority P1] [--note ...]` | Record review decisions |
| `design <name>` | Workload model and test plan |
| `generate <name> [--force]` | k6 scripts (`--force` overwrites hand-edited scenario files) |
| `run <name> --type <t> [--scale] [--dry-run] [--headed] [--influx] [-e K=V] [--allow-production]` | Execute a test |
| `audit <name> [--runs] [--form-factor] [--page] [--headed]` | Lighthouse front-end audit (single user, no load) |
| `report <name> [run_id]` | Rebuild a run's report (default: the latest) |
| `baseline <name> <run_id>` | Set the comparison baseline |
| `runs <name>` | List runs |
| `agents check\|sync` | Validate or sync the Copilot and Claude files |

All commands are `python -m perfagent <command>`.

## 12. Troubleshooting
| Symptom | Fix |
|---|---|
| `k6 not found` | Install k6 (§1), or set `K6_PATH` to the binary |
| Test finished but the command doesn't return | Close the live dashboard tab (http://localhost:5665) |
| `Safety: host ... is not in safety.allowed_hosts` | Add the host to `project.yaml` only if you're authorised to test it |
| Smoke FAIL (4xx/5xx, failed `extract`, browser selector errors) | Check `k6.log` and the report's Failed checks and Errors tables. Ask the assistant to use the `perf-smoke-debug` skill, or see its table in `.claude/skills/perf-smoke-debug/SKILL.md` |
| `Playwright is required for web discovery` | `pip install playwright` then `python -m playwright install chromium` |
| `No approved scenarios` | Approve some: `catalog <name> approve ...` |
| `PERF_PASSWORD not set` / 401 errors | Put secrets in `projects/<name>/.env` or set them as environment variables |
| Dropped iterations > 0 | The target load wasn't reached: the app saturated, or the load generator lacked CPU. Run k6 on a separate machine for heavy tests |
| `Lighthouse not found` | `npm install` in the repo root (Node.js 22.19+) |
| Audit shows "Redirected to login" | The login replay failed or the session expired. Check `audit.login_scenario` and smoke-test that scenario first |
| Odd characters or crashes in the Windows console | Use Windows Terminal. perfagent forces UTF-8 output |

## 13. Repository layout and safety
```
perfagent/              framework: discovery, traffic, catalog, design, generator, runner, report, templates, k6lib
projects/<app>/         project.yaml, .env (git-ignored), model/, catalog.yaml, review/, design/, data/, setup/, scripts/, runs/
projects/petstore-demo  example: public API with spec (smoke only), includes logs/sample_access.log for `traffic`
projects/demo-app       example: local app without a spec, cookie login, seeded data, load run with findings
projects/partial-pin-example  example: two-step partial-PIN bank login (API, HTML forms, browser) + mock bank
.claude/skills/         AI skills (shared by Copilot and Claude)
.github/                Copilot instructions, path rules, prompts, agents
AGENTS.md / CLAUDE.md   AI operating guide
monitoring/             InfluxDB + Grafana with a provisioned dashboard
docs/PERF_STRATEGY.md   performance engineering standards (lifecycle, NFRs, workload modelling, entry/exit criteria, defect template)
tools/                  optional portable k6 (git-ignored)
```

**Safety built in:**
- Load only goes to `safety.allowed_hosts`.
- Production requires `--allow-production`.
- You're warned when no smoke run has passed.
- The crawler is read-only: it never submits forms and skips logout/delete links.
- Secrets come only from environment variables or the git-ignored `.env`.
- The AI must get your approval for scenarios and for every load run.
