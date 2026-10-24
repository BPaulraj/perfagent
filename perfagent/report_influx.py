"""Combined report for a distributed run (several k6 machines, execution segments) built from InfluxDB.

Every machine streams raw samples to the same InfluxDB with one shared `testid` tag, so percentiles here are
computed over all samples (exact), not averaged from per-machine summaries. SLAs are evaluated across machines
from the workload model - per-machine k6 thresholds only see their own share of the load.
"""

import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from perfagent import report
from perfagent.config import Project
from perfagent.util import now_iso, read_json, render, write_json, write_text


class Influx:
    def __init__(self, url: str):
        u = urlparse(url)
        self.base = f"{u.scheme}://{u.netloc}"
        self.db = u.path.strip("/") or "k6"

    def query(self, q: str) -> list[dict]:
        try:
            res = requests.get(f"{self.base}/query", params={"db": self.db, "q": q, "epoch": "ms"}, timeout=120)
        except requests.ConnectionError as e:
            raise SystemExit(f"Cannot reach InfluxDB at {self.base} ({e.__class__.__name__}). Start it: docker compose -f monitoring/docker-compose.yml up -d")
        res.raise_for_status()
        result = res.json()["results"][0]
        if "error" in result:
            raise SystemExit(f"InfluxDB query failed: {result['error']}\n{q}")
        return [{"tags": s.get("tags") or {}, **dict(zip(s["columns"], s["values"][0]))} for s in result.get("series") or []]

    def one(self, q: str) -> dict:
        rows = self.query(q)
        return rows[0] if rows else {}

    def by(self, q: str, tag: str) -> dict:
        return {r["tags"].get(tag, ""): r for r in self.query(q)}


TREND = 'count("value") AS count, mean("value") AS avg, percentile("value", 90) AS p90, percentile("value", 95) AS p95, percentile("value", 99) AS p99, max("value") AS max'


def cmd_report_influx(project: Project, testid: str) -> None:
    if not re.fullmatch(r"[\w.\-:]+", testid):
        raise SystemExit("testid may contain only letters, digits and . - _ :")
    db = Influx((project.cfg.get("monitoring") or {}).get("influxdb_url") or "http://localhost:8086/k6")
    workload = read_json(project.scripts_dir / "workload.json")
    w = f"\"testid\" = '{testid}'"

    span = db.one(f'SELECT first("value") FROM "http_reqs" WHERE {w}'), db.one(f'SELECT last("value") FROM "http_reqs" WHERE {w}')
    if not span[0]:
        raise SystemExit(f"No data for testid '{testid}' in InfluxDB {db.base}/{db.db}. Did every machine run with --influx and the same --testid?")
    duration_s = round((span[1]["time"] - span[0]["time"]) / 1000, 1)
    segments = sorted({row["value"] for row in _tag_values(db, "segment", w)})   # e.g. ["1of3", "2of3", "3of3"]
    test_type = next(iter(sorted({row["value"] for row in _tag_values(db, "test_type", w)})), "unknown")

    dur_s = db.by(f'SELECT {TREND} FROM "http_req_duration" WHERE {w} GROUP BY "scenario"', "scenario")
    fail_s = db.by(f'SELECT mean("value") AS rate FROM "http_req_failed" WHERE {w} GROUP BY "scenario"', "scenario")
    iter_s = db.by(f'SELECT sum("value") AS n FROM "iterations" WHERE {w} GROUP BY "scenario"', "scenario")
    steps_http = db.by(f'SELECT {TREND} FROM "http_req_duration" WHERE {w} GROUP BY "name"', "name")
    steps_web = db.by(f'SELECT {TREND} FROM "web_step_duration" WHERE {w} GROUP BY "name"', "name")
    vitals = {v: db.by(f'SELECT percentile("value", 75) AS p75 FROM "browser_web_vital_{v}" WHERE {w} GROUP BY "scenario"', "scenario")
              for v in ("lcp", "fcp", "ttfb", "cls")}
    web_fail = db.by(f'SELECT mean("value") AS rate FROM "web_step_failed" WHERE {w} GROUP BY "scenario"', "scenario")

    def mean_of(metric):
        return db.one(f'SELECT mean("value") AS v FROM "{metric}" WHERE {w}').get("v")

    dur = db.one(f'SELECT {TREND} FROM "http_req_duration" WHERE {w}')
    reqs = db.one(f'SELECT count("value") AS n FROM "http_reqs" WHERE {w}').get("n", 0)
    overall = {
        "duration_s": duration_s, "requests": reqs, "rps": round(reqs / duration_s, 2) if duration_s else 0,
        "iterations": db.one(f'SELECT sum("value") AS n FROM "iterations" WHERE {w}').get("n", 0),
        "dropped_iterations": db.one(f'SELECT sum("value") AS n FROM "dropped_iterations" WHERE {w}').get("n", 0) or 0,
        "vus_max": db.one(f'SELECT sum("m") AS v FROM (SELECT max("value") AS m FROM "vus" WHERE {w} GROUP BY "segment")').get("v"),
        "avg": dur.get("avg"), "p95": dur.get("p95"), "p99": dur.get("p99"), "max": dur.get("max"),
        "error_rate": mean_of("http_req_failed") or 0, "checks_rate": mean_of("checks"),
        "waiting_avg": mean_of("http_req_waiting"), "blocked_avg": mean_of("http_req_blocked"),
        "connecting_avg": mean_of("http_req_connecting"), "tls_avg": mean_of("http_req_tls_handshaking"),
        "received_mb": round((db.one(f'SELECT sum("value") AS v FROM "data_received" WHERE {w}').get("v") or 0) / 1e6, 2),
        "sent_mb": round((db.one(f'SELECT sum("value") AS v FROM "data_sent" WHERE {w}').get("v") or 0) / 1e6, 2),
    }

    thresholds, scenarios, steps = [], [], []
    for s in workload["scenarios"]:
        sid, sla = s["id"], s["sla"]
        d = dur_s.get(sid, {})
        row = {"id": sid, "name": s["name"], "kind": s["kind"], "priority": s.get("priority"), "sla": sla,
               "requests": d.get("count", 0), "avg": d.get("avg"), "p90": d.get("p90"), "p95": d.get("p95"),
               "p99": d.get("p99"), "max": d.get("max"), "error_rate": fail_s.get(sid, {}).get("rate"),
               "iterations": iter_s.get(sid, {}).get("n", 0)}
        checks = []
        if d.get("count"):
            checks += [(f"http_req_duration{{scenario:{sid}}}", f"p(95)<{sla['p95_ms']}", d["p95"] < sla["p95_ms"]),
                       (f"http_req_duration{{scenario:{sid}}}", f"p(99)<{sla['p99_ms']}", d["p99"] < sla["p99_ms"])]
        if row["error_rate"] is not None:
            checks.append((f"http_req_failed{{scenario:{sid}}}", f"rate<{sla['error_rate']}", row["error_rate"] < sla["error_rate"]))
        if s["kind"] == "web":
            row["web"] = {v: vitals[v].get(sid, {}).get("p75") for v in vitals}
            row["step_fail_rate"] = web_fail.get(sid, {}).get("rate")
            if row["web"]["lcp"] is not None:
                checks.append((f"browser_web_vital_lcp{{scenario:{sid}}}", f"p(75)<{sla['lcp_ms']}", row["web"]["lcp"] < sla["lcp_ms"]))
            if row["web"]["cls"] is not None:
                checks.append((f"browser_web_vital_cls{{scenario:{sid}}}", "p(75)<0.1", row["web"]["cls"] < 0.1))
            if row["step_fail_rate"] is not None:
                checks.append((f"web_step_failed{{scenario:{sid}}}", f"rate<{sla['error_rate']}", row["step_fail_rate"] < sla["error_rate"]))
        thresholds += [{"metric": m, "expr": e, "ok": bool(ok)} for m, e, ok in checks]
        row["sla_ok"] = all(ok for _, _, ok in checks)
        scenarios.append(row)
        for tag in s["step_tags"]:
            sv = steps_http.get(tag) or steps_web.get(tag)
            if sv:
                steps.append({"scenario": sid, "name": tag, "count": sv.get("count"), "avg": sv.get("avg"), "p95": sv.get("p95"),
                              "p99": sv.get("p99"), "max": sv.get("max"),
                              "sla_p95": sla["lcp_ms"] if s["kind"] == "web" else sla["p95_ms"]})
    thresholds.append({"metric": "http_req_failed", "expr": f"rate<{workload['nfr']['error_rate']}", "ok": overall["error_rate"] < workload["nfr"]["error_rate"]})
    if overall["checks_rate"] is not None:
        thresholds.append({"metric": "checks", "expr": "rate>0.99", "ok": overall["checks_rate"] > 0.99})
    failed_checks = [{"name": r["tags"].get("check"), "passes": int(r["passes"] or 0), "fails": int(r["total"] - (r["passes"] or 0))}
                     for r in db.query(f'SELECT sum("value") AS passes, count("value") AS total FROM "checks" WHERE {w} GROUP BY "check"')]

    run_id = f"{testid}_combined"
    run_dir = project.runs_dir / run_id
    r = {"meta": {"run_id": run_id, "project": project.name, "test_type": test_type, "scale": "see machines",
                  "environment": project.environment, "base_url": project.target.get("api_base_url"),
                  "k6_status": f"combined from {len(segments) or 1} load generator(s) {', '.join(segments)}".strip(),
                  "testid": testid, "segments": segments, "source": f"{db.base}/{db.db}"},
         "verdict": "PASS" if thresholds and all(t["ok"] for t in thresholds) else "FAIL",
         "overall": overall, "scenarios": scenarios, "steps": steps, "thresholds": thresholds, "checks": failed_checks,
         "errors": [], "findings": report.load_findings(run_dir), "generated_at": now_iso()}
    r["observations"] = report.observations(r)
    r["observations"].append(f"Combined view of {len(segments) or 1} machine(s) from InfluxDB. Transport/script errors are listed in each machine's own report (k6.log).")
    expected = int(segments[0].split("of")[1]) if segments else 1
    if len(segments) < expected:
        r["observations"].insert(0, f"Only {len(segments)} of {expected} segments reported data ({', '.join(segments)}) - a machine failed or "
                                    "was not started, so the planned load was not delivered. Verdict set to FAIL (invalid test).")
        r["verdict"] = "FAIL"
        r["thresholds"].append({"metric": "load generators", "expr": f"{expected} segments reporting", "ok": False})
    r["baseline"] = None
    if report.baseline_file(project).exists():
        base_id = read_json(report.baseline_file(project))["run_id"]
        base = project.runs_dir / base_id / "report.json"
        if base.exists():
            r["baseline"] = {"run_id": base_id, "rows": report.compare(r, read_json(base))}

    write_json(run_dir / "report.json", r)
    write_json(run_dir / "meta.json", {"run_id": run_id, "project": project.name, "test_type": test_type, "testid": testid,
                                       "base_url": project.target.get("api_base_url"), "started_at": now_iso(),
                                       "exit_code": 0 if r["verdict"] == "PASS" else 99, "k6_status": r["meta"]["k6_status"]})
    write_text(run_dir / "report.html", render("report.html.j2", r=r, project=project.cfg, has_dashboard=False))
    md = render("report.md.j2", r=r)
    write_text(run_dir / "report.md", md)
    print("\n" + md)
    print(f"HTML report: {run_dir / 'report.html'}")
    raise SystemExit(0 if r["verdict"] == "PASS" else 99)


def _tag_values(db: Influx, key: str, where: str) -> list[dict]:
    res = requests.get(f"{db.base}/query", params={"db": db.db, "q": f'SHOW TAG VALUES FROM "http_reqs" WITH KEY = "{key}" WHERE {where}'}, timeout=60)
    series = (res.json()["results"][0].get("series") or [])
    return [dict(zip(s["columns"], v)) for s in series for v in s["values"]]
