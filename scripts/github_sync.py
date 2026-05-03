"""GitHub Pages data sync: export merged JSON, heartbeat, git commit/push."""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid

import schedule

from constants import _PRICE_UNRELIABLE
from scripts.data_json_utils import (
    _atomic_write_json,
    _data_write_lock,
    _inventory_row_key,
)


def _next_github_actions_scrape_utc(after=None):
    """Next time matching `.github/workflows/scrape.yml` schedule: ``0 */4 * * *`` (UTC)."""
    if after is None:
        after = datetime.datetime.now(datetime.timezone.utc)
    elif after.tzinfo is None:
        after = after.replace(tzinfo=datetime.timezone.utc)
    else:
        after = after.astimezone(datetime.timezone.utc)
    slots = (0, 4, 8, 12, 16, 20)
    for d in range(0, 2):
        day = after.date() + datetime.timedelta(days=d)
        for h in slots:
            cand = datetime.datetime.combine(day, datetime.time(h, 0, 0, tzinfo=datetime.timezone.utc))
            if cand > after:
                return cand
    return after + datetime.timedelta(hours=4)


def export_data_to_json(results):
    """Exports scraped data to data.json and appends today's snapshot to scrape_history.

    This is the SINGLE write path for the dashboard data file.
    Preserves existing per-item fields (scrape_history, price_history, metadata)
    while overlaying fresh scraped prices.
    """
    import re

    try:
        os.makedirs("docs", exist_ok=True)
        data_path = "docs/data.json"
        now = datetime.datetime.now()
        now_str = (
            datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        )
        today_str = now.strftime("%Y-%m-%d")

        existing_by_key = {}
        if os.path.exists(data_path):
            try:
                with open(data_path, "r") as f:
                    raw = json.load(f)
                existing_items = raw if isinstance(raw, list) else raw.get("items", [])
                for ei in existing_items:
                    k = _inventory_row_key(ei)
                    if k:
                        existing_by_key[k] = ei
            except Exception:
                existing_by_key = {}

        merged = []
        for item in results:
            if not item.get("item_id") and item.get("name"):
                legacy = existing_by_key.get("name:" + item["name"])
                if legacy and legacy.get("item_id"):
                    item["item_id"] = legacy["item_id"]
                else:
                    item["item_id"] = str(uuid.uuid4())
            key = _inventory_row_key(item)
            existing = existing_by_key.get(key, {}) if key else {}
            if not existing and item.get("name"):
                existing = existing_by_key.get("name:" + item["name"], {})

            def _is_effectively_empty(value):
                if value is None:
                    return True
                if isinstance(value, (dict, list, tuple, set)):
                    return len(value) == 0
                if isinstance(value, str):
                    return value == ""
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return value == 0
                return False

            for keep_field in (
                "scrape_history",
                "price_history",
                "brand",
                "subcategory",
                "size",
                "tags",
                "target_confidence",
                "target_method",
                "target_data_points",
                "target_updated",
                "last_purchased",
                "local_image",
                "on_special",
                "was_price",
                "type",
                "all_stores",
                "coles",
                "last_layer_a_check",
            ):
                if keep_field in existing and (
                    keep_field not in item or _is_effectively_empty(item.get(keep_field))
                ):
                    item[keep_field] = existing[keep_field]

            sh = item.get("scrape_history", [])
            sh = [
                entry
                for entry in sh
                if not (
                    entry.get("date") == today_str
                    and isinstance(entry.get("price"), (int, float))
                    and entry["price"] >= _PRICE_UNRELIABLE
                )
            ]
            snapshot_price = item.get("eff_price", item.get("price", 0))
            snapshot_reliable = (
                isinstance(snapshot_price, (int, float))
                and 0 < snapshot_price < _PRICE_UNRELIABLE
                and not item.get("price_unavailable")
            )
            if snapshot_reliable:
                entry = {
                    "date": today_str,
                    "price": snapshot_price,
                    "is_special": item.get("on_special", False),
                    "was_price": item.get("was_price"),
                    "store": item.get("store"),
                }
                nc = item.get("name_check", "")
                if nc:
                    entry["matched_name"] = nc
                if sh and sh[-1].get("date") == today_str:
                    sh[-1] = entry
                else:
                    sh.append(entry)
            item["scrape_history"] = sh

            if not item.get("store") or item.get("store") == "none":
                recent_store = next(
                    (
                        entry.get("store")
                        for entry in reversed(item.get("scrape_history", []))
                        if entry.get("store") and entry["store"] != "none"
                    ),
                    None,
                )
                if recent_store:
                    item["store"] = recent_store

            today_snap = next(
                (
                    e
                    for e in reversed(item.get("scrape_history", []))
                    if e.get("date") == today_str and e.get("price") and e["price"] > 0
                ),
                None,
            )
            if today_snap:
                fresh = today_snap["price"]
                existing_price = item.get("price") or 0
                cat = item.get("type", "")
                sane_ceiling = 60 if cat in ("produce", "dairy", "bakery") else 300
                if existing_price > sane_ceiling or fresh < existing_price * 0.7:
                    logging.info(
                        f"Correcting stale price for {item['name']}: "
                        f"${existing_price:.2f} → ${fresh:.2f} (from today's scrape)"
                    )
                    item["price"] = fresh
                    item["eff_price"] = fresh

            cat = item.get("type", "")
            _PRICE_ERROR_THRESHOLD = 60 if cat in ("produce", "dairy", "bakery") else 1000
            for price_field in ("price", "eff_price", "was_price"):
                v = item.get(price_field)
                if v is not None and v > _PRICE_ERROR_THRESHOLD:
                    logging.warning(f"Clamping bad {price_field} for {item['name']}: ${v}")
                    item.pop(price_field, None)
                    item["price_unavailable"] = True

            raw_size = item.get("size", "")
            name_lower = item.get("name", "").lower()
            if raw_size:
                m_size = re.match(r"^(\d+\.?\d*)(l|kg|ml|g)$", raw_size.strip().lower())
                if m_size:
                    api_num = float(m_size.group(1))
                    unit = m_size.group(2)
                    m_name = re.search(r"(\d+\.?\d*)\s*" + unit + r"\b", name_lower)
                    if m_name:
                        name_num = float(m_name.group(1))
                        if abs(api_num - name_num) > 0.01 and abs(name_num) > 0:
                            corrected = f"{name_num}{unit.upper()}"
                            logging.warning(
                                f"Size mismatch for '{item['name']}': API={raw_size} name={corrected} — using name"
                            )
                            item["size"] = corrected

            merged.append(item)

        payload = {
            "last_updated": now_str,
            "items": merged,
        }
        with _data_write_lock:
            _atomic_write_json(data_path, payload)
        logging.info(f"Exported data.json successfully ({len(merged)} items, scrape_history updated).")
    except Exception as e:
        logging.error(f"Error exporting data.json: {e}")


def sync_to_github(next_scheduled=None):
    """Commits and pushes the docs/ folder and updated JSON data to GitHub.

    next_scheduled: optional datetime for the next scrape (pass from run_report on the main
    thread so heartbeat matches schedule library state; avoids stale NEXT_SCHEDULED_RUN).
    """
    import chef_os as _co

    lock_fd = _co._acquire_git_push_lock()
    if lock_fd is None:
        logging.warning("GitHub sync skipped: another sync is already in progress.")
        return

    def _run_git(args, check=False):
        return subprocess.run(args, capture_output=True, text=True, check=check)

    try:
        try:
            gen = subprocess.run(
                [sys.executable, "scripts/generate_runtime_env.py"],
                capture_output=True,
                text=True,
                check=False,
            )
            if gen.returncode != 0:
                logging.error(
                    "Runtime env generation failed before sync_to_github: "
                    f"{(gen.stderr or gen.stdout or '').strip()}"
                )
                return
            logging.info((gen.stdout or "Runtime env generated.").strip())
        except Exception as env_exc:
            logging.error(f"Runtime env generation exception before sync_to_github: {env_exc}")
            return

        logging.info("Syncing data to GitHub...")
        pull_r = _run_git(["git", "pull", "--rebase", "--autostash", "origin", "main"])
        if pull_r.returncode != 0:
            logging.error(
                f"git pull --rebase --autostash failed (exit {pull_r.returncode}): "
                f"{(pull_r.stderr or pull_r.stdout or '').strip()}"
            )
            return

        heartbeat_path = os.path.join("docs", "heartbeat.json")
        nr = next_scheduled
        if nr is None:
            try:
                nr = schedule.next_run()
            except Exception:
                nr = None
        if nr is None:
            nr = _co.NEXT_SCHEDULED_RUN
        if nr is None and os.environ.get("GITHUB_ACTIONS") == "true":
            nr = _next_github_actions_scrape_utc()
        if nr is not None:
            if nr.tzinfo is None:
                nr = nr.astimezone(datetime.timezone.utc)
            next_run_str = nr.isoformat().replace("+00:00", "Z")
        else:
            next_run_str = None

        _atomic_write_json(
            heartbeat_path,
            {
                "last_heartbeat": datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "next_run": next_run_str,
                "status": "active",
            },
        )

        data_path = os.path.join("docs", "data.json")
        prev_path = os.path.join("docs", "data.prev.json")
        if os.path.exists(data_path):
            try:
                for slot in (3, 2, 1):
                    src = os.path.join("docs", f"data.prev-{slot - 1}.json") if slot > 1 else prev_path
                    dst = os.path.join("docs", f"data.prev-{slot}.json")
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
                shutil.copy2(data_path, prev_path)
                logging.info("Rotated snapshot chain: data.json → data.prev.json (prev-1..3 shifted).")
            except Exception as snap_exc:
                logging.warning(f"data.prev snapshot rotation failed (non-fatal): {snap_exc}")

        try:
            with open(data_path, "r", encoding="utf-8") as fv:
                _dv = json.load(fv)
            _dv_items = _dv.get("items") if isinstance(_dv, dict) else _dv
            if not isinstance(_dv_items, list) or len(_dv_items) < 200:
                raise RuntimeError(
                    f"data.json shape check failed: items={type(_dv_items).__name__} "
                    f"len={len(_dv_items) if isinstance(_dv_items, list) else 'n/a'}"
                )
            logging.info(f"data.json pre-commit OK ({len(_dv_items)} items).")
        except (json.JSONDecodeError, OSError, RuntimeError) as exc:
            logging.error(f"Refusing to commit corrupt/short data.json: {exc}")
            raise

        add_r = _run_git(["git", "add", "docs/"])
        if add_r.returncode != 0:
            logging.error(f"git add docs/ failed (exit {add_r.returncode}): {add_r.stderr.strip()}")
            return

        diff_r = _run_git(["git", "diff", "--cached", "--quiet"])
        if diff_r.returncode == 0:
            logging.info("GitHub sync: nothing to commit under docs/ (working tree matches HEAD).")
            return

        subprocess.run(
            ["git", "commit", "-m", "Auto-update dashboard data [skip ci]"],
            check=True,
            capture_output=True,
            text=True,
        )

        from scripts.git_sync_helpers import push_main_with_pr_fallback

        push_main_with_pr_fallback()
    except subprocess.CalledProcessError as e:
        err = e.stderr or e.stdout or ""
        if isinstance(err, bytes):
            err = err.decode()
        logging.error(f"GitHub sync failed: {err}")
        raise
    except Exception as e:
        logging.error(f"Error during GitHub sync: {e}")
        raise
    finally:
        _co._release_git_push_lock(lock_fd)


__all__ = ["export_data_to_json", "sync_to_github", "_next_github_actions_scrape_utc"]
