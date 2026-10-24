// perfagent k6 runtime library - copied into each project's scripts/lib on `generate`.
// Do not edit the copy; change perfagent/k6lib/common.js instead.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import encoding from 'k6/encoding';

export const BASE_URL = (__ENV.BASE_URL || '').replace(/\/$/, '');
export const WEB_URL = (__ENV.WEB_URL || BASE_URL).replace(/\/$/, '');
const SCALE = parseFloat(__ENV.SCALE || '1');

export const webStepFailed = new Rate('web_step_failed');
export const webStepDuration = new Trend('web_step_duration', true);

// ---------------------------------------------------------------- options
export function buildOptions(w, testType) {
  const profile = w.profiles[testType];
  if (!profile) throw new Error(`Unknown TEST_TYPE '${testType}'. Use one of: ${Object.keys(w.profiles).join(', ')}`);
  const smoke = testType === 'smoke';
  const scenarios = {};
  const thresholds = {};
  const maxMult = Math.max(1, ...profile.stages.map((s) => s.target));

  for (const s of w.scenarios) {
    if (s.kind === 'web') {
      scenarios[s.id] = smoke
        ? { executor: 'per-vu-iterations', vus: 1, iterations: 1, exec: s.fn }
        : { executor: 'constant-vus', vus: s.users, duration: profile.total_duration, exec: s.fn };
      scenarios[s.id].options = { browser: { type: 'chromium' } };
      thresholds[`browser_web_vital_lcp{scenario:${s.id}}`] = [`p(75)<${s.sla.lcp_ms}`];
      thresholds[`browser_web_vital_cls{scenario:${s.id}}`] = ['p(75)<0.1'];
      thresholds[`browser_web_vital_fcp{scenario:${s.id}}`] = ['max>=0'];
      thresholds[`browser_web_vital_ttfb{scenario:${s.id}}`] = ['max>=0'];
      thresholds[`web_step_failed{scenario:${s.id}}`] = [`rate<${s.sla.error_rate}`];
      for (const t of s.step_tags) thresholds[`web_step_duration{name:${t}}`] = ['max>=0'];
    } else if (smoke) {
      scenarios[s.id] = { executor: 'per-vu-iterations', vus: 1, iterations: profile.iterations || 1, exec: s.fn };
    } else {
      const rate = s.rate_per_min * SCALE;
      scenarios[s.id] = {
        executor: 'ramping-arrival-rate',
        exec: s.fn,
        startRate: 0,
        timeUnit: '1m',
        preAllocatedVUs: Math.ceil(s.users * SCALE * maxMult * 1.5) + 2, // headroom for randomised think time
        maxVUs: Math.ceil(s.users * SCALE * maxMult * 2) + 5,
        stages: profile.stages.map((st) => ({
          duration: st.duration,
          target: st.target > 0 ? Math.max(1, Math.round(rate * st.target)) : 0,
        })),
        gracefulStop: '30s',
      };
    }
    // SLA thresholds per scenario (http metrics also come from api steps inside web scenarios)
    thresholds[`http_req_duration{scenario:${s.id}}`] = [`p(95)<${s.sla.p95_ms}`, `p(99)<${s.sla.p99_ms}`];
    thresholds[`http_req_failed{scenario:${s.id}}`] = [`rate<${s.sla.error_rate}`];
    thresholds[`iterations{scenario:${s.id}}`] = ['count>=0']; // reporting only
    if (s.kind !== 'web') for (const t of s.step_tags) thresholds[`http_req_duration{name:${t}}`] = ['max>=0']; // reporting only
  }

  thresholds.checks = ['rate>0.99'];
  thresholds.http_req_failed = profile.abort_on_fail
    ? [{ threshold: `rate<${Math.max(w.nfr.error_rate, 0.05)}`, abortOnFail: true, delayAbortEval: '1m' }]
    : [`rate<${w.nfr.error_rate}`];

  return {
    scenarios,
    thresholds,
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(75)', 'p(90)', 'p(95)', 'p(99)', 'count'],
    tags: { project: w.project, test_type: testType },
    insecureSkipTLSVerify: __ENV.INSECURE_TLS === 'true',
  };
}

// ---------------------------------------------------------------- auth
export function setupAuth(auth) {
  const a = auth || { type: 'none' };
  switch (a.type) {
    case 'bearer':
      return { headers: { Authorization: `Bearer ${__ENV[a.token_env] || ''}` } };
    case 'apikey':
      return { headers: { [a.apikey_header]: __ENV[a.apikey_env] || '' } };
    case 'basic':
      return { headers: { Authorization: `Basic ${encoding.b64encode(`${__ENV[a.username_env]}:${__ENV[a.password_env]}`)}` } };
    case 'login': {
      const l = a.login;
      const res = http.request(l.method || 'POST', BASE_URL + l.path, JSON.stringify(interpolate(l.body, {})), {
        headers: { 'Content-Type': 'application/json' }, tags: { name: 'setup login' },
      });
      if (res.status >= 400) throw new Error(`setup login failed: HTTP ${res.status}`);
      return { headers: { Authorization: `Bearer ${getPath(res.json(), l.token_path)}` } };
    }
    default:
      return { headers: {} };
  }
}

// ---------------------------------------------------------------- data + context
const DATASETS = {};
const DATA_MODES = {};
// data_mode per scenario: 'shared' (default, rows rotate across VUs/iterations) or
// 'unique-per-vu' (each VU keeps its own row - one account per user, avoids lockouts/session kick-outs)
export function registerData(map, workload) {
  Object.assign(DATASETS, map);
  for (const s of (workload && workload.scenarios) || []) DATA_MODES[s.id] = s.data_mode || 'shared';
}

// RFC 4180-ish: handles quoted fields containing commas and doubled quotes
function splitCsvLine(line) {
  const out = []; let cur = ''; let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted && c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') { quoted = !quoted; } else if (c === ',' && !quoted) { out.push(cur); cur = ''; } else { cur += c; }
  }
  out.push(cur);
  return out.map((v) => v.trim());
}

export function parseCsv(text) {
  const [head, ...rows] = text.split(/\r?\n/).filter((l) => l.trim() && !l.trim().startsWith('#'));
  const cols = splitCsvLine(head);
  return rows.map((r) => Object.fromEntries(splitCsvLine(r).map((v, i) => [cols[i], v])));
}

export function newContext(setupData, scenarioId) {
  const rows = DATASETS[scenarioId];
  let row = {};
  if (rows && rows.length) {
    row = DATA_MODES[scenarioId] === 'unique-per-vu' ? rows[(__VU - 1) % rows.length] : rows[(__VU * 7919 + __ITER) % rows.length];
    if (DATA_MODES[scenarioId] === 'unique-per-vu' && __VU > rows.length && __ITER === 0) {
      console.warn(`[${scenarioId}] ${__VU} VUs but only ${rows.length} data rows - rows are being shared; add more accounts`);
    }
  }
  return { vars: { ...row }, headers: (setupData && setupData.headers) || {} };
}

// ---------------------------------------------------------------- compute helpers
// Built-in functions callable from a step's `compute:` block; projects add their own in hooks.js.
function toList(v) {
  if (Array.isArray(v)) return v;
  if (v === undefined || v === null) return [];
  if (typeof v === 'string') return v.match(/\d+/g) || [];
  return [v];
}

const HOOKS = {
  // partialPin('12345678', [2,5,7]) -> ['2','5','7'] ; positions may also be text like "2nd, 5th and 7th"
  partialPin: (secret, positions, base) => {
    const digits = String(secret).replace(/\D/g, '');
    const b = base === undefined ? 1 : Number(base);
    return toList(positions).map((p) => {
      const d = digits.charAt(Number(p) - b);
      if (d === '') throw new Error(`partialPin: position ${p} outside secret of length ${digits.length}`);
      return d;
    });
  },
  chars: (secret, positions, base) => { // generic version for alphanumeric memorable words
    const b = base === undefined ? 1 : Number(base);
    return toList(positions).map((p) => String(secret).charAt(Number(p) - b));
  },
  pick: (list, index) => toList(list)[Number(index) || 0],
  randomItem: (list) => { const l = Array.isArray(list) ? list : toList(list); return l[Math.floor(Math.random() * l.length)]; },
  join: (list, sep) => toList(list).join(sep === undefined ? '' : sep),
  upper: (s) => String(s).toUpperCase(),
  lower: (s) => String(s).toLowerCase(),
  concat: (...parts) => parts.join(''),
};
export function registerHooks(h) { Object.assign(HOOKS, h || {}); }

// compute: { pinDigits: { fn: partialPin, args: ["{{pin}}", "{{positions}}"] } }
function applyCompute(ctx, step) {
  for (const [name, spec] of Object.entries(step.compute || {})) {
    const fn = HOOKS[spec.fn];
    if (!fn) throw new Error(`compute ${name}: unknown function '${spec.fn}' (built-ins: ${Object.keys(HOOKS).join(', ')})`);
    ctx.vars[name] = fn(...interpolate(spec.args || [], ctx.vars));
  }
}

export function computeStep(ctx, step) {
  try {
    applyCompute(ctx, step);
    return check(null, { [`${step.tag} computed`]: () => true });
  } catch (e) {
    console.warn(`[${step.tag}] ${String(e)}`);
    return check(null, { [`${step.tag} computed`]: () => false });
  }
}

// ---------------------------------------------------------------- templating
function dynamicValue(name) {
  switch (name) {
    case '$uuid': return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0; return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16); });
    case '$randomInt': return Math.floor(Math.random() * 1000000);
    case '$timestamp': return Date.now();
    case '$randomString': return Math.random().toString(36).slice(2, 10);
    case '$vu': return __VU;
    case '$iter': return __ITER;
    default: return undefined;
  }
}

function lookup(name, vars) {
  if (name.startsWith('env.')) return __ENV[name.slice(4)];
  if (name.startsWith('$')) return dynamicValue(name);
  if (name in vars) return vars[name];
  return name.includes('.') ? getPath(vars, name) : undefined; // {{pinDigits.0}}, {{user.id}}
}

// "{{var}}" alone keeps the value's type; embedded placeholders are stringified.
export function interpolate(value, vars) {
  if (typeof value === 'string') {
    const whole = value.match(/^\{\{\s*([\w.$]+)\s*\}\}$/);
    if (whole) { const v = lookup(whole[1], vars); return v === undefined ? value : v; }
    return value.replace(/\{\{\s*([\w.$]+)\s*\}\}/g, (m, k) => { const v = lookup(k, vars); return v === undefined ? m : String(v); });
  }
  if (Array.isArray(value)) return value.map((v) => interpolate(v, vars));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, interpolate(v, vars)]));
  return value;
}

export function getPath(obj, path) {
  return String(path).split('.').filter(Boolean).reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function buildUrl(step, vars) {
  // OpenAPI-style {param} path segments are filled from context vars
  const path = interpolate(step.path, vars).replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? encodeURIComponent(vars[k]) : m));
  const q = interpolate(step.query || {}, vars);
  const qs = Object.entries(q).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
  return BASE_URL + path + (qs ? `?${qs}` : '');
}

// ---------------------------------------------------------------- steps
// Returns true when the step passed; generated scenarios stop the iteration on false
// so dependent (correlated) steps are not fired with missing data.
// extract spec: "a.b.0"                                     JSON dot path (default)
//               { from: json,   path: data.items, random: true, field: id }   random element of a list
//               { from: header, name: Location }
//               { from: regex,  pattern: 'name="csrf" value="([^"]+)"', all: false }   capture group 1 (all: list)
//               { from: cookie, name: session }
function extractValue(res, spec, jsonCache) {
  const json = () => {
    if (!('v' in jsonCache)) { try { jsonCache.v = res.json(); } catch (e) { jsonCache.v = null; } }
    return jsonCache.v;
  };
  if (typeof spec === 'string') return getPath(json(), spec);
  switch (spec.from || 'json') {
    case 'json': {
      let v = spec.path ? getPath(json(), spec.path) : json();
      if (spec.random && Array.isArray(v)) v = v.length ? v[Math.floor(Math.random() * v.length)] : undefined;
      return spec.field && v !== undefined ? getPath(v, spec.field) : v;
    }
    case 'header': {
      const key = Object.keys(res.headers).find((h) => h.toLowerCase() === String(spec.name).toLowerCase());
      return key ? res.headers[key] : undefined;
    }
    case 'cookie': {
      const c = res.cookies[spec.name];
      return c && c.length ? c[0].value : undefined;
    }
    case 'regex': {
      const body = typeof res.body === 'string' ? res.body : '';
      if (spec.all) {
        const list = [...body.matchAll(new RegExp(spec.pattern, 'g'))].map((m) => (m[1] !== undefined ? m[1] : m[0]));
        return list.length ? list : undefined;
      }
      const m = body.match(new RegExp(spec.pattern));
      return m ? (m[1] !== undefined ? m[1] : m[0]) : undefined;
    }
    default: throw new Error(`unknown extract source '${spec.from}'`);
  }
}

export function apiStep(ctx, step) {
  try { applyCompute(ctx, step); } catch (e) {
    console.warn(`[${step.tag}] ${String(e)}`);
    return check(null, { [`${step.tag} computed`]: () => false });
  }
  const expected = step.expect || [200, 201, 202, 204];
  let body = null;
  const headers = { Accept: 'application/json, text/html;q=0.9, */*;q=0.8' };
  if (step.form) {
    body = interpolate(step.form, ctx.vars); // k6 sends objects as application/x-www-form-urlencoded
  } else if (step.body !== undefined && step.body !== null) {
    body = JSON.stringify(interpolate(step.body, ctx.vars));
    headers['Content-Type'] = 'application/json';
  }
  const res = http.request(step.method, buildUrl(step, ctx.vars), body, {
    headers: Object.assign(headers, ctx.headers, interpolate(step.headers || {}, ctx.vars)),
    tags: { name: step.tag },
    redirects: step.follow_redirects === false ? 0 : 10,
    // intermediate 3xx hops are recorded as separate requests - they are not failures; the final status is checked below
    responseCallback: step.follow_redirects === false ? http.expectedStatuses(...expected)
      : http.expectedStatuses(...expected, { min: 300, max: 399 }),
  });
  const ok = check(res, { [`${step.tag} -> ${expected.join('|')}`]: (r) => expected.includes(r.status) });
  if (ok && step.extract) {
    const cache = {};
    for (const [k, spec] of Object.entries(step.extract)) {
      const v = extractValue(res, spec, cache);
      if (v === undefined) {
        check(res, { [`${step.tag} extract ${k}`]: () => false });
        return false;
      }
      ctx.vars[k] = v;
    }
  }
  if (ok && step.assert_text) {
    const found = typeof res.body === 'string' && res.body.includes(interpolate(step.assert_text, ctx.vars));
    if (!check(res, { [`${step.tag} contains text`]: () => found })) return false;
  }
  return ok;
}

export async function webStep(page, ctx, step) {
  const start = Date.now();
  const tags = { name: step.tag };
  try {
    applyCompute(ctx, step);
    const sel = step.selector ? interpolate(step.selector, ctx.vars) : null;
    const val = step.value !== undefined ? String(interpolate(step.value, ctx.vars)) : undefined;
    switch (step.action) {
      case 'goto': await page.goto(WEB_URL + interpolate(step.url || '/', ctx.vars), { waitUntil: step.wait || 'networkidle' }); break;
      case 'click': await Promise.all([step.navigates ? page.waitForNavigation() : Promise.resolve(), page.locator(sel).click()]); break;
      case 'fill': await page.locator(sel).fill(val); break;
      case 'select': await page.locator(sel).selectOption(val); break;
      case 'press': await page.locator(sel).press(step.key || 'Enter'); break;
      case 'waitFor': await page.locator(sel).first().waitFor({ state: 'visible', timeout: step.timeout || 10000 }); break;
      case 'assertText': {
        const t = await page.locator(sel).first().textContent();
        if (!t || !t.includes(val)) throw new Error(`text '${val}' not found in ${sel}`);
        break;
      }
      case 'extract': { // read text/attribute from the page into a variable (e.g. the PIN positions asked for)
        const loc = page.locator(sel).first();
        await loc.waitFor({ state: 'attached', timeout: step.timeout || 10000 });
        const raw = step.attr ? await loc.getAttribute(step.attr) : await loc.textContent();
        let v = raw === null ? undefined : raw.trim();
        if (v !== undefined && step.pattern) {
          if (step.all) {
            const list = [...v.matchAll(new RegExp(step.pattern, 'g'))].map((m) => (m[1] !== undefined ? m[1] : m[0]));
            v = list.length ? list : undefined;
          } else {
            const m = v.match(new RegExp(step.pattern));
            v = m ? (m[1] !== undefined ? m[1] : m[0]) : undefined;
          }
        }
        if (v === undefined) throw new Error(`extract ${step.var}: nothing matched in ${sel}`);
        ctx.vars[step.var] = v;
        break;
      }
      default: throw new Error(`unknown web action ${step.action}`);
    }
    webStepFailed.add(false, tags);
    webStepDuration.add(Date.now() - start, tags);
    return true;
  } catch (e) {
    webStepFailed.add(true, tags);
    console.warn(`[${step.tag}] ${String(e)}`);
    return false;
  }
}

// Randomised think time (+/-30%) avoids lock-step synchronised users.
export function thinkTime(seconds) {
  const s = seconds === undefined ? 1 : seconds;
  if (s > 0) sleep(s * (0.7 + Math.random() * 0.6));
}

export async function webThink(page, seconds) {
  const s = seconds === undefined ? 1 : seconds;
  if (s > 0) await page.waitForTimeout(s * 1000 * (0.7 + Math.random() * 0.6));
}

// ---------------------------------------------------------------- standalone summary
// Readable end-of-test summary so the scripts are useful with plain `k6 run` (no framework, no AI).
function fmt(v, unit) { return v === undefined || v === null ? '-' : `${Math.round(v)}${unit || ''}`; }

export function textReport(data, w) {
  const m = data.metrics;
  const val = (name, stat) => (m[name] && m[name].values ? m[name].values[stat] : undefined);
  const failed = [];
  for (const [name, metric] of Object.entries(m)) {
    for (const [expr, res] of Object.entries(metric.thresholds || {})) {
      if (!res.ok && expr !== 'max>=0' && expr !== 'count>=0') failed.push(`${name} ${expr}`);
    }
  }
  const lines = [
    '',
    `perfagent | ${w.project} | ${__ENV.TEST_TYPE || 'smoke'} test | ${failed.length ? 'FAIL' : 'PASS'}`,
    `requests ${fmt(val('http_reqs', 'count'))} | ${(val('http_reqs', 'rate') || 0).toFixed(2)} req/s | p95 ${fmt(val('http_req_duration', 'p(95)'), 'ms')}` +
      ` | errors ${((val('http_req_failed', 'rate') || 0) * 100).toFixed(2)}% | dropped iterations ${fmt(val('dropped_iterations', 'count') || 0)}`,
    '',
    `${'Scenario'.padEnd(46)}${'Iter'.padStart(6)}${'p95'.padStart(9)}${'p99'.padStart(9)}${'SLA'.padStart(8)}${'Errors'.padStart(9)}`,
  ];
  for (const s of w.scenarios) {
    const d = (k) => val(`http_req_duration{scenario:${s.id}}`, k);
    const lcp = val(`browser_web_vital_lcp{scenario:${s.id}}`, 'p(75)');
    const p95 = s.kind === 'web' && !d('count') ? (lcp !== undefined ? `${fmt(lcp)}LCP` : '-') : fmt(d('p(95)'));
    lines.push(`${`${s.id} ${s.name}`.slice(0, 45).padEnd(46)}${fmt(val(`iterations{scenario:${s.id}}`, 'count') || 0).padStart(6)}` +
      `${String(p95).padStart(9)}${(d('count') ? fmt(d('p(99)')) : '-').padStart(9)}${String(s.kind === 'web' ? s.sla.lcp_ms : s.sla.p95_ms).padStart(8)}` +
      `${(((val(`http_req_failed{scenario:${s.id}}`, 'rate') || 0) * 100).toFixed(2) + '%').padStart(9)}`);
  }
  if (failed.length) lines.push('', 'Failed thresholds (SLA breaches):', ...failed.map((f) => `  - ${f}`));
  lines.push('', `Full data: ${__ENV.SUMMARY_PATH || 'summary.json'}`, '');
  return lines.join('\n');
}
