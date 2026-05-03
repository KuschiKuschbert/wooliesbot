"""Persisted scraper health metrics (cffi success, HTTP errors)."""

import json
import logging
import os

from scraper.config import (
    _ADAPTIVE_ENABLED,
    _BASE_CHROME_THRESHOLD,
    _MAX_METRICS_RUNS,
    _METRICS_PATH,
)


def _read_metrics_runs():
    try:
        if os.path.exists(_METRICS_PATH):
            with open(_METRICS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data[-_MAX_METRICS_RUNS:]
    except Exception as e:
        logging.debug(f"metrics read: {e}")
    return []


def _append_metrics_run(entry):
    try:
        os.makedirs(os.path.dirname(_METRICS_PATH), exist_ok=True)
        runs = _read_metrics_runs()
        runs.append(entry)
        runs = runs[-_MAX_METRICS_RUNS:]
        with open(_METRICS_PATH, "w", encoding="utf-8") as f:
            json.dump(runs, f, indent=2)
    except Exception as e:
        logging.warning(f"metrics write failed: {e}")


def _get_chrome_fallback_threshold():
    t = _BASE_CHROME_THRESHOLD
    if os.environ.get("WOOLIESBOT_CHROME_FALLBACK_THRESHOLD"):
        return min(0.95, max(0.35, t))
    if not _ADAPTIVE_ENABLED:
        return min(0.95, max(0.35, t))
    recent = [r for r in _read_metrics_runs()[-5:] if r.get("cffi_success_rate") is not None]
    if len(recent) >= 3 and all(r.get("cffi_success_rate", 0) >= 0.95 for r in recent[-3:]):
        t = min(0.88, t + 0.03)
    if recent:
        latest = recent[-1].get("cffi_success_rate", 1)
        if latest < 0.55:
            t = max(0.4, t - 0.15)
        elif latest < 0.75:
            t = max(0.45, t - 0.08)
    return min(0.95, max(0.35, t))
