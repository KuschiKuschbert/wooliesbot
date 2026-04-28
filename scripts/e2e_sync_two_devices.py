#!/usr/bin/env python3
"""
e2e_sync_two_devices.py — Playwright: two isolated browser profiles ("devices") vs the real Worker.

Device A adds an item from the specials grid (real push). Device B starts with empty storage
and must show the item after a cloud pull (real GET + merge).

Requires:
  pip install playwright && python -m playwright install chromium
  WOOLIESBOT_WRITE_API_TOKEN — Bearer token the Worker accepts (same as dashboard pairing).

Worker CORS must allow the page origin (e.g. GitHub Pages). Local http://127.0.0.1:* only works
if the deployed Worker includes that origin in ALLOWED_ORIGINS — otherwise use --base-url with
the production dashboard URL (default).

Usage:
  export WOOLIESBOT_WRITE_API_TOKEN="your-token"
  python3 scripts/e2e_sync_two_devices.py
  python3 scripts/e2e_sync_two_devices.py --headed
  python3 scripts/e2e_sync_two_devices.py --base-url "http://127.0.0.1:9333" --local   # serve docs/
  python3 scripts/e2e_sync_two_devices.py --base-url "https://kuschikuschbert.github.io/wooliesbot"

Env:
  WOOLIESBOT_WRITE_API_URL   — override Worker base (else parsed from docs/env.js)
  WOOLIESBOT_WRITE_API_TOKEN — required for token-auth Worker
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
ENV_JS = DOCS_DIR / "env.js"

try:
    from mobile_server import pick_free_port, serve_docs
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.stderr.write(
        "ERROR: need playwright + mobile_server. Run:\n"
        "  pip install playwright && python -m playwright install chromium\n"
    )
    sys.exit(2)


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


def ls_init_script(write_url: str, token: str, device_id: str) -> str:
    return f"""
        localStorage.setItem('write_api_url', {json.dumps(write_url)});
        localStorage.setItem('write_api_token', {json.dumps(token)});
        localStorage.setItem('shoppingDeviceId', {json.dumps(device_id)});
    """


def wait_for_app_ready(page) -> None:
    page.wait_for_selector("#tab-deals.tab-content.active", timeout=20_000)
    page.wait_for_function(
        """() => {
            const rail = document.getElementById('mobile-priority-rail');
            const specials = document.getElementById('specials-grid');
            const allItems = document.getElementById('all-items-tbody');
            const railReady = rail && rail.children.length > 0;
            const specialsReady = specials && specials.children.length > 0;
            const allItemsReady = allItems && allItems.children.length > 0;
            return railReady || specialsReady || allItemsReady;
        }""",
        timeout=25_000,
    )


def list_badge_count(page) -> int:
    return int(
        page.evaluate(
            """() => {
            const m = document.getElementById('mobile-list-count');
            const h = document.getElementById('list-count');
            const el = m || h;
            return el ? (parseInt(String(el.textContent || '0'), 10) || 0) : 0;
        }"""
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--base-url",
        default=os.environ.get("WOOLIESBOT_E2E_BASE_URL", "https://kuschikuschbert.github.io/wooliesbot").strip(),
        help="Dashboard origin (trailing slash optional). Default: GitHub Pages (CORS-friendly).",
    )
    ap.add_argument(
        "--local",
        action="store_true",
        help="Serve docs/ on 127.0.0.1 and use that URL (needs Worker ALLOWED_ORIGINS to include it).",
    )
    ap.add_argument("--port", type=int, default=0, help="Port for --local (0 = random).")
    ap.add_argument("--headed", action="store_true", help="Show browser windows.")
    args = ap.parse_args()

    token = os.environ.get("WOOLIESBOT_WRITE_API_TOKEN", "").strip()
    if not token:
        print("ERROR: set WOOLIESBOT_WRITE_API_TOKEN to a valid Worker Bearer token.", file=sys.stderr)
        print("  (Same value you store after opening a pairing link on a real device.)", file=sys.stderr)
        return 1

    write_url = read_write_base()
    if not write_url:
        print("ERROR: no write API URL (WOOLIESBOT_WRITE_API_URL or docs/env.js writeApiUrl).", file=sys.stderr)
        return 1

    base = args.base_url.rstrip("/")
    if args.local:
        port = args.port or pick_free_port()
        server_cm = serve_docs(DOCS_DIR, port)
    else:
        server_cm = contextlib.nullcontext(base)

    print("Write API:", write_url)
    print("---")

    with server_cm as base_url:
        print("Dashboard:", base_url)
        print("---")
        start = time.time()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            try:
                ca = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    locale="en-AU",
                    service_workers="block",
                )
                ca.add_init_script(ls_init_script(write_url, token, "e2e_playwright_device_a"))
                page_a = ca.new_page()

                cb = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    locale="en-AU",
                    service_workers="block",
                )
                cb.add_init_script(ls_init_script(write_url, token, "e2e_playwright_device_b"))
                page_b = cb.new_page()

                # ---- Device A: load, add first special to list, wait for POST ----
                page_a.goto(f"{base_url}/", wait_until="domcontentloaded")
                wait_for_app_ready(page_a)
                before = list_badge_count(page_a)
                add_btn = page_a.query_selector(
                    "#specials-grid .add-to-list-btn, #near-misses-grid .add-to-list-btn"
                )
                if not add_btn:
                    print("FAIL: no .add-to-list-btn (grid empty or layout changed).")
                    return 1

                post_seen: list[int] = []

                def _on_response(res):
                    u = res.url
                    if "/shopping_list" in u and res.request.method == "POST":
                        post_seen.append(res.status)

                page_a.on("response", _on_response)
                add_btn.scroll_into_view_if_needed()
                add_btn.click()
                # debounce ~900ms + network
                page_a.wait_for_timeout(2500)
                if not post_seen:
                    print(
                        "FAIL: no POST to .../shopping_list seen after add. "
                        "Check token, CORS (Origin must be allowed for this page URL), and console.",
                    )
                    return 1
                if not any(200 <= s < 300 for s in post_seen):
                    print(f"FAIL: shopping_list POST statuses: {post_seen}")
                    return 1
                after = list_badge_count(page_a)
                if after <= before:
                    print(f"FAIL: list badge did not increment locally ({before} -> {after}).")
                    return 1
                print(f"  [A] add-to-list OK (badge {before} -> {after}); POST status(es) {post_seen}")

                # ---- Device B: empty effective state, must receive remote list ----
                page_b.goto(f"{base_url}/?e2e_bust={int(time.time())}", wait_until="domcontentloaded")
                wait_for_app_ready(page_b)
                b0 = list_badge_count(page_b)
                if b0 > 0:
                    print(f"  [B] note: starting badge was {b0} (expected 0 if storage isolated).")

                try:
                    page_b.wait_for_function(
                        "() => {"
                        "  const m = document.getElementById('mobile-list-count');"
                        "  const h = document.getElementById('list-count');"
                        "  const el = m || h;"
                        "  const n = el ? (parseInt(String(el.textContent || '0'), 10) || 0) : 0;"
                        "  return n > 0;"
                        "}",
                        timeout=45_000,
                    )
                except PWTimeoutError:
                    print(
                        "FAIL: device B list count stayed 0 after 45s. "
                        "Cloud pull may be failing (401, CORS), or remote not newer than local stamp."
                    )
                    return 1

                b1 = list_badge_count(page_b)
                elapsed = time.time() - start
                print(f"  [B] list badge now {b1} (was {b0}) after pull — cross-device sync observed.")
                print(f"PASS ({elapsed:.1f}s)")
                return 0
            finally:
                browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
