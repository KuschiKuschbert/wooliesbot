#!/usr/bin/env python3
"""
Weekly variant-discovery report for all compare_group taxonomies.

Runs sequentially (never parallel) — one WW+Coles search pair per group.
Does NOT modify docs/data.json. Writes docs/discovery_report.json with
new candidate rows that are not already tracked, for human review.

Usage:
  python scripts/discover_all_groups.py
  python scripts/discover_all_groups.py --max-per-store 4 --min-score 0.50

Designed to be called from .github/workflows/variant-discovery.yml.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Reuse discovery helpers from discover_variants.py
import discover_variants as dv  # noqa: E402
import chef_os as co            # noqa: E402

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stderr,
)

# ── Config: seed query + price_mode per known group ───────────────────────────
# For groups not listed here, the script falls back to the shortest existing
# member name with size signals stripped.
GROUP_CONFIG: dict[str, dict] = {
    "cola":             {"query": "cola soft drink 1.25L",   "price_mode": "litre",  "pack_litres": 1.25},
    "tp_quilton":       {"query": "Quilton toilet paper",     "price_mode": "each"},
    "butter_block_250g":{"query": "butter block 250g",        "price_mode": "each"},
    "cheese_tasty_block":{"query":"tasty cheese block 500g",  "price_mode": "each"},
    "sourdough_loaf":   {"query": "sourdough bread loaf",     "price_mode": "each"},
    "omo_wonder_wash":  {"query": "Omo laundry liquid",        "price_mode": "each"},
    "peanut_butter":    {"query": "peanut butter 380g",        "price_mode": "each"},
    "milk_full_cream":  {"query": "full cream milk 2L",        "price_mode": "litre", "pack_litres": 2.0},
    "bread_white":      {"query": "white sandwich bread 700g", "price_mode": "each"},
    "eggs_free_range":  {"query": "free range eggs 12 pack",  "price_mode": "each"},
    "pasta_dry":        {"query": "pasta spaghetti 500g",      "price_mode": "kg"},
    "mince_beef":       {"query": "lean beef mince 500g",      "price_mode": "kg"},
    "beef_strips":      {"query": "beef stir fry strips",      "price_mode": "kg"},
}

_SIZE_RE = re.compile(
    r"\b\d+\s*(?:g|kg|ml|l|litre|litres|pk|pack|x)\b",
    re.I,
)


def load_data(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return co._normalize_items_payload(raw)


def derive_config(group: str, members: list[dict]) -> dict:
    """Fallback config for groups not in GROUP_CONFIG."""
    # Pick shortest name as most generic
    names = sorted([m.get("name", "") for m in members], key=len)
    base = _SIZE_RE.sub("", names[0]).strip() if names else group.replace("_", " ")
    modes = [m.get("price_mode", "each") for m in members]
    mode = max(set(modes), key=modes.count)
    return {"query": base, "price_mode": mode}


def known_names(data: list[dict]) -> set[str]:
    return {(item.get("name") or "").lower().strip() for item in data}


def known_urls(data: list[dict]) -> set[str]:
    urls: set[str] = set()
    for item in data:
        for key in ("woolworths", "coles"):
            u = (item.get(key) or "").strip()
            if u:
                urls.add(u)
    return urls


def is_already_tracked(candidate: dict, names: set[str], urls: set[str]) -> bool:
    if (candidate.get("name") or "").lower().strip() in names:
        return True
    for key in ("woolworths", "coles"):
        u = (candidate.get(key) or "").strip()
        if u and u in urls:
            return True
    return False


def run_group(
    group: str,
    cfg: dict,
    *,
    max_per_store: int,
    min_score: float,
    sleep_sec: float,
) -> list[dict]:
    query = cfg["query"]
    price_mode = cfg.get("price_mode", "each")
    pack_litres = cfg.get("pack_litres")

    logging.info("[%s] query=%r price_mode=%s", group, query, price_mode)

    try:
        ww, cc, warns = dv.fetch_search_results(
            query,
            max_ww=max_per_store,
            max_coles=max_per_store,
            sleep_sec=sleep_sec,
        )
    except Exception as exc:
        logging.error("[%s] fetch failed: %s", group, exc)
        return []

    if warns:
        for w in warns:
            logging.warning("[%s] %s", group, w)

    excludes: list[re.Pattern] = []

    ww_keep = dv.filter_hits(query, ww, "woolworths", min_score, excludes)
    co_keep = dv.filter_hits(query, cc, "coles", min_score, excludes)

    drafts = dv.merge_drafts(
        query,
        ww_keep,
        co_keep,
        compare_group=group,
        price_mode=price_mode,
        pack_litres=pack_litres,
        type_=None,
        target=None,
    )

    return drafts


def iso_week_id(dt: datetime) -> str:
    """Return a stable weekly ID, e.g. '2026-W18'."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly variant discovery report for all compare_groups")
    parser.add_argument("--max-per-store", type=int, default=5, help="Max search results per store per group")
    parser.add_argument("--min-score", type=float, default=0.45, help="Minimum token overlap score to include a candidate")
    parser.add_argument("--sleep-sec", type=float, default=2.5, help="Sleep between requests")
    parser.add_argument("--data", default=str(ROOT / "docs" / "data.json"), help="Path to data.json")
    parser.add_argument("--out", default=str(ROOT / "docs" / "discovery_report.json"), help="Output path")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    data_path = Path(args.data)
    data = load_data(data_path)

    # Build lookup sets for dedup
    t_names = known_names(data)
    t_urls = known_urls(data)

    # Collect all groups from data
    all_groups: dict[str, list[dict]] = {}
    for item in data:
        g = item.get("compare_group")
        if g:
            all_groups.setdefault(g, []).append(item)

    if not all_groups:
        logging.error("No compare_groups found in data.json — nothing to discover.")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    report: dict = {
        "generated_at": now.isoformat(),
        "report_id": iso_week_id(now),
        "total_new_candidates": 0,
        "groups": {},
    }

    for group in sorted(all_groups):
        cfg = GROUP_CONFIG.get(group) or derive_config(group, all_groups[group])
        candidates = run_group(
            group, cfg,
            max_per_store=args.max_per_store,
            min_score=args.min_score,
            sleep_sec=args.sleep_sec,
        )

        new = [c for c in candidates if not is_already_tracked(c, t_names, t_urls)]

        report["groups"][group] = {
            "checked_at": now.isoformat(),
            "new_candidates": new,
        }
        report["total_new_candidates"] += len(new)
        logging.info("[%s] %d new candidate(s) after dedup", group, len(new))

        # Polite sleep between groups
        time.sleep(args.sleep_sec)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logging.info("Report written to %s — total_new_candidates=%d", out_path, report["total_new_candidates"])


if __name__ == "__main__":
    main()
