"""Per-scrape mutable counters (shared across woolworths / coles / batch paths)."""

scrape_run_stats = {
    "http_429": 0,
    "http_5xx": 0,
    "cffi_attempts": 0,
    "stores_used_chrome": [],
    "coles_challenge": 0,
    "coles_429": 0,
}


def reset_scrape_run_stats():
    scrape_run_stats["http_429"] = 0
    scrape_run_stats["http_5xx"] = 0
    scrape_run_stats["cffi_attempts"] = 0
    scrape_run_stats["stores_used_chrome"] = []
    scrape_run_stats["coles_challenge"] = 0
    scrape_run_stats["coles_429"] = 0
