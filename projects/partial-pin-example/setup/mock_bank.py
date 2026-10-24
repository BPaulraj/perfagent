"""Mock bank with a two-step partial-PIN login - a local target for the partial-pin-example project.

Step 1: account number + date of birth          Step 2: three random digits of an 8-digit PIN
Both an HTML form flow (CSRF tokens, redirects, session cookie) and a JSON API flow are provided.
Three wrong PIN attempts lock the account (HTTP 423), like a real bank.

  python projects/partial-pin-example/setup/mock_bank.py --write-users   # create data/users.secret.csv
  python projects/partial-pin-example/setup/mock_bank.py                 # serve on http://localhost:5180
"""

import argparse
import csv
import html
import json
import random
import secrets
import threading
import time
from datetime import date, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

USERS_FILE = Path(__file__).resolve().parents[1] / "data" / "users.secret.csv"
N_USERS = 50


def make_users() -> dict:
    rng = random.Random(4242)  # deterministic fake accounts - NOT real customer data
    users = {}
    for i in range(1, N_USERS + 1):
        acct = f"{10000000 + i}"
        dob = (date(1970, 1, 1) + timedelta(days=rng.randint(0, 12000))).strftime("%d/%m/%Y")
        pin = "".join(str(rng.randint(0, 9)) for _ in range(8))
        users[acct] = {"dob": dob, "pin": pin, "name": f"Test Customer {i:02d}", "failures": 0,
                       "accounts": [{"id": f"{acct}-{k}", "type": t, "balance": round(rng.uniform(100, 90000), 2)}
                                    for k, t in enumerate(["CURRENT", "SAVINGS", "ISA"][: rng.randint(1, 3)], 1)]}
    return users


USERS = make_users()
SESSIONS: dict = {}
LOCK = threading.Lock()


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Mock Bank</title>
<style>body{{font-family:system-ui;max-width:420px;margin:60px auto}}label{{display:block;margin-top:12px}}
input{{padding:6px;width:100%}} .pin input{{width:3em}} .err{{color:#b00}}</style></head><body>{body}</body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    # ---- helpers ---------------------------------------------------------
    def session(self, create=False):
        sid = SimpleCookie(self.headers.get("Cookie", "")).get("sid")
        sid = sid.value if sid else None
        if sid in SESSIONS:
            return sid, SESSIONS[sid]
        if create:
            sid = secrets.token_hex(16)
            SESSIONS[sid] = {"stage": "new", "csrf": secrets.token_hex(8)}
            return sid, SESSIONS[sid]
        return None, None

    def send(self, status, body, ctype="text/html; charset=utf-8", sid=None, location=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if sid:
            self.send_header("Set-Cookie", f"sid={sid}; HttpOnly; Path=/; SameSite=Lax")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, obj, sid=None):
        self.send(status, json.dumps(obj), "application/json", sid)

    def body(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if "json" in (self.headers.get("Content-Type") or ""):
            return json.loads(raw or "{}")
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def start_login(self, sess, acct, dob):
        user = USERS.get(str(acct).strip())
        if not user or user["dob"] != str(dob).strip():
            return 401, "Account number or date of birth not recognised"
        if user["failures"] >= 3:
            return 423, "Account locked after too many failed PIN attempts"
        sess.update(stage="pin", account=acct, positions=sorted(random.sample(range(1, 9), 3)),
                    challenge=secrets.token_hex(8), csrf=secrets.token_hex(8))
        return 200, None

    def verify_pin(self, sess, digits):
        if sess.get("stage") != "pin":
            return 409, "Start the login again"
        user = USERS[sess["account"]]
        time.sleep(0.03)  # simulated hashing / HSM call
        expected = [user["pin"][p - 1] for p in sess["positions"]]
        with LOCK:
            if user["failures"] >= 3:
                return 423, "Account locked"
            if [str(d) for d in digits] != expected:
                user["failures"] += 1
                return 401, "Incorrect PIN digits"
            user["failures"] = 0
        sess.update(stage="auth", csrf=secrets.token_hex(8))
        return 200, None

    # ---- routes ----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/login"):
            sid, sess = self.session(create=True)
            sess.update(stage="new", csrf=secrets.token_hex(8))
            return self.send(200, PAGE.format(body=f"""<h1>Log in</h1>
<form id="step1" method="post" action="/login"><input type="hidden" name="csrf" value="{sess['csrf']}">
<label for="accountNumber">Account number</label><input id="accountNumber" name="accountNumber" inputmode="numeric">
<label for="dob">Date of birth (DD/MM/YYYY)</label><input id="dob" name="dob">
<p><button id="continue" type="submit">Continue</button></p></form>"""), sid=sid)
        if path == "/login/pin":
            sid, sess = self.session()
            if not sess or sess.get("stage") != "pin":
                return self.send(302, "", location="/login")
            pos = sess["positions"]
            fields = "".join(f'<label for="pin{i}">{ordinal(p)} digit</label><input id="pin{i}" name="pin{i}" maxlength="1" type="password">'
                             for i, p in enumerate(pos, 1))
            return self.send(200, PAGE.format(body=f"""<h1>Security check</h1>
<p id="pin-prompt">Please enter the {ordinal(pos[0])}, {ordinal(pos[1])} and {ordinal(pos[2])} digits of your PIN</p>
<form id="step2" class="pin" method="post" action="/login/pin"><input type="hidden" name="csrf" value="{sess['csrf']}">
{fields}<p><button id="login" type="submit">Log in</button></p></form>"""))
        if path == "/accounts":
            sid, sess = self.session()
            if not sess or sess.get("stage") != "auth":
                return self.send(302, "", location="/login")
            user = USERS[sess["account"]]
            rows = "".join(f"<li>{a['type']} {a['id']}: £{a['balance']:,.2f}</li>" for a in user["accounts"])
            return self.send(200, PAGE.format(body=f'<h1 id="welcome">Welcome, {html.escape(user["name"])}</h1><ul id="accounts">{rows}</ul>'))
        if path == "/api/accounts":
            sid, sess = self.session()
            if not sess or sess.get("stage") != "auth":
                return self.json(401, {"error": "Not authenticated"})
            return self.json(200, {"accounts": USERS[sess["account"]]["accounts"]})
        if path.startswith("/api/accounts/") and path.endswith("/transactions"):
            sid, sess = self.session()
            if not sess or sess.get("stage") != "auth":
                return self.json(401, {"error": "Not authenticated"})
            time.sleep(0.02)
            return self.json(200, {"transactions": [{"id": i, "amount": round(random.uniform(-200, 500), 2)} for i in range(25)]})
        if path == "/health":
            return self.json(200, {"ok": True})
        self.send(404, "Not found")

    def do_POST(self):
        path = urlparse(self.path).path
        data = self.body()
        if path == "/login":
            sid, sess = self.session()
            if not sess or data.get("csrf") != sess.get("csrf"):
                return self.send(403, "CSRF token missing or invalid")
            status, err = self.start_login(sess, data.get("accountNumber", ""), data.get("dob", ""))
            if status != 200:
                return self.send(status, PAGE.format(body=f'<p class="err">{err}</p><a href="/login">Try again</a>'))
            return self.send(302, "", location="/login/pin")
        if path == "/login/pin":
            sid, sess = self.session()
            if not sess or data.get("csrf") != sess.get("csrf"):
                return self.send(403, "CSRF token missing or invalid")
            status, err = self.verify_pin(sess, [data.get(f"pin{i}", "") for i in (1, 2, 3)])
            if status != 200:
                return self.send(status, PAGE.format(body=f'<p class="err">{err}</p><a href="/login">Start again</a>'))
            return self.send(302, "", location="/accounts")
        if path == "/api/login/start":
            sid, sess = self.session(create=True)
            status, err = self.start_login(sess, data.get("accountNumber", ""), data.get("dob", ""))
            if status != 200:
                return self.json(status, {"error": err}, sid)
            return self.json(200, {"challengeId": sess["challenge"], "positions": sess["positions"],
                                   "prompt": f"Enter digits {', '.join(map(str, sess['positions']))} of your PIN"}, sid)
        if path == "/api/login/verify":
            sid, sess = self.session()
            if not sess or data.get("challengeId") != sess.get("challenge"):
                return self.json(400, {"error": "Unknown or expired challenge"})
            status, err = self.verify_pin(sess, data.get("digits") or [])
            return self.json(status, {"status": "authenticated"} if status == 200 else {"error": err})
        if path == "/api/logout":
            sid, sess = self.session()
            if sid:
                SESSIONS.pop(sid, None)
            return self.send(204, "")
        self.send(404, "Not found")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5180)
    ap.add_argument("--write-users", action="store_true", help="write the test accounts to data/users.secret.csv and exit")
    args = ap.parse_args()
    if args.write_users:
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(USERS_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["account_number", "dob", "pin"])
            for acct, u in USERS.items():
                w.writerow([acct, u["dob"], u["pin"]])
        print(f"Wrote {len(USERS)} fake test accounts to {USERS_FILE} (git-ignored)")
        return
    print(f"Mock bank on http://localhost:{args.port}/login  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
