"""Replay catalog steps in a Playwright (Python) browser - used by `audit` to log in before Lighthouse runs.

Mirrors the k6 runtime (perfagent/k6lib/common.js) for the same catalog schema: web actions, compute steps
(built-in helpers such as partialPin) and simple api steps sent through the browser's own request context,
so cookies/sessions are shared. Project hooks.js (JavaScript) helpers are not available here.
"""

import json
import os
import random
import re
import time
import uuid

PLACEHOLDER = re.compile(r"\{\{\s*([\w.$]+)\s*\}\}")


def _get_path(obj, path):
    for key in str(path).split("."):
        if key == "":
            continue
        if isinstance(obj, list):
            obj = obj[int(key)] if key.isdigit() and int(key) < len(obj) else None
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def _lookup(name, variables):
    if name.startswith("env."):
        return os.environ.get(name[4:])
    dynamic = {"$uuid": lambda: str(uuid.uuid4()), "$randomInt": lambda: random.randint(0, 999999),
               "$timestamp": lambda: int(time.time() * 1000), "$randomString": lambda: uuid.uuid4().hex[:8],
               "$vu": lambda: 1, "$iter": lambda: 0}
    if name in dynamic:
        return dynamic[name]()
    if name in variables:
        return variables[name]
    return _get_path(variables, name) if "." in name else None


def interpolate(value, variables):
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value.strip())
        if whole:
            v = _lookup(whole[1], variables)
            return value if v is None else v
        return PLACEHOLDER.sub(lambda m: str(v) if (v := _lookup(m[1], variables)) is not None else m[0], value)
    if isinstance(value, list):
        return [interpolate(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: interpolate(v, variables) for k, v in value.items()}
    return value


def _to_list(v):
    if isinstance(v, list):
        return v
    if v is None:
        return []
    if isinstance(v, str):
        return re.findall(r"\d+", v)
    return [v]


def _partial_pin(secret, positions, base=1):
    digits = re.sub(r"\D", "", str(secret))
    out = []
    for p in _to_list(positions):
        i = int(p) - int(base)
        if not 0 <= i < len(digits):
            raise ValueError(f"partialPin: position {p} outside secret of length {len(digits)}")
        out.append(digits[i])
    return out


HOOKS = {
    "partialPin": _partial_pin,
    "chars": lambda secret, positions, base=1: [str(secret)[int(p) - int(base)] for p in _to_list(positions)],
    "pick": lambda lst, index=0: _to_list(lst)[int(index or 0)],
    "randomItem": lambda lst: random.choice(lst if isinstance(lst, list) else _to_list(lst)),
    "join": lambda lst, sep="": str(sep).join(str(x) for x in _to_list(lst)),
    "upper": lambda s: str(s).upper(),
    "lower": lambda s: str(s).lower(),
    "concat": lambda *parts: "".join(str(p) for p in parts),
}


def apply_compute(step, variables):
    for name, spec in (step.get("compute") or {}).items():
        fn = HOOKS.get(spec.get("fn"))
        if fn is None:
            raise ValueError(f"compute {name}: '{spec.get('fn')}' is not a built-in (project hooks.js helpers run only in k6)")
        variables[name] = fn(*interpolate(spec.get("args") or [], variables))


def _regex(text, pattern, all_matches=False):
    if all_matches:
        found = [m.group(1) if m.groups() else m.group(0) for m in re.finditer(pattern, text)]
        return found or None
    m = re.search(pattern, text)
    return (m.group(1) if m.groups() else m.group(0)) if m else None


def _web(page, step, variables, web_url):
    sel = interpolate(step["selector"], variables) if step.get("selector") else None
    val = str(interpolate(step["value"], variables)) if step.get("value") is not None else None
    timeout = step.get("timeout", 15000)
    action = step["action"]
    if action == "goto":
        page.goto(web_url + interpolate(step.get("url", "/"), variables), wait_until="networkidle", timeout=60000)
    elif action == "click":
        if step.get("navigates"):
            with page.expect_navigation(timeout=60000):
                page.locator(sel).click(timeout=timeout)
        else:
            page.locator(sel).click(timeout=timeout)
    elif action == "fill":
        page.locator(sel).fill(val, timeout=timeout)
    elif action == "select":
        page.locator(sel).select_option(val, timeout=timeout)
    elif action == "press":
        page.locator(sel).press(step.get("key", "Enter"), timeout=timeout)
    elif action == "waitFor":
        page.locator(sel).first.wait_for(state="visible", timeout=timeout)
    elif action == "assertText":
        text = page.locator(sel).first.text_content(timeout=timeout) or ""
        if val not in text:
            raise AssertionError(f"text '{val}' not found in {sel}")
    elif action == "extract":
        loc = page.locator(sel).first
        loc.wait_for(state="attached", timeout=timeout)
        raw = loc.get_attribute(step["attr"]) if step.get("attr") else loc.text_content()
        v = raw.strip() if raw is not None else None
        if v is not None and step.get("pattern"):
            v = _regex(v, step["pattern"], step.get("all"))
        if v is None:
            raise ValueError(f"extract {step.get('var')}: nothing matched in {sel}")
        variables[step["var"]] = v
    else:
        raise ValueError(f"unknown web action {action}")


def _api(context, step, variables, api_url):
    path = interpolate(step["path"], variables)
    path = re.sub(r"\{(\w+)\}", lambda m: str(variables.get(m[1], m[0])), path)
    params = {k: str(v) for k, v in interpolate(step.get("query") or {}, variables).items()}
    kwargs = {"params": params or None, "headers": interpolate(step.get("headers") or {}, variables) or None,
              "max_redirects": 0 if step.get("follow_redirects") is False else 10}
    if step.get("form"):
        kwargs["form"] = {k: str(v) for k, v in interpolate(step["form"], variables).items()}
    elif step.get("body") is not None:
        kwargs["data"] = json.dumps(interpolate(step["body"], variables))
        kwargs["headers"] = {**(kwargs["headers"] or {}), "Content-Type": "application/json"}
    res = context.request.fetch(api_url + path, method=step["method"].upper(), **kwargs)
    expected = step.get("expect") or [200, 201, 202, 204]
    if res.status not in expected:
        raise AssertionError(f"{step['method']} {path} returned {res.status}, expected {expected}")
    text = res.text()
    for name, spec in (step.get("extract") or {}).items():
        if isinstance(spec, str) or (spec.get("from", "json") == "json"):
            data = json.loads(text) if text else None
            v = _get_path(data, spec if isinstance(spec, str) else spec.get("path", ""))
            if isinstance(spec, dict) and spec.get("random") and isinstance(v, list):
                v = random.choice(v) if v else None
            if isinstance(spec, dict) and spec.get("field") and v is not None:
                v = _get_path(v, spec["field"])
        elif spec["from"] == "regex":
            v = _regex(text, spec["pattern"], spec.get("all"))
        elif spec["from"] == "header":
            v = res.headers.get(str(spec["name"]).lower())
        else:
            raise ValueError(f"extract source '{spec['from']}' not supported in audit login")
        if v is None:
            raise ValueError(f"extract {name} failed on {path}")
        variables[name] = v
    if step.get("assert_text") and interpolate(step["assert_text"], variables) not in text:
        raise AssertionError(f"'{step['assert_text']}' not in response of {path}")


def run_steps(page, steps, variables, web_url, api_url, log=print):
    """Execute catalog steps in order; raises on the first failure with the step name."""
    for i, step in enumerate(steps, 1):
        name = step.get("name") or step.get("action") or step.get("path") or "step"
        try:
            apply_compute(step, variables)
            kind = step.get("kind", "api")
            if kind == "web":
                _web(page, step, variables, web_url)
            elif kind == "api":
                _api(page.context, step, variables, api_url)
            log(f"    {i:>2}. ok   {name}")
        except Exception as e:  # report which step broke the login
            raise RuntimeError(f"login step {i} '{name}' failed: {e}") from e
