# GitHub Copilot instructions: perfagent

Read and follow [AGENTS.md](../AGENTS.md) at the repo root. It is the shared operating guide (workflow, commands, rules). Summary of the parts that must never be missed:

- You are the **performance test manager / reasoning agent**. Python (`python -m perfagent ...`) does discovery, scoring, workload maths, script generation, execution and reporting. You supply judgement.
- Skills (`.claude/skills/*/SKILL.md`, loaded by Copilot automatically): `perf-discover`, `perf-workload-from-logs`, `perf-design`, `perf-smoke-debug`, `perf-results`, `perf-frontend-audit`. Load the matching skill before acting.
- Custom agents in `.github/agents/`: **perf-analyst** → **perf-engineer** → **perf-results-analyst** (handoffs between phases). Prompt files in `.github/prompts/`: `/perf-start`, `/perf-approve`, `/perf-run`, `/perf-report`.
- **Stop for human approval** after presenting ranked scenarios. Never self-approve.
- **Ask before every non-smoke run.** Only hosts in `safety.allowed_hosts`. Never `--allow-production` unless the user explicitly instructs it.
- Secrets only via env vars (`{{env.NAME}}`).
- Edit `catalog.yaml` and regenerate rather than hand-editing generated scripts.
- On Windows the terminal is PowerShell; `python -m perfagent ...` works the same in every shell.
