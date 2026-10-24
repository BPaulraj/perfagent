"""Workload model + test plan from the approved scenarios.

Uses an open workload model (arrival-rate executors) sized with Little's Law:
    concurrent users (N) = arrival rate (X) x time a user spends in the scenario (R + Z)
so  iterations/min per scenario = users_for_scenario * 60 / iteration_seconds
"""

import math
import re

from perfagent import catalog as cat
from perfagent.config import Project
from perfagent.util import now_iso, read_json, render, safe_tag, slug, write_json, write_text

# Stage targets are multipliers of the designed peak (1.0 = 100% of expected peak load).
PROFILES = {
    "smoke": {
        "purpose": "Verify scripts, data and environment work (1 VU, few iterations). Gate before every other test.",
        "iterations": 2, "stages": [],
    },
    "load": {
        "purpose": "Validate SLAs at expected peak load (100%) for a sustained period.",
        "stages": [{"duration": "5m", "target": 1.0}, {"duration": "20m", "target": 1.0}, {"duration": "2m", "target": 0}],
    },
    "stress": {
        "purpose": "Step beyond peak (50% -> 200%) to find the degradation point and how the system fails.",
        "stages": [{"duration": "2m", "target": 0.5}, {"duration": "5m", "target": 0.5},
                   {"duration": "2m", "target": 1.0}, {"duration": "5m", "target": 1.0},
                   {"duration": "2m", "target": 1.5}, {"duration": "5m", "target": 1.5},
                   {"duration": "2m", "target": 2.0}, {"duration": "5m", "target": 2.0},
                   {"duration": "3m", "target": 0}],
    },
    "spike": {
        "purpose": "Sudden surge to 300% of peak (sale launch, batch release) and recovery back to normal.",
        "stages": [{"duration": "2m", "target": 0.3}, {"duration": "2m", "target": 0.3},
                   {"duration": "30s", "target": 3.0}, {"duration": "3m", "target": 3.0},
                   {"duration": "30s", "target": 0.3}, {"duration": "3m", "target": 0.3},
                   {"duration": "1m", "target": 0}],
    },
    "soak": {
        "purpose": "80% of peak for hours to expose memory leaks, connection-pool exhaustion and resource creep.",
        "stages": [{"duration": "5m", "target": 0.8}, {"duration": "4h", "target": 0.8}, {"duration": "5m", "target": 0}],
    },
    "breakpoint": {
        "purpose": "Linear ramp to 500% until the error-rate guard aborts - establishes maximum capacity.",
        "stages": [{"duration": "30m", "target": 5.0}], "abort_on_fail": True,
    },
}


def duration_s(text: str) -> int:
    return sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in re.findall(r"(\d+)([hms])", str(text)))


def fmt_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return "".join(f"{v}{u}" for v, u in ((h, "h"), (m, "m"), (s, "s")) if v) or "0s"


def build_workload(project: Project) -> dict:
    catalog = cat.score(cat.load(project))
    approved = [s for s in catalog["scenarios"] if s.get("status") == "approved"]
    if not approved:
        raise SystemExit("No approved scenarios. Approve with: python -m perfagent catalog <project> approve SC-001 SC-002 ... (or --priority P1)")

    nfr = project.nfr
    peak_users = int(nfr.get("peak_concurrent_users", 100))
    browser_vus = int(nfr.get("browser_vus", 2))
    est_resp = float(nfr.get("est_response_s", 0.3))
    api_scenarios = [s for s in approved if s["type"] != "web"]
    web_scenarios = [s for s in approved if s["type"] == "web"]

    total_share = sum(float(s.get("workload_share", 0)) for s in api_scenarios) or len(api_scenarios)
    custom = project.cfg.get("test_profiles") or {}
    profiles = {k: {**PROFILES.get(k, {"purpose": "Custom profile (project.yaml test_profiles)"}), **(custom.get(k) or {})}
                for k in [*PROFILES, *[c for c in custom if c not in PROFILES]]}
    for p in profiles.values():
        p["total_duration"] = fmt_duration(sum(duration_s(st["duration"]) for st in p["stages"])) if p["stages"] else None

    scenarios = []
    for s in approved:
        sla = {**project.default_sla, **(s.get("sla") or {})}
        steps = s.get("steps") or []
        think = sum(float(st.get("think_time", 1)) for st in steps)
        n_api = sum(1 for st in steps if st.get("kind", "api") == "api")
        n_web = sum(1 for st in steps if st.get("kind") == "web")
        iter_s = round(think + n_api * est_resp + n_web * 2.0, 2)
        entry = {
            "id": s["id"], "fn": slug(s["id"]), "name": s["name"], "kind": "web" if s["type"] == "web" else "api",
            "priority": s.get("priority"), "sla": sla, "iteration_seconds": iter_s, "requests_per_iteration": n_api,
            "data_file": s.get("data_file"), "data_mode": s.get("data_mode", "shared"),
            "step_tags": [f"{s['id']} {i:02d} {safe_tag(st.get('name', st.get('path') or st.get('action')))}"
                          for i, st in enumerate(steps, 1)],
        }
        if entry["kind"] == "api":
            share = float(s.get("workload_share", 0)) or (100 / len(api_scenarios))
            share_pct = round(share * 100 / total_share, 2) if total_share else 0
            # rate from the exact (fractional) user count so small shares keep their true weight;
            # the VU pool is rounded up separately
            exact_users = peak_users * share_pct / 100
            entry.update({"share_pct": share_pct, "users": max(1, math.ceil(exact_users)),
                          "rate_per_min": round(exact_users * 60 / max(iter_s, 0.1), 2)})
        else:
            entry.update({"share_pct": None, "users": max(1, browser_vus // max(1, len(web_scenarios))), "rate_per_min": None})
        scenarios.append(entry)

    planned_rps = round(sum((s["rate_per_min"] or 0) * s["requests_per_iteration"] for s in scenarios) / 60, 2)
    return {
        "project": project.name,
        "planned_peak_rps": planned_rps,
        "generated_at": now_iso(),
        "model": "open (arrival-rate) - Little's Law sizing",
        "nfr": {"peak_concurrent_users": peak_users, "browser_vus": browser_vus, "est_response_s": est_resp,
                "error_rate": project.default_sla["error_rate"]},
        "auth": project.cfg.get("auth") or {"type": "none"},
        "profiles": profiles,
        "scenarios": scenarios,
    }


def cmd_design(project: Project) -> None:
    workload = build_workload(project)
    catalog = cat.load(project)
    by_id = {s["id"]: s for s in catalog["scenarios"]}
    write_json(project.design_dir / "workload.json", workload)
    plan = render("test_plan.md.j2", project=project.cfg, workload=workload, scenarios=by_id,
                  coverage=cat.coverage(project, catalog), generated=now_iso())
    write_text(project.design_dir / "test_plan.md", plan)

    print(f"{'ID':<9}{'Kind':<6}{'Share':<8}{'Users':<7}{'Iter(s)':<9}{'Rate/min':<10}SLA p95")
    for s in workload["scenarios"]:
        print(f"{s['id']:<9}{s['kind']:<6}{str(s['share_pct'] or '-'):<8}{s['users']:<7}{s['iteration_seconds']:<9}"
              f"{str(s['rate_per_min'] or '-'):<10}{s['sla']['p95_ms']}ms")
    print(f"\nPlanned peak: {workload['planned_peak_rps']} API req/s (excluding browser traffic)")
    traffic = project.model_dir / "traffic_profile.json"
    if traffic.exists():
        observed = read_json(traffic)
        ratio = workload["planned_peak_rps"] / max(observed["peak_hour"]["avg_rps"], 0.01)
        print(f"Observed: peak-hour avg {observed['peak_hour']['avg_rps']} req/s, peak minute {observed['peak_minute']['rps']} req/s "
              f"-> plan is {ratio:.1f}x the observed peak-hour average. Adjust nfr.peak_concurrent_users if unintended.")
    print(f"\nWorkload model: {project.design_dir / 'workload.json'}")
    print(f"Test plan:      {project.design_dir / 'test_plan.md'}")
