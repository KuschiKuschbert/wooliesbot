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
- **Two-device cloud cart sync (Playwright):** needs `WOOLIESBOT_WRITE_API_TOKEN` and Playwright Chromium (Bearer auth when `ALLOW_INSECURE_PUBLIC_WRITES=0`). Defaults to the production dashboard URL (Worker CORS); use `--local` to serve `docs/` (your Worker must allow `http://127.0.0.1:*` in `ALLOWED_ORIGINS` for that to work):
  - `python3 scripts/e2e_sync_two_devices.py --headed`

## Shopping list sync and Worker auth

The dashboard talks to the Cloudflare Worker at `writeApiUrl` (from `docs/env.js` or `localStorage` `write_api_url`). **Production should use Bearer tokens**, not anonymous writes.

1. **Operator — create tokens (never commit them):** generate one or more secrets (e.g. `openssl rand -hex 32`), then store them in Cloudflare for the `wooliesbot-write` Worker:
   - `cd workers/wooliesbot-write && npx wrangler secret put WRITE_API_TOKENS`
   - Paste a **comma-separated** list if you use more than one token.
2. **Users — pair each browser:** open [`docs/pairing.html`](docs/pairing.html) (or the published `/pairing.html` on GitHub Pages), paste the Worker base URL if needed, paste one of those tokens, and open the generated link on each device so `localStorage` gets `write_api_token`.
3. **Deploy:** [`workers/wooliesbot-write/wrangler.toml`](workers/wooliesbot-write/wrangler.toml) sets `ALLOW_INSECURE_PUBLIC_WRITES = "0"`. Deploy only after step 1 is done, or writes from the static site will return **401** until tokens exist.
4. **CORS:** `ALLOWED_ORIGINS` must include your GitHub Pages origin (scheme + host only, no path).

### Rollback (if a deploy breaks sync)

- **GitHub Pages / dashboard only:** revert the merged PR on `main`, or bump `SHELL_VERSION` in [`docs/sw.js`](docs/sw.js) if users still see stale JavaScript.
- **Worker:** in the Cloudflare dashboard, roll back to the previous Worker version, or from git run `npx wrangler deploy` at a known-good commit. **Emergency only:** temporarily set `ALLOW_INSECURE_PUBLIC_WRITES = "1"` in `wrangler.toml`, redeploy, then restore `0` after tokens are fixed.
- **GitHub file `docs/shopping_list_sync.json`:** Worker rollback does not undo file edits; repair from git history if the document was corrupted.

## Important Docs

- Service setup: `LAUNCHD_SETUP.md`
- Stop-at-stable closeout checklist: `docs/STOP_AT_STABLE_OPERATOR_CHECKLIST.md`
- Data/operations safety rules: `.cursor/rules/wooliesbot-data-safety.mdc`

## Notes

- If editing `docs/data.json`, keep backups/checkpoints and validate taxonomy/duplicates.
- If `chef_os.py` is running as a service, restart it after manual data/path fixes so stale in-memory state does not overwrite changes.
