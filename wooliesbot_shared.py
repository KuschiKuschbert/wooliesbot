"""Shared pure helpers for scraper/validator matching and URL parsing."""

from __future__ import annotations

import re
from typing import Mapping

_BASE_STOPWORDS = {
    "woolworths",
    "coles",
    "soft",
    "drink",
    "drinks",
    "product",
    "pack",
    "pk",
    "multipack",
    "bottle",
    "bottles",
    "can",
    "cans",
    "tub",
    "tray",
    "bag",
    "bags",
    "punnet",
    "punnets",
    "approx",
    "prepacked",
    "each",
}


def extract_coles_product_id(url: str | None) -> str | None:
    """Extract trailing Coles PDP numeric product id."""
    m = re.search(r"-(\d{4,})$", (url or "").rstrip("/"))
    return m.group(1) if m else None


def extract_size_signals(text: str | None) -> dict[str, set[int]]:
    """Extract comparable pack/volume/weight markers from free text."""
    if not text:
        return {"packs": set(), "volumes_ml": set(), "weights_g": set()}
    low = text.lower()
    packs = {int(m.group(1)) for m in re.finditer(r"\b(\d+)\s*(?:pk|pack)\b", low)}
    volumes_ml: set[int] = set()
    weights_g: set[int] = set()

    for m in re.finditer(r"\b(\d+)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(ml|l|g|kg)\b", low):
        count = int(m.group(1))
        qty = float(m.group(2))
        unit = m.group(3)
        packs.add(count)
        if unit == "ml":
            volumes_ml.add(int(round(qty)))
            volumes_ml.add(int(round(count * qty)))
        elif unit == "l":
            volumes_ml.add(int(round(qty * 1000)))
            volumes_ml.add(int(round(count * qty * 1000)))
        elif unit == "g":
            weights_g.add(int(round(qty)))
            weights_g.add(int(round(count * qty)))
        elif unit == "kg":
            weights_g.add(int(round(qty * 1000)))
            weights_g.add(int(round(count * qty * 1000)))

    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(ml|l)\b", low):
        qty = float(m.group(1))
        volumes_ml.add(int(round(qty if m.group(2) == "ml" else qty * 1000)))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(g|kg)\b", low):
        qty = float(m.group(1))
        weights_g.add(int(round(qty if m.group(2) == "g" else qty * 1000)))

    return {"packs": packs, "volumes_ml": volumes_ml, "weights_g": weights_g}


def size_signals_compatible(inventory_name: str | None, scraped_name: str | None) -> bool:
    """Return False if both labels carry conflicting size markers."""
    a = extract_size_signals(inventory_name)
    b = extract_size_signals(scraped_name)
    for key in ("packs", "volumes_ml", "weights_g"):
        if a[key] and b[key] and not (a[key] & b[key]):
            return False
    return True


def token_overlap_score(
    inv_name: str | None,
    scraped_name: str | None,
    *,
    abbreviations: Mapping[str, str] | None = None,
    normalize_brand_aliases: bool = False,
    plural_stem: bool = False,
) -> float:
    """Jaccard-like token overlap with optional normalization knobs."""
    if not inv_name or not scraped_name:
        return 0.0

    def _stem(token: str) -> str:
        if plural_stem and len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            return token[:-1]
        return token

    def _norm(text: str) -> set[str]:
        expanded = text
        if abbreviations:
            for abbr, full in abbreviations.items():
                if full:
                    expanded = re.sub(
                        rf"\b{re.escape(abbr)}\b", full, expanded, flags=re.IGNORECASE
                    )
        if normalize_brand_aliases:
            expanded = re.sub(r"\bcoke\b", "coca cola", expanded, flags=re.IGNORECASE)
            expanded = re.sub(r"\bcoca-?cola\b", "coca cola", expanded, flags=re.IGNORECASE)
            expanded = re.sub(r"\bno sugar\b", "zero sugar", expanded, flags=re.IGNORECASE)
            expanded = re.sub(r"\bt\\/tiss(?:ue)?\b", "toilet tissue", expanded, flags=re.IGNORECASE)
        return {
            _stem(t)
            for t in re.findall(r"[a-z0-9]+", expanded.lower())
            if t not in _BASE_STOPWORDS
        }

    ta = _norm(inv_name)
    tb = _norm(scraped_name)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
