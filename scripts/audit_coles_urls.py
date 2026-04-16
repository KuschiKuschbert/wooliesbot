#!/usr/bin/env python3
"""
audit_coles_urls.py — BFF-based audit of all Coles URLs in data.json.

Walks every item that has a Coles /product/ URL, calls the BFF API, builds
a full label (brand + name + size), then checks:
  - _size_signals_compatible(inventory_name, live_label)  — hard size conflict
  - _token_overlap_score(inventory_name, live_label) < 0.30  — low name overlap
  - price=None (out of stock)
  - BFF returns nothing (delisted / dead ID)

Usage:
  python scripts/audit_coles_urls.py              # full audit, all items
  python scripts/audit_coles_urls.py --fix        # auto-clear confirmed dead/wrong IDs
  python scripts/audit_coles_urls.py --item Pepsi # filter by name substring
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from chef_os import (
        _create_cffi_session,
        _extract_size_signals,
        _size_signals_compatible,
        _token_overlap_score,
        _COLES_BFF_STORE_ID,
        _COLES_BFF_SUBSCRIPTION_KEY,
    )
    HAS_CHEF = True
except ImportError as e:
    print(f"[WARN] Could not import from chef_os: {e} — using local helpers")
    HAS_CHEF = False

# ---------------------------------------------------------------------------
# Fallback helpers (only used if chef_os import fails)
# ---------------------------------------------------------------------------

def _extract_size_signals_local(text):
    if not text:
        return {"packs": set(), "volumes_ml": set(), "weights_g": set()}
    low = text.lower()
    packs = {int(m.group(1)) for m in re.finditer(r"\b(\d+)\s*(?:pk|pack)\b", low)}
    volumes_ml: set = set()
    weights_g: set = set()
    for m in re.finditer(r"\b(\d+)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(ml|l|g|kg)\b", low):
        count, qty, unit = int(m.group(1)), float(m.group(2)), m.group(3)
        packs.add(count)
        if unit == "ml":
            volumes_ml.add(int(round(qty))); volumes_ml.add(int(round(count * qty)))
        elif unit == "l":
            volumes_ml.add(int(round(qty * 1000))); volumes_ml.add(int(round(count * qty * 1000)))
        elif unit == "g":
            weights_g.add(int(round(qty))); weights_g.add(int(round(count * qty)))
        elif unit == "kg":
            weights_g.add(int(round(qty * 1000))); weights_g.add(int(round(count * qty * 1000)))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(ml|l)\b", low):
        qty = float(m.group(1))
        volumes_ml.add(int(round(qty if m.group(2) == "ml" else qty * 1000)))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(g|kg)\b", low):
        qty = float(m.group(1))
        weights_g.add(int(round(qty if m.group(2) == "g" else qty * 1000)))
    return {"packs": packs, "volumes_ml": volumes_ml, "weights_g": weights_g}


def _size_signals_compatible_local(a_name, b_name):
    a = _extract_size_signals_local(a_name)
    b = _extract_size_signals_local(b_name)
    for key in ("packs", "volumes_ml", "weights_g"):
        if a[key] and b[key] and not (a[key] & b[key]):
            return False
    return True


def _token_overlap_score_local(inv, scraped):
    if not inv or not scraped:
        return 0.0
    stop = {"woolworths", "coles", "soft", "drink", "product", "pack", "pk",
            "multipack", "bottle", "can", "tub", "tray", "bag", "punnet", "each"}
    def _stem(t):
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            return t[:-1]
        return t
    def _tok(text):
        return {_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in stop}
    ta, tb = _tok(inv), _tok(scraped)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


if not HAS_CHEF:
    _extract_size_signals = _extract_size_signals_local  # noqa: F811
    _size_signals_compatible = _size_signals_compatible_local  # noqa: F811
    _token_overlap_score = _token_overlap_score_local  # noqa: F811
    _COLES_BFF_STORE_ID = "0584"
    _COLES_BFF_SUBSCRIPTION_KEY = "eae83861d1cd4de6bb9cd8a2cd6f041e"

    try:
        import curl_cffi.requests as _cffi_req
        def _create_cffi_session(store):
            s = _cffi_req.Session(impersonate="chrome131")
            return s
    except ImportError:
        import requests as _req
        def _create_cffi_session(store):
            return _req.Session()

# ---------------------------------------------------------------------------
# BFF helpers
# ---------------------------------------------------------------------------

BFF_BASE = "https://www.coles.com.au/api/bff/products/{pid}?storeId={sid}&subscription-key={key}"

def _extract_coles_pid(url):
    if not url:
        return None
    m = re.search(r"-(\d{4,})$", url.rstrip("/").split("?")[0])
    return m.group(1) if m else None


def _bff_fetch(session, pid):
    url = BFF_BASE.format(pid=pid, sid=_COLES_BFF_STORE_ID, key=_COLES_BFF_SUBSCRIPTION_KEY)
    try:
        r = session.get(url, headers={"Accept": "application/json"}, timeout=15)
        if r.status_code != 200 or not r.text:
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

COLS = ("STATUS", "ITEM", "LIVE_LABEL", "OVERLAP")
STATUS_ORDER = {"SIZE_CONFLICT": 0, "DIFF": 1, "OOS": 2, "DEAD": 3, "WARN": 4, "OK": 5}


def run_audit(items, filter_name=None):
    session = _create_cffi_session("coles")
    results = []

    target = [
        it for it in items
        if it.get("coles") and "/product/" in it.get("coles", "")
        and not it.get("coles", "").startswith("https://www.coles.com.au/search")
        and (not filter_name or filter_name.lower() in it.get("name", "").lower())
    ]

    print(f"\nAuditing {len(target)} Coles product URLs against BFF API...\n")
    print(f"{'STATUS':<16} {'OVERLAP':>7}  {'INVENTORY NAME':<45} {'BFF LABEL'}")
    print("-" * 120)

    for it in target:
        name = it.get("name", "?")
        coles_url = it.get("coles", "")
        pid = _extract_coles_pid(coles_url)
        if not pid:
            continue

        data = _bff_fetch(session, pid)
        time.sleep(0.25)

        if data is None:
            print(f"{'DEAD':<16} {'—':>7}  {name:<45} (BFF returned nothing)")
            results.append({"name": name, "url": coles_url, "pid": pid, "status": "DEAD"})
            continue

        pricing = data.get("pricing")
        live_brand = data.get("brand", "")
        live_name = data.get("name", "")
        live_size = data.get("size", "")
        live_label = " ".join(filter(None, [live_brand, live_name, live_size])).strip()
        ov = _token_overlap_score(name, live_label)
        size_ok = _size_signals_compatible(name, live_label)

        if not pricing or not pricing.get("now"):
            status = "OOS"
        elif not size_ok:
            status = "SIZE_CONFLICT"
        elif ov < 0.10:
            status = "DIFF"
        elif ov < 0.30:
            status = "WARN"
        else:
            status = "OK"

        if status not in ("OK",):
            print(f"{status:<16} {ov:>7.2f}  {name:<45} {live_label!r}")

        results.append({
            "name": name, "url": coles_url, "pid": pid,
            "live_label": live_label, "overlap": ov,
            "size_ok": size_ok, "status": status,
        })

    # Summary
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for st in sorted(by_status, key=lambda s: STATUS_ORDER.get(s, 99)):
        print(f"  {st:<16}: {len(by_status[st])}")
    print(f"  {'TOTAL':<16}: {len(results)}")

    needs_fix = [r for r in results if r["status"] in ("SIZE_CONFLICT", "DIFF", "DEAD")]
    if needs_fix:
        print(f"\nACTION REQUIRED ({len(needs_fix)} items):")
        for r in needs_fix:
            lbl = r.get("live_label", "—")
            ov = r.get("overlap", 0.0)
            print(f"  [{r['status']}] {r['name']!r}")
            print(f"         url  = {r['url']}")
            print(f"         live = {lbl!r} (overlap={ov:.2f})")

    return results


def main():
    parser = argparse.ArgumentParser(description="Audit all Coles URLs against BFF API")
    parser.add_argument("--item", type=str, default=None, help="Filter by item name substring")
    args = parser.parse_args()

    data_path = REPO_ROOT / "docs" / "data.json"
    with open(data_path, encoding="utf-8") as f:
        raw = json.load(f)
    items = raw.get("items", raw) if isinstance(raw, dict) else raw

    run_audit(items, filter_name=args.item)


if __name__ == "__main__":
    main()
