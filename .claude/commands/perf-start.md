---
description: Start performance testing a new application - discover it, rank business scenarios and stop for approval.
argument-hint: <project-name> <web-url> <openapi-url-or-file>
---

<!-- GENERATED from .github/prompts/perf-start.prompt.md by `python -m perfagent agents sync` - edit the source, not this file -->
Start a performance testing engagement as the **performance test manager**.

Inputs:
- Project name: <project>
- Web URL: <webUrl>
- API base URL: <apiUrl>
- OpenAPI / Swagger: <openapi>

Follow the **perf-discover** skill (`.claude/skills/perf-discover/SKILL.md`) exactly:
1. Confirm I am authorised to load test these hosts and that this is not production. Ask once for: auth method (env var *names* only), peak concurrent users or business volumes, known SLAs. Use documented defaults for anything I don't know, and list them as assumptions.
2. `python -m perfagent init` (if the project doesn't exist), then `python -m perfagent discover`.
3. If I can provide access logs or analytics, use the **perf-workload-from-logs** skill to ground frequency and workload share in real traffic.
4. Analyze the models, write `projects/<project>/catalog.yaml`, run `python -m perfagent catalog <project> review`.
5. Present the ranked scenario table, assumptions, coverage and exclusions, plus your recommendation. Then **stop and wait for my approval**.

Arguments (project, webUrl, apiUrl, openapi): $ARGUMENTS
Ask for any value not supplied.
