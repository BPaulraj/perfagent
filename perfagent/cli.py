import argparse
import sys
from urllib.parse import urlparse

from perfagent.config import PROJECT_TEMPLATE, PROJECTS, Project
from perfagent.util import write_json


def cmd_init(args) -> None:
    target = PROJECTS / args.project
    if (target / "project.yaml").exists():
        raise SystemExit(f"Project already exists: {target}")
    hosts = sorted({urlparse(u).hostname for u in (args.web_url, args.api_url, args.openapi)
                    if u and u.startswith("http") and urlparse(u).hostname})
    text = (PROJECT_TEMPLATE.read_text(encoding="utf-8")
            .replace("__NAME__", args.project)
            .replace("__WEB_URL__", args.web_url or "")
            .replace("__API_URL__", args.api_url or "")
            .replace("__OPENAPI__", args.openapi or "")
            .replace("__HOSTS__", ", ".join(hosts)))
    for sub in ("model", "data", "design", "scripts", "runs", "review"):
        (target / sub).mkdir(parents=True, exist_ok=True)
    (target / "project.yaml").write_text(text, encoding="utf-8")
    print(f"Created {target / 'project.yaml'}\nReview auth, safety.allowed_hosts and nfr, then: python -m perfagent discover {args.project}")


def cmd_discover(args) -> None:
    project = Project(args.project)
    t = project.target
    do_api = args.api or not args.web
    do_web = args.web or not args.api
    if do_api and t.get("openapi"):
        from perfagent.discovery import openapi
        model = openapi.parse(t["openapi"])
        if not t.get("api_base_url") and model["base_url"]:
            print(f"  note: spec declares base URL {model['base_url']} - set target.api_base_url in project.yaml")
        write_json(project.model_dir / "api_model.json", model)
        s = model["stats"]
        print(f"API model: {s['endpoints']} endpoints, {s['resources']} resources {s['by_kind']} -> {project.model_dir / 'api_model.json'}")
    elif do_api:
        print("  skip API discovery: target.openapi not set")
    if do_web and t.get("web_url"):
        from perfagent.discovery import web
        c = project.cfg.get("crawl") or {}
        model = web.crawl(t["web_url"], c.get("max_pages", 25), c.get("max_depth", 3), c.get("headless", True),
                          c.get("exclude_patterns"), c.get("login"))
        write_json(project.model_dir / "web_model.json", model)
        s = model["stats"]
        print(f"Web model: {s['pages']} pages, {s['forms']} forms, {s['api_calls']} XHR/fetch endpoints -> {project.model_dir / 'web_model.json'}")
    elif do_web:
        print("  skip web discovery: target.web_url not set")
    print("Next: ask Claude Code to analyze the model ('perf-discover' skill) -> catalog.yaml")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; k6 prints unicode
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="perfagent", description="Performance test agent framework")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project for an application under test")
    s.add_argument("project")
    s.add_argument("--web-url")
    s.add_argument("--api-url", help="API base URL used by the scripts")
    s.add_argument("--openapi", help="Swagger/OpenAPI URL or file")

    s = sub.add_parser("discover", help="build app model from OpenAPI and/or crawling the web UI")
    s.add_argument("project")
    s.add_argument("--api", action="store_true", help="API only")
    s.add_argument("--web", action="store_true", help="web only")

    s = sub.add_parser("traffic", help="derive endpoint mix and peak rates from access logs")
    s.add_argument("project")
    s.add_argument("log", help="access log file")
    s.add_argument("--format", choices=["combined", "json"], default="combined")

    s = sub.add_parser("agents", help="validate / sync GitHub Copilot + Claude Code customisation files")
    s.add_argument("action", choices=["check", "sync"])

    s = sub.add_parser("catalog", help="score / review / approve scenarios")
    s.add_argument("project")
    s.add_argument("action", choices=["score", "status", "review", "approve", "reject", "defer"])
    s.add_argument("ids", nargs="*", help="scenario ids, or 'all'")
    s.add_argument("--priority", choices=["P1", "P2", "P3"], help="apply to every scenario in this band")
    s.add_argument("--note", help="reviewer note recorded on the decision")

    s = sub.add_parser("design", help="workload model + test plan from approved scenarios")
    s.add_argument("project")

    s = sub.add_parser("generate", help="generate k6 scripts")
    s.add_argument("project")
    s.add_argument("--force", action="store_true", help="overwrite manually edited scenario files")

    s = sub.add_parser("run", help="execute a test")
    s.add_argument("project")
    s.add_argument("--type", default="smoke", help="smoke | load | stress | spike | soak | breakpoint")
    s.add_argument("--scale", type=float, default=1.0, help="multiply designed load (e.g. 0.25 for a quarter-size env)")
    s.add_argument("--influx", action="store_true", help="stream metrics to InfluxDB for Grafana")
    s.add_argument("--headed", action="store_true", help="show the browser for web scenarios")
    s.add_argument("--dry-run", action="store_true", help="print the k6 command only")
    s.add_argument("--allow-production", action="store_true")
    s.add_argument("-e", "--env", action="append", default=[], help="extra KEY=VALUE passed to k6")

    s = sub.add_parser("audit", help="front-end Lighthouse audit (single user, no load) of configured pages")
    s.add_argument("project")
    s.add_argument("--runs", type=int, help="Lighthouse runs per page and form factor (median reported, default 3)")
    s.add_argument("--form-factor", choices=["mobile", "desktop", "both"])
    s.add_argument("--page", help="only pages whose name contains this text")
    s.add_argument("--headed", action="store_true", help="show the browser")

    s = sub.add_parser("report", help="(re)build the report for a run (default: latest)")
    s.add_argument("project")
    s.add_argument("run_id", nargs="?")

    s = sub.add_parser("baseline", help="mark a run as the comparison baseline")
    s.add_argument("project")
    s.add_argument("run_id")

    s = sub.add_parser("runs", help="list runs")
    s.add_argument("project")

    args = p.parse_args()
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "discover":
        return cmd_discover(args)
    if args.cmd == "agents":
        from perfagent.agents_sync import cmd_agents
        return cmd_agents(args.action)

    project = Project(args.project)
    if args.cmd == "traffic":
        from perfagent.traffic import cmd_traffic
        cmd_traffic(project, args.log, args.format)
    elif args.cmd == "catalog":
        from perfagent import catalog
        if args.action in ("score", "status"):
            catalog.cmd_score(project)
        elif args.action == "review":
            catalog.cmd_review(project)
        else:
            if not args.ids and not args.priority:
                raise SystemExit("Give scenario ids, 'all', or --priority P1")
            status = {"approve": "approved", "reject": "rejected", "defer": "deferred"}[args.action]
            catalog.cmd_set_status(project, args.ids, status, args.priority, args.note)
    elif args.cmd == "design":
        from perfagent.design import cmd_design
        cmd_design(project)
    elif args.cmd == "generate":
        from perfagent.generator import cmd_generate
        cmd_generate(project, args.force)
    elif args.cmd == "run":
        from perfagent.runner import cmd_run
        cmd_run(project, args.type, args.scale, args.influx, args.dry_run, args.allow_production, args.headed, args.env)
    elif args.cmd == "audit":
        from perfagent.audit import cmd_audit
        cmd_audit(project, args.runs, args.form_factor, args.page, args.headed)
    elif args.cmd == "report":
        from perfagent.report import cmd_report
        cmd_report(project, args.run_id)
    elif args.cmd == "baseline":
        from perfagent.report import cmd_baseline
        cmd_baseline(project, args.run_id)
    elif args.cmd == "runs":
        from perfagent.runner import list_runs
        for r in list_runs(project):
            print(f"{r['run_id']:<28}{r.get('k6_status', 'running?'):<20}{r.get('base_url', '')}")
