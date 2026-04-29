# WooliesBot

Automated grocery price tracking for Woolworths/Coles with dashboard output and scheduled scraping.

## Quick Start

```bash
cd "/Users/danielkuschmierz/Woolies Script"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 chef_os.py
```

## Contributing (shipping to `main`)

The `main` branch is **protected** — land work via **pull request**, not a direct push.

For a typical flow with local changes, use **shippr** (creates a branch, commits, pushes, opens a PR with `gh`):

```bash
./scripts/shippr.sh <branch-name> "<pr-title>" "<commit-message>"
```

Run `./scripts/shippr.sh --help` for full behavior and arguments. Agent-side conventions for this repo live in [`.cursor/rules/wooliesbot-shippr-mainline.mdc`](.cursor/rules/wooliesbot-shippr-mainline.mdc).

## Common Commands

- Install/start launchd service: `./manage_services.sh install`
- Stop launchd service: `./manage_services.sh stop`
- Run validation (recommended after scrape/data changes):
  - `python3 scripts/e2e_validate.py --layer B`
  - `python3 scripts/e2e_validate.py --layer C`
- **Two-device cloud cart sync (Playwright):** if the Worker uses token-only auth, set `WOOLIESBOT_WRITE_API_TOKEN` and Playwright Chromium. Defaults to the production dashboard URL (Worker CORS); use `--local` to serve `docs/` (your Worker must allow `http://127.0.0.1:*` in `ALLOWED_ORIGINS` for that to work):
  - `python3 scripts/e2e_sync_two_devices.py --headed`

## Shopping list sync and Worker auth

The dashboard calls the Cloudflare Worker at `writeApiUrl` ([`docs/env.js`](docs/env.js) or `localStorage` `write_api_url`). **You do not need a token in the browser by default** for this repo: [`wrangler.toml`](workers/wooliesbot-write/wrangler.toml) uses `ALLOW_INSECURE_PUBLIC_WRITES = "1"`, so requests from **[`ALLOWED_ORIGINS`](workers/wooliesbot-write/wrangler.toml)** are accepted with per-IP rate limiting (GitHub still uses `GH_TOKEN` on the server only).

**Optional stricter mode:** set `ALLOW_INSECURE_PUBLIC_WRITES = "0"`, set `WRITE_API_TOKENS` in Cloudflare (`npx wrangler secret put WRITE_API_TOKENS`), then open the dashboard on each device with `#wbt=<token>` appended to the URL — the token is stored in `localStorage` and the fragment is stripped automatically.

### Rollback (if a deploy breaks sync)

- **GitHub Pages / dashboard only:** revert the merged PR on `main`, or bump `SHELL_VERSION` in [`docs/sw.js`](docs/sw.js) if users still see stale JavaScript.
- **Worker:** in the Cloudflare dashboard, roll back the deployment, or from git run `npx wrangler deploy` at a known-good commit.
- **GitHub file `docs/shopping_list_sync.json`:** Worker rollback does not undo file edits; repair from git history if the document was corrupted.

## Important Docs

- Service setup: `LAUNCHD_SETUP.md`
- Stop-at-stable closeout checklist: `docs/STOP_AT_STABLE_OPERATOR_CHECKLIST.md`
- Data/operations safety rules: `.cursor/rules/wooliesbot-data-safety.mdc`

## Notes

- If editing `docs/data.json`, keep backups/checkpoints and validate taxonomy/duplicates.
- If `chef_os.py` is running as a service, restart it after manual data/path fixes so stale in-memory state does not overwrite changes.
