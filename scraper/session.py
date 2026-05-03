"""curl_cffi / HTTP session helpers: UA lock, jitter, proxies, headers, retry budget."""

import os
import random
import time

from curl_cffi import requests as cffi_requests

from scraper.config import (
    _ADAPTIVE_ENABLED,
    _BASE_HTTP_RETRIES,
    _HTTP_PROXY_COLES,
    _HTTP_PROXY_GLOBAL,
    _HTTP_PROXY_WOOLIES,
    _REQ_JITTER_MAX_SEC,
    _REQ_JITTER_MIN_SEC,
)
from scraper.metrics import _read_metrics_runs

_UA_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="24"',
        "platform": '"macOS"',
        "impersonate": "chrome131",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="24"',
        "platform": '"Windows"',
        "impersonate": "chrome131",
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": '"macOS"',
        "impersonate": "chrome124",
    },
]

_run_ua_profile = None  # set once at start of each scrape run

# Preferred TLS fingerprints (ordered by reliability against Akamai)
_CFFI_IMPERSONATIONS = ["chrome131", "chrome124", "chrome120", "chrome116"]


def reset_run_ua_profile():
    """Pick a new locked UA profile for the current scrape run (call from check_prices)."""
    global _run_ua_profile
    _run_ua_profile = random.choice(_UA_PROFILES)


def _get_run_ua_profile():
    """Return the UA profile locked for the entire scrape run (fingerprint consistency)."""
    global _run_ua_profile
    if _run_ua_profile is None:
        _run_ua_profile = random.choice(_UA_PROFILES)
    return _run_ua_profile


def _get_random_ua_profile():
    return _get_run_ua_profile()


def _sleep_request_jitter(multiplier=1.0):
    lo = max(0.0, _REQ_JITTER_MIN_SEC * multiplier)
    hi = max(lo, _REQ_JITTER_MAX_SEC * multiplier)
    if hi > 0:
        time.sleep(random.uniform(lo, hi))


def _proxy_for_store(store_key):
    if store_key == "woolworths":
        return _HTTP_PROXY_WOOLIES or _HTTP_PROXY_GLOBAL
    if store_key == "coles":
        return _HTTP_PROXY_COLES or _HTTP_PROXY_GLOBAL
    return _HTTP_PROXY_GLOBAL


def _get_woolworths_headers(url=None, profile=None):
    """Browser-like headers for Woolworths PDP fetches (Akamai)."""
    profile = profile or _get_random_ua_profile()
    h = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": profile["sec_ch_ua"],
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": profile["platform"],
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if (url and "woolworths.com.au" in url) else "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": profile["ua"],
    }
    if url and "woolworths.com.au" in url:
        h["Referer"] = "https://www.woolworths.com.au/"
    return h


def _get_coles_headers(profile=None):
    """Returns headers that mimic a real Chrome browser on macOS to bypass Akamai."""
    profile = profile or _get_random_ua_profile()
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": profile["sec_ch_ua"],
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": profile["platform"],
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": profile["ua"],
        "X-Requested-With": "XMLHttpRequest",
    }


def _http_retry_budget():
    r = _BASE_HTTP_RETRIES
    if os.environ.get("WOOLIESBOT_CFFI_HTTP_RETRIES"):
        return max(1, r)
    if not _ADAPTIVE_ENABLED:
        return max(1, r)
    runs = _read_metrics_runs()
    if runs and runs[-1].get("http_5xx", 0) >= 3:
        return min(5, r + 1)
    return max(1, r)


def _create_cffi_session(store_key=None):
    """Create a curl_cffi session matching the run's locked UA fingerprint."""
    profile = _get_run_ua_profile()
    imp = profile.get("impersonate", "chrome131")
    proxy = _proxy_for_store(store_key)
    kwargs = {}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    try:
        return cffi_requests.Session(impersonate=imp, **kwargs)
    except TypeError:
        return cffi_requests.Session(impersonate=imp)
    except Exception:
        pass
    for fallback_imp in _CFFI_IMPERSONATIONS:
        try:
            return cffi_requests.Session(impersonate=fallback_imp, **kwargs)
        except TypeError:
            return cffi_requests.Session(impersonate=fallback_imp)
        except Exception:
            continue
    return cffi_requests.Session(impersonate="chrome124")
