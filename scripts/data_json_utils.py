"""Shared helpers for docs/data.json reads/writes (single lock, atomic JSON)."""

from __future__ import annotations

import json
import os
import tempfile
import threading

_data_write_lock = threading.Lock()


def _normalize_items_payload(raw):
    """Return list of item dicts whether data.json is wrapped {items:[]} or a legacy bare array."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        items = raw.get("items")
        if isinstance(items, list):
            return items
    return []


def _inventory_row_key(item):
    """Stable dict key for merging inventory with data.json (prefer item_id)."""
    if not isinstance(item, dict):
        return ""
    iid = item.get("item_id")
    if iid:
        return str(iid)
    name = item.get("name")
    return "name:" + name if name else ""


def _atomic_write_json(path, payload, *, indent=2):
    """Write JSON atomically (temp + fsync + os.replace) — never leaves a partial file."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "_atomic_write_json",
    "_data_write_lock",
    "_inventory_row_key",
    "_normalize_items_payload",
]
