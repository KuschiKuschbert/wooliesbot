"""Pure price / unit-price helpers (no I/O, no scraper globals)."""

import logging
import re

from constants import _PRICE_UNRELIABLE


def _parse_price_text(text):
    """Extract a float price from text like '$18.70' or '18.70'."""
    text = text.replace("$", "").replace(",", "").strip()
    match = re.search(r"(\d+\.?\d*)", text)
    return float(match.group(1)) if match else None


def _parse_unit_price_text(text):
    """Extract unit price and unit from text like '$11.00 / 1KG'."""
    if "$" not in text:
        return None, None
    val_part = text.split("/")[0].replace("$", "").strip()
    try:
        price = float(re.search(r"(\d+\.?\d*)", val_part).group(1))
    except Exception:
        return None, None
    text_lower = text.lower()
    if "kg" in text_lower:
        unit = "kg"
    elif "100g" in text_lower:
        unit = "100g"
    elif "litre" in text_lower or "1l" in text_lower:
        unit = "litre"
    else:
        unit = "each"
    return price, unit


def _effective_price(item, store_result):
    """Return the price to compare against the target.
    'kg'    → use scraped unit_price (per kg); normalise 100g→kg.
    'litre' → ALWAYS calculate shelf_price / pack_litres when possible.
    'each'  → shelf price."""
    mode = item.get("price_mode", "each")
    if mode == "kg":
        up = store_result.get("unit_price")
        scraped_unit = store_result.get("unit", "")
        if up is not None and scraped_unit in ("kg", "100g"):
            val = up * 10 if scraped_unit == "100g" else up
            if val > 100:
                logging.warning(f"Rejecting absurd $/kg {val:.2f} for {item.get('name')}")
                return _PRICE_UNRELIABLE
            return val
        return _PRICE_UNRELIABLE
    if mode == "litre":
        pack_l = item.get("pack_litres")
        if pack_l and pack_l > 0:
            return store_result["price"] / pack_l
        up = store_result.get("unit_price")
        unit = store_result.get("unit", "")
        if up is not None and unit == "litre":
            return up
        return store_result["price"]
    return store_result["price"]


def _normalize_unit_price(cup_price, cup_measure):
    """Normalize unit price to a common base for cross-store comparison.
    Returns (normalized_price, base_unit_label) e.g. (42.50, "/kg")."""
    if cup_price is None or cup_price <= 0:
        return (None, "")
    measure = (cup_measure or "").strip().upper().replace(" ", "")
    if measure in ("1KG", "KG"):
        return (cup_price, "/kg")
    if measure in ("100G", "G"):
        return (cup_price * 10, "/kg")
    if measure in ("1L", "L"):
        return (cup_price, "/L")
    if measure in ("100ML", "ML"):
        return (cup_price * 10, "/L")
    if measure in ("1EA", "EA", "EACH", ""):
        return (cup_price, "/ea")
    return (cup_price, f"/{measure.lower()}")


def _parse_woolworths_cup(cup_string):
    """Parse Woolworths CupString like '$42.50 / 1KG' into (price, measure)."""
    if not cup_string:
        return (None, "")
    m = re.match(r"\$?([\d.]+)\s*/\s*(.+)", cup_string.strip())
    if m:
        try:
            return (float(m.group(1)), m.group(2).strip())
        except ValueError:
            pass
    return (None, "")


def _parse_coles_unit(pricing):
    """Parse Coles pricing dict into (cup_price, cup_measure)."""
    unit_data = pricing.get("unit", {}) or {}
    up = unit_data.get("price")
    unit_type = (unit_data.get("ofMeasureUnits") or "").strip().lower()
    if up is not None and up > 0:
        return (float(up), unit_type)
    comp = pricing.get("comparable", "")
    if comp:
        m = re.match(r"\$?([\d.]+)\s*/\s*(.+)", comp.strip())
        if m:
            try:
                return (float(m.group(1)), m.group(2).strip())
            except ValueError:
                pass
    return (None, "")


def _enrich_with_unit_price(result):
    """Add norm_unit_price and norm_unit_label to a search result dict."""
    cup_price = result.get("cup_price")
    cup_measure = result.get("cup_measure", "")
    norm_price, norm_label = _normalize_unit_price(cup_price, cup_measure)
    result["norm_unit_price"] = norm_price
    result["norm_unit_label"] = norm_label
    return result


__all__ = [
    "_parse_price_text",
    "_parse_unit_price_text",
    "_effective_price",
    "_normalize_unit_price",
    "_parse_woolworths_cup",
    "_parse_coles_unit",
    "_enrich_with_unit_price",
]
