# Auto-start WoolesBot on boot (macOS)

GitHub Actions is now the primary automation path (4-hour scrape workflow + weekly summary workflow).
This launchd setup is kept as a local fallback/legacy option.

Stop-at-stable closeout checklist: `docs/STOP_AT_STABLE_OPERATOR_CHECKLIST.md`.

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

## Dashboard status + stock writes

The dashboard now keeps shopping list state local to each browser/device.

- Worker endpoint kept for stock updates: `POST /update_stock`
- Dashboard status signal is scrape freshness from `docs/heartbeat.json` (Last published / Next scheduled + status badge)
- The Cloud write Worker URL is set at build time in `docs/env.js` (via `scripts/generate_runtime_env.py` / `WOOLIESBOT_WRITE_API_URL`) and cached in browser `localStorage` as `write_api_url` on first load

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
- Optional rewards receipt enrichment (self-hosted only): [`.github/workflows/receipt-sync.yml`](.github/workflows/receipt-sync.yml).
- Weekly workflow chains receipt sync automatically after the weekly price scrape.
- One-shot runner used by workflows: [`scripts/scrape_pipeline.py`](scripts/scrape_pipeline.py).
- Telegram is output-only in this mode (no bot command listener required).
- Cutover checklist/runbook: [`docs/github-cutover-runbook.md`](docs/github-cutover-runbook.md).
- Minimal hardening checklist: [`docs/github-only-basics-hardening.md`](docs/github-only-basics-hardening.md).

### Receipt sync auth note

`receipt_sync.py` needs an authenticated Everyday Rewards browser profile. GitHub-hosted runners are ephemeral, so they cannot keep this session. Use a **self-hosted runner** plus a persistent profile directory (workflow variable `RECEIPT_SYNC_PROFILE_DIR`, default `$HOME/.wooliesbot/chrome_profile`), and complete login/MFA once on that machine.

### Self-hosted runner auto-restart (recommended)

To keep receipt sync resilient, install the GitHub runner as a launchd service on the runner host:

```bash
cd ~/actions-runner
./svc.sh install
./svc.sh start
./svc.sh status
```

Health checks:

```bash
gh api repos/KuschiKuschbert/wooliesbot/actions/runners --jq '.total_count, (.runners[]? | {name,status,busy})'
```

Recovery checklist when receipt sync is queued/pending:

1. Confirm runner is online via the command above.
2. If offline, restart service: `cd ~/actions-runner && ./svc.sh restart`.
3. If still missing, reconfigure runner token and restart (`./config.sh ...`, then `./svc.sh start`).
4. Re-run `.github/workflows/receipt-sync.yml`.

Workflow-level safety nets:

- `.github/workflows/receipt-sync.yml` now fails fast with a clear error if no self-hosted runner is online.
- `.github/workflows/receipt-runner-health.yml` checks for queued/pending stalls and sends Telegram alerts when runs are stuck.
