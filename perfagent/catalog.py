"""Scenario catalog: scoring, validation, coverage and the human review/approval gate."""

from perfagent.config import Project
from perfagent.util import now_iso, read_json, read_yaml, render, write_text, write_yaml

# Risk-based prioritisation (weights sum to 1). Each factor is scored 1-5 by the analyst (Claude Code) and
# can be overridden by the reviewer.
WEIGHTS = {"business_criticality": 0.40, "frequency": 0.35, "technical_risk": 0.25}
BANDS = [(75, "P1"), (50, "P2"), (0, "P3")]
STATUSES = ("proposed", "approved", "rejected", "deferred")
REQUIRED = ("id", "name", "type", "steps", "business_criticality", "frequency", "technical_risk")


def load(project: Project) -> dict:
    if not project.catalog_file.exists():
        raise SystemExit(f"No catalog yet: {project.catalog_file}\nAsk Claude Code to run the 'perf-discover' skill to analyze the app model.")
    return read_yaml(project.catalog_file)


def save(project: Project, catalog: dict) -> None:
    catalog["updated_at"] = now_iso()
    write_yaml(project.catalog_file, catalog)


def validate(catalog: dict) -> list[str]:
    problems = []
    ids = set()
    for s in catalog.get("scenarios", []):
        sid = s.get("id", "?")
        for key in REQUIRED:
            if key not in s:
                problems.append(f"{sid}: missing '{key}'")
        if sid in ids:
            problems.append(f"{sid}: duplicate id")
        ids.add(sid)
        for f in WEIGHTS:
            v = s.get(f)
            if v is not None and not (isinstance(v, (int, float)) and 1 <= v <= 5):
                problems.append(f"{sid}: {f} must be 1-5 (got {v})")
        if s.get("status", "proposed") not in STATUSES:
            problems.append(f"{sid}: status must be one of {STATUSES}")
        for i, st in enumerate(s.get("steps") or [], 1):
            kind = st.get("kind", "api")
            if kind == "api" and not (st.get("method") and st.get("path")):
                problems.append(f"{sid} step {i}: api steps need method + path")
            if kind == "web" and not st.get("action"):
                problems.append(f"{sid} step {i}: web steps need an action")
            if kind == "compute" and not st.get("compute"):
                problems.append(f"{sid} step {i}: compute steps need a compute block")
            if kind not in ("api", "web", "compute"):
                problems.append(f"{sid} step {i}: unknown kind '{kind}'")
        if s.get("data_mode", "shared") not in ("shared", "unique-per-vu"):
            problems.append(f"{sid}: data_mode must be shared or unique-per-vu")
    approved = [s for s in catalog.get("scenarios", []) if s.get("status") == "approved"]
    share = sum(s.get("workload_share", 0) for s in approved)
    if approved and abs(share - 100) > 0.5:
        problems.append(f"warning: approved workload_share totals {share}% (design will normalise to 100%)")
    return problems


def score(catalog: dict) -> dict:
    for s in catalog.get("scenarios", []):
        raw = sum(WEIGHTS[f] * float(s.get(f, 1)) for f in WEIGHTS)
        s["priority_score"] = round(raw / 5 * 100)
        s["priority"] = next(p for limit, p in BANDS if s["priority_score"] >= limit)
        s.setdefault("status", "proposed")
    catalog["scenarios"].sort(key=lambda s: (-s["priority_score"], s["id"]))
    for i, s in enumerate(catalog["scenarios"], 1):
        s["rank"] = i
    catalog["scoring"] = {"weights": WEIGHTS, "bands": {p: f">= {l}" for l, p in BANDS}}
    return catalog


def coverage(project: Project, catalog: dict) -> dict:
    api_file = project.model_dir / "api_model.json"
    if not api_file.exists():
        return {}
    endpoints = {e["id"] for e in read_json(api_file)["endpoints"]}
    used = {f"{st.get('method', '').upper()} {st.get('path')}" for s in catalog.get("scenarios", [])
            if s.get("status") != "rejected" for st in s.get("steps") or [] if st.get("kind", "api") == "api"}
    covered = endpoints & used
    return {"total": len(endpoints), "covered": len(covered),
            "pct": round(100 * len(covered) / len(endpoints)) if endpoints else 0,
            "uncovered": sorted(endpoints - used)}


def print_table(catalog: dict) -> None:
    print(f"{'Rank':<5}{'ID':<9}{'Pri':<5}{'Score':<7}{'Type':<8}{'Share':<7}{'Status':<10}Name")
    for s in catalog["scenarios"]:
        print(f"{s.get('rank', ''):<5}{s['id']:<9}{s.get('priority', ''):<5}{s.get('priority_score', ''):<7}"
              f"{s['type']:<8}{str(s.get('workload_share', '-')) + '%':<7}{s.get('status', 'proposed'):<10}{s['name']}")


def cmd_score(project: Project) -> None:
    catalog = score(load(project))
    problems = validate(catalog)
    save(project, catalog)
    print_table(catalog)
    for p in problems:
        print("  !", p)


def cmd_review(project: Project) -> None:
    catalog = score(load(project))
    save(project, catalog)
    out = project.dir / "review" / "scenario_review.html"
    write_text(out, render("review.html.j2", project=project.cfg, catalog=catalog, weights=WEIGHTS,
                           coverage=coverage(project, catalog), problems=validate(catalog), generated=now_iso(),
                           approved_share=round(sum(s.get("workload_share", 0) for s in catalog["scenarios"] if s.get("status") == "approved"), 1)))
    print_table(catalog)
    print(f"\nReview page: {out}")


def cmd_set_status(project: Project, ids: list[str], status: str, priority: str | None, note: str | None) -> None:
    catalog = score(load(project))
    changed = []
    for s in catalog["scenarios"]:
        if s["id"] in ids or (priority and s.get("priority") == priority) or ids == ["all"]:
            s["status"] = status
            s.setdefault("review", {})
            s["review"].update({"decision": status, "at": now_iso()})
            if note:
                s["review"]["note"] = note
            changed.append(s["id"])
    save(project, catalog)
    print(f"{status}: {', '.join(changed) or 'nothing matched'}")
    cmd_review(project)
