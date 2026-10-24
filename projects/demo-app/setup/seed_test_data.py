"""Seed perf test data for demo-app (StockBroker Demo, cookie-auth backend).

Idempotent: registers perf.userNN@example.test (or logs in if they exist), tops wallets up to a
working balance, makes sure each user holds shares (for SELL / invoice scenarios), then writes
data/users.csv (email, search_term). Password comes from PERF_PASSWORD (projects/demo-app/.env).

Usage: python projects/demo-app/setup/seed_test_data.py [--users 20]
"""

import argparse
import csv
import os
import random
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from perfagent.config import Project  # noqa: E402

TARGET_BALANCE = 200_000
MIN_HOLDING_POSITIONS = 3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=20)
    args = ap.parse_args()

    project = Project("demo-app")
    project.load_env()
    base = project.target["api_base_url"].rstrip("/")
    project.check_host_allowed(base)
    password = os.environ.get("PERF_PASSWORD")
    if not password:
        raise SystemExit("PERF_PASSWORD not set (projects/demo-app/.env)")

    rows, companies = [], None
    for i in range(1, args.users + 1):
        email = f"perf.user{i:02d}@example.test"
        s = requests.Session()
        r = s.post(f"{base}/auth/register", json={"name": f"Perf User {i:02d}", "email": email,
                                                  "phone": f"+1 555 01{i:02d}", "password": password}, timeout=30)
        if r.status_code == 409:
            r = s.post(f"{base}/auth/login", json={"email": email, "password": password}, timeout=30)
        r.raise_for_status()
        state = "registered" if r.status_code == 201 else "existing"

        if companies is None:
            companies = s.get(f"{base}/companies", timeout=30).json()

        balance = s.get(f"{base}/wallet", timeout=30).json()["balance"]
        if balance < TARGET_BALANCE / 2:
            s.post(f"{base}/wallet/add-funds", json={"method": "BANK_TRANSFER", "amount": TARGET_BALANCE,
                                                      "accountNumber": "123456789012", "ifsc": "PERF0001234"},
                   timeout=30).raise_for_status()

        holdings = s.get(f"{base}/holdings", timeout=30).json()
        for c in random.sample(companies, max(0, MIN_HOLDING_POSITIONS - len(holdings))):
            s.post(f"{base}/trades", json={"companyId": c["id"], "type": "BUY", "quantity": 20}, timeout=30).raise_for_status()
        # top up existing positions so repeated SELL iterations don't run dry
        for h in s.get(f"{base}/holdings", timeout=30).json():
            if h["quantity"] < 10:
                s.post(f"{base}/trades", json={"companyId": h["companyId"], "type": "BUY", "quantity": 20}, timeout=30).raise_for_status()

        c = random.choice(companies)
        term = random.choice([c["ticker"], c["sector"], c["name"].split()[0]])
        rows.append({"email": email, "search_term": term})
        print(f"  {email:<28} {state:<11} balance ok, holdings ok, search_term={term}")

    out = project.data_dir / "users.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["email", "search_term"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {out} ({len(rows)} users)")


if __name__ == "__main__":
    main()
