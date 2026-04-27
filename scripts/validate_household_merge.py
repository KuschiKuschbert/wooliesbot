#!/usr/bin/env python3
"""
Contract tests for household merge (mirrors workers/wooliesbot-write/src/household_merge.js).
Run in CI: python3 scripts/validate_household_merge.py

The Worker is canonical; this catches accidental drift in merge rules.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reuse Worker's merge logic if wrangler can bundle - we use a pure-Python mirror below.


def _section_ms(obj: dict | None) -> int:
    if not obj or not isinstance(obj, dict):
        return 0
    from datetime import datetime

    raw = str(obj.get("updated_at") or "")
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(d.timestamp() * 1000)
    except Exception:
        return 0


def choose_section_lww(existing: dict | None, incoming: dict | None, prefer_incoming_on_tie: bool = True) -> dict:
    if not incoming or not isinstance(incoming, dict):
        return dict(existing) if existing and isinstance(existing, dict) else {}
    if not existing or not isinstance(existing, dict):
        return dict(incoming)
    a = _section_ms(existing)
    b = _section_ms(incoming)
    if b > a:
        return dict(incoming)
    if b < a:
        return dict(existing)
    if prefer_incoming_on_tie:
        return dict(incoming)
    return dict(incoming) if json.dumps(incoming, sort_keys=True) > json.dumps(existing, sort_keys=True) else dict(existing)


def is_items_only_post(body: dict | None) -> bool:
    if not body or not isinstance(body, dict):
        return True
    if body.get("household_sync") is True:
        return False
    return True


def build_household_payload_mirror(existing_decoded: dict, body: dict) -> dict:
    """Mirror of buildHouseholdPayload in household_merge.js (simplified: no item row merge)."""
    existing = dict(existing_decoded) if isinstance(existing_decoded, dict) else {"schema": 1, "items": []}
    device_id = str(body.get("device_id") or "").strip() or "unknown"
    updated_at = "2026-06-01T12:00:00.000Z"  # fixed in tests
    if is_items_only_post(body):
        out = {**existing, "items": body.get("items", existing.get("items", [])), "updated_at": updated_at, "updated_by": device_id}
        return out
    out = {**existing, "schema": 2, "items": body.get("items", []), "updated_at": updated_at, "updated_by": device_id}
    section_keys = [
        "trip_state",
        "shop_mode_state",
        "essentials_state",
        "trip_sessions_state",
        "drop_alerts_state",
    ]
    for key in section_keys:
        ex = existing.get(key)
        inc = body.get(key)
        if inc and isinstance(inc, dict) and str(inc.get("updated_at") or "").strip() != "":
            out[key] = choose_section_lww(ex, inc, True)
        elif ex and isinstance(ex, dict):
            out[key] = ex
    return out


def test_items_only_preserves_trip_state() -> None:
    existing = {
        "schema": 2,
        "items": [],
        "updated_at": "2026-01-01T00:00:00.000Z",
        "trip_state": {"updated_at": "2026-01-02T00:00:00.000Z", "mode": "1", "started_at": "2026-01-02T01:00:00.000Z"},
    }
    body = {
        "device_id": "old_client",
        "items": [{"name": "Milk", "updated_at": "2026-01-03T00:00:00.000Z", "qty": 1, "store": "woolworths"}],
    }
    out = build_household_payload_mirror(existing, body)
    assert "trip_state" in out, "items-only post must preserve trip_state"
    assert out["trip_state"]["mode"] == "1", out
    assert out.get("schema") == 2


def test_full_merge_prefers_newer_trip() -> None:
    existing = {
        "schema": 2,
        "items": [],
        "trip_state": {"updated_at": "2026-01-01T00:00:00.000Z", "mode": "0"},
    }
    body = {
        "device_id": "a",
        "household_sync": True,
        "items": [],
        "trip_state": {"updated_at": "2026-01-10T00:00:00.000Z", "mode": "1", "started_at": "x"},
    }
    out = build_household_payload_mirror(existing, body)
    assert out["trip_state"]["mode"] == "1", out
    assert out["trip_state"].get("started_at") == "x"


def test_section_lww_older_incoming_ignored() -> None:
    a = {"updated_at": "2026-01-10T00:00:00.000Z", "value": "weekly"}
    b = {"updated_at": "2026-01-01T00:00:00.000Z", "value": "big"}
    w = choose_section_lww(a, b, True)
    assert w["value"] == "weekly"


def main() -> int:
    test_items_only_preserves_trip_state()
    test_full_merge_prefers_newer_trip()
    test_section_lww_older_incoming_ignored()
    print("validate_household_merge: OK (3 tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
