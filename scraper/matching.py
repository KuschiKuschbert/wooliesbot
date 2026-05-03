"""Product name overlap and size-signal checks for scrape/search matching."""

import logging

from wooliesbot_shared import (
    extract_size_signals as _shared_extract_size_signals,
    size_signals_compatible as _shared_size_signals_compatible,
    token_overlap_score as _shared_token_overlap_score,
)

from constants import _RECEIPT_ABBREVIATIONS


def _token_overlap_score(inv_name, scraped_name):
    """Jaccard-like overlap on alphanumeric tokens (0..1).
    Expands receipt abbreviations before comparing so 'Ww'='Woolworths' etc."""
    return _shared_token_overlap_score(
        inv_name,
        scraped_name,
        abbreviations=_RECEIPT_ABBREVIATIONS,
        normalize_brand_aliases=True,
    )


def _extract_size_signals(text):
    """Extract comparable size signals from a label.
    Used to reject obvious mismatches like 600ml vs 10x375ml."""
    return _shared_extract_size_signals(text)


def _size_signals_compatible(inventory_name, scraped_name):
    """Return False when inventory and scraped labels clearly disagree on size."""
    return _shared_size_signals_compatible(inventory_name, scraped_name)


def _finalize_cffi_product_dict(data, inventory_name, store_key=None):
    """Build stored product dict; reject results with very low name overlap."""
    if inventory_name and data.get("name_check"):
        ov = _token_overlap_score(inventory_name, data["name_check"])
        min_overlap = 0.12 if store_key == "coles" else 0.15
        if not _size_signals_compatible(inventory_name, data["name_check"]):
            logging.warning(
                f"Rejecting size mismatch ({store_key}): inventory={inventory_name!r} vs page={data.get('name_check')!r} price=${data.get('price')}"
            )
            return None
        if ov < min_overlap:
            logging.warning(
                f"Rejecting low overlap ({ov:.2f}): inventory={inventory_name!r} vs page={data.get('name_check')!r} price=${data.get('price')}"
            )
            return None
    return {
        "price": data["price"],
        "unit_price": data.get("unit_price"),
        "unit": data.get("unit", "each"),
        "image_url": data.get("image_url", ""),
        "was_price": data.get("was_price"),
        "on_special": bool(data.get("is_special") or data.get("was_price")),
        "name_check": data.get("name_check", ""),
    }
