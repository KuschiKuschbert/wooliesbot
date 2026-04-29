#!/usr/bin/env python3
"""GitHub-friendly one-shot scrape pipeline for WooliesBot."""

import argparse
import logging
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
    summary = bot._build_run_summary(raw_results)
    if weekly:
        summary += (
            "\n\nWeekly checkpoint completed.\n"
            "Data and dashboard are in sync."
        )
    bot.send_telegram(summary)


def run_pipeline(discover_coles_batch_size=20, link_self_heal=True, sync=True, layer_a_smoke=True):
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
