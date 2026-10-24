"""Derive a traffic profile (endpoint mix, peak rates) from access logs, matched to the OpenAPI model.

Supported inputs:
  combined  NCSA common/combined log format (Apache, Nginx default, many LBs)
  json      one JSON object per line with method, path|url|uri, time|timestamp, status, optional duration_ms
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import urlparse

from perfagent.config import Project
from perfagent.util import normalize_path, now_iso, read_json, write_json

COMBINED = re.compile(r'^\S+ \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<url>\S+)[^"]*" (?P<status>\d{3}) \S+')


def _parse_time(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value)
    for fmt in ("%d/%b/%Y:%H:%M:%S %z", None):
        try:
            return datetime.strptime(value, fmt) if fmt else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
    return None


def read_log(path: str, fmt: str):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if fmt == "json":
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = o.get("path") or o.get("url") or o.get("uri") or ""
                yield (o.get("method", "GET").upper(), url, _parse_time(o.get("time") or o.get("timestamp")),
                       int(o.get("status", 0) or 0), o.get("duration_ms"))
            else:
                m = COMBINED.match(line)
                if m:
                    yield m["method"], m["url"], _parse_time(m["time"]), int(m["status"]), None


def endpoint_matchers(project: Project):
    api_file = project.model_dir / "api_model.json"
    if not api_file.exists():
        return [], ""
    model = read_json(api_file)
    base_path = urlparse(project.target.get("api_base_url") or model.get("base_url") or "").path.rstrip("/")
    matchers = []
    for e in model["endpoints"]:
        pattern = re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(e["path"]))
        # literal paths first so /pet/findByStatus wins over /pet/{petId}
        matchers.append((e["path"].count("{"), e["method"], re.compile(f"^{re.escape(base_path)}{pattern}/?$"), e["id"]))
    matchers.sort(key=lambda m: m[0])
    return matchers, base_path


def cmd_traffic(project: Project, log_path: str, fmt: str) -> None:
    matchers, base_path = endpoint_matchers(project)
    per_minute: Counter = Counter()
    endpoints: dict = defaultdict(lambda: {"count": 0, "errors": 0, "durations": []})
    total = matched = 0
    first = last = None

    for method, url, ts, status, duration in read_log(log_path, fmt):
        path = urlparse(url).path
        key = next((eid for _, m, rx, eid in matchers if m == method and rx.match(path)), None)
        if key:
            matched += 1
        else:
            key = f"{method} {normalize_path(path)}"
        total += 1
        e = endpoints[key]
        e["count"] += 1
        e["errors"] += status >= 500 or status == 0
        if duration is not None:
            e["durations"].append(float(duration))
        if ts:
            per_minute[ts.replace(second=0, microsecond=0)] += 1
            first = ts if first is None or ts < first else first
            last = ts if last is None or ts > last else last

    if not total:
        raise SystemExit(f"No parsable lines in {log_path} (format '{fmt}').")

    per_hour: Counter = Counter()
    for minute, n in per_minute.items():
        per_hour[minute.replace(minute=0)] += n
    peak_minute = max(per_minute.items(), key=lambda kv: kv[1]) if per_minute else (None, 0)
    peak_hour = max(per_hour.items(), key=lambda kv: kv[1]) if per_hour else (None, 0)

    rows = []
    for key, e in sorted(endpoints.items(), key=lambda kv: -kv[1]["count"]):
        d = sorted(e["durations"])
        rows.append({"endpoint": key, "in_spec": key in {m[3] for m in matchers}, "count": e["count"],
                     "share_pct": round(100 * e["count"] / total, 2),
                     "error_pct": round(100 * e["errors"] / e["count"], 2),
                     "p95_ms": d[int(0.95 * (len(d) - 1))] if d else None})

    profile = {
        "generated_at": now_iso(), "source": str(log_path), "format": fmt,
        "requests": total, "matched_to_spec_pct": round(100 * matched / total, 1),
        "window": {"from": first.isoformat() if first else None, "to": last.isoformat() if last else None},
        "peak_minute": {"at": peak_minute[0].isoformat() if peak_minute[0] else None, "requests": peak_minute[1],
                        "rps": round(peak_minute[1] / 60, 2)},
        "peak_hour": {"at": peak_hour[0].isoformat() if peak_hour[0] else None, "requests": peak_hour[1],
                      "avg_rps": round(peak_hour[1] / 3600, 2)},
        "endpoints": rows,
    }
    write_json(project.model_dir / "traffic_profile.json", profile)

    print(f"{total} requests, {profile['matched_to_spec_pct']}% matched to OpenAPI (base path '{base_path}')")
    print(f"Peak hour {profile['peak_hour']['at']}: {peak_hour[1]} req ({profile['peak_hour']['avg_rps']} rps avg); "
          f"peak minute {peak_minute[1]} req ({profile['peak_minute']['rps']} rps)")
    print(f"\n{'Share':>7} {'Count':>8} {'Err%':>6} {'p95':>7}  Endpoint")
    for r in rows[:25]:
        print(f"{r['share_pct']:>6}% {r['count']:>8} {r['error_pct']:>6} {str(r['p95_ms'] or '-'):>7}  {r['endpoint']}{'' if r['in_spec'] else '  (not in spec)'}")
    print(f"\nTraffic profile: {project.model_dir / 'traffic_profile.json'}")
