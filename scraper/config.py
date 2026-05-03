"""Env-derived scraper tuning (shared by scraper/* and chef_os orchestration)."""

import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_int(key, default):
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _env_float(key, default):
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


_BASE_CHROME_THRESHOLD = min(0.95, max(0.35, _env_float("WOOLIESBOT_CHROME_FALLBACK_THRESHOLD", 0.6)))
_BASE_HTTP_RETRIES = max(1, _env_int("WOOLIESBOT_CFFI_HTTP_RETRIES", 4))
_ADAPTIVE_ENABLED = os.environ.get("WOOLIESBOT_ADAPTIVE", "1").strip().lower() not in ("0", "false", "no")
_METRICS_PATH = os.path.join(_REPO_ROOT, "logs", "scraper_metrics.json")
_MAX_METRICS_RUNS = max(5, _env_int("WOOLIESBOT_METRICS_HISTORY", 30))
_COLES_CFFI_WORKERS_CAP = max(1, _env_int("WOOLIESBOT_CFFI_COLES_WORKERS", 2))
_COLES_SEQUENTIAL = os.environ.get("WOOLIESBOT_COLES_SEQUENTIAL", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
_COLES_CHALLENGE_BACKOFF_SEC = min(180, max(15, _env_int("WOOLIESBOT_COLES_CHALLENGE_BACKOFF_SEC", 45)))
_COLES_DISCOVERY_SLEEP_SEC = max(0.5, _env_float("WOOLIESBOT_COLES_DISCOVERY_SLEEP_SEC", 2.0))
_COLES_DISCOVERY_MIN_SCORE = max(0.0, min(0.5, _env_float("WOOLIESBOT_COLES_DISCOVERY_MIN_SCORE", 0.12)))
_WOOLIES_WARMUP_MIN_CHARS = max(2000, _env_int("WOOLIESBOT_WOOLIES_WARMUP_MIN_CHARS", 3500))
_COLES_WARMUP_MIN_CHARS = max(400, _env_int("WOOLIESBOT_COLES_WARMUP_MIN_CHARS", 1800))
_PDP_MIN_HTML_CHARS = max(2000, _env_int("WOOLIESBOT_PDP_MIN_HTML_CHARS", 4500))
_REQ_JITTER_MIN_SEC = max(0.0, _env_float("WOOLIESBOT_REQUEST_JITTER_MIN_SEC", 1.5))
_REQ_JITTER_MAX_SEC = max(_REQ_JITTER_MIN_SEC, _env_float("WOOLIESBOT_REQUEST_JITTER_MAX_SEC", 4.0))
_HTTP_PROXY_GLOBAL = os.environ.get("WOOLIESBOT_HTTP_PROXY", "").strip()
_HTTP_PROXY_WOOLIES = os.environ.get("WOOLIESBOT_WOOLIES_PROXY", "").strip()
_HTTP_PROXY_COLES = os.environ.get("WOOLIESBOT_COLES_PROXY", "").strip()
_COLES_BFF_SUBSCRIPTION_KEY = os.environ.get(
    "WOOLIESBOT_COLES_BFF_KEY", "eae83861d1cd4de6bb9cd8a2cd6f041e"
).strip()
_COLES_BFF_STORE_ID = os.environ.get("WOOLIESBOT_COLES_STORE_ID", "0584").strip()
_BATCH_SIZE = max(5, _env_int("WOOLIESBOT_BATCH_SIZE", 20))
_BATCH_PAUSE_MIN = max(5.0, _env_float("WOOLIESBOT_BATCH_PAUSE_MIN_SEC", 20.0))
_BATCH_PAUSE_MAX = max(_BATCH_PAUSE_MIN, _env_float("WOOLIESBOT_BATCH_PAUSE_MAX_SEC", 40.0))
_CIRCUIT_BREAKER_STREAK = max(2, _env_int("WOOLIESBOT_CIRCUIT_BREAKER_STREAK", 3))
_CIRCUIT_BREAKER_PAUSE = max(30, _env_int("WOOLIESBOT_CIRCUIT_BREAKER_PAUSE_SEC", 120))
