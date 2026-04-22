from __future__ import annotations

import datetime
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlparse


def extract_search_term(url: str) -> str:
    url = url.replace("searchTerm=#", "searchTerm=")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    term = qs.get("searchTerm", [""])[0]
    if not term and parsed.fragment:
        term = parsed.fragment
    return unquote(term).strip() if term else ""


def is_search_url(url: str) -> bool:
    return "search" in url.lower()


def is_pdp_url(url: str) -> bool:
    return "productdetails" in url.lower()


def url_type_for_store_url(store: str, url: str, extract_coles_product_id) -> str:
    url = (url or "").strip()
    if not url:
        return "none"
    if store == "woolworths":
        if is_pdp_url(url):
            return "pdp"
        if is_search_url(url):
            return "search"
        return "other"
    if store == "coles":
        if extract_coles_product_id(url):
            return "pdp"
        if is_search_url(url):
            return "search"
        return "other"
    return "other"


def build_url_metadata_records(layer_d_results, extract_coles_product_id):
    verified_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    out = []
    for r in layer_d_results:
        store = (r.get("store") or "").strip().lower()
        url = (r.get("url") or "").strip()
        if not store or not url:
            continue
        out.append(
            {
                "item": r.get("item"),
                "item_id": r.get("item_id"),
                "store": store,
                "url": url,
                "url_type": url_type_for_store_url(store, url, extract_coles_product_id),
                "url_verdict": (r.get("match") or "SKIP").lower(),
                "url_verified_at": verified_at,
                "verification_source": "e2e_validate_layer_d",
                "http_status": r.get("http_status"),
                "overlap": r.get("overlap"),
                "live_name": r.get("live_name"),
                "notes": r.get("notes"),
            }
        )
    return out


def best_search_term_for_item(item, store, live_name=None):
    if live_name:
        ln = str(live_name).strip()
        if ln:
            return ln
    hist = item.get("scrape_history") or []
    if isinstance(hist, list):
        for entry in reversed(hist):
            if not isinstance(entry, dict):
                continue
            if (entry.get("store") or "").strip().lower() != store:
                continue
            mn = str(entry.get("matched_name") or "").strip()
            if mn:
                return mn
    nc = str(item.get("name_check") or "").strip()
    if nc:
        return nc
    return str(item.get("name") or "").strip()


def build_store_search_url(store, term):
    q = (term or "").strip()
    if store == "coles":
        return (
            f"https://www.coles.com.au/search?q={quote_plus(q)}"
            if q
            else "https://www.coles.com.au/search"
        )
    return (
        f"https://www.woolworths.com.au/shop/search/products?searchTerm={quote(q, safe='')}"
        if q
        else "https://www.woolworths.com.au/shop/search/products"
    )


def repair_bad_link_records(records, items):
    by_id = {}
    by_name = {}
    for it in items:
        iid = str(it.get("item_id") or "").strip()
        name_key = str(it.get("name") or "").strip().lower()
        if iid and iid not in by_id:
            by_id[iid] = it
        if name_key and name_key not in by_name:
            by_name[name_key] = it

    repaired = []
    stats = {
        "eligible": 0,
        "repaired": 0,
        "already_search": 0,
        "not_found": 0,
        "unchanged": 0,
    }
    for rec in records:
        out = dict(rec)
        verdict = str(out.get("url_verdict") or "").lower()
        store = str(out.get("store") or "").lower()
        if verdict not in {"dead", "diff"} or store not in {"woolworths", "coles"}:
            repaired.append(out)
            continue

        stats["eligible"] += 1
        if (out.get("url_type") or "").lower() == "search":
            stats["already_search"] += 1
            repaired.append(out)
            continue

        item = None
        iid = str(out.get("item_id") or "").strip()
        if iid:
            item = by_id.get(iid)
        if item is None:
            item = by_name.get(str(out.get("item") or "").strip().lower())
        if item is None:
            stats["not_found"] += 1
            repaired.append(out)
            continue

        search_term = best_search_term_for_item(item, store, live_name=out.get("live_name"))
        fallback_url = build_store_search_url(store, search_term)
        if fallback_url == out.get("url"):
            stats["unchanged"] += 1
            repaired.append(out)
            continue

        out["original_url"] = out.get("url")
        out["original_url_verdict"] = out.get("url_verdict")
        out["url"] = fallback_url
        out["url_type"] = "search"
        out["url_verdict"] = "repaired_search_fallback"
        out["repair_reason"] = f"auto_repair_{verdict}"
        stats["repaired"] += 1
        repaired.append(out)
    return repaired, stats


def resolve_item_for_metadata_record(items, record):
    item_id = (record.get("item_id") or "").strip()
    name = (record.get("item") or "").strip().lower()
    if item_id:
        matches = [i for i in items if str(i.get("item_id") or "").strip() == item_id]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, "ambiguous_item_id"
    if not name:
        return None, "missing_item_key"
    by_name = [i for i in items if str(i.get("name") or "").strip().lower() == name]
    if len(by_name) == 1:
        return by_name[0], None
    if len(by_name) > 1:
        return None, "ambiguous_name"
    return None, "not_found"


def set_if_changed(obj, key, value):
    old = obj.get(key)
    if old == value:
        return False
    obj[key] = value
    return True


def apply_url_metadata_records(items, records):
    stats = {
        "records_total": len(records),
        "applied": 0,
        "changed_fields": 0,
        "skipped_not_found": 0,
        "skipped_ambiguous": 0,
        "skipped_invalid": 0,
    }
    for rec in records:
        store = (rec.get("store") or "").strip().lower()
        url = (rec.get("url") or "").strip()
        if store not in {"woolworths", "coles"} or not url:
            stats["skipped_invalid"] += 1
            continue
        item, err = resolve_item_for_metadata_record(items, rec)
        if item is None:
            if err in {"ambiguous_item_id", "ambiguous_name"}:
                stats["skipped_ambiguous"] += 1
            elif err in {"not_found", "missing_item_key"}:
                stats["skipped_not_found"] += 1
            else:
                stats["skipped_invalid"] += 1
            continue

        item_changed = False
        if set_if_changed(item, store, url):
            stats["changed_fields"] += 1
            item_changed = True

        all_stores = item.setdefault("all_stores", {})
        store_obj = all_stores.setdefault(store, {})
        for key in (
            "url",
            "url_type",
            "url_verdict",
            "url_verified_at",
            "verification_source",
            "http_status",
            "overlap",
            "live_name",
            "notes",
        ):
            if key in rec:
                if set_if_changed(store_obj, key, rec.get(key)):
                    stats["changed_fields"] += 1
                    item_changed = True
        if item_changed:
            stats["applied"] += 1
    return stats
