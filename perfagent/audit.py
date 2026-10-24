"""Front-end audits: Playwright logs in (reusing a catalog web scenario), Lighthouse audits pages in the same browser.

Complements load tests: a single user on a quiet system, with fixed mobile/desktop emulation, reporting
lab Core Web Vitals, the performance score and concrete fixes (unused JS, image weight, render-blocking...).
Each page is audited `runs` times per form factor; the median is reported and budgets are enforced.
"""

import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from perfagent import catalog as cat
from perfagent.browser_steps import run_steps
from perfagent.config import ROOT, Project
from perfagent.util import now_iso, read_json, render, slug, write_json, write_text

DEFAULT_BUDGETS = {"performance": 80, "lcp_ms": 2500, "fcp_ms": 1800, "tbt_ms": 200, "cls": 0.1}  # Google "good" thresholds
METRICS = {  # report key -> (lighthouse audit id, unit)
    "lcp_ms": ("largest-contentful-paint", "ms"),
    "fcp_ms": ("first-contentful-paint", "ms"),
    "tbt_ms": ("total-blocking-time", "ms"),
    "cls": ("cumulative-layout-shift", ""),
    "si_ms": ("speed-index", "ms"),
    "ttfb_ms": ("server-response-time", "ms"),
    "tti_ms": ("interactive", "ms"),
}
CATEGORIES = ("performance", "accessibility", "best-practices", "seo")


def find_lighthouse() -> list[str]:
    local = ROOT / "node_modules" / ".bin" / ("lighthouse.cmd" if os.name == "nt" else "lighthouse")
    if local.exists():
        return [str(local)]
    if shutil.which("lighthouse"):
        return [shutil.which("lighthouse")]
    raise SystemExit("Lighthouse not found. Install the Node tooling once: npm install   (needs Node.js 22.19+)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def audit_config(project: Project) -> dict:
    cfg = project.cfg.get("audit") or {}
    pages = cfg.get("pages") or [{"name": "Home", "url": "/", "auth": False}]
    return {
        "login_scenario": cfg.get("login_scenario"),
        "pages": pages,
        "form_factors": cfg.get("form_factors") or ["mobile", "desktop"],
        "runs": int(cfg.get("runs") or 3),
        "budgets": {**DEFAULT_BUDGETS, **(cfg.get("budgets") or {})},
    }


def login_variables(project: Project, scenario: dict) -> dict:
    """First data row of the scenario's data file + .env secrets (already in os.environ)."""
    variables = {}
    if scenario.get("data_file"):
        from perfagent.util import read_csv_rows
        rows = read_csv_rows(project.data_dir / scenario["data_file"])
        if rows:
            variables.update(rows[0])
    return variables


def run_lighthouse(lh: list[str], url: str, port: int, form_factor: str, keep_session: bool, out_base: Path) -> dict:
    cmd = lh + [url, f"--port={port}", "--output=json", "--output=html", f"--output-path={out_base}", "--quiet",
                f"--only-categories={','.join(CATEGORIES)}", "--max-wait-for-load=60000"]
    if form_factor == "desktop":
        cmd.append("--preset=desktop")
    if keep_session:
        cmd.append("--disable-storage-reset")  # keep the login cookies (cache is therefore warm)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    try:
        out, _ = proc.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)  # lighthouse.cmd -> node: kill the whole tree or the pipes never close on Windows
        proc.communicate()
        raise RuntimeError(f"lighthouse timed out after 240s on {url}")
    report = Path(f"{out_base}.report.json")
    if proc.returncode != 0 or not report.exists():
        raise RuntimeError(f"lighthouse failed for {url}: {(out or '').strip()[-400:]}")
    return json.loads(report.read_text(encoding="utf-8"))


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        subprocess.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True)
        subprocess.run(["kill", "-TERM", str(pid)], capture_output=True)


def launch_browser(pw, profile: Path, port: int | None, headed: bool):
    """Persistent context = the browser's default context, which is where Lighthouse opens its tab."""
    args = [f"--remote-debugging-port={port}"] if port else []
    try:  # full Chromium in new headless mode gives Lighthouse the most faithful browser
        return pw.chromium.launch_persistent_context(str(profile), channel="chromium", headless=not headed, args=args)
    except Exception:
        return pw.chromium.launch_persistent_context(str(profile), headless=not headed, args=args)


def restore_session(context, state: dict) -> None:
    """Inject a saved login (cookies + localStorage) into a fresh browser."""
    if state.get("cookies"):
        context.add_cookies(state["cookies"])
    origins = [o for o in state.get("origins") or [] if o.get("localStorage")]
    if origins:
        page = context.pages[0] if context.pages else context.new_page()
        for o in origins:
            page.goto(o["origin"], wait_until="domcontentloaded")
            page.evaluate("items => items.forEach(i => localStorage.setItem(i.name, i.value))", o["localStorage"])
        page.goto("about:blank")


def summarize_lhr(lhr: dict) -> dict:
    audits = lhr["audits"]
    out = {"final_url": lhr.get("finalDisplayedUrl") or lhr.get("finalUrl"),
           "scores": {c: round((lhr["categories"][c]["score"] or 0) * 100) for c in CATEGORIES if c in lhr["categories"]},
           "bytes_kb": round((audits.get("total-byte-weight", {}).get("numericValue") or 0) / 1024),
           "requests": len(((audits.get("network-requests") or {}).get("details") or {}).get("items") or [])}
    for key, (aid, _) in METRICS.items():
        v = (audits.get(aid) or {}).get("numericValue")
        out[key] = round(v, 3) if key == "cls" and v is not None else (round(v) if v is not None else None)
    # improvement suggestions: failing audits that carry estimated savings (works for classic + insight audits)
    fixes = []
    for a in audits.values():
        score = a.get("score")
        if score is None or score >= 0.9 or a.get("scoreDisplayMode") in ("informative", "notApplicable", "manual"):
            continue
        details = a.get("details") or {}
        saving_ms = details.get("overallSavingsMs") or sum(v for k, v in (a.get("metricSavings") or {}).items() if k in ("LCP", "FCP", "TBT") and v)
        saving_kb = round((details.get("overallSavingsBytes") or 0) / 1024)
        if saving_ms or saving_kb:
            fixes.append({"title": a.get("title"), "display": a.get("displayValue") or "", "saving_ms": round(saving_ms or 0), "saving_kb": saving_kb})
    out["fixes"] = sorted(fixes, key=lambda f: (-f["saving_ms"], -f["saving_kb"]))[:5]
    return out


def median_result(runs: list[dict]) -> dict:
    """Median per metric; the run with the median performance score is the representative (its HTML is kept)."""
    ordered = sorted(runs, key=lambda r: r["scores"].get("performance", 0))
    rep = dict(ordered[len(ordered) // 2])
    for key in list(METRICS) + ["bytes_kb"]:
        vals = [r[key] for r in runs if r.get(key) is not None]
        rep[key] = statistics.median(vals) if vals else None
    rep["scores"] = {c: statistics.median([r["scores"][c] for r in runs if c in r["scores"]]) for c in rep["scores"]}
    rep["spread_performance"] = (ordered[0]["scores"].get("performance"), ordered[-1]["scores"].get("performance"))
    return rep


def check_budgets(result: dict, budgets: dict) -> list[dict]:
    checks = []
    if "performance" in budgets:
        checks.append({"metric": "performance score", "value": result["scores"].get("performance"), "budget": f">= {budgets['performance']}",
                       "ok": (result["scores"].get("performance") or 0) >= budgets["performance"]})
    for key in ("lcp_ms", "fcp_ms", "tbt_ms", "cls", "si_ms", "ttfb_ms"):
        if key in budgets and result.get(key) is not None:
            checks.append({"metric": key, "value": result[key], "budget": f"<= {budgets[key]}", "ok": result[key] <= budgets[key]})
    return checks


def latest_load_vitals(project: Project) -> list[dict]:
    """Browser vitals measured under load in the latest load-type run, for side-by-side comparison."""
    for d in sorted(project.runs_dir.glob("*"), reverse=True) if project.runs_dir.exists() else []:
        rep = d / "report.json"
        if rep.exists() and d.name.split("_")[-1] in ("load", "stress", "spike", "soak"):
            r = read_json(rep)
            rows = [{"id": s["id"], "name": s["name"], **(s.get("web") or {})} for s in r["scenarios"] if s["kind"] == "web"]
            return [{"run_id": d.name, **row} for row in rows]
    return []


def cmd_audit(project: Project, runs: int | None, form_factor: str | None, page_filter: str | None, headed: bool) -> None:
    from playwright.sync_api import sync_playwright

    project.load_env()
    cfg = audit_config(project)
    if runs:
        cfg["runs"] = runs
    if form_factor:
        cfg["form_factors"] = ["mobile", "desktop"] if form_factor == "both" else [form_factor]
    pages = [p for p in cfg["pages"] if not page_filter or page_filter.lower() in p["name"].lower()]
    if not pages:
        raise SystemExit(f"No audit pages match '{page_filter}'. Configure audit.pages in project.yaml")
    web_url = (project.target.get("web_url") or "").rstrip("/")
    api_url = (project.target.get("api_base_url") or web_url).rstrip("/")
    project.check_host_allowed(web_url)

    login = None
    if any(p.get("auth") for p in pages):
        sid = cfg["login_scenario"]
        scenarios = {s["id"]: s for s in cat.load(project)["scenarios"]}
        if not sid or sid not in scenarios:
            raise SystemExit("Pages marked auth: true need audit.login_scenario (a catalog scenario whose steps log in)")
        login = scenarios[sid]

    lh = find_lighthouse()
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}_audit"
    run_dir = project.runs_dir / run_id
    (run_dir / "lighthouse").mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="perfagent-lh-"))
    results, errors = [], []
    total = len(pages) * len(cfg["form_factors"]) * cfg["runs"]
    print(f"Auditing {len(pages)} page(s) x {len(cfg['form_factors'])} form factor(s) x {cfg['runs']} run(s) = {total} Lighthouse runs")

    # Lighthouse only works reliably for ONE run per browser that Playwright drives, so: log in once and save the
    # session, then give every Lighthouse run its own fresh browser with that session injected.
    state = None
    with sync_playwright() as pw:
        if login:
            print(f"  logging in with {login['id']} ({login['name']})")
            context = launch_browser(pw, tmp / "login-profile", None, headed)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                run_steps(page, list(login.get("steps") or []), login_variables(project, login), web_url, api_url)
                state = context.storage_state()
            except RuntimeError as e:
                errors.append(str(e))
                print(f"  ! {e}")
            context.close()

        for p in pages:
            if p.get("auth") and state is None:
                errors.append(f"{p['name']}: skipped - login failed")
                continue
            for ff in cfg["form_factors"]:
                runs_out = []
                for n in range(1, cfg["runs"] + 1):
                    base = tmp / f"{slug(p['name'])}_{ff}_{n}"
                    port = free_port()
                    context = launch_browser(pw, tmp / f"profile_{base.name}", port, headed)
                    try:
                        if p.get("auth"):
                            restore_session(context, state)
                        lhr = run_lighthouse(lh, web_url + p["url"], port, ff, bool(p.get("auth")), base)
                        runs_out.append({**summarize_lhr(lhr), "_base": base})
                        print(f"  {p['name']:<28} {ff:<8} run {n}: performance {runs_out[-1]['scores'].get('performance')}  LCP {runs_out[-1]['lcp_ms']}ms")
                    except Exception as e:
                        errors.append(f"{p['name']} {ff} run {n}: {e}")
                        print(f"  ! {errors[-1][:200]}")
                    finally:
                        context.close()
                if not runs_out:
                    continue
                med = median_result(runs_out)
                html_name = f"{slug(p['name'])}_{ff}.html"
                shutil.copy(f"{med['_base']}.report.html", run_dir / "lighthouse" / html_name)
                redirected = bool(p.get("auth")) and "login" in (med["final_url"] or "").lower() and "login" not in p["url"].lower()
                med.pop("_base", None)
                results.append({"page": p["name"], "url": p["url"], "auth": bool(p.get("auth")), "form_factor": ff,
                                "runs": len(runs_out), "html": f"lighthouse/{html_name}", "redirected_to_login": redirected,
                                "budgets": check_budgets(med, cfg["budgets"]), **med})
    shutil.rmtree(tmp, ignore_errors=True)

    verdict = "PASS" if results and not errors and all(b["ok"] for r in results for b in r["budgets"]) and not any(r["redirected_to_login"] for r in results) else "FAIL"
    report = {"meta": {"run_id": run_id, "project": project.name, "test_type": "audit", "web_url": web_url,
                       "environment": project.environment, "generated_at": now_iso(), "lighthouse": subprocess.run(lh + ["--version"], capture_output=True, text=True).stdout.strip(),
                       "runs_per_page": cfg["runs"], "form_factors": cfg["form_factors"]},
              "verdict": verdict, "budgets": cfg["budgets"], "results": results, "errors": errors,
              "load_vitals": latest_load_vitals(project)}
    write_json(run_dir / "report.json", report)
    write_json(run_dir / "meta.json", {"run_id": run_id, "project": project.name, "test_type": "audit", "base_url": web_url,
                                       "started_at": report["meta"]["generated_at"], "exit_code": 0 if verdict == "PASS" else 1,
                                       "k6_status": f"audit {verdict.lower()}"})
    write_text(run_dir / "report.html", render("audit.html.j2", r=report, project=project.cfg))
    md = render("audit.md.j2", r=report)
    write_text(run_dir / "report.md", md)
    print("\n" + md)
    print(f"HTML report: {run_dir / 'report.html'}")
    sys.exit(0 if verdict == "PASS" else 1)
