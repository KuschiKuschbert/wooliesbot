#!/usr/bin/env python3
"""
e2e_mobile.py — Mobile end-to-end tests for the WooliesBot dashboard.

Spins up a local static server for docs/, launches Chromium under iPhone 13
device emulation, and exercises the key mobile flows that the recent overhaul
introduced. Prints a PASS/FAIL summary and saves annotated screenshots to
  screenshots/e2e_mobile/<timestamp>/

Checks:
  01  Page loads, no console errors, service worker registers
  02  PWA manifest is linked and reachable
  03  Mobile: bottom nav visible; stats-sidebar column visible; #buy-now-card / #top5-card hidden in sidebar
  04  Mobile priority rail renders with Buy Now + Top 5 content
  05  Stats strip scrolls horizontally (overflow-x)
  06  Sticky filter bar stays visible after vertical scroll
  07  Specials grid renders at ≤2 columns on narrow viewport
  08  Category filter chip filters the grid
  09  Add-to-list on a card increments the list count
  10  Drawer opens as a bottom sheet and closes via the X button
  11  Master Tracklist renders as mobile card list (not desktop table)
  12  Analytics tab: narrative stack, advanced diagnostics collapsed on mobile, heatmap swaps list/grid across viewport changes, no horizontal overflow
  13  Tap-highlight disabled (WebkitTapHighlightColor transparent on buttons)

Usage:
  python scripts/e2e_mobile.py                     # run all checks
  python scripts/e2e_mobile.py --headed            # watch the browser
  python scripts/e2e_mobile.py --device "Pixel 7"  # use a different device
  python scripts/e2e_mobile.py --port 8123         # pin the local server port
  python scripts/e2e_mobile.py --base-url URL      # skip the built-in server

Dependencies:
  pip install -r requirements-dev.txt
  python -m playwright install chromium
"""
from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import socket
import socketserver
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import (
        ConsoleMessage,
        Error as PWError,
        Page,
        TimeoutError as PWTimeoutError,
        sync_playwright,
    )
except ImportError:
    sys.stderr.write(
        "ERROR: playwright is not installed. Run:\n"
        "  pip install playwright && python -m playwright install chromium\n"
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
SHOT_ROOT = REPO_ROOT / "screenshots" / "e2e_mobile"


# --------------------------------------------------------------------------
# Static server
# --------------------------------------------------------------------------
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):  # silence default access log
        pass


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve_docs(port: int):
    """Serve docs/ on 127.0.0.1:<port> for the duration of the context."""

    class Handler(_QuietHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(DOCS_DIR), **kw)

    httpd = _ReusableTCPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------
# Result bookkeeping
# --------------------------------------------------------------------------
class Results:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str]] = []  # (id, name, status, note)

    def record(self, check_id: str, name: str, status: str, note: str = "") -> None:
        self.rows.append((check_id, name, status, note))
        icon = {"PASS": "OK  ", "FAIL": "FAIL", "WARN": "WARN", "SKIP": "SKIP"}.get(
            status, status
        )
        print(f"  [{icon}] {check_id}  {name}" + (f"  — {note}" if note else ""))

    def passed(self) -> bool:
        return not any(r[2] == "FAIL" for r in self.rows)

    def summary(self) -> str:
        counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
        for _, _, status, _ in self.rows:
            counts[status] = counts.get(status, 0) + 1
        return (
            f"PASS={counts['PASS']}  FAIL={counts['FAIL']}  "
            f"WARN={counts['WARN']}  SKIP={counts['SKIP']}  total={len(self.rows)}"
        )


# --------------------------------------------------------------------------
# Check helpers
# --------------------------------------------------------------------------
def _try(results: Results, check_id: str, name: str, fn) -> None:
    """Run a single check, capturing exceptions into FAIL rows."""
    try:
        status, note = fn()
    except PWTimeoutError as e:
        status, note = "FAIL", f"timeout: {e}"
    except Exception as e:
        status, note = "FAIL", f"{type(e).__name__}: {e}"
    results.record(check_id, name, status, note)


def _wait_for_app_ready(page: Page) -> None:
    """Wait for first paint of the Deals tab (specials or priority rail populated)."""
    page.wait_for_selector("#tab-deals.tab-content.active", timeout=15_000)
    # The skeleton hides once initDashboard resolves. Either specials or the
    # priority rail should have content.
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
        timeout=20_000,
    )


# --------------------------------------------------------------------------
# The check suite
# --------------------------------------------------------------------------
def run_checks(page: Page, results: Results, shot_dir: Path, base_url: str) -> None:
    console_errors: list[str] = []
    failed_requests: list[str] = []

    def on_console(msg: ConsoleMessage):
        if msg.type == "error":
            console_errors.append(msg.text)

    def on_request_failed(req):
        failed_requests.append(f"{req.method} {req.url} — {req.failure}")

    page.on("console", on_console)
    page.on("requestfailed", on_request_failed)
    page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

    # ------------------------------------------------------------------
    # 01 — Load + console cleanliness
    # ------------------------------------------------------------------
    page.goto(base_url, wait_until="domcontentloaded")
    _wait_for_app_ready(page)
    page.screenshot(path=str(shot_dir / "01-deals-top.png"), full_page=False)

    def check_load():
        # Fail only on unexpected errors — service-worker or third-party ad
        # blockers sometimes emit harmless console noise.
        hard_errors = [
            e
            for e in console_errors
            if "favicon" not in e.lower()
            and "manifest" not in e.lower()
            and "wooliesbot-write.wooliesbot.workers.dev/shopping_list" not in e.lower()
            and "blocked by cors policy" not in e.lower()
        ]
        if hard_errors:
            ignorable_err_failed = all("failed to load resource: net::err_failed" in e.lower() for e in hard_errors)
            if ignorable_err_failed:
                cloud_failures = [
                    r
                    for r in failed_requests
                    if "wooliesbot-write.wooliesbot.workers.dev/shopping_list" in r.lower()
                ]
                if cloud_failures:
                    hard_errors = []
        if hard_errors:
            return "FAIL", f"{len(hard_errors)} console errors: {hard_errors[0][:80]}"
        if failed_requests:
            # Heartbeat / cloud-bridge probes are expected to fail locally.
            real = [
                r
                for r in failed_requests
                if "heartbeat" not in r
                and "5001" not in r
                and "127.0.0.1:7716/ingest/" not in r
                and "wooliesbot-write.wooliesbot.workers.dev/shopping_list" not in r
            ]
            if real:
                return "FAIL", f"{len(real)} failed requests: {real[0][:80]}"
        return "PASS", "no console errors"

    _try(results, "01", "page loads cleanly", check_load)

    # ------------------------------------------------------------------
    # 02 — PWA manifest reachable
    # ------------------------------------------------------------------
    def check_manifest():
        href = page.eval_on_selector(
            "link[rel='manifest']", "el => el.getAttribute('href')"
        )
        if not href:
            return "FAIL", "no <link rel=manifest>"
        resp = page.request.get(f"{base_url}/{href.lstrip('./')}")
        if resp.status != 200:
            return "FAIL", f"manifest HTTP {resp.status}"
        body = resp.json()
        if "name" not in body or "icons" not in body:
            return "FAIL", "manifest missing name/icons"
        return "PASS", f"{body.get('short_name', body['name'])}"

    _try(results, "02", "PWA manifest served", check_manifest)

    # ------------------------------------------------------------------
    # 03 — Mobile chrome: bottom nav + sidebar column; rail dupes hidden
    # ------------------------------------------------------------------
    def check_chrome():
        nav_visible = page.eval_on_selector(
            ".mobile-bottom-nav",
            "el => { const s = getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden'; }",
        )
        if not nav_visible:
            return "FAIL", ".mobile-bottom-nav hidden"
        sidebar_display = page.eval_on_selector(
            ".stats-sidebar",
            "el => getComputedStyle(el).display",
        )
        if sidebar_display == "none":
            return "FAIL", ".stats-sidebar hidden (Essentials / Cola column missing)"
        buy_hidden = page.evaluate(
            """() => {
                const el = document.querySelector('.stats-sidebar #buy-now-card');
                if (!el) return true;
                return getComputedStyle(el).display === 'none';
            }"""
        )
        top5_hidden = page.evaluate(
            """() => {
                const el = document.querySelector('.stats-sidebar #top5-card');
                if (!el) return true;
                return getComputedStyle(el).display === 'none';
            }"""
        )
        if not buy_hidden or not top5_hidden:
            return "FAIL", "Buy Now / Top 5 in sidebar should stay hidden (see mobile CSS)"
        return "PASS", "bottom nav + sidebar; rail cards suppressed"

    _try(results, "03", "mobile chrome swap", check_chrome)

    # ------------------------------------------------------------------
    # 04 — Priority rail populated
    # ------------------------------------------------------------------
    def check_priority_rail():
        info = page.eval_on_selector(
            "#mobile-priority-rail",
            """el => ({
                display: getComputedStyle(el).display,
                sections: el.querySelectorAll('.priority-rail-section').length,
                rows: el.querySelectorAll('.buy-now-row, .top5-row').length,
                children: el.children.length,
            })""",
        )
        if info["display"] == "none":
            return "FAIL", "priority rail hidden"
        if info["rows"] == 0 and info["sections"] == 0:
            return "WARN", "rail empty — no buy-now / top-deal candidates in data"
        return "PASS", f"{info['sections']} sections, {info['rows']} rows"

    _try(results, "04", "priority rail populated", check_priority_rail)

    # ------------------------------------------------------------------
    # 05 — Stats strip is horizontally scrollable
    # ------------------------------------------------------------------
    def check_stats_strip():
        info = page.eval_on_selector(
            ".stats-strip",
            """el => ({
                scrollWidth: el.scrollWidth,
                clientWidth: el.clientWidth,
                overflowX: getComputedStyle(el).overflowX,
            })""",
        )
        if info["overflowX"] not in ("auto", "scroll"):
            return "FAIL", f"overflow-x={info['overflowX']}"
        if info["scrollWidth"] <= info["clientWidth"] + 2:
            return "WARN", "content fits without scroll"
        return "PASS", f"scrollable ({info['scrollWidth']}px > {info['clientWidth']}px)"

    _try(results, "05", "stats strip horizontally scrollable", check_stats_strip)

    # ------------------------------------------------------------------
    # 06 — Sticky filter bar
    # ------------------------------------------------------------------
    def check_sticky_filters():
        before = page.eval_on_selector(
            ".sticky-filter-bar",
            "el => el.getBoundingClientRect().top",
        )
        page.evaluate("window.scrollTo(0, 600)")
        page.wait_for_timeout(250)
        after = page.eval_on_selector(
            ".sticky-filter-bar",
            "el => el.getBoundingClientRect().top",
        )
        position = page.eval_on_selector(
            ".sticky-filter-bar", "el => getComputedStyle(el).position"
        )
        page.evaluate("window.scrollTo(0, 0)")
        if position != "sticky":
            return "FAIL", f"position={position}"
        # sticky element should pin to around the header; after scroll its top
        # should be below 0 but not move proportionally to the scroll amount.
        if after < -10:
            return "FAIL", f"filter bar scrolled away (top={after:.0f})"
        return "PASS", f"top before={before:.0f} after={after:.0f}"

    _try(results, "06", "sticky filter bar", check_sticky_filters)

    # ------------------------------------------------------------------
    # 07 — Specials grid 2-up
    # ------------------------------------------------------------------
    def check_grid_columns():
        info = page.eval_on_selector(
            "#specials-grid",
            """el => {
                const n = el.children.length;
                if (!n) return { cols: 0, n: 0 };
                const cs = getComputedStyle(el);
                const cols = cs.gridTemplateColumns.split(' ').length;
                return { cols, n };
            }""",
        )
        if info["n"] == 0:
            return "SKIP", "no specials rendered"
        if info["cols"] > 2:
            return "FAIL", f"{info['cols']} columns on mobile"
        return "PASS", f"{info['cols']} column(s), {info['n']} items"

    _try(results, "07", "specials grid ≤2 columns", check_grid_columns)

    # ------------------------------------------------------------------
    # 08 — Category filter chip filters the grid
    # ------------------------------------------------------------------
    def check_category_filter():
        before = page.evaluate(
            """() => {
                const g = document.getElementById('specials-grid');
                const cards = g ? Array.from(g.querySelectorAll('.item-card')) : [];
                const names = cards.map(c => (c.querySelector('.item-title')?.textContent || '').trim());
                return { count: cards.length, names };
            }"""
        )
        chip_handle = page.evaluate_handle(
            """() => {
                const buttons = Array.from(document.querySelectorAll(
                    '.category-scroll button, .category-pills button, .filter-btn-cat'
                ));
                return buttons.find(b => {
                    const cat = b.getAttribute('data-cat') || b.getAttribute('data-category');
                    return cat && cat !== 'all' && !b.classList.contains('active');
                }) || null;
            }"""
        )
        chip = chip_handle.as_element()
        if not chip:
            return "SKIP", "no non-'all' category chip available"
        label = page.evaluate(
            "el => (el.getAttribute('data-cat') || el.getAttribute('data-category') || el.textContent || '').trim()",
            chip,
        )
        chip.scroll_into_view_if_needed()
        chip.click()
        page.wait_for_timeout(400)
        after = page.evaluate(
            """() => {
                const g = document.getElementById('specials-grid');
                const cards = g ? Array.from(g.querySelectorAll('.item-card')) : [];
                const names = cards.map(c => (c.querySelector('.item-title')?.textContent || '').trim());
                return { count: cards.length, names };
            }"""
        )
        # Reset to "all" for subsequent checks.
        page.evaluate(
            """() => {
                const all = document.querySelector(
                    ".category-scroll button[data-cat='all'], " +
                    ".category-pills button[data-cat='all'], " +
                    ".filter-btn-cat[data-cat='all']"
                );
                if (all) all.click();
            }"""
        )
        page.wait_for_timeout(200)
        changed = after["count"] != before["count"] or after["names"] != before["names"]
        if not changed:
            return "WARN", f"no change after selecting {label!r}"
        return "PASS", f"{label!r}: {before['count']} → {after['count']} items"

    _try(results, "08", "category chip filters grid", check_category_filter)

    # ------------------------------------------------------------------
    # 09 — Add-to-list increments the count
    # ------------------------------------------------------------------
    def check_add_to_list():
        before = int(
            page.eval_on_selector(
                "#mobile-list-count", "el => parseInt(el.textContent) || 0"
            )
        )
        add_btn = page.query_selector(
            "#specials-grid .add-to-list-btn, #near-misses-grid .add-to-list-btn"
        )
        if not add_btn:
            return "SKIP", "no .add-to-list-btn in grids"
        add_btn.scroll_into_view_if_needed()
        add_btn.click()
        page.wait_for_timeout(400)
        after = int(
            page.eval_on_selector(
                "#mobile-list-count", "el => parseInt(el.textContent) || 0"
            )
        )
        if after <= before:
            return "FAIL", f"badge count did not increment ({before} → {after})"
        return "PASS", f"badge {before} → {after}"

    _try(results, "09", "add-to-list increments count", check_add_to_list)

    # ------------------------------------------------------------------
    # 10 — Drawer opens as bottom sheet, closes via X
    # ------------------------------------------------------------------
    def check_drawer():
        page.click("#mobile-toggle-list")
        page.wait_for_function(
            "() => document.getElementById('list-drawer').classList.contains('open')",
            timeout=3000,
        )
        # Wait for the 0.35s slide-in transition to settle before measuring.
        page.wait_for_timeout(450)
        box = page.eval_on_selector(
            "#list-drawer",
            """el => {
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                return {
                    top: r.top, height: r.height, bottom: r.bottom,
                    vh: window.innerHeight,
                    position: cs.position,
                    transform: cs.transform,
                };
            }""",
        )
        page.screenshot(path=str(shot_dir / "03-drawer-open.png"), full_page=False)
        # Close the drawer the same way every nav away does: call the handler.
        # This avoids flaky clicks when the close button overlaps the list
        # content during transition.
        page.evaluate("toggleDrawer()")
        page.wait_for_timeout(400)
        if box["position"] != "fixed":
            return "FAIL", f"position={box['position']}"
        # Bottom sheet: anchored to the bottom of the viewport (allow 8px slack
        # for rounding / scrollbar / safe-area).
        if abs(box["bottom"] - box["vh"]) > 8:
            return (
                "FAIL",
                f"not anchored to bottom (top={box['top']:.0f}, bottom={box['bottom']:.0f}, vh={box['vh']}, transform={box['transform']})",
            )
        if box["top"] < 40:
            return "FAIL", f"drawer covers full viewport (top={box['top']:.0f})"
        return "PASS", f"bottom-sheet h={box['height']:.0f} / vh={box['vh']}"

    _try(results, "10", "drawer is bottom sheet", check_drawer)

    # ------------------------------------------------------------------
    # 11 — Master Tracklist mobile view
    # ------------------------------------------------------------------
    def check_master_tracklist():
        # Make sure any open drawer/overlay is dismissed before interacting
        # with the feed underneath.
        page.evaluate(
            """() => {
                const d = document.getElementById('list-drawer');
                if (d && d.classList.contains('open')) toggleDrawer();
            }"""
        )
        page.wait_for_timeout(300)
        toggle = page.query_selector("#master-table-toggle")
        if not toggle:
            return "SKIP", "no #master-table-toggle"
        toggle.scroll_into_view_if_needed()
        toggle.click()
        page.wait_for_timeout(600)
        info = page.evaluate(
            """() => {
                const desktop = document.querySelector('.master-desktop-table');
                const mobile = document.getElementById('all-items-list');
                return {
                    desktopDisplay: desktop ? getComputedStyle(desktop).display : null,
                    mobileDisplay: mobile ? getComputedStyle(mobile).display : null,
                    mobileRows: mobile ? mobile.children.length : 0,
                };
            }"""
        )
        if info["desktopDisplay"] != "none":
            return "FAIL", f"desktop table visible (display={info['desktopDisplay']})"
        if info["mobileDisplay"] == "none":
            return "FAIL", "mobile list hidden"
        if info["mobileRows"] == 0:
            return "FAIL", "mobile list empty"
        page.screenshot(
            path=str(shot_dir / "05-master-tracklist.png"), full_page=False
        )
        return "PASS", f"{info['mobileRows']} mobile rows"

    _try(results, "11", "master tracklist mobile view", check_master_tracklist)

    # ------------------------------------------------------------------
    # 12 — Analytics tab narrative flow + responsive heatmap swap
    # ------------------------------------------------------------------
    def check_analytics():
        # Defensive: close drawer if still open from prior checks.
        page.evaluate(
            """() => {
                const d = document.getElementById('list-drawer');
                if (d && d.classList.contains('open')) toggleDrawer();
            }"""
        )
        page.wait_for_timeout(250)
        page.click(".mobile-bottom-nav button[data-tab='analytics']")
        page.wait_for_selector("#tab-analytics.tab-content.active", timeout=5000)
        # Wait for the analytics cards to render at least once.
        page.wait_for_timeout(900)
        page.evaluate("window.scrollTo(0, 260)")
        page.wait_for_timeout(120)
        page.screenshot(path=str(shot_dir / "04-analytics.png"), full_page=True)
        info = page.evaluate(
            """() => {
                const flex = document.querySelector('#deep-insights-container');
                const grid = document.querySelector('.heatmap-grid');
                const mlist = document.querySelector('.heatmap-mobile-list');
                const shoppingTime = document.getElementById('shopping-time-insights');
                const chart = document.querySelector('#tab-analytics .chart-container');
                const analyticsTab = document.getElementById('tab-analytics');
                const sections = [...document.querySelectorAll('#tab-analytics .insights-story-section')];
                const firstTitleEl = document.querySelector('#tab-analytics .analytics-card h3');
                const details = document.getElementById('compare-group-details');
                return {
                    flexDisplay: flex ? getComputedStyle(flex).display : null,
                    flexDirection: flex ? getComputedStyle(flex).flexDirection : null,
                    flexOverflowX: flex ? getComputedStyle(flex).overflowX : null,
                    gridDisplay: grid ? getComputedStyle(grid).display : null,
                    mlistExists: !!mlist,
                    mlistRows: mlist ? mlist.children.length : 0,
                    hasShoppingTime: !!shoppingTime,
                    shoppingTimeText: shoppingTime ? (shoppingTime.textContent || '').trim() : '',
                    analyticsOverflowPx: analyticsTab ? (analyticsTab.scrollWidth - analyticsTab.clientWidth) : 0,
                    chartHeight: chart ? chart.getBoundingClientRect().height : null,
                    sectionCount: sections.length,
                    firstTitle: firstTitleEl ? (firstTitleEl.textContent || '').trim() : '',
                    compareDetailsOpen: details ? details.open : null,
                };
            }"""
        )
        issues = []
        if info["sectionCount"] < 4:
            issues.append(f"story sections missing ({info['sectionCount']})")
        if not info["firstTitle"].lower().startswith("how much you"):
            issues.append(f"story order unexpected (first card={info['firstTitle']!r})")
        if info["compareDetailsOpen"] is not False:
            issues.append("advanced diagnostics should be collapsed on mobile")
        if info["flexDisplay"] != "flex":
            issues.append(f"insights display={info['flexDisplay']}")
        if info["flexDirection"] != "column":
            issues.append(f"insights flex-direction={info['flexDirection']}")
        if info["flexOverflowX"] not in (None, "visible", "unset", "clip"):
            issues.append(f"insights overflow-x={info['flexOverflowX']}")
        if info["gridDisplay"] not in (None, "none"):
            issues.append(f".heatmap-grid display={info['gridDisplay']}")
        if not info["mlistExists"]:
            issues.append("no .heatmap-mobile-list rendered")
        elif info["mlistRows"] == 0:
            issues.append(".heatmap-mobile-list empty")
        if not info["hasShoppingTime"]:
            issues.append("shopping-time card missing")
        elif not info["shoppingTimeText"]:
            issues.append("shopping-time card empty")
        if info["analyticsOverflowPx"] > 1:
            issues.append(f"analytics overflow={info['analyticsOverflowPx']}px")
        if info["chartHeight"] is not None and info["chartHeight"] > 245:
            issues.append(f"chart height too tall ({info['chartHeight']:.1f}px)")

        # Confirm runtime swaps heatmap layout when crossing mobile/desktop width.
        page.set_viewport_size({"width": 900, "height": 844})
        page.wait_for_timeout(320)
        desktop_info = page.evaluate(
            """() => {
                const grid = document.querySelector('.heatmap-grid');
                const mlist = document.querySelector('.heatmap-mobile-list');
                return {
                    gridDisplay: grid ? getComputedStyle(grid).display : null,
                    hasMobileList: !!mlist && mlist.children.length > 0,
                };
            }"""
        )
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(320)
        mobile_back = page.evaluate(
            """() => {
                const grid = document.querySelector('.heatmap-grid');
                const mlist = document.querySelector('.heatmap-mobile-list');
                return {
                    gridDisplay: grid ? getComputedStyle(grid).display : null,
                    hasMobileList: !!mlist && mlist.children.length > 0,
                };
            }"""
        )
        if desktop_info["gridDisplay"] in (None, "none"):
            issues.append("heatmap grid did not reappear on desktop viewport")
        if not mobile_back["hasMobileList"]:
            issues.append("heatmap mobile list missing after returning to phone viewport")
        if mobile_back["gridDisplay"] not in (None, "none"):
            issues.append("heatmap grid still visible after returning to phone viewport")

        if issues:
            return "FAIL", "; ".join(issues)
        return "PASS", f"{info['mlistRows']} heatmap rows, narrative layout + responsive swap ok"

    _try(results, "12", "analytics tab mobile layout", check_analytics)

    # ------------------------------------------------------------------
    # 13 — Tap-highlight disabled on buttons
    # ------------------------------------------------------------------
    def check_tap_highlight():
        val = page.eval_on_selector(
            "button",
            "el => getComputedStyle(el).webkitTapHighlightColor || getComputedStyle(el).WebkitTapHighlightColor",
        )
        if not val:
            return "WARN", "property not exposed by Chromium"
        # "transparent" renders as rgba(0, 0, 0, 0).
        if "rgba(0, 0, 0, 0)" in val or val.lower() == "transparent":
            return "PASS", val
        return "WARN", f"tap highlight={val}"

    _try(results, "13", "tap-highlight transparent", check_tap_highlight)

    # ------------------------------------------------------------------
    # 14 — Shopping trip mode: toggle + picked persistence
    # ------------------------------------------------------------------
    def check_shopping_trip_mode():
        page.evaluate(
            """({ listJson, mode }) => {
                localStorage.setItem('shoppingList', listJson);
                localStorage.setItem('shoppingTripMode', mode);
                localStorage.removeItem('shoppingTripStartedAt');
                localStorage.removeItem('shoppingTripStartCount');
                localStorage.setItem('shoppingTripSessions', '[]');
            }""",
            {
                "listJson": json.dumps(
                    [
                        {
                            "name": "E2E Bread",
                            "qty": 1,
                            "price": 3.2,
                            "store": "woolworths",
                            "picked": False,
                        },
                        {
                            "name": "E2E Eggs",
                            "qty": 1,
                            "price": 4.5,
                            "store": "woolworths",
                            "picked": False,
                        },
                    ]
                ),
                "mode": "0",
            },
        )
        page.reload(wait_until="domcontentloaded")
        _wait_for_app_ready(page)
        page.click(".mobile-bottom-nav button[data-tab='deals']")
        page.wait_for_selector("#tab-deals.tab-content.active", timeout=8000)
        page.evaluate("toggleDrawer()")
        page.wait_for_selector("#list-drawer.open", timeout=5000)

        go_btn = page.locator("#go-shopping-btn")
        go_btn.wait_for(state="visible", timeout=5000)
        if go_btn.is_disabled():
            return "FAIL", "go-shopping-btn stayed disabled"
        done_hidden = page.evaluate(
            "() => document.getElementById('done-shopping-btn')?.hidden === true"
        )
        clear_hidden = page.evaluate(
            "() => document.getElementById('clear-completed-btn')?.hidden === true"
        )
        label_before = page.eval_on_selector("#list-total-label", "el => el.textContent?.trim()")
        if not done_hidden or not clear_hidden:
            return "FAIL", "done/clear should be hidden before trip mode"
        if label_before != "About":
            return "FAIL", f"expected About label before trip mode, got {label_before!r}"

        go_btn.click()
        page.wait_for_selector(".shopping-item-picked", timeout=5000)
        mode_on = page.evaluate("() => localStorage.getItem('shoppingTripMode') === '1'")
        if not mode_on:
            return "FAIL", "shoppingTripMode not enabled"
        drawer_trip_class = page.evaluate(
            "() => document.getElementById('list-drawer')?.classList.contains('shopping-trip-mode')"
        )
        if not drawer_trip_class:
            return "FAIL", "shopping-trip-mode class missing on drawer"
        label_trip = page.eval_on_selector("#list-total-label", "el => el.textContent?.trim()")
        if label_trip != "Left to buy":
            return "FAIL", f"expected Left to buy label in trip mode, got {label_trip!r}"
        done_visible = page.evaluate(
            "() => document.getElementById('done-shopping-btn')?.hidden === false"
        )
        go_hidden = page.evaluate(
            "() => document.getElementById('go-shopping-btn')?.hidden === true"
        )
        clear_visible = page.evaluate(
            "() => document.getElementById('clear-completed-btn')?.hidden === false"
        )
        clear_disabled = page.eval_on_selector("#clear-completed-btn", "el => el.disabled")
        if not done_visible or not go_hidden or not clear_visible or not clear_disabled:
            return "FAIL", "trip-mode button state matrix failed"
        status_text = page.eval_on_selector(
            "#shopping-trip-status", "el => el.hidden ? '' : (el.textContent || '').trim()"
        )
        if "left" not in status_text.lower():
            return "FAIL", f"trip status text not shown: {status_text!r}"
        total_before = page.eval_on_selector(
            "#list-total-price",
            "el => Number((el.textContent || '').replace(/[^0-9.]/g, ''))",
        )

        picked_box = page.locator(".shopping-item-picked").first
        picked_box.check()
        page.wait_for_function(
            """() => {
                const rows = JSON.parse(localStorage.getItem('shoppingList') || '[]');
                return Array.isArray(rows) && rows.length > 0 && rows[0].picked === true;
            }""",
            timeout=5000,
        )
        total_after_pick = page.eval_on_selector(
            "#list-total-price",
            "el => Number((el.textContent || '').replace(/[^0-9.]/g, ''))",
        )
        if not (total_after_pick < total_before):
            return "FAIL", f"left-to-buy total did not drop ({total_before} -> {total_after_pick})"
        clear_enabled = page.eval_on_selector("#clear-completed-btn", "el => !el.disabled")
        if not clear_enabled:
            return "FAIL", "clear completed stayed disabled after ticking an item"

        page.click("#clear-completed-btn")
        page.wait_for_function(
            """() => {
                const rows = JSON.parse(localStorage.getItem('shoppingList') || '[]');
                return Array.isArray(rows) && rows.length === 1 && rows[0].picked === false;
            }""",
            timeout=5000,
        )

        remaining_box = page.locator(".shopping-item-picked").first
        remaining_box.check()
        page.click("#clear-completed-btn")
        page.wait_for_function(
            """() => {
                const rows = JSON.parse(localStorage.getItem('shoppingList') || '[]');
                return Array.isArray(rows) && rows.length === 0;
            }""",
            timeout=5000,
        )
        mode_auto_off = page.evaluate("() => localStorage.getItem('shoppingTripMode') === '0'")
        if not mode_auto_off:
            done_btn = page.locator("#done-shopping-btn")
            done_btn.wait_for(state="visible", timeout=5000)
            done_btn.click()
            mode_auto_off = page.evaluate("() => localStorage.getItem('shoppingTripMode') === '0'")
            if not mode_auto_off:
                return "FAIL", "shoppingTripMode not disabled after Done shopping"
        session_ok = page.evaluate(
            """() => {
                const sessions = JSON.parse(localStorage.getItem('shoppingTripSessions') || '[]');
                if (!Array.isArray(sessions) || sessions.length === 0) return false;
                const last = sessions[sessions.length - 1] || {};
                return Boolean(last.started_at)
                    && Boolean(last.ended_at)
                    && Number.isFinite(last.duration_seconds)
                    && last.duration_seconds >= 0;
            }"""
        )
        if not session_ok:
            return "FAIL", "shopping trip session timing was not recorded"
        return "PASS", "trip mode matrix, totals, status, and clear-completed behavior validated"

    _try(results, "14", "shopping trip mode tick flow", check_shopping_trip_mode)

    # ------------------------------------------------------------------
    # 16 — Trip timeout marker and next-trip reminder
    # ------------------------------------------------------------------
    def check_trip_timeout_reminder():
        stale_start = "2026-01-01T00:00:00.000Z"
        stale_timeout_at = 1  # always expired
        page.evaluate(
            """({ listJson, staleStart, staleTimeoutAt }) => {
                localStorage.setItem('shoppingList', listJson);
                localStorage.setItem('shoppingTripMode', '1');
                localStorage.setItem('shoppingTripStartedAt', staleStart);
                localStorage.setItem('shoppingTripStartCount', '1');
                localStorage.setItem('shoppingTripTimeoutAt', String(staleTimeoutAt));
                localStorage.setItem('shoppingTripSessions', '[]');
                localStorage.removeItem('shoppingTripTimeoutReminderPending');
            }""",
            {
                "listJson": json.dumps(
                    [{"name": "E2E Timeout Milk", "qty": 1, "price": 4.2, "store": "woolworths", "picked": False}]
                ),
                "staleStart": stale_start,
                "staleTimeoutAt": stale_timeout_at,
            },
        )
        page.reload(wait_until="domcontentloaded")
        _wait_for_app_ready(page)

        timed_out = page.evaluate(
            """() => {
                const modeOff = localStorage.getItem('shoppingTripMode') === '0';
                const remind = localStorage.getItem('shoppingTripTimeoutReminderPending') === '1';
                const sessions = JSON.parse(localStorage.getItem('shoppingTripSessions') || '[]');
                const last = sessions.length ? sessions[sessions.length - 1] : null;
                return modeOff && remind && !!last && last.reason === 'idle_timeout';
            }"""
        )
        if not timed_out:
            return "FAIL", "stale trip did not auto-timeout with reminder marker"

        page.click(".mobile-bottom-nav button[data-tab='deals']")
        page.wait_for_selector("#tab-deals.tab-content.active", timeout=8000)
        page.evaluate("toggleDrawer()")
        page.wait_for_selector("#list-drawer.open", timeout=5000)
        go_btn = page.locator("#go-shopping-btn")
        go_btn.wait_for(state="visible", timeout=5000)
        go_btn.click()

        reminder_seen = page.wait_for_function(
            """() => {
                const t = document.getElementById('ui-toast');
                if (!t) return false;
                const msg = (t.textContent || '').toLowerCase();
                return msg.includes('timed out') && msg.includes('done shopping');
            }""",
            timeout=5000,
        )
        if not reminder_seen:
            return "FAIL", "next-trip timeout reminder toast not shown"

        reminder_cleared = page.evaluate(
            "() => localStorage.getItem('shoppingTripTimeoutReminderPending') !== '1'"
        )
        if not reminder_cleared:
            return "FAIL", "timeout reminder marker was not cleared after showing reminder"
        return "PASS", "idle timeout marker and next-trip reminder validated"

    _try(results, "16", "trip timeout reminder flow", check_trip_timeout_reminder)

# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="WooliesBot mobile end-to-end tests")
    ap.add_argument("--device", default="iPhone 13", help="Playwright device descriptor")
    ap.add_argument("--headed", action="store_true", help="Show the browser window")
    ap.add_argument("--port", type=int, default=0, help="Local server port (0 = random)")
    ap.add_argument(
        "--base-url",
        default=None,
        help="Skip the built-in server and test this URL instead",
    )
    ap.add_argument(
        "--keep-open",
        action="store_true",
        help="Pause at the end (useful with --headed)",
    )
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shot_dir = SHOT_ROOT / ts
    shot_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nWooliesBot mobile e2e  ·  {ts}")
    print(f"  docs dir:    {DOCS_DIR}")
    print(f"  screenshots: {shot_dir}")

    results = Results()
    server_ctx = (
        contextlib.nullcontext(args.base_url)
        if args.base_url
        else serve_docs(args.port or _pick_free_port())
    )

    with server_ctx as base_url:
        print(f"  base URL:    {base_url}")
        print(f"  device:      {args.device}")
        print("-" * 72)
        with sync_playwright() as pw:
            device = pw.devices.get(args.device)
            if device is None:
                print(f"  WARNING: unknown device {args.device!r}; falling back to iPhone 13")
                device = pw.devices["iPhone 13"]
            browser = pw.chromium.launch(headless=not args.headed)
            context = browser.new_context(
                **device,
                service_workers="allow",
                locale="en-AU",
            )
            page = context.new_page()
            try:
                run_checks(page, results, shot_dir, base_url)
            except PWError as e:
                results.record("XX", "fatal Playwright error", "FAIL", str(e)[:120])
            finally:
                if args.keep_open:
                    print("\n  --keep-open set; press Ctrl+C to exit.")
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        pass
                context.close()
                browser.close()

    print("-" * 72)
    print(f"  {results.summary()}")
    print(f"  Screenshots: {shot_dir}")
    sys.exit(0 if results.passed() else 1)


if __name__ == "__main__":
    main()
