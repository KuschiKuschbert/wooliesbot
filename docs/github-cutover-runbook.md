# GitHub Cutover Verification Runbook

This runbook verifies that GitHub Actions fully replaces daemon-style automation for WooliesBot.

## Scope

- Workflow cadence and reliability
- Data publish reliability (`docs/data.json`, `docs/heartbeat.json`)
- Link audit artifact generation (`logs/e2e_validate_links_full.json`)
- Telegram output-only notifications (success/failure)
- No dependency on launchd + Telegram listener commands
- Dashboard (GitHub Pages) status: **Last published** is the last successful `heartbeat.json` / data commit, not the last time the schedule fired. Failed or skipped runs do not advance it. **Next scheduled** in the UI comes from `next_run` in `heartbeat.json` (for Actions, the next 4h UTC slot matching `.github/workflows/scrape.yml`).

## Verify recent workflow health

From a machine with `gh` and access to the repo:

`gh run list --workflow "Scrape & push data" --limit 20`

Use this when the site shows a stale “Last published” to see whether recent runs **failed** (red) or succeeded (green).

## Workflows under test

- `.github/workflows/scrape.yml` (every 4 hours)
- `.github/workflows/weekly-notify.yml` (Sunday summary path)

## Pre-cutover baseline

1. Confirm Actions secrets exist:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
2. Confirm `main` branch can receive workflow commits.
3. Confirm launchd service is not required for scheduled updates during validation window.

## Manual smoke sequence (day 0)

Run in this order from the GitHub Actions UI:

1. Dispatch `Scrape & push data` workflow once.
2. Confirm workflow succeeds end-to-end.
3. Verify repository changes:
   - `docs/data.json` changed only when expected
   - `docs/heartbeat.json` updated
4. Verify Layer D artifact:
   - artifact `e2e-validate-links-full` exists
   - JSON is readable and recent
5. Dispatch `Weekly scrape + Telegram summary` workflow once.
6. Confirm success Telegram message is delivered.
7. Trigger one controlled failure (optional but recommended) in a test branch to verify failure Telegram alert path.

## Per-run verification checklist (scheduled runs)

Use this checklist for each automatic run:

- Workflow status is `success`.
- No stuck/concurrency-cancelled run unexpectedly.
- `docs/data.json` is updated or intentionally unchanged.
- `docs/heartbeat.json` reflects current execution.
- Layer B/C validations pass.
- Layer D audit artifact exists.
- No unexpected regression in link-health counts.

## 7-run acceptance gate

Cutover is accepted only when all are true:

1. 7 consecutive scheduled `scrape.yml` runs succeed.
2. No missed publish (heartbeat/data update path intact).
3. Telegram notifications are reliable:
   - weekly success summary is received
   - failure alert path has been validated at least once
4. No operational need for:
   - `telegram_bot_listener()`
   - launchd scheduler (`com.wooliesbot.automation.plist`)

## Decommission steps (after acceptance)

1. Stop using launchd as primary scheduler.
2. Keep `chef_os.py` compatibility wrapper temporarily for manual recovery.
3. Monitor 1 additional week in GitHub-first mode.
4. Then remove daemon-only orchestration paths from `chef_os.py`:
   - scheduler loop
   - listener command handling
   - deep-sync loop trigger paths that depend on long-running process lifecycle

## Rollback plan

If two consecutive scheduled workflow failures occur:

1. Re-enable launchd fallback temporarily.
2. Keep workflows enabled for diagnostics.
3. Fix pipeline/workflow issue.
4. Restart the 7-run acceptance gate from zero.

Operational trigger paragraph: if `scrape.yml` fails twice in a row (or once in `scrape.yml` and once in `weekly-notify.yml` within the same day), treat it as production-impacting automation instability and immediately switch to launchd fallback while triaging root cause.
