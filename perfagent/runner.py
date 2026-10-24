"""Execute k6 with safety guards, the live web dashboard, optional InfluxDB streaming, and run bookkeeping."""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from perfagent import report
from perfagent.config import ROOT, Project
from perfagent.util import now_iso, read_json, write_json

K6_EXIT = {0: "completed", 99: "thresholds failed", 107: "script error", 108: "aborted by threshold", 105: "aborted"}


def find_k6() -> str:
    exe = os.environ.get("K6_PATH") or shutil.which("k6")
    if not exe:
        for candidate in (str(ROOT / "tools" / "k6.exe"), str(ROOT / "tools" / "k6"), r"C:\Program Files\k6\k6.exe",
                          str(Path.home() / "AppData/Local/Microsoft/WinGet/Links/k6.exe")):
            if Path(candidate).exists():
                return candidate
        raise SystemExit("k6 not found. Install: winget install k6 --source winget  (or https://grafana.com/docs/k6/latest/set-up/install-k6/)")
    return exe


def cmd_run(project: Project, test_type: str, scale: float, influx: bool, dry_run: bool,
            allow_production: bool, headed: bool, extra_env: list[str]) -> None:
    main_js = project.scripts_dir / "main.js"
    if not main_js.exists():
        raise SystemExit("No scripts. Run: python -m perfagent generate " + project.name)
    workload = read_json(project.scripts_dir / "workload.json")
    if test_type not in workload["profiles"]:
        raise SystemExit(f"Unknown test type '{test_type}'. Options: {', '.join(workload['profiles'])}")

    base_url = project.target.get("api_base_url") or ""
    web_url = project.target.get("web_url") or base_url
    # --- safety gates ---------------------------------------------------
    project.check_host_allowed(base_url)
    if any(s["kind"] == "web" for s in workload["scenarios"]):
        project.check_host_allowed(web_url)
    if project.environment in ("prod", "production") and not allow_production:
        raise SystemExit("Safety: environment is production. Re-run with --allow-production only with written approval and a change ticket.")
    if test_type != "smoke" and not dry_run:
        last = latest_run(project, "smoke")
        if not last or last.get("exit_code") not in (0, 99):
            print("WARNING: no successful smoke run on record - run '--type smoke' first to validate scripts and data.")

    run_id = f"{datetime.now():%Y%m%d-%H%M%S}_{test_type}"
    run_dir = project.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    project.load_env()
    k6 = find_k6()
    cmd = [k6, "run", str(main_js), "--quiet", "--tag", f"testid={run_id}",
           "-e", f"TEST_TYPE={test_type}", "-e", f"BASE_URL={base_url}", "-e", f"WEB_URL={web_url}",
           "-e", f"SCALE={scale}", "-e", f"SUMMARY_PATH={run_dir / 'summary.json'}"]
    for kv in extra_env:
        cmd += ["-e", kv]
    if influx:
        cmd += ["--out", f"influxdb={(project.cfg.get('monitoring') or {}).get('influxdb_url', 'http://localhost:8086/k6')}"]

    env = {**os.environ,
           "K6_WEB_DASHBOARD": "true",
           "K6_WEB_DASHBOARD_EXPORT": str(run_dir / "dashboard.html"),
           "K6_WEB_DASHBOARD_PERIOD": "5s",
           "K6_BROWSER_HEADLESS": "false" if headed else "true"}

    print("Command:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    if dry_run:
        run_dir.rmdir()
        return
    print("Live dashboard: http://localhost:5665  (exported to dashboard.html at the end)\n")

    meta = {"run_id": run_id, "project": project.name, "test_type": test_type, "scale": scale,
            "environment": project.environment, "base_url": base_url, "web_url": web_url,
            "started_at": now_iso(), "k6_version": k6_version(k6),
            "scenarios": [s["id"] for s in workload["scenarios"]]}
    write_json(run_dir / "meta.json", meta)
    with open(run_dir / "k6.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        proc.wait()

    meta.update({"finished_at": now_iso(), "exit_code": proc.returncode,
                 "k6_status": K6_EXIT.get(proc.returncode, "error")})
    write_json(run_dir / "meta.json", meta)
    if (run_dir / "summary.json").exists():
        report.build(project, run_dir)
    else:
        print(f"No summary produced (exit {proc.returncode}). See {run_dir / 'k6.log'}")
    raise SystemExit(proc.returncode)


def k6_version(k6: str) -> str:
    try:
        return subprocess.run([k6, "version"], capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return "unknown"


def list_runs(project: Project) -> list[dict]:
    runs = []
    if project.runs_dir.exists():
        for d in sorted(project.runs_dir.iterdir()):
            if (d / "meta.json").exists():
                runs.append(read_json(d / "meta.json"))
    return runs


def latest_run(project: Project, test_type: str) -> dict | None:
    matches = [r for r in list_runs(project) if r["test_type"] == test_type]
    return matches[-1] if matches else None
