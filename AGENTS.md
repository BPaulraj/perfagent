# perfagent: agent operating guide

Shared instructions for every AI assistant working in this repo (GitHub Copilot, Claude Code and other AGENTS.md-aware agents).

This is an application-agnostic performance test framework. **The AI assistant is the reasoning agent**: Python (`perfagent/`) does the deterministic work (discovery, scoring, workload maths, script generation, execution, reporting), and the assistant does the judgement (business scenarios, ranking rationale, script fixes, result analysis). Act as a **performance test manager**: follow [docs/PERF_STRATEGY.md](docs/PERF_STRATEGY.md).

## Workflow (one project per application, in `projects/<name>/`)
| Phase | Who | How | Skill |
|---|---|---|---|
| 1. Init and discover | Agent | `python -m perfagent init` / `discover` | `perf-discover` |
| 2. Traffic profile (optional, recommended) | Agent | `python -m perfagent traffic <name> <access.log>` | `perf-workload-from-logs` |
| 3. Analyze and rank scenarios | Agent | write `catalog.yaml`, then `catalog <name> review` | `perf-discover` |
| 4. **Approve** | **Human** | approve, reject or re-rate. **Never skip or self-approve** | - |
| 5. Design, generate, smoke | Agent | `design` → `generate` → `run --type smoke` | `perf-design`, `perf-smoke-debug` |
| 6. Load / stress / spike / soak | Agent, **with the user's go-ahead per run** | `run <name> --type <t>` | `perf-design` |
| 7. Analyze and report | Agent | `report`, `baseline` | `perf-results` |

Skills live in `.claude/skills/<name>/SKILL.md` (the Agent Skills standard). Both GitHub Copilot and Claude Code load them from there, so do not copy them to `.github/skills/`: duplicates load twice in Copilot.

## Commands
```
python -m perfagent init <name> --web-url URL --api-url URL --openapi URL|FILE
python -m perfagent discover <name> [--api|--web]
python -m perfagent traffic <name> <access.log> [--format combined|json]
python -m perfagent catalog <name> score|review|approve|reject|defer [IDs|all] [--priority P1] [--note ...]
python -m perfagent design <name>
python -m perfagent generate <name> [--force]
python -m perfagent run <name> --type smoke|load|stress|spike|soak|breakpoint [--scale 0.25] [--influx] [--dry-run]
python -m perfagent report <name> [run_id]   |   baseline <name> <run_id>   |   runs <name>
python -m perfagent agents check|sync        # keep Copilot/Claude customisation files in step
```

## Non-negotiable rules
- **Authorisation**: only send load to hosts in `safety.allowed_hosts` that the user is authorised to test. Never add hosts on your own initiative.
- **Confirm before load**: ask before every non-smoke run (it affects shared environments). Never pass `--allow-production` unless the user explicitly instructs it.
- **Human gate**: never mark scenarios approved without the user's explicit decision in the conversation.
- **Secrets** go in env vars (`{{env.NAME}}`), never in YAML, CSV, scripts or chat output.
- **Single source of truth**: fix scenarios in `catalog.yaml` and regenerate. Hand-edit scripts only when necessary (delete the `// GENERATED` marker line so they are kept).
- Keep scenario IDs stable after review. Report results honestly: a FAIL is a finding, not something to hide.

## Repo map
`perfagent/` framework · `perfagent/k6lib/common.js` k6 runtime · `perfagent/templates/` Jinja templates · `projects/<app>/` per-application artefacts · `monitoring/` Grafana + InfluxDB · `.claude/skills/` agent skills (shared) · `.github/` Copilot instructions, prompts and agents · `tools/k6.exe` portable k6 (git-ignored; set `K6_PATH` to override).

## Editing this framework
- Python 3.11+, stdlib + PyYAML/Jinja2/requests/Playwright only. Keep modules small and match the existing style.
- After changing `.github/instructions/` or `.github/prompts/`, run `python -m perfagent agents sync` (generates `.claude/rules/` and `.claude/commands/`).
- Validate changes end to end on `projects/petstore-demo` with **smoke only** (it is a shared public demo).
