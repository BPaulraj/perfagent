"""Turn a k6 summary.json into an SLA verdict report (HTML + Markdown) with baseline comparison and
automatic first-pass bottleneck observations."""

import re
from pathlib import Path
from urllib.parse import urlparse

from perfagent.config import Project
from perfagent.util import now_iso, read_json, read_yaml, render, write_json, write_text

REPORT_ONLY = ("max>=0", "count>=0")
REGRESSION_PCT = 10  # p95 slower than baseline by more than this = regression


def _m(metrics: dict, name: str) -> dict:
    return (metrics.get(name) or {}).get("values") or {}


def _checks(group: dict) -> list[dict]:
    out = [{"name": c["name"], "passes": c["passes"], "fails": c["fails"]} for c in group.get("checks", [])]
    for g in group.get("groups", []):
        out += _checks(g)
    return out


def summarize(summary: dict, workload: dict, meta: dict) -> dict:
    m = summary["metrics"]
    duration_s = (summary.get("state") or {}).get("testRunDurationMs", 0) / 1000

    thresholds = []
    for name, metric in m.items():
        for expr, res in (metric.get("thresholds") or {}).items():
            if expr not in REPORT_ONLY:
                thresholds.append({"metric": name, "expr": expr, "ok": bool(res.get("ok"))})

    thresholds.sort(key=lambda t: (t["metric"].split("{")[-1], t["metric"]))
    scenarios, steps = [], []
    for s in workload["scenarios"]:
        sid = s["id"]
        d = _m(m, f"http_req_duration{{scenario:{sid}}}")
        if not d.get("count"):
            d = {}
        row = {
            "id": sid, "name": s["name"], "kind": s["kind"], "priority": s.get("priority"), "sla": s["sla"],
            "requests": d.get("count", 0), "avg": d.get("avg"), "p90": d.get("p(90)"), "p95": d.get("p(95)"),
            "p99": d.get("p(99)"), "max": d.get("max"),
            "error_rate": _m(m, f"http_req_failed{{scenario:{sid}}}").get("rate"),
            "iterations": _m(m, f"iterations{{scenario:{sid}}}").get("count", 0),
        }
        if s["kind"] == "web":
            row["web"] = {v: _m(m, f"browser_web_vital_{v}{{scenario:{sid}}}").get("p(75)") for v in ("lcp", "fcp", "ttfb", "cls")}
            row["step_fail_rate"] = _m(m, f"web_step_failed{{scenario:{sid}}}").get("rate")
        row["sla_ok"] = all(t["ok"] for t in thresholds if f"scenario:{sid}}}" in t["metric"])
        scenarios.append(row)
        for tag in s["step_tags"]:
            sv = _m(m, f"http_req_duration{{name:{tag}}}") or _m(m, f"web_step_duration{{name:{tag}}}")
            if sv:
                steps.append({"scenario": sid, "name": tag, "count": sv.get("count"), "avg": sv.get("avg"),
                              "p95": sv.get("p(95)"), "p99": sv.get("p(99)"), "max": sv.get("max"),
                              "sla_p95": s["sla"]["lcp_ms"] if s["kind"] == "web" else s["sla"]["p95_ms"]})

    dur = _m(m, "http_req_duration")
    overall = {
        "duration_s": round(duration_s, 1),
        "requests": _m(m, "http_reqs").get("count", 0),
        "rps": round(_m(m, "http_reqs").get("rate", 0), 2),
        "iterations": _m(m, "iterations").get("count", 0),
        "dropped_iterations": _m(m, "dropped_iterations").get("count", 0),
        "vus_max": _m(m, "vus_max").get("max") or _m(m, "vus_max").get("value"),
        "avg": dur.get("avg"), "p95": dur.get("p(95)"), "p99": dur.get("p(99)"), "max": dur.get("max"),
        "error_rate": _m(m, "http_req_failed").get("rate", 0),
        "checks_rate": _m(m, "checks").get("rate"),
        "waiting_avg": _m(m, "http_req_waiting").get("avg"),
        "blocked_avg": _m(m, "http_req_blocked").get("avg"),
        "connecting_avg": _m(m, "http_req_connecting").get("avg"),
        "tls_avg": _m(m, "http_req_tls_handshaking").get("avg"),
        "received_mb": round(_m(m, "data_received").get("count", 0) / 1e6, 2),
        "sent_mb": round(_m(m, "data_sent").get("count", 0) / 1e6, 2),
    }
    aborted = meta.get("exit_code") in (105, 107, 108)
    verdict = "PASS" if thresholds and all(t["ok"] for t in thresholds) and not aborted else "FAIL"
    return {"meta": meta, "verdict": verdict, "overall": overall, "scenarios": scenarios, "steps": steps,
            "thresholds": thresholds, "checks": _checks(summary.get("root_group") or {})}


def observations(r: dict) -> list[str]:
    """Heuristic first-pass analysis - a starting point for the engineer, not a root cause."""
    o, notes = r["overall"], []
    if o["dropped_iterations"]:
        notes.append(f"{o['dropped_iterations']} iterations were dropped: the target arrival rate was not achieved. "
                     "Either the system under test saturated (response times grew) or maxVUs/load-generator capacity is too low.")
    # judged per scenario: a global p99 is skewed by mixing fast and deliberately slow transactions
    tails = [s["id"] for s in r["scenarios"] if s.get("p95") and s.get("p99") and s["p99"] > 3 * s["p95"]]
    if tails:
        notes.append(f"Long tail latency (p99 > 3x p95) in {', '.join(tails)}: typical of GC pauses, lock contention, cold caches or queueing in thread/connection pools.")
    if o["avg"] and o["waiting_avg"] and o["waiting_avg"] > 0.8 * o["avg"]:
        notes.append("Most request time is server wait (TTFB) - the bottleneck is server-side processing (app code, DB, downstream calls), not the network.")
    if o["requests"] > 100 and o["avg"] and o["blocked_avg"] and o["blocked_avg"] > 0.2 * o["avg"]:
        notes.append("High http_req_blocked: time is spent acquiring connections - check keep-alive, connection limits, load balancer or TLS handshakes.")
    if o["error_rate"] and o["error_rate"] > 0:
        notes.append(f"Error rate {o['error_rate'] * 100:.2f}% - inspect status codes in k6.log / dashboard and correlate with server logs for the same time window.")
    if o["checks_rate"] is not None and o["checks_rate"] < 0.99:
        failed = [c for c in r["checks"] if c["fails"]]
        notes.append("Functional checks failing under load: " + ", ".join(f"{c['name']} ({c['fails']})" for c in failed[:5]))
    for s in r["scenarios"]:
        if s.get("p95") and s["p95"] > s["sla"]["p95_ms"]:
            slow = sorted((st for st in r["steps"] if st["scenario"] == s["id"] and st.get("p95")), key=lambda x: -x["p95"])
            hint = f" Slowest step: {slow[0]['name']} (p95 {slow[0]['p95']:.0f}ms)." if slow else ""
            notes.append(f"{s['id']} breaches p95 SLA ({s['p95']:.0f}ms > {s['sla']['p95_ms']}ms).{hint}")
    if not notes:
        notes.append("No anomalies detected by heuristics. Correlate with server-side metrics (CPU, memory, DB, GC) before sign-off.")
    return notes


def compare(r: dict, base: dict) -> list[dict]:
    base_by_id = {s["id"]: s for s in base["scenarios"]}
    rows = []
    for s in r["scenarios"]:
        b = base_by_id.get(s["id"])
        if not b or not b.get("p95") or s.get("p95") is None:
            continue
        delta = (s["p95"] - b["p95"]) / b["p95"] * 100
        err_delta = ((s.get("error_rate") or 0) - (b.get("error_rate") or 0)) * 100
        rows.append({"id": s["id"], "base_p95": b["p95"], "p95": s["p95"], "delta_pct": round(delta, 1),
                     "base_err": (b.get("error_rate") or 0) * 100, "err": (s.get("error_rate") or 0) * 100,
                     "regression": delta > REGRESSION_PCT or err_delta > 0.5})
    return rows


ERROR_KINDS = [  # (pattern in the k6 error text, readable classification)
    (re.compile(r"forcibly closed|connection reset|ECONNRESET", re.I), "Connection reset by server"),
    (re.compile(r"\bEOF\b", re.I), "Connection closed (EOF)"),
    (re.compile(r"refused", re.I), "Connection refused"),
    (re.compile(r"timeout|deadline exceeded", re.I), "Request timeout"),
]
REQUEST_FAILED = re.compile(r'msg="Request Failed" error="(?P<method>[A-Za-z]+) \\?"(?P<url>[^"\\]+)\\?": (?P<detail>[^"]*)"')
LOG_LINE = re.compile(r'level=(?P<level>warning|error) msg="(?P<msg>(?:[^"\\]|\\.)*)"(?:.*?source=(?P<source>\w+))?')


def error_breakdown(run_dir: Path) -> list[dict]:
    """Group transport failures and script/browser errors from k6.log - status-code failures are in the checks table."""
    log = run_dir / "k6.log"
    if not log.exists():
        return []
    groups: dict = {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = REQUEST_FAILED.search(line)
        if m:
            kind = next((k for rx, k in ERROR_KINDS if rx.search(m["detail"])), m["detail"][:80])
            key = (f"{m['method'].upper()} {urlparse(m['url']).path}", kind)
        else:
            m = LOG_LINE.search(line)
            if not m or "thresholds on metrics" in m["msg"]:
                continue
            msg = re.sub(r"\d+(\.\d+)?\s*ms|PID \d+|:\d{4,5}\b", "", m["msg"].replace('\\"', '"'))
            key = (f"{m['source'] or 'k6'} ({m['level']})", msg[:140])
        groups[key] = groups.get(key, 0) + 1
    return [{"where": w, "error": e, "count": n} for (w, e), n in sorted(groups.items(), key=lambda kv: -kv[1])]


def load_findings(run_dir: Path) -> list[dict]:
    """Analyst findings written by the agent (perf-results skill) to runs/<id>/findings.yaml."""
    f = run_dir / "findings.yaml"
    return (read_yaml(f) or {}).get("findings", []) if f.exists() else []


def baseline_file(project: Project) -> Path:
    return project.runs_dir / "baseline.json"


def build(project: Project, run_dir: Path) -> dict:
    meta = read_json(run_dir / "meta.json")
    workload = read_json(project.scripts_dir / "workload.json")
    r = summarize(read_json(run_dir / "summary.json"), workload, meta)
    r["observations"] = observations(r)
    r["errors"] = error_breakdown(run_dir)
    r["findings"] = load_findings(run_dir)
    r["generated_at"] = now_iso()

    r["baseline"] = None
    if baseline_file(project).exists():
        base_id = read_json(baseline_file(project))["run_id"]
        base_report = project.runs_dir / base_id / "report.json"
        if base_id != meta["run_id"] and base_report.exists():
            r["baseline"] = {"run_id": base_id, "rows": compare(r, read_json(base_report))}
            if any(x["regression"] for x in r["baseline"]["rows"]):
                r["observations"].insert(0, f"Regression versus baseline {base_id} (p95 +{REGRESSION_PCT}% or errors +0.5pp).")

    write_json(run_dir / "report.json", r)
    write_text(run_dir / "report.html", render("report.html.j2", r=r, project=project.cfg,
                                               has_dashboard=(run_dir / "dashboard.html").exists()))
    md = render("report.md.j2", r=r)
    write_text(run_dir / "report.md", md)
    print("\n" + md)
    print(f"HTML report: {run_dir / 'report.html'}")
    if (run_dir / "dashboard.html").exists():
        print(f"k6 dashboard (time series): {run_dir / 'dashboard.html'}")
    return r


def cmd_report(project: Project, run_id: str | None) -> None:
    from perfagent.runner import list_runs
    runs = list_runs(project)
    if not runs:
        raise SystemExit("No runs yet.")
    run_id = run_id or runs[-1]["run_id"]
    if run_id.endswith("_audit"):
        raise SystemExit(f"{run_id} is a Lighthouse audit - its report is runs/{run_id}/report.html (re-run with: python -m perfagent audit {project.name})")
    build(project, project.runs_dir / run_id)


def cmd_baseline(project: Project, run_id: str) -> None:
    if not (project.runs_dir / run_id / "report.json").exists():
        raise SystemExit(f"Run {run_id} has no report.json")
    write_json(baseline_file(project), {"run_id": run_id, "set_at": now_iso()})
    print(f"Baseline set to {run_id}")
