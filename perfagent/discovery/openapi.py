"""Parse an OpenAPI 3.x / Swagger 2.0 document into a normalized API model for scenario analysis."""

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml

from perfagent.util import now_iso

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
AUTH_HINT = re.compile(r"(auth|login|logon|signin|sign-in|token|oauth|session|register|signup)", re.I)
SEARCH_HINT = re.compile(r"(search|find|query|filter|list|browse)", re.I)
VERSION_SEGMENT = re.compile(r"^(api|rest|v\d+(\.\d+)?)$", re.I)


def load_spec(source: str) -> tuple[dict, str]:
    if re.match(r"^https?://", source):
        resp = requests.get(source, timeout=30, headers={"Accept": "application/json, application/yaml, */*"})
        resp.raise_for_status()
        text = resp.text
    else:
        text = Path(source).read_text(encoding="utf-8")
    try:
        return json.loads(text), source
    except json.JSONDecodeError:
        return yaml.safe_load(text), source


class RefResolver:
    def __init__(self, spec: dict, max_depth: int = 6):
        self.spec = spec
        self.max_depth = max_depth

    def lookup(self, ref: str):
        if not ref.startswith("#/"):
            return {}  # external refs not supported - treated as opaque
        node = self.spec
        for part in ref[2:].split("/"):
            node = node.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(node, dict) else {}
        return node

    def resolve(self, node, depth: int = 0, seen: tuple = ()):
        """depth counts nested $ref expansions only, so deep but ref-free documents are untouched."""
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                if ref in seen or depth >= self.max_depth:
                    return {"type": "object", "x-ref": ref}
                return self.resolve(self.lookup(ref), depth + 1, seen + (ref,))
            return {k: self.resolve(v, depth, seen) for k, v in node.items()}
        if isinstance(node, list):
            return [self.resolve(v, depth, seen) for v in node]
        return node


def example_from_schema(schema: dict, depth: int = 0):
    """Build a plausible example payload from a JSON schema (used as a starting request body)."""
    if not isinstance(schema, dict) or depth > 5:
        return None
    for key in ("example", "default"):
        if key in schema:
            return schema[key]
    if schema.get("enum"):
        return schema["enum"][0]
    if "allOf" in schema:
        merged: dict = {}
        for sub in schema["allOf"]:
            ex = example_from_schema(sub, depth + 1)
            if isinstance(ex, dict):
                merged.update(ex)
        return merged
    for key in ("oneOf", "anyOf"):
        if schema.get(key):
            return example_from_schema(schema[key][0], depth + 1)
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = next((t for t in typ if t != "null"), "string")
    if typ == "object" or "properties" in schema:
        return {k: example_from_schema(v, depth + 1) for k, v in (schema.get("properties") or {}).items()}
    if typ == "array":
        item = example_from_schema(schema.get("items") or {}, depth + 1)
        return [item] if item is not None else []
    if typ == "integer":
        return schema.get("minimum", 1)
    if typ == "number":
        return schema.get("minimum", 1.0)
    if typ == "boolean":
        return True
    fmt = schema.get("format") if isinstance(schema.get("format"), str) else ""
    return {
        "date-time": "2026-01-01T00:00:00Z",
        "date": "2026-01-01",
        "email": "perf.user@example.com",
        "uuid": "00000000-0000-0000-0000-000000000000",
        "uri": "https://example.com",
        "password": "{{env.PERF_PASSWORD}}",
    }.get(fmt, "string")


def resource_of(path: str) -> str:
    segs = [s for s in path.split("/") if s and not s.startswith("{")]
    segs = [s for s in segs if not VERSION_SEGMENT.match(s)]
    return segs[0] if segs else "root"


def base_url_of(spec: dict, source: str) -> str:
    if spec.get("servers"):
        url = spec["servers"][0].get("url", "")
        for name, var in (spec["servers"][0].get("variables") or {}).items():
            url = url.replace("{" + name + "}", str(var.get("default", "")))
        return urljoin(source, url) if source.startswith("http") else url
    if spec.get("host"):
        scheme = (spec.get("schemes") or ["https"])[0]
        return f"{scheme}://{spec['host']}{spec.get('basePath', '')}"
    return ""


def parse(source: str) -> dict:
    raw, src = load_spec(source)
    spec = RefResolver(raw).resolve(raw)
    is_v2 = "swagger" in raw
    global_security = spec.get("security")
    endpoints = []

    for path, item in (spec.get("paths") or {}).items():
        shared_params = item.get("parameters") or []
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params, body_schema, content_type = [], None, None
            for p in shared_params + (op.get("parameters") or []):
                if is_v2 and p.get("in") == "body":
                    body_schema, content_type = p.get("schema") or {}, "application/json"
                    continue
                schema = p.get("schema") or {k: p[k] for k in ("type", "format", "enum", "default") if k in p}
                params.append({
                    "name": p.get("name"),
                    "in": p.get("in"),
                    "required": bool(p.get("required")),
                    "type": schema.get("type"),
                    "example": p.get("example", example_from_schema(schema)),
                })
            if not is_v2 and op.get("requestBody"):
                content = op["requestBody"].get("content") or {}
                content_type = next((c for c in content if "json" in c), next(iter(content), None))
                if content_type:
                    body_schema = content[content_type].get("schema") or {}

            responses = op.get("responses") or {}
            success = next((c for c in responses if str(c).startswith("2")), None)
            success_schema = None
            if success:
                r = responses[success] or {}
                if is_v2:
                    success_schema = r.get("schema")
                else:
                    rc = r.get("content") or {}
                    ct = next((c for c in rc if "json" in c), None)
                    success_schema = rc[ct].get("schema") if ct else None

            returns_array = isinstance(success_schema, dict) and success_schema.get("type") == "array"
            if returns_array:
                success_schema = success_schema.get("items") or {}
            text = " ".join(filter(None, [path, op.get("operationId"), op.get("summary")]))
            path_params = [p["name"] for p in params if p["in"] == "path"]
            endpoints.append({
                "id": f"{method.upper()} {path}",
                "method": method.upper(),
                "path": path,
                "operation_id": op.get("operationId"),
                "summary": op.get("summary") or op.get("description", "")[:160],
                "tags": op.get("tags") or [],
                "resource": resource_of(path),
                "kind": {"GET": "read", "HEAD": "read", "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}.get(method.upper(), "other"),
                "is_collection": method == "get" and not path_params,
                "path_params": path_params,
                "params": params,
                "content_type": content_type,
                "request_example": example_from_schema(body_schema) if body_schema else None,
                "success_status": int(success) if success and str(success).isdigit() else None,
                "response_fields": sorted((success_schema or {}).get("properties", {}).keys())[:25] if isinstance(success_schema, dict) else [],
                "returns_array": returns_array,
                "responses": [str(c) for c in responses],
                "secured": bool(op.get("security", global_security)),
                "auth_related": bool(AUTH_HINT.search(text)),
                "search_like": bool(SEARCH_HINT.search(text)) or (method == "get" and any(p["in"] == "query" for p in params)),
                "deprecated": bool(op.get("deprecated")),
            })

    # dependency hints: an endpoint needing {xId} can get it from a create/list endpoint of the same resource
    by_resource: dict[str, list] = {}
    for ep in endpoints:
        by_resource.setdefault(ep["resource"], []).append(ep)
    for ep in endpoints:
        if ep["path_params"]:
            ep["id_providers"] = [o["id"] for o in by_resource[ep["resource"]]
                                  if o is not ep and not o["path_params"] and (o["kind"] == "create" or o["is_collection"])]

    resources = {
        name: {
            "endpoints": [e["id"] for e in eps],
            "crud": sorted({e["kind"] for e in eps}),
            "tags": sorted({t for e in eps for t in e["tags"]}),
        }
        for name, eps in by_resource.items()
    }

    sec_schemes = (spec.get("components") or {}).get("securitySchemes") or spec.get("securityDefinitions") or {}
    return {
        "generated_at": now_iso(),
        "source": src,
        "spec_version": raw.get("openapi") or raw.get("swagger"),
        "title": (spec.get("info") or {}).get("title"),
        "description": ((spec.get("info") or {}).get("description") or "")[:1000],
        "base_url": base_url_of(raw, src),
        "security_schemes": {k: {"type": v.get("type"), "scheme": v.get("scheme"), "in": v.get("in"), "name": v.get("name")} for k, v in sec_schemes.items()},
        "stats": {
            "endpoints": len(endpoints),
            "resources": len(resources),
            "by_kind": {k: sum(1 for e in endpoints if e["kind"] == k) for k in ("read", "create", "update", "delete")},
            "auth_endpoints": [e["id"] for e in endpoints if e["auth_related"]],
        },
        "resources": resources,
        "endpoints": endpoints,
    }
