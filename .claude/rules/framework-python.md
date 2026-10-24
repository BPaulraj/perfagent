---
description: Coding conventions for the perfagent Python framework, its Jinja templates and CLI.
paths:
- perfagent/**/*.py
- perfagent/templates/**
---

<!-- GENERATED from .github/instructions/framework-python.instructions.md by `python -m perfagent agents sync` - edit the source, not this file -->
# perfagent framework code

- Python 3.11+; dependencies limited to PyYAML, Jinja2, requests, Playwright (web discovery and audits). Node tooling (Lighthouse) via the root package.json. No new dependencies without discussion.
- One module per pipeline stage: `discovery/openapi.py`, `discovery/web.py`, `traffic.py`, `catalog.py`, `design.py`, `generator.py`, `runner.py`, `report.py`, `audit.py` (Lighthouse) + `browser_steps.py` (Playwright replay of catalog steps), `agents_sync.py`. CLI wiring lives only in `cli.py`.
- Paths come from `config.Project` properties; file I/O goes through `util.read_*` / `write_*` (UTF-8 everywhere; Windows consoles are cp1252).
- User-facing failures: `raise SystemExit("clear message + the command to fix it")`, not tracebacks.
- Safety checks (`Project.check_host_allowed`, production guard) must never be bypassed or weakened.
- HTML templates share `_style.html.j2` (colour tokens with dark mode). Keep reports self-contained (no external assets).
- The crawler stays read-only: it never submits forms other than the configured login.
- Verify changes with `python -m perfagent ...` against `projects/petstore-demo`, **smoke only**.
