#!/usr/bin/env python3
"""Lint docs/data.json compare_group taxonomy.

Checks:
  1. Near-duplicate group names (e.g. 'tp_quilton' vs 'tp_quilted') —
     hard fail in CI; likely copy-paste typos that fragment comparisons.
  2. Mixed price_mode within a group — hard fail; breaks apples-to-apples ranking.
  3. Singleton groups (only one member) — warning only; may be fine, but worth review.

Usage:
  python3 scripts/check_compare_groups.py          # default data.json
  python3 scripts/check_compare_groups.py --data docs/data.json
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO_ROOT / "docs" / "data.json"

# Levenshtein distance threshold for near-dup detection.
# Ratio = distance / max(len(a), len(b)); values below this are flagged.
NEAR_DUP_RATIO = 0.30


def levenshtein(a: str, b: str) -> int:
    """Standard DP Levenshtein distance. Pure Python, no dependencies."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(
                prev[j] + 1,       # deletion
                curr[j - 1] + 1,   # insertion
                prev[j - 1] + (ca != cb),  # substitution
            ))
        prev = curr
    return prev[-1]


def near_dup_pairs(names: list[str]) -> list[tuple[str, str, float]]:
    """Return (a, b, ratio) for every pair that is suspiciously similar."""
    pairs = []
    names = sorted(set(names))
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            dist = levenshtein(a, b)
            ratio = dist / max(len(a), len(b)) if max(len(a), len(b)) > 0 else 0.0
            if ratio < NEAR_DUP_RATIO:
                pairs.append((a, b, ratio))
    return pairs


def main():
    parser = argparse.ArgumentParser(description="Lint compare_group taxonomy in data.json")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="Path to data.json")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"SKIP: {data_path} not found (first-ever run?)")
        sys.exit(0)

    raw = json.loads(data_path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("items", [])

    group_members: dict[str, list[str]] = defaultdict(list)
    group_modes: dict[str, set[str]] = defaultdict(set)

    for item in items:
        if not isinstance(item, dict):
            continue
        g = str(item.get("compare_group") or "").strip()
        if not g:
            continue
        name = str(item.get("name") or "?")
        mode = str(item.get("price_mode") or "each").strip() or "each"
        group_members[g].append(name)
        group_modes[g].add(mode)

    all_groups = sorted(group_members.keys())
    errors = []
    warnings = []

    # 1. Near-duplicate group names
    dup_pairs = near_dup_pairs(all_groups)
    for a, b, ratio in dup_pairs:
        errors.append(
            f"  NEAR-DUP: '{a}' and '{b}' are suspiciously similar "
            f"(Levenshtein ratio={ratio:.2f} < {NEAR_DUP_RATIO}). "
            f"Likely a typo — merge into one compare_group."
        )

    # 2. Mixed price_mode within a group
    for g, modes in group_modes.items():
        if len(modes) > 1:
            members_preview = group_members[g][:4]
            errors.append(
                f"  MIXED-MODE: compare_group='{g}' has mixed price_mode values {sorted(modes)}. "
                f"All members must share one mode. First members: {members_preview}"
            )

    # 3. Singleton groups (warn only)
    for g, members in group_members.items():
        if len(members) == 1:
            warnings.append(
                f"  SINGLETON: compare_group='{g}' has only 1 member ({members[0]}). "
                f"Single-member groups cannot be compared — remove the group or add a second item."
            )

    # Report
    print(f"\ncompare_group taxonomy check")
    print(f"  data.json: {data_path}")
    print(f"  Groups found: {len(all_groups)} → {all_groups}")
    print(f"  Items with a group: {sum(len(v) for v in group_members.values())}")

    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for w in warnings:
            print(w)

    if errors:
        print(f"\nERRORS ({len(errors)}) — FIX BEFORE MERGING:")
        for e in errors:
            print(e)
        sys.exit(1)

    print("\nOK: no compare_group taxonomy issues found.")


if __name__ == "__main__":
    main()
