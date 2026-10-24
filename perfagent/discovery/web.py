"""Read-only crawler: maps pages, navigation, forms and the XHR/fetch API calls each page triggers.

It never submits forms (except the optional configured login) and skips links matching exclude patterns,
so it is safe to point at shared test environments.
"""

import os
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from perfagent.util import normalize_path, now_iso

EXTRACT_JS = """
() => {
  const sel = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.getAttribute('name')) return el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    if (el.getAttribute('data-testid')) return '[data-testid="' + el.getAttribute('data-testid') + '"]';
    return null;
  };
  const text = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 60);
  return {
    links: [...document.querySelectorAll('a[href]')].map(a => ({ href: a.href, text: text(a) })),
    forms: [...document.querySelectorAll('form')].map(f => ({
      selector: sel(f), action: f.getAttribute('action'), method: (f.getAttribute('method') || 'get').toLowerCase(),
      fields: [...f.querySelectorAll('input, select, textarea')].filter(i => i.type !== 'hidden').map(i => ({
        selector: sel(i), tag: i.tagName.toLowerCase(), type: i.type || null, name: i.name || null,
        placeholder: i.placeholder || null, required: i.required, label: (i.labels && i.labels[0] ? i.labels[0].innerText.trim() : null)
      })),
      submit: (() => { const b = f.querySelector('button[type=submit], input[type=submit], button:not([type])'); return b ? { selector: sel(b), text: text(b) } : null; })()
    })),
    buttons: [...document.querySelectorAll('button, [role=button]')].slice(0, 40).map(b => ({ selector: sel(b), text: text(b) })).filter(b => b.text),
    headings: [...document.querySelectorAll('h1, h2')].slice(0, 10).map(h => h.innerText.trim()).filter(Boolean),
    nav: [...document.querySelectorAll('nav a, header a')].slice(0, 40).map(a => ({ href: a.href, text: text(a) })),
  };
}
"""

TIMING_JS = """
() => {
  const n = performance.getEntriesByType('navigation')[0];
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  return n ? { ttfb_ms: Math.round(n.responseStart), dom_content_loaded_ms: Math.round(n.domContentLoadedEventEnd),
               load_ms: Math.round(n.loadEventEnd), transfer_kb: Math.round((n.transferSize || 0) / 1024),
               fcp_ms: fcp ? Math.round(fcp.startTime) : null,
               resources: performance.getEntriesByType('resource').length } : {};
}
"""


def _canon(url: str) -> str:
    u = urlparse(urldefrag(url)[0])
    return u._replace(path=u.path or "/").geturl()


def _login(page, start_url: str, login: dict) -> None:
    page.goto(urljoin(start_url, login["url"]), wait_until="networkidle")
    page.fill(login["username_selector"], os.environ.get(login.get("username_env", "PERF_USER"), ""))
    page.fill(login["password_selector"], os.environ.get(login.get("password_env", "PERF_PASSWORD"), ""))
    page.click(login["submit_selector"])
    page.wait_for_load_state("networkidle")


def crawl(start_url: str, max_pages: int = 25, max_depth: int = 3, headless: bool = True,
          exclude_patterns: list | None = None, login: dict | None = None) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit("Playwright is required for web discovery: pip install playwright && python -m playwright install chromium") from e

    origin = urlparse(start_url).netloc
    excludes = [p.lower() for p in (exclude_patterns or [])]
    seen, pages, api_calls, edges = set(), [], {}, []
    queue = deque([(_canon(start_url), 0)])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        current = {"url": None}

        def on_response(resp):
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            u = urlparse(req.url)
            key = f"{req.method} {u.netloc}{normalize_path(u.path)}"
            entry = api_calls.setdefault(key, {"method": req.method, "host": u.netloc, "path": normalize_path(u.path),
                                                "statuses": set(), "seen_on": set(), "count": 0})
            entry["count"] += 1
            entry["statuses"].add(resp.status)
            if current["url"]:
                entry["seen_on"].add(urlparse(current["url"]).path or "/")

        page.on("response", on_response)
        if login:
            _login(page, start_url, login)

        while queue and len(pages) < max_pages:
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            current["url"] = url
            record = {"url": url, "path": urlparse(url).path or "/", "depth": depth}
            try:
                resp = page.goto(url, wait_until="networkidle", timeout=30000)
                record["status"] = resp.status if resp else None
                record["title"] = page.title()
                record["timing"] = page.evaluate(TIMING_JS)
                record.update(page.evaluate(EXTRACT_JS))
            except Exception as e:  # keep crawling; record the failure
                record["error"] = str(e)[:300]
                pages.append(record)
                continue

            for link in record.get("links", []):
                target = _canon(link["href"])
                p = urlparse(target)
                if p.scheme not in ("http", "https") or p.netloc != origin:
                    continue
                if any(x in target.lower() or x in (link["text"] or "").lower() for x in excludes):
                    continue
                edges.append([record["path"], p.path or "/"])
                if target not in seen and depth + 1 <= max_depth:
                    queue.append((target, depth + 1))
            # keep the model compact: unique same-origin links only
            record["links"] = sorted({urlparse(l["href"]).path for l in record.get("links", [])
                                      if urlparse(l["href"]).netloc == origin})[:60]
            pages.append(record)

        browser.close()

    calls = sorted(api_calls.values(), key=lambda c: -c["count"])
    for c in calls:
        c["statuses"] = sorted(c["statuses"])
        c["seen_on"] = sorted(c["seen_on"])
    return {
        "generated_at": now_iso(),
        "start_url": start_url,
        "stats": {"pages": len(pages), "forms": sum(len(p.get("forms", [])) for p in pages), "api_calls": len(calls)},
        "pages": pages,
        "api_calls": calls,
        "navigation_edges": sorted({tuple(e) for e in edges}),
    }
