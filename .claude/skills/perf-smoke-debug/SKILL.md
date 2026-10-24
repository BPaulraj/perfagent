---
name: perf-smoke-debug
description: Diagnose and fix failing perfagent/k6 smoke tests - failed checks, HTTP 4xx/5xx, broken correlation/extraction, auth, test data, browser selector and timeout errors. Use when a smoke run FAILs or a script error appears.
argument-hint: "<project> [run-id]"
---

# perf-smoke-debug: make the smoke test green

The smoke test (1 VU, 2 iterations) exists to catch script, data and environment problems **before** load. Never move on to load with a failing smoke test, and never "fix" one by weakening `expect`, checks or thresholds.

## 1. Collect evidence
- `projects/<p>/runs/<run_id>/report.md`: which scenario and step failed, and the failed check names (`SC-00x 0y <step> -> 200`).
- `k6.log`: `level=warning` lines from browser steps, script errors, setup failures.
- Reproduce one request outside k6 with `curl -i` (Bash or PowerShell `curl.exe -i`) using the same method, URL, headers and body. Run it once only; this is a debug check, not load.

## 2. Classify and fix (in `catalog.yaml`, then `generate`)
| Symptom | Likely cause | Fix |
|---|---|---|
| 400 / 422 | Body or query doesn't match what the API really accepts (OpenAPI examples are placeholders) | Read the error body. Add required fields and valid enum values. Make unique fields unique with `{{$uuid}}` or `{{$randomString}}` |
| 401 / 403 | Auth missing, expired or under-privileged | Check `auth.type` in project.yaml and that the env vars are set in this shell. For `login`, check `token_path`. Use a test user with the right role |
| 404 on `/x/{id}` | Correlation failed or picked a stale ID | Check the `extract` path against the real response (`0.id` for arrays, `data.id` for wrappers). Extract from the create step instead of the list |
| 409 | Duplicate data | Unique values per VU and iteration, or a bigger CSV |
| 415 | Wrong content type | The API wants form or multipart data. Add `headers: {Content-Type: ...}` and the matching body |
| 429 | Rate limited | Ask the environment owner for an allow-list. Don't hammer it |
| 5xx | Server bug or invalid input crashing the server | Reproduce with curl. If valid input still gives 5xx, it's a **defect**: report it |
| `extract <var>` check failed | Response shape differs or is empty | Inspect the JSON. Adjust the path or ensure test data exists |
| Browser `strictmodeviolation` | Selector matches several elements | Use a more specific selector. `waitFor`/`assertText` already use the first match |
| Browser timeout | Selector wrong, page slow, or needs login | Check the selector in `model/web_model.json` or in a headed run: `run <p> --type smoke --headed` |
| `setup login failed` | Login endpoint, body or credentials | Fix `auth.login` and the env vars |
| 423 Locked / account locked | A failed login burned PIN attempts (wrong digits, wrong positions) | **Stop immediately.** Fix the compute/extract step, get the accounts unlocked, use `data_mode: unique-per-vu` |
| `compute ...: unknown function` or position outside secret | Bad `compute` block or wrong extracted positions | Check the `extract` pattern output and the helper name. Positions must be 1-based unless `base` is given |
| Script error (exit 107) | Invalid YAML-to-JS value, or a missing data file | Read the k6.log stack trace. Check `data_file` exists in `projects/<p>/data/` |

## 3. Re-verify
`python -m perfagent generate <p>` then `python -m perfagent run <p> --type smoke`, until the verdict is PASS, errors are 0% and checks are 100%. Summarise each fix for the user. Raise any genuine application defects found (smoke 5xx with valid input) as findings.
