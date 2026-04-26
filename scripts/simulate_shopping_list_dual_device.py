#!/usr/bin/env python3
"""
Simulate two devices pushing different shopping-list rows to the write Worker.

Uses the same JSON shape as docs/app.js (device_id, items, updated_at).

Read-only (default):
  python3 scripts/simulate_shopping_list_dual_device.py

Mutates docs/shopping_list_sync.json in the repo (adds two rows with item_id
__sim__dev_a / __sim__dev_b) — only with --write:
  python3 scripts/simulate_shopping_list_dual_device.py --write

Base URL: env WOOLIESBOT_WRITE_API_URL, else parsed from docs/env.js.

If GET/POST return 401 on the public workers.dev URL, the edge often will not
forward spoofed identity headers—use one of:
  • Local Worker: `cd workers/wooliesbot-write && npx wrangler dev` then
    `python3 scripts/simulate_shopping_list_dual_device.py --base-url http://127.0.0.1:8787 --write`
  • Or set WOOLIESBOT_SIMULATE_IDENTITY_EMAIL (only works against dev / no-Access; not on locked-down edge).
  • Or redeploy with ALLOW_INSECURE_PUBLIC_WRITES=1 in [vars] so the API accepts calls without identity.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_JS = REPO_ROOT / "docs" / "env.js"


def read_write_base() -> str:
    url = os.environ.get("WOOLIESBOT_WRITE_API_URL", "").strip()
    if url:
        return url.rstrip("/")
    if ENV_JS.exists():
        text = ENV_JS.read_text(encoding="utf-8")
        m = re.search(r'"?writeApiUrl"?\s*:\s*"([^"]*)"', text)
        if m:
            return m.group(1).rstrip("/")
    return ""


def http_json(method: str, url: str, body: object | None = None) -> tuple[int, object]:
    data: bytes | None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    else:
        data = None
    sim_email = os.environ.get("WOOLIESBOT_SIMULATE_IDENTITY_EMAIL", "").strip()
    headers = {
        "User-Agent": "WooliesBot-simulate-shopping-list/1.0 (python-urllib; +https://github.com/KuschiKuschbert/wooliesbot)",
    }
    if sim_email:
        # Same header name the Worker reads; real Access overwrites on edge. For CLI/IDE sim only.
        headers["CF-Access-Authenticated-User-Email"] = sim_email
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            raw = res.read().decode("utf-8", errors="replace")
            status = res.getcode()
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
    try:
        return status, json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return status, {"_raw": raw}


def options_preflight(base: str, origin: str) -> tuple[int, dict]:
    url = f"{base}/shopping_list"
    req = urllib.request.Request(url, method="OPTIONS")
    req.add_header("Origin", origin)
    req.add_header("Access-Control-Request-Method", "GET")
    req.add_header("Access-Control-Request-Headers", "x-wooliesbot-device")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            status = res.getcode()
            headers = dict(res.headers.items())
    except urllib.error.HTTPError as e:
        status = e.code
        headers = dict(e.headers.items()) if e.headers else {}
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}
    return status, normalized


def one_item(device: str, token: str) -> dict:
    now = f"2026-01-01T00:00:00.000Z"  # fixed for stable keys in logs; merge uses row updated_at
    t = f"2026-01-15T12:{30 + int(device)}:00.000Z"
    return {
        "item_id": f"__sim__{device}__{token}",
        "name": f"Sim product ({device} #{token})",
        "price": 2.5,
        "qty": 1,
        "store": "woolworths",
        "image": None,
        "on_special": False,
        "was_price": None,
        "picked": False,
        "updated_at": t,
    }


def post_device(base: str, device_id: str, token: str) -> dict:
    url = f"{base}/shopping_list"
    body = {
        "device_id": device_id,
        "reason": "simulate_dual_device",
        "updated_at": f"2026-01-15T12:30:00.000Z",
        "items": [one_item(device_id, token)],
    }
    status, payload = http_json("POST", url, body)
    return {
        "device_id": device_id,
        "status": status,
        "body": payload,
    }


def run_parallel(base: str) -> tuple[list[dict], float]:
    out: list[dict] = []
    t0 = time.time()

    def job(dev: str, tok: str) -> dict:
        return post_device(base, dev, tok)

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {
            ex.submit(job, "dev_a", "1"): "dev_a",
            ex.submit(job, "dev_b", "2"): "dev_b",
        }
        for fut in as_completed(futs):
            out.append(fut.result())
    out.sort(key=lambda x: x.get("device_id", ""))
    return out, time.time() - t0


def run_sequential(base: str) -> tuple[list[dict], float]:
    t0 = time.time()
    a = post_device(base, "dev_a_seq", "1")
    b = post_device(base, "dev_b_seq", "2")
    return [a, b], time.time() - t0


def get_list(base: str) -> tuple[int, object]:
    return http_json("GET", f"{base}/shopping_list?t={int(time.time() * 1000)}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--write",
        action="store_true",
        help="POST two different rows (mutates cloud shopping list). Default is read-only (health + GET).",
    )
    p.add_argument(
        "--sequential",
        action="store_true",
        help="POST one device after the other (sanity) instead of in parallel (race stress).",
    )
    p.add_argument("--base-url", default="", help="Override write API base URL (no trailing slash).")
    args = p.parse_args()
    base = (args.base_url or read_write_base()).strip().rstrip("/")
    if not base:
        print("No write API URL: set WOOLIESBOT_WRITE_API_URL or ensure docs/env.js has writeApiUrl", file=sys.stderr)
        return 1

    print("Base:", base)
    app_origin = os.environ.get("WOOLIESBOT_APP_ORIGIN", "https://kuschikuschbert.github.io").strip()
    pf_status, pf_headers = options_preflight(base, app_origin)
    print(
        "OPTIONS /shopping_list ->",
        pf_status,
        "| allow-origin:",
        pf_headers.get("access-control-allow-origin", ""),
        "| allow-credentials:",
        pf_headers.get("access-control-allow-credentials", ""),
    )
    if pf_status >= 400:
        print("  preflight failed; CORS policy likely misconfigured.")
    if pf_headers.get("access-control-allow-origin") not in (app_origin, "*"):
        print("  warning: preflight allow-origin does not match app origin.")
    if pf_headers.get("access-control-allow-credentials", "").lower() != "true":
        print("  warning: allow-credentials is not true (required for credentials mode).")

    try:
        st, health = http_json("GET", f"{base}/health")
    except urllib.error.HTTPError as e:
        st, health = e.code, {"error": e.reason}
    except OSError as e:
        print("Health check failed:", e, file=sys.stderr)
        return 1
    print("GET /health ->", st, json.dumps(health, indent=2)[:800])

    st, remote = get_list(base)
    print("GET /shopping_list ->", st)
    if st == 401:
        print(
            "  (401: use wrangler dev + --base-url, or deploy Worker with insecure_public_writes / Access.)"
        )
    items = (remote or {}).get("items") or []
    print("  current rows:", len(items), "| updated_at:", (remote or {}).get("updated_at", ""))
    sim = [i for i in items if str(i.get("item_id", "")).startswith("__sim__")]
    if sim:
        print("  (found __sim__* rows from earlier runs — remove in GitHub or dashboard if unwanted)")

    if not args.write:
        print()
        print("Read-only. To stress two devices (writes cloud file), run:")
        print("  python3 scripts/simulate_shopping_list_dual_device.py --write")
        if args.sequential:
            print("(ignored without --write)")
        return 0

    print()
    if args.sequential:
        print("SEQUENTIAL POSTS (2 devices, different items)...")
        results, dt = run_sequential(base)
    else:
        print("PARALLEL POSTS (2 devices, different items; exercises merge + 409 retry)...")
        results, dt = run_parallel(base)
    for r in results:
        print(" ", r.get("device_id"), "HTTP", r.get("status"), json.dumps(r.get("body"))[:400])
    print(f" wall time: {dt:.2f}s")

    st, after = get_list(base)
    items2 = (after or {}).get("items") or []
    ids = {str(i.get("item_id", "")) for i in items2 if i.get("item_id")}
    print("GET /shopping_list after ->", st, "rows:", len(items2))
    for prefix in ("__sim__dev_a", "__sim__dev_b", "__sim__dev_a_seq", "__sim__dev_b_seq"):
        if any((x or "").startswith(prefix) for x in ids):
            print("  saw", prefix, "*")
    if args.sequential:
        ok = any(x.startswith("__sim__dev_a_seq") for x in ids) and any(
            x.startswith("__sim__dev_b_seq") for x in ids
        )
    else:
        ok = any(x.startswith("__sim__dev_a__") for x in ids) and any(
            x.startswith("__sim__dev_b__") for x in ids
        )
    if not ok:
        print(
            "\nNote: if one device row is missing, you may be hitting auth (401) or a merge race; "
            "re-run with --sequential to compare."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
