#!/usr/bin/env python3
"""
audit_mobile.py — Observational mobile audit for the WooliesBot dashboard.

Complements scripts/e2e_mobile.py: instead of asserting pass/fail, it measures
real-world UX signals and prints them as a ranked list of improvement
opportunities. Use it to spot wasted vertical space, tiny tap targets,
horizontal overflow, persistent toasts, filter bars eating the viewport, etc.

Usage:
  python scripts/audit_mobile.py              # run full audit, print report
  python scripts/audit_mobile.py --headed     # watch the browser
  python scripts/audit_mobile.py --json       # machine-readable output
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
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.stderr.write(
        "ERROR: pip install playwright && python -m playwright install chromium\n"
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
SHOT_ROOT = REPO_ROOT / "screenshots" / "audit_mobile"


# ---------------------------------------------------------------------------
# Tiny static server (same pattern as e2e_mobile.py)
# ---------------------------------------------------------------------------
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_a, **_kw):
        pass


class _ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve_docs(port: int):
    class H(_QuietHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(DOCS_DIR), **kw)

    httpd = _ReusableServer(("127.0.0.1", port), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# The probes
# ---------------------------------------------------------------------------
# Each probe returns a dict:
#   { id, name, severity, value, recommendation }
# severity: "high" | "med" | "low" | "ok"

PROBE_JS = r"""
() => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const doc = document.documentElement;
    const results = [];
    const push = (id, name, severity, value, recommendation = '') =>
        results.push({ id, name, severity, value, recommendation });

    // ---- Horizontal overflow ------------------------------------------
    const hoverflow = Math.max(doc.scrollWidth, document.body.scrollWidth) - vw;
    push(
        'horizontal-overflow',
        'Horizontal page overflow',
        hoverflow > 2 ? 'high' : 'ok',
        { overflow_px: hoverflow, vw },
        hoverflow > 2
            ? 'Find the offending element (often a chart canvas, wide card, or un-truncated text) and constrain width / add overflow:hidden.'
            : '',
    );

    // ---- Sticky/fixed chrome height -----------------------------------
    const header = document.querySelector('header, .dashboard-header, .app-header');
    const bottomNav = document.querySelector('.mobile-bottom-nav');
    const sticky = document.querySelector('.sticky-filter-bar');
    const headerH = header ? header.getBoundingClientRect().height : 0;
    const bottomH = bottomNav ? bottomNav.getBoundingClientRect().height : 0;
    const stickyH = sticky ? sticky.getBoundingClientRect().height : 0;
    const chromeH = headerH + bottomH + stickyH;
    push(
        'chrome-height',
        'Fixed chrome vertical footprint',
        chromeH / vh > 0.35 ? 'high' : chromeH / vh > 0.25 ? 'med' : 'ok',
        { header: Math.round(headerH), sticky: Math.round(stickyH),
          bottomNav: Math.round(bottomH), chrome: Math.round(chromeH), vh,
          pct: Math.round((chromeH / vh) * 100) },
        chromeH / vh > 0.25
            ? 'Compact header/sticky filter bar so content area is at least 70% of viewport.'
            : '',
    );

    // ---- Tap targets smaller than 44×44 --------------------------------
    const MIN = 44;
    const interactive = document.querySelectorAll(
        'button, a, [role="button"], input[type="checkbox"], input[type="radio"], .filter-btn, .filter-btn-cat, .sort-pill'
    );
    const tooSmall = [];
    interactive.forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return; // skip hidden
        // Only consider elements currently in viewport-ish range
        if (r.bottom < 0 || r.top > (vh + 2000)) return;
        if (r.width < MIN - 2 || r.height < MIN - 2) {
            tooSmall.push({
                tag: el.tagName.toLowerCase(),
                cls: (el.className || '').toString().slice(0, 50),
                id: el.id || '',
                text: (el.textContent || '').trim().slice(0, 24),
                w: Math.round(r.width),
                h: Math.round(r.height),
            });
        }
    });
    push(
        'tap-targets',
        'Tap targets below 44×44',
        tooSmall.length > 10 ? 'high' : tooSmall.length > 3 ? 'med' : tooSmall.length ? 'low' : 'ok',
        { count: tooSmall.length, samples: tooSmall.slice(0, 6) },
        tooSmall.length
            ? 'Bump min-width/min-height to 44px on affected buttons (or wrap in a larger hit-area).'
            : '',
    );

    // ---- Wasted vertical whitespace ------------------------------------
    // Look for consecutive empty regions (no painted children) >80px tall
    // between major sections on the Deals tab.
    const tab = document.getElementById('tab-deals');
    let whiteGap = 0;
    let gapRange = null;
    if (tab) {
        const rects = [];
        tab.querySelectorAll(':scope > *, :scope > * > *').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.height > 0 && getComputedStyle(el).visibility !== 'hidden') {
                rects.push([r.top + window.scrollY, r.bottom + window.scrollY]);
            }
        });
        rects.sort((a, b) => a[0] - b[0]);
        let last = 0;
        for (const [t, b] of rects) {
            const gap = t - last;
            if (gap > whiteGap) {
                whiteGap = gap;
                gapRange = [Math.round(last), Math.round(t)];
            }
            last = Math.max(last, b);
        }
    }
    push(
        'whitespace',
        'Largest empty vertical gap (Deals tab)',
        whiteGap > 150 ? 'high' : whiteGap > 80 ? 'med' : 'ok',
        { gap_px: Math.round(whiteGap), range: gapRange },
        whiteGap > 80
            ? 'Tighten margins between sections; a gap this large usually means a hidden/empty placeholder.'
            : '',
    );

    // ---- Toast / banner that overlays content persistently --------------
    const toastCandidates = document.querySelectorAll(
        '.toast, .notification, .banner, [class*="notify"], [class*="toast"]'
    );
    const persistent = [];
    toastCandidates.forEach(el => {
        if (el.classList && el.classList.contains('price-drop-toast')) {
            return; /* auto-dismiss + anchored; ignore during snapshot */
        }
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        if ((cs.position === 'fixed' || cs.position === 'sticky')
            && cs.display !== 'none' && cs.visibility !== 'hidden'
            && r.height > 20) {
            persistent.push({
                cls: el.className.toString().slice(0, 50),
                text: (el.textContent || '').trim().slice(0, 60),
                top: Math.round(r.top), left: Math.round(r.left),
                w: Math.round(r.width), h: Math.round(r.height),
            });
        }
    });
    push(
        'persistent-toast',
        'Persistent fixed toast/banner',
        persistent.length ? 'med' : 'ok',
        { count: persistent.length, samples: persistent },
        persistent.length
            ? 'Auto-dismiss after 6–8s, or reposition above the bottom nav so it does not overlap feed content.'
            : '',
    );

    // ---- Hidden-but-rendered sections (paint cost) ---------------------
    const maybeHidden = document.querySelectorAll(
        '.tab-content:not(.active), .hidden, [style*="display: none"], [style*="display:none"]'
    );
    let heavyHidden = 0;
    maybeHidden.forEach(el => {
        heavyHidden += el.querySelectorAll('canvas, img, svg').length;
    });
    push(
        'hidden-paint',
        'Heavy media in inactive tabs',
        heavyHidden > 20 ? 'med' : 'ok',
        { canvas_img_svg: heavyHidden },
        heavyHidden > 20
            ? 'Lazy-render Insights charts on tab switch to cut first-paint cost.'
            : '',
    );

    // ---- Images missing loading="lazy" --------------------------------
    const imgs = document.querySelectorAll('img');
    let eagerImgs = 0;
    imgs.forEach(i => { if (i.loading !== 'lazy' && !i.hasAttribute('data-lazy')) eagerImgs++; });
    push(
        'lazy-images',
        'Images without loading="lazy"',
        eagerImgs > 50 ? 'med' : eagerImgs > 10 ? 'low' : 'ok',
        { eager: eagerImgs, total: imgs.length },
        eagerImgs > 10
            ? 'Add loading="lazy" to item-card <img> tags to defer offscreen loads.'
            : '',
    );

    // ---- Stats strip: is overflow affordance visible? -----------------
    const strip = document.querySelector('.stats-strip');
    let stripFade = null;
    if (strip) {
        const cs = getComputedStyle(strip);
        const mask = (cs.maskImage || cs.webkitMaskImage || '').toString();
        const hasMask = mask && mask !== 'none' && mask !== 'initial';
        const scrollable = strip.scrollWidth > strip.clientWidth + 4;
        stripFade = { scrollable, hasMask };
        push(
            'strip-affordance',
            'Horizontal scroll has fade/mask affordance',
            scrollable && !hasMask ? 'low' : 'ok',
            stripFade,
            (scrollable && !hasMask)
                ? 'Add a right-edge gradient (mask-image) so users know content continues.'
                : '',
        );
    }

    // ---- Body padding-bottom big enough for bottom nav? ---------------
    const bodyPB = parseFloat(getComputedStyle(document.body).paddingBottom) || 0;
    push(
        'safe-bottom',
        'Body padding-bottom vs bottom nav',
        bodyPB < bottomH - 4 ? 'med' : 'ok',
        { body_padding: Math.round(bodyPB), bottom_nav: Math.round(bottomH) },
        bodyPB < bottomH - 4
            ? 'Increase body padding-bottom so the last feed row is not hidden behind the bottom nav.'
            : '',
    );

    // ---- Filter bar total height -------------------------------------
    if (sticky) {
        push(
            'filter-bar-height',
            'Sticky filter bar height',
            stickyH > 180 ? 'high' : stickyH > 140 ? 'med' : 'ok',
            { height: Math.round(stickyH), pct_of_viewport: Math.round((stickyH / vh) * 100) },
            stickyH > 140
                ? 'Combine store filter + sort pills into one row, or collapse category chips into a single-row scroller.'
                : '',
        );
    }

    // ---- Pagination button alignment ---------------------------------
    const pag = document.getElementById('pagination-controls');
    if (pag && pag.children.length) {
        const r = pag.getBoundingClientRect();
        const grid = document.getElementById('specials-grid');
        const g = grid ? grid.getBoundingClientRect() : null;
        const gap = g ? r.top - g.bottom : 0;
        push(
            'pagination-gap',
            'Distance from grid to pagination',
            gap > 200 ? 'med' : 'ok',
            { gap_px: Math.round(gap) },
            gap > 200
                ? 'Pagination looks orphaned — tighten the space or move it below the grid.'
                : '',
        );
    }

    return { vw, vh, results };
}
"""


def bucket(sev):
    return {"high": 0, "med": 1, "low": 2, "ok": 3}[sev]


def _emoji(sev):
    return {"high": "!!", "med": "!", "low": ".", "ok": " "}.get(sev, " ")


def run(args):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shot_dir = SHOT_ROOT / ts
    shot_dir.mkdir(parents=True, exist_ok=True)

    with serve_docs(_pick_port()) as base_url, sync_playwright() as pw:
        device = pw.devices["iPhone 13"]
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(**device, locale="en-AU")
        page = context.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        # Wait for dashboard to hydrate.
        page.wait_for_selector("#tab-deals.tab-content.active", timeout=15_000)
        page.wait_for_function(
            "() => document.querySelectorAll('#specials-grid .item-card').length > 0",
            timeout=20_000,
        )
        # Let any opening animations / toast slide-ins settle.
        page.wait_for_timeout(1200)
        page.screenshot(path=str(shot_dir / "deals-top.png"), full_page=False)

        report = page.evaluate(PROBE_JS)

        # Perf timing snapshot.
        timing = page.evaluate(
            """() => {
                const nav = performance.getEntriesByType('navigation')[0] || {};
                const paint = performance.getEntriesByType('paint').map(p => [p.name, Math.round(p.startTime)]);
                return {
                    dom_content_loaded: Math.round(nav.domContentLoadedEventEnd || 0),
                    load: Math.round(nav.loadEventEnd || 0),
                    paint: Object.fromEntries(paint),
                };
            }"""
        )

        # Scroll to bottom of deals to catch gaps further down.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(shot_dir / "deals-bottom.png"), full_page=False)

        context.close()
        browser.close()

    results = report["results"]
    results.sort(key=lambda r: (bucket(r["severity"]), r["id"]))

    if args.json:
        out = {
            "ts": ts,
            "viewport": {"vw": report["vw"], "vh": report["vh"]},
            "timing": timing,
            "findings": results,
            "screenshots": str(shot_dir),
        }
        print(json.dumps(out, indent=2))
        return

    print(f"\nWooliesBot mobile audit  ·  {ts}")
    print(f"  viewport: {report['vw']}×{report['vh']}   screenshots: {shot_dir}")
    print(
        f"  timing:   DCL={timing.get('dom_content_loaded', 0)}ms  "
        f"load={timing.get('load', 0)}ms  "
        f"FP={timing.get('paint', {}).get('first-paint', '?')}ms  "
        f"FCP={timing.get('paint', {}).get('first-contentful-paint', '?')}ms"
    )
    print("-" * 78)

    high = [r for r in results if r["severity"] == "high"]
    med = [r for r in results if r["severity"] == "med"]
    low = [r for r in results if r["severity"] == "low"]
    oks = [r for r in results if r["severity"] == "ok"]

    for r in results:
        marker = _emoji(r["severity"])
        print(f"[{marker}] {r['severity'].upper():4}  {r['id']:22}  {r['name']}")
        val = r["value"]
        if isinstance(val, dict):
            for k, v in val.items():
                if isinstance(v, list) and v:
                    print(f"        {k}:")
                    for sample in v[:4]:
                        print(f"          - {sample}")
                elif v not in (None, [], {}):
                    print(f"        {k}: {v}")
        if r["recommendation"]:
            print(f"        → {r['recommendation']}")

    print("-" * 78)
    print(
        f"  {len(high)} high  ·  {len(med)} med  ·  {len(low)} low  ·  {len(oks)} ok"
    )
    print()


def main():
    ap = argparse.ArgumentParser(description="WooliesBot mobile audit")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
