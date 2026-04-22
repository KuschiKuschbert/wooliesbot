from __future__ import annotations

import re


def tokens(name):
    n = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return [t for t in n.split() if len(t) > 1]


def match_score(receipt_name, inv_name):
    rt = set(tokens(receipt_name))
    it = set(tokens(inv_name))
    if not rt or not it:
        return 0.0
    overlap = len(rt & it) / len(rt | it)
    receipt_low = (receipt_name or "").lower()
    inv_low = (inv_name or "").lower()
    exact_sub = 0.15 if (receipt_low in inv_low or inv_low in receipt_low) else 0.0
    return overlap + exact_sub


def find_best_inv_match(receipt_name, inventory, threshold=0.45):
    best_item = None
    best_score = 0.0
    for item in inventory:
        s = match_score(receipt_name, item.get("name", ""))
        if s > best_score:
            best_score = s
            best_item = item
    if best_item is None or best_score < threshold:
        return None, best_score
    return best_item, best_score
