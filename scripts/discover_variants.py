#!/usr/bin/env python3
"""
Propose draft inventory rows for multi-size variant tracking via Woolworths + Coles search.

Sequential curl_cffi only — never parallelizes network calls. Outputs JSON to stdout;
does not modify docs/data.json unless --write-snippet is used.

Phase 2 UI: open docs/discovery-review.html (or GitHub Pages …/discovery-review.html),
paste JSON or load a snippet file, select rows, copy export into data.json after review.

Usage:
  python scripts/discover_variants.py "Quilton Toilet Tissue" --compare-group tp_quilton \\
      --price-mode each --type household

  python scripts/discover_variants.py --from-item "Coca-Cola Classic 1.25L" --compare-group cola \\
      --price-mode litre --pack-litres 1.25

  python scripts/discover_variants.py --inventory-scan --compare-group seed --only-type household \\
      --max-queries 10 --price-mode each
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chef_os as co  # noqa: E402


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "product"


def woolworths_pdp_url(hit: dict) -> str | None:
    sc = hit.get("stockcode")
    slug = (hit.get("url_friendly") or "").strip() or _slugify(hit.get("name", ""))
    if sc and slug:
        return f"https://www.woolworths.com.au/shop/productdetails/{sc}/{slug}"
    return None


def coles_pdp_url(hit: dict) -> str | None:
    return co._coles_product_url_from_search_hit(hit)


def combined_label(hit: dict) -> str:
    return " ".join(filter(None, [hit.get("brand"), hit.get("name"), hit.get("size")])).strip()


def stable_bucket_key(label: str) -> tuple:
    sig = co._extract_size_signals(label)
    tup = (
        tuple(sorted(sig["packs"])),
        tuple(sorted(sig["volumes_ml"])),
        tuple(sorted(sig["weights_g"])),
    )
    if any(tup):
        return ("sig", tup)
    norm = re.sub(r"\s+", " ", label.lower().strip())
    return ("label", norm)


def excluded(label: str, patterns: list[re.Pattern]) -> bool:
    low = label.lower()
    return any(p.search(low) for p in patterns)


def score_vs_anchor(anchor: str, label: str) -> float:
    return co._token_overlap_score(anchor, label)


def warm_coles_session(session) -> str | None:
    try:
        w = session.get("https://www.coles.com.au/", timeout=20)
        if w.status_code == 200 and not co._coles_body_looks_blocked(w.text):
            co._apply_coles_build_id_from_html(w.text, "discover_variants_warmup")
    except Exception as e:
        logging.warning("Coles warm-up failed: %s", e)
    return co._cffi_get_coles_build_id(session)


def warm_woolworths_session(session) -> bool:
    try:
        resp = session.get("https://www.woolworths.com.au/", timeout=15)
        ok = resp.status_code == 200 and len(resp.text) >= getattr(co, "_WOOLIES_WARMUP_MIN_CHARS", 4000)
        if not ok:
            logging.warning("Woolworths warm-up weak: HTTP %s len=%s", resp.status_code, len(resp.text or ""))
        return ok
    except Exception as e:
        logging.warning("Woolworths warm-up failed: %s", e)
        return False


def fetch_search_results(
    anchor: str,
    *,
    max_ww: int,
    max_coles: int,
    sleep_sec: float,
) -> tuple[list[dict], list[dict], list[str]]:
    warnings: list[str] = []
    co._run_ua_profile = None  # noqa: SLF001 — one fresh profile for this CLI run

    # Coles first (same order as typical discovery pacing)
    c_session = co._create_cffi_session("coles")
    bid = warm_coles_session(c_session)
    if not bid:
        warnings.append("Coles buildId missing — no Coles search results.")
        coles_results = []
    else:
        raw, dym = co._cffi_search_coles(c_session, anchor, bid, max_results=max_coles)
        retry_q = co._coles_needs_spelling_retry(anchor, raw, dym)
        if retry_q:
            time.sleep(sleep_sec * random.uniform(0.8, 1.2))
            raw, _ = co._cffi_search_coles(c_session, retry_q, bid, max_results=max_coles)
        coles_results = co._rank_coles_search_results_for_inventory(anchor, raw)

    time.sleep(max(sleep_sec, co._COLES_DISCOVERY_SLEEP_SEC * 0.5))

    w_session = co._create_cffi_session("woolworths")
    if not warm_woolworths_session(w_session):
        warnings.append("Woolworths warm-up may be blocked — WW results may be empty.")
    ww_raw, _sug = co._cffi_search_woolworths(w_session, anchor, max_results=max_ww)
    # Sort WW by overlap with anchor (desc)
    ww_scored = [(score_vs_anchor(anchor, combined_label(h)), h) for h in ww_raw]
    ww_scored.sort(key=lambda x: -x[0])
    ww_results = [h for _, h in ww_scored]

    return ww_results, coles_results, warnings


def filter_hits(
    anchor: str,
    hits: list[dict],
    store: str,
    min_score: float,
    exclude_patterns: list[re.Pattern],
) -> list[tuple[float, dict, str]]:
    out: list[tuple[float, dict, str]] = []
    for h in hits:
        lab = combined_label(h)
        if not lab:
            continue
        if excluded(lab, exclude_patterns):
            continue
        if not co._size_signals_compatible(anchor, lab):
            continue
        sc = score_vs_anchor(anchor, lab)
        if sc < min_score:
            continue
        url = woolworths_pdp_url(h) if store == "woolworths" else coles_pdp_url(h)
        if not url:
            continue
        out.append((sc, h, url))
    return out


def merge_drafts(
    anchor: str,
    ww_scored: list[tuple[float, dict, str]],
    co_scored: list[tuple[float, dict, str]],
    *,
    compare_group: str,
    price_mode: str,
    pack_litres: float | None,
    type_: str | None,
    target: float | None,
) -> list[dict]:
    buckets: dict[tuple, dict] = {}

    def absorb(store: str, triples: list[tuple[float, dict, str]]) -> None:
        for sc, hit, url in triples:
            lab = combined_label(hit)
            key = stable_bucket_key(lab)
            slot = buckets.setdefault(key, {"ww": None, "co": None, "labels": []})
            slot["labels"].append(lab)
            prev = slot["ww"] if store == "woolworths" else slot["co"]
            if prev is None or sc > prev[0]:
                if store == "woolworths":
                    slot["ww"] = (sc, hit, url)
                else:
                    slot["co"] = (sc, hit, url)

    absorb("woolworths", ww_scored)
    absorb("coles", co_scored)

    drafts: list[dict] = []
    for key, slot in buckets.items():
        ww_t = slot.get("ww")
        co_t = slot.get("co")
        # Pick display name: highest combined score row
        candidates = [x for x in (ww_t, co_t) if x]
        if not candidates:
            continue
        best_hit = max(candidates, key=lambda x: x[0])[1]
        name = combined_label(best_hit)

        w_url = ww_t[2] if ww_t else ""
        c_url = co_t[2] if co_t else ""

        row: dict = {
            "name": name,
            "woolworths": w_url or "",
            "coles": c_url or "",
            "compare_group": compare_group,
            "price_mode": price_mode,
            "stock": "medium",
            "last_purchased": None,
            "price_history": [],
            "_discovery_meta": {
                "anchor_query": anchor,
                "bucket": str(key),
                "overlap_labels": slot.get("labels", [])[:8],
            },
        }
        if type_:
            row["type"] = type_
        if target is not None:
            row["target"] = target
        if price_mode == "litre" and pack_litres:
            row["pack_litres"] = pack_litres
        drafts.append(row)

    # Stable sort: name
    drafts.sort(key=lambda r: r["name"].lower())
    return drafts


def run_single_query(args, anchor: str, source_item: str | None = None) -> dict:
    ww, cc, warns = fetch_search_results(
        anchor,
        max_ww=args.max_ww_results,
        max_coles=args.max_coles_results,
        sleep_sec=args.sleep_sec,
    )
    excludes = [re.compile(p, re.I) for p in args.exclude_regex]

    ww_keep = filter_hits(anchor, ww, "woolworths", args.min_score, excludes)
    co_keep = filter_hits(anchor, cc, "coles", args.min_score, excludes)

    drafts = merge_drafts(
        anchor,
        ww_keep,
        co_keep,
        compare_group=args.compare_group,
        price_mode=args.price_mode,
        pack_litres=args.pack_litres,
        type_=args.item_type,
        target=args.target,
    )

    out_warns = list(warns)
    if not drafts:
        out_warns.append(f"No draft rows after filtering for query {anchor!r}.")
    if len(drafts) == 1:
        out_warns.append("Only one variant matched — add more specific anchor or lower --min-score.")

    return {
        "anchor_query": anchor,
        "source_inventory_name": source_item,
        "warnings": out_warns,
        "draft_items": drafts,
    }


def load_inventory(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return co._normalize_items_payload(raw)


def append_query_log(path: Path | None, anchor: str, source: str | None = None) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        src = f" src={source!r}" if source else ""
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\t{anchor}{src}\n")
    except OSError as e:
        logging.warning("query-log write failed: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover multi-size variants via WW + Coles search (draft JSON only).")
    parser.add_argument("query", nargs="?", help="Search phrase (brand + product line, sizes optional)")
    parser.add_argument("--from-item", metavar="NAME", help="Inventory display name — cleaned via _clean_search_term")
    parser.add_argument("--compare-group", required=True, help="compare_group value for all draft rows")
    parser.add_argument("--price-mode", choices=("each", "kg", "litre"), default="each")
    parser.add_argument("--pack-litres", type=float, default=None, help="For price_mode=litre (per-pack total litres)")
    parser.add_argument("--type", dest="item_type", default=None, help="Optional inventory type")
    parser.add_argument("--target", type=float, default=None)
    parser.add_argument("--min-score", type=float, default=co._COLES_DISCOVERY_MIN_SCORE, help="Min token overlap vs anchor")
    parser.add_argument("--exclude-regex", action="append", default=[], metavar="PATTERN", help="Repeatable; exclude matching labels")
    parser.add_argument("--max-ww-results", type=int, default=24)
    parser.add_argument("--max-coles-results", type=int, default=12)
    parser.add_argument("--sleep-sec", type=float, default=2.0, help="Pause between Coles and Woolworths searches")
    parser.add_argument(
        "--inventory-scan",
        action="store_true",
        help="Derive queries from docs/data.json (deduped cleaned names)",
    )
    parser.add_argument("--inventory-path", type=Path, default=ROOT / "docs" / "data.json")
    parser.add_argument("--only-type", default=None, help="Only inventory rows with this type")
    parser.add_argument("--max-queries", type=int, default=30, help="Cap inventory-scan iterations")
    parser.add_argument(
        "--include-existing-group",
        action="store_true",
        help="Inventory-scan: include items that already have compare_group set",
    )
    parser.add_argument(
        "--write-snippet",
        type=Path,
        default=None,
        help="Also write JSON output to this file (never touches data.json)",
    )
    parser.add_argument(
        "--query-log",
        type=Path,
        default=None,
        help="Append one ISO timestamp line per search anchor (inventory-scan or single query)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Less log noise from chef_os")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    if args.price_mode == "litre" and not args.pack_litres:
        parser.error("--pack-litres is strongly recommended for price_mode=litre")

    results_payload: list[dict] = []

    if args.inventory_scan:
        items = load_inventory(args.inventory_path)
        seen_q: set[str] = set()
        queued: list[tuple[str, str]] = []
        for it in items:
            if args.only_type and it.get("type") != args.only_type:
                continue
            if it.get("compare_group") and not args.include_existing_group:
                continue
            raw_name = it.get("name") or ""
            q = co._clean_search_term(raw_name) if raw_name else ""
            if len(q) < 3:
                continue
            if q in seen_q:
                continue
            seen_q.add(q)
            queued.append((q, raw_name))
            if len(queued) >= args.max_queries:
                break

        if not queued:
            print(json.dumps({"error": "inventory-scan: no qualifying rows"}, indent=2))
            return 1

        for i, (q, src_name) in enumerate(queued):
            append_query_log(args.query_log, q, src_name)
            block = run_single_query(args, q, source_item=src_name)
            results_payload.append(block)
            if i + 1 < len(queued):
                time.sleep(args.sleep_sec * random.uniform(1.0, 1.8))

        envelope = {
            "mode": "inventory-scan",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "queries_run": len(queued),
            "compare_group": args.compare_group,
            "results": results_payload,
            "merge_instructions": _merge_instructions(),
        }
        text = json.dumps(envelope, indent=2)
        print(text)
        if args.write_snippet:
            args.write_snippet.write_text(text, encoding="utf-8")
        return 0

    anchor = args.query
    if args.from_item:
        anchor = co._clean_search_term(args.from_item)
    if not anchor or len(anchor) < 2:
        parser.error("Provide a query string or --from-item")

    append_query_log(args.query_log, anchor, args.from_item)
    single = run_single_query(args, anchor, source_item=args.from_item)
    envelope = {
        "mode": "single",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compare_group": args.compare_group,
        **single,
        "merge_instructions": _merge_instructions(),
    }
    text = json.dumps(envelope, indent=2)
    print(text)
    if args.write_snippet:
        args.write_snippet.write_text(text, encoding="utf-8")
    return 0


def _merge_instructions() -> list[str]:
    return [
        "Review draft_items: remove wrong flavours/lines before merging.",
        "Paste chosen objects into docs/data.json items array (backup first).",
        "Run chef_os scrape or wait for scheduled run; then ./scripts/verify_wooliesbot_stack.sh (or e2e_validate.py --layer B/C).",
        "Never run discovery in parallel with curl_cffi scrapes on the same host when avoidable.",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
