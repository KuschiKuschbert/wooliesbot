# Auto-start WoolesBot on boot (macOS)

GitHub Actions is now the primary automation path (4-hour scrape workflow + weekly summary workflow).
This launchd setup is kept as a local fallback/legacy option.

The long-running service is `com.wooliesbot.automation.plist` (runs `chef_os.py`) and uses the project `.venv` Python.

## Optional components (not required for scrape → `data.json` → GitHub Pages)

| Piece | Purpose |
|-------|---------|
| **`com.wooliesbot.automation.plist`** | Core: scheduled scrapes + dashboard updates |
| **Telegram** (`.env`) | Alerts and `/shop` commands — scrapes still run without it |
| **`receipt_sync.py`** | Receipt history enrichment — optional |
| **`scripts/e2e_validate.py`** | Manual validation — optional |
| **`scripts/e2e_mobile.py`** | Playwright tests — optional (`requirements-dev.txt`) |

**Install or restart automation service:**

```bash
cd "/Users/danielkuschmierz/Woolies Script"
chmod +x manage_services.sh
./manage_services.sh install
```

**Stop** (so processes stay down — required before manual `chef_os.py` tests if you do not want launchd to relaunch):

```bash
./manage_services.sh stop
```

**Why `kill` seemed to “respawn” the bot:** `KeepAlive` in the plist tells launchd to restart `chef_os.py` when it exits. Use `manage_services.sh stop` or `launchctl unload ~/Library/LaunchAgents/com.wooliesbot.automation.plist` instead of killing the PID.

Telegram credentials belong in `.env` (loaded by `chef_os.py`); do not embed tokens in plist files.

Optional **scraper / anti-bot tuning** (`WOOLIESBOT_*`) is documented in `.env.example`; copy keys into `.env` only if you need to override defaults.

**Inventory `item_id`:** rows get a stable UUID from [`export_data_to_json`](chef_os.py) on the next scrape if missing. No separate migration script is required.

## Cross-device shopping cart sync (mobile + desktop)

Shopping cart sync now uses the cloud write Worker (same origin as `write_api_url`) and a shared repo file:

- Worker endpoints: `GET /shopping_list`, `POST /shopping_list`
- Shared payload path: `docs/shopping_list_sync.json`
- Merge semantics:
  - item key = `item_id` (fallback `name`)
  - conflicts keep `qty` max and `picked` OR
  - newer row metadata wins by `updated_at`
  - deletions use tombstones to avoid resurrecting removed rows
- Expected delay across devices: usually 5-30s (poll interval + network)

If sync is unavailable, the cart still works locally and retries in the background when the Worker is reachable.

## Optional: scheduled variant discovery (snippet only — Phase 3)

This does **not** edit `docs/data.json`. It runs [`scripts/discover_variants.py`](scripts/discover_variants.py) in `--inventory-scan` mode and writes `logs/discovery-snippet-*.json`. Review with [`docs/discovery-review.html`](docs/discovery-review.html) before any merge.

1. **Manual run:** `./scripts/discovery_weekly_snippet.sh`
2. **Env (optional):** `WOOLIESBOT_DISCOVERY_GROUP` (default `pending_review`), `WOOLIESBOT_DISCOVERY_MAX_QUERIES` (default `8`), `WOOLIESBOT_DISCOVERY_SLEEP_SEC`, `WOOLIESBOT_DISCOVERY_ONLY_TYPE` (e.g. `household`), `WOOLIESBOT_DISCOVERY_QUERY_LOG` (default `logs/discovery-query.log` from the repo root — path is passed to `--query-log`).
3. **Weekly launchd (off by default):** edit paths in [`com.wooliesbot.discovery.plist.example`](com.wooliesbot.discovery.plist.example), copy to `~/Library/LaunchAgents/`, `launchctl load`. Avoid overlapping the main `chef_os` scrape window if possible (extra supermarket traffic).

## Schedule and Telegram (long-running `chef_os.py`, legacy mode)

The automation plist runs **one** long-lived `chef_os.py` process (`KeepAlive`). Inside that process:

- **Every 4 hours** — `run_report()` runs with **no** per-scrape “WooliesBot updated…” Telegram; it still syncs `docs/data.json` and pushes to GitHub Pages.
- **Sunday 09:00** — same silent scrape, then a **weekly** Telegram message (“Weekly Prices Updated!” + dashboard link).
- **`chef_os.py --now`**, **`/shop`**, and **`/show_staples`** — still send the short **summary** Telegram (deals count + dashboard link) when `run_report` completes successfully.

**Single-instance lock:** `run_report` acquires an exclusive `fcntl` lock on `logs/chef_os_scrape.lock`. If the daemon is mid-scrape, a concurrent `--now` (or a second thread) **skips** and logs a warning instead of racing on `data.json`. Set `WOOLIESBOT_TELEGRAM_STARTUP=0` in `.env` to mute the “Supervisor active” message on each daemon start. Set `WOOLIESBOT_TELEGRAM_ERRORS=1` to receive Telegram alerts when a **silent** scheduled scrape throws inside `run_report`.

## GitHub-first operation (recommended)

- Primary orchestrator: [`.github/workflows/scrape.yml`](.github/workflows/scrape.yml) every 4 hours.
- Weekly summary: [`.github/workflows/weekly-notify.yml`](.github/workflows/weekly-notify.yml).
- One-shot runner used by workflows: [`scripts/scrape_pipeline.py`](scripts/scrape_pipeline.py).
- Telegram is output-only in this mode (no bot command listener required).
- Cutover checklist/runbook: [`docs/github-cutover-runbook.md`](docs/github-cutover-runbook.md).
- Minimal hardening checklist: [`docs/github-only-basics-hardening.md`](docs/github-only-basics-hardening.md).
