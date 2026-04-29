#!/usr/bin/env python3
"""GitHub-friendly one-shot scrape pipeline for WooliesBot."""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import chef_os as bot  # noqa: E402


def _run_validator(layer):
    validator = ROOT_DIR / "scripts" / "e2e_validate.py"
    cmd = [sys.executable, str(validator), "--layer", layer, "--strict-exit"]
    result = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Layer {layer} validation failed: {details[:500]}")


def _load_items(path):
    """Load items array from a data.json-shaped file. Returns [] on missing/invalid."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(raw, list):
        return raw
    return raw.get("items", []) if isinstance(raw, dict) else []


def _comparable_price(item):
    """Return a single number we can compare across runs, or None if unreliable.

    Prefers eff_price (normalised pack/unit) so a pack-size-only change is not
    misread as a price spike. Falls back to price. Skips price_unavailable rows.
    """
    if item.get("price_unavailable"):
        return None
    for key in ("eff_price", "price"):
        v = item.get(key)
        if isinstance(v, (int, float)) and 0 < v < 9999:
            return float(v)
    return None


def _run_bulk_diff_guard():
    """Halt the push if too many items moved by too much vs the previous green scrape.

    Catches whole-category parser bugs (e.g. WW BFF response shape change) that
    Layer A smoke can miss because the smoke set is small. Only fires when there
    are enough comparable items to be statistically meaningful.

    Thresholds (env-overridable):
      WOOLIESBOT_DIFF_PCT_THRESHOLD   default 15 (%)
      WOOLIESBOT_DIFF_FRAC_THRESHOLD  default 0.25 (25% of comparable items)
      WOOLIESBOT_DIFF_MIN_ITEMS       default 50 (skip guard below this many)
    """
    pct_threshold = float(os.environ.get("WOOLIESBOT_DIFF_PCT_THRESHOLD", "15"))
    frac_threshold = float(os.environ.get("WOOLIESBOT_DIFF_FRAC_THRESHOLD", "0.25"))
    min_items = int(os.environ.get("WOOLIESBOT_DIFF_MIN_ITEMS", "50"))

    docs = ROOT_DIR / "docs"
    current = _load_items(docs / "data.json")
    previous = _load_items(docs / "data.prev.json")

    if not previous:
        logging.info("Bulk diff guard: no data.prev.json (first run?) — skipping.")
        return

    prev_by_key = {}
    for it in previous:
        key = it.get("name") or it.get("item_id")
        if key:
            prev_by_key[key] = it

    moves_big = []
    comparable = 0
    for it in current:
        key = it.get("name") or it.get("item_id")
        if not key:
            continue
        prev = prev_by_key.get(key)
        if not prev:
            continue
        new_p = _comparable_price(it)
        old_p = _comparable_price(prev)
        if new_p is None or old_p is None or old_p == 0:
            continue
        comparable += 1
        delta_pct = abs(new_p - old_p) / old_p * 100.0
        if delta_pct > pct_threshold:
            moves_big.append((key, old_p, new_p, delta_pct))

    if comparable < min_items:
        logging.info(
            f"Bulk diff guard: only {comparable} comparable items (< {min_items}) — skipping."
        )
        return

    fraction = len(moves_big) / comparable
    logging.info(
        f"Bulk diff guard: {len(moves_big)}/{comparable} items moved >{pct_threshold:.0f}% "
        f"({fraction:.1%}, threshold {frac_threshold:.0%})"
    )

    if fraction > frac_threshold:
        moves_big.sort(key=lambda m: m[3], reverse=True)
        sample = "\n".join(
            f"  - {name}: ${old:.2f} -> ${new:.2f} ({delta:+.1f}%)"
            for name, old, new, delta in moves_big[:5]
        )
        raise RuntimeError(
            f"Bulk diff guard tripped: {fraction:.1%} of {comparable} items moved >"
            f"{pct_threshold:.0f}% (threshold {frac_threshold:.0%}). "
            f"Likely a parser regression — push aborted to protect main.\n"
            f"Top moves:\n{sample}"
        )


def _run_validator_smoke_a():
    """Pre-push Layer A smoke gate: 15 curated items, strict exit.

    Runs between Layer C and sync_to_github so a live-price mismatch blocks
    the push before bad data lands on main. Uses --smoke sample (compare_group
    + on_special + WW PDP items) for broad coverage in ~2-3 minutes.
    Writes last_layer_a_check timestamps back to data.json via --persist-checks
    so post-push Layer A rotation skips recently verified items.
    """
    validator = ROOT_DIR / "scripts" / "e2e_validate.py"
    cmd = [
        sys.executable, str(validator),
        "--layer", "A", "--smoke", "--persist-checks", "--strict-exit",
    ]
    result = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Layer A smoke gate failed (pre-push): {details[:800]}")


def _notify_failure(exc):
    bot.send_telegram(f"🚨 *Pipeline Error*:\n{bot._escape_md(str(exc))}")


def _notify_success(raw_results, weekly=False):
    if weekly:
        summary = bot._build_weekly_shopping_reminder(raw_results)
    else:
        summary = bot._build_run_summary(raw_results)
    bot.send_telegram(summary)


def run_pipeline(
    discover_coles_batch_size=20,
    link_self_heal=True,
    sync=True,
    layer_a_smoke=True,
    bulk_diff_guard=True,
):
    raw_results = bot.check_prices()
    bot.export_data_to_json(raw_results)

    try:
        bot._recalculate_smart_targets()
        logging.info("Smart target recalculation complete.")
    except Exception as exc:
        logging.warning(f"Smart target recalculation skipped: {exc}")

    if discover_coles_batch_size > 0:
        try:
            bot._discover_coles_prices(batch_size=discover_coles_batch_size)
        except Exception as exc:
            logging.warning(f"Coles discovery skipped: {exc}")

    if link_self_heal:
        try:
            bot._run_local_link_self_heal(report_basename="e2e_validate_links_ci.json")
        except Exception as exc:
            logging.warning(f"Local link self-heal skipped: {exc}")

    _run_validator("B")
    _run_validator("C")

    if layer_a_smoke:
        _run_validator_smoke_a()

    if bulk_diff_guard:
        _run_bulk_diff_guard()

    if sync:
        bot.sync_to_github(next_scheduled=None)

    return raw_results


def main():
    parser = argparse.ArgumentParser(description="Run one-shot WooliesBot scrape pipeline.")
    parser.add_argument(
        "--discover-coles-batch-size",
        type=int,
        default=20,
        help="How many items to include in Coles discovery step (0 disables).",
    )
    parser.add_argument(
        "--skip-link-self-heal",
        action="store_true",
        help="Skip Layer D self-heal apply step.",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip sync/push step (useful for dry test runs).",
    )
    parser.add_argument(
        "--skip-layer-a-smoke",
        action="store_true",
        help="Skip the pre-push Layer A smoke gate (15-item live-price check). Use only for local debugging.",
    )
    parser.add_argument(
        "--skip-bulk-diff-guard",
        action="store_true",
        help=(
            "Skip the bulk-diff guard (halts push when too many prices moved too much). "
            "Use only for intentional bulk price-data updates."
        ),
    )
    parser.add_argument(
        "--notify",
        choices=("off", "success", "failure", "always"),
        default="off",
        help="Telegram output mode for this run.",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Append weekly note to success notification.",
    )
    args = parser.parse_args()

    lock_fd = bot._acquire_scrape_lock()
    if lock_fd is None:
        raise RuntimeError("Scrape skipped: another run_report is already in progress (lock held).")

    try:
        results = run_pipeline(
            discover_coles_batch_size=max(0, args.discover_coles_batch_size),
            link_self_heal=not args.skip_link_self_heal,
            sync=not args.skip_sync,
            layer_a_smoke=not args.skip_layer_a_smoke,
            bulk_diff_guard=not args.skip_bulk_diff_guard,
        )
        if args.notify in ("success", "always"):
            _notify_success(results, weekly=args.weekly)
    except Exception as exc:
        logging.error(f"Pipeline failed: {exc}")
        if args.notify in ("failure", "always"):
            _notify_failure(exc)
        raise
    finally:
        bot._release_scrape_lock(lock_fd)


if __name__ == "__main__":
    main()
