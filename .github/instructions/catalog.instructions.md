---
name: Scenario catalog
description: Rules for editing perfagent scenario catalogs (catalog.yaml) - schema, scoring, steps, correlation.
applyTo: "projects/**/catalog.yaml"
---

# Editing `catalog.yaml`

- Full schema and scoring rubric: `.claude/skills/perf-discover/SKILL.md`. Run `python -m perfagent catalog <project> score` after every edit. It recomputes `priority_score`, `priority` and `rank` (never hand-edit those three) and prints validation problems.
- Keep `id` stable (`SC-001`...). New scenarios take the next free number.
- `status` changes (`approved` / `rejected` / `deferred`) only on an explicit human decision. Record it via `catalog <project> approve|reject|defer IDs --note "..."`.
- Ratings `business_criticality`, `frequency`, `technical_risk` are integers 1-5, each justified in `rationale`.
- `workload_share` of approved `api` scenarios should total 100. `web` scenarios use browser VUs and have no share.
- Steps:
  - api: `method`, `path` (OpenAPI form, `{param}` filled from vars), optional `query`, `headers`, `body`, `expect` (status list), `extract` (`var: dot.path`, e.g. `0.id`, `data.token`), `think_time` seconds.
  - web: `kind: web`, `action` in goto|click|fill|select|press|waitFor|assertText, plus `url`/`selector`/`value`/`key`; `navigates: true` for clicks that load a page.
  - Placeholders: `{{var}}` (data or extracted), `{{env.NAME}}` (secrets), `{{$uuid}} {{$randomInt}} {{$timestamp}} {{$randomString}} {{$vu}} {{$iter}}`.
- **Challenge-based logins and dynamic values (partial PIN, CSRF, OTP)**
- **extract** sources: `"a.b.0"` (JSON path) · `{ from: json, path: items, random: true, field: id }` · `{ from: header, name: Location }` · `{ from: cookie, name: sid }` · `{ from: regex, pattern: 'name="csrf" value="([^"]+)"', all: false }` (group 1; `all: true` returns a list).
- **compute** (on any step, or a `kind: compute` step): `pinDigits: { fn: partialPin, args: ["{{pin}}", "{{positions}}"] }`. Built-ins: `partialPin` (positions may be a list or text like "2nd, 5th and 7th"), `chars`, `pick`, `randomItem`, `join`, `upper`, `lower`, `concat`. Project-specific helpers go in `projects/<name>/hooks.js` (`export function x(...)`).
- **Dotted variables**: `{{pinDigits.0}}`, `{{user.id}}`.
- **Form posts**: `form: { field: value }` sends `application/x-www-form-urlencoded`. Redirects are followed (set `follow_redirects: false` to stop). `assert_text: Welcome` checks the body.
- **Browser** `action: extract` with `selector`, `var`, optional `attr`, `pattern`, `all`: reads on-page text, such as which PIN digits are requested.
- **`data_mode: unique-per-vu`** keeps one data row (account) per virtual user. Use it for logins that lock out, or that kick out concurrent sessions. You need at least as many rows as peak VUs.
- **Secret test data** (PINs, passwords) goes in `data/*.secret.csv`, which git ignores.
- Worked example: `projects/partial-pin-example` (API, HTML-form and browser variants, with a mock bank in `setup/mock_bank.py`).
- Step `name`s become transaction names in reports. Keep them short and business-meaningful, without `:{},` characters.
- Never put credentials in the catalog. Use `{{env.*}}` or a CSV in `projects/<project>/data/` referenced by `data_file`.
- Exclude destructive operations (DELETE, real payments, emails/SMS) unless stubbed. List them under top-level `excluded:` with the reason.
