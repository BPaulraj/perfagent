@AGENTS.md

## Claude Code specifics
- Skills in `.claude/skills/` are available as `/perf-discover`, `/perf-design`, `/perf-results`, `/perf-smoke-debug`, `/perf-workload-from-logs`, `/perf-frontend-audit`. Commands in `.claude/commands/` are generated from `.github/prompts/` (`/perf-start`, `/perf-approve`, `/perf-run`, `/perf-report`).
- Path rules in `.claude/rules/` are generated from `.github/instructions/`. Edit the `.github` originals, then run `python -m perfagent agents sync`.
- Offer to publish review pages and reports as Artifacts when stakeholders need to see them.
