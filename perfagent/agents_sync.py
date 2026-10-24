"""Keep GitHub Copilot and Claude Code customisation files in step, with one source of truth each.

Sources (edit these)                         Generated (never edit)
  AGENTS.md                         ->  read directly by Copilot; imported by CLAUDE.md (@AGENTS.md)
  .claude/skills/*/SKILL.md         ->  read directly by both tools (no copy - duplicates load twice in Copilot)
  .github/instructions/*.instructions.md  ->  .claude/rules/*.md        (applyTo -> paths)
  .github/prompts/*.prompt.md             ->  .claude/commands/*.md     (${input:x} -> $ARGUMENTS)
"""

import re

import yaml

from perfagent.config import ROOT

GENERATED = "<!-- GENERATED from {src} by `python -m perfagent agents sync` - edit the source, not this file -->"
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONT = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.S)


def split_front(text: str) -> tuple[dict, str]:
    m = FRONT.match(text)
    return (yaml.safe_load(m[1]) or {}, m[2]) if m else ({}, text)


def join_front(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000) + "---\n" + body


def rule_from_instruction(src) -> str:
    meta, body = split_front(src.read_text(encoding="utf-8"))
    out = {}
    if meta.get("description"):
        out["description"] = meta["description"]
    if meta.get("applyTo"):
        out["paths"] = [p.strip() for p in str(meta["applyTo"]).split(",") if p.strip()]
    rel = src.relative_to(ROOT).as_posix()
    return join_front(out, "\n" + GENERATED.format(src=rel) + "\n" + body.lstrip("\n"))


def command_from_prompt(src) -> str:
    meta, body = split_front(src.read_text(encoding="utf-8"))
    names = re.findall(r"\$\{input:(\w+)(?::[^}]*)?\}", body)
    body = re.sub(r"\$\{input:(\w+)(?::[^}]*)?\}", r"<\1>", body)
    out = {k: meta[k] for k in ("description", "argument-hint") if meta.get(k)}
    rel = src.relative_to(ROOT).as_posix()
    args = f"\nArguments ({', '.join(dict.fromkeys(names))}): $ARGUMENTS\nAsk for any value not supplied.\n" if names else ""
    return join_front(out, "\n" + GENERATED.format(src=rel) + "\n" + body.lstrip("\n") + args)


def planned() -> dict:
    files = {}
    for src in sorted((ROOT / ".github" / "instructions").glob("*.instructions.md")):
        files[ROOT / ".claude" / "rules" / src.name.replace(".instructions.md", ".md")] = rule_from_instruction(src)
    for src in sorted((ROOT / ".github" / "prompts").glob("*.prompt.md")):
        files[ROOT / ".claude" / "commands" / src.name.replace(".prompt.md", ".md")] = command_from_prompt(src)
    return files


def validate() -> list[str]:
    problems = []
    skills_dir = ROOT / ".claude" / "skills"
    for skill in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        f = skill / "SKILL.md"
        if not f.exists():
            problems.append(f"{skill.name}: missing SKILL.md")
            continue
        meta, _ = split_front(f.read_text(encoding="utf-8"))
        name, desc = meta.get("name", ""), meta.get("description", "")
        if name != skill.name:
            problems.append(f"{skill.name}: frontmatter name '{name}' must equal the folder name")
        if not SKILL_NAME.match(name or "") or len(name) > 64:
            problems.append(f"{skill.name}: name must be lowercase letters/digits/hyphens, <= 64 chars")
        if not desc or len(desc) > 1024:
            problems.append(f"{skill.name}: description required, <= 1024 chars (has {len(desc)})")
    if (ROOT / ".github" / "skills").exists():
        problems.append(".github/skills exists - Copilot also reads .claude/skills, so skills would load twice. Keep them only in .claude/skills.")
    if (ROOT / ".claude" / "agents").exists():
        problems.append(".claude/agents exists - VS Code Copilot also loads it next to .github/agents; keep custom agents in .github/agents only.")
    claude_md = ROOT / "CLAUDE.md"
    if not claude_md.exists() or "@AGENTS.md" not in claude_md.read_text(encoding="utf-8"):
        problems.append("CLAUDE.md must import the shared guide with '@AGENTS.md'")
    for agent in (ROOT / ".github" / "agents").glob("*.agent.md"):
        meta, _ = split_front(agent.read_text(encoding="utf-8"))
        known = {a.name.replace(".agent.md", "") for a in (ROOT / ".github" / "agents").glob("*.agent.md")} | {"agent", "ask", "plan"}
        for h in meta.get("handoffs") or []:
            if h.get("agent") not in known:
                problems.append(f"{agent.name}: handoff target '{h.get('agent')}' not found")
    return problems


def cmd_agents(action: str) -> None:
    problems = validate()
    files = planned()
    stale = [p for p, content in files.items() if not p.exists() or p.read_text(encoding="utf-8") != content]
    expected_dirs = {ROOT / ".claude" / "rules", ROOT / ".claude" / "commands"}
    orphans = [p for d in expected_dirs if d.exists() for p in d.glob("*.md")
               if p not in files and "GENERATED from" in p.read_text(encoding="utf-8")]

    if action == "sync":
        for p in stale:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(files[p], encoding="utf-8")
            print(f"  wrote  {p.relative_to(ROOT).as_posix()}")
        for p in orphans:
            p.unlink()
            print(f"  removed {p.relative_to(ROOT).as_posix()} (source deleted)")
        if not stale and not orphans:
            print("  already in sync")
    else:
        for p in stale:
            print(f"  out of date: {p.relative_to(ROOT).as_posix()}")
        for p in orphans:
            print(f"  orphaned:    {p.relative_to(ROOT).as_posix()}")
    for pr in problems:
        print(f"  ! {pr}")
    if problems or (action == "check" and (stale or orphans)):
        raise SystemExit(1)
    print(f"OK - {len(files)} generated files, {len(list((ROOT / '.claude' / 'skills').glob('*/SKILL.md')))} skills validated")
