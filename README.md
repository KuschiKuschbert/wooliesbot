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

## Common Commands

- Install/start launchd service: `./manage_services.sh install`
- Stop launchd service: `./manage_services.sh stop`
- Run validation (recommended after scrape/data changes):
  - `python3 scripts/e2e_validate.py --layer B`
  - `python3 scripts/e2e_validate.py --layer C`

## Important Docs

- Service setup: `LAUNCHD_SETUP.md`
- Stop-at-stable closeout checklist: `docs/STOP_AT_STABLE_OPERATOR_CHECKLIST.md`
- Data/operations safety rules: `.cursor/rules/wooliesbot-data-safety.mdc`

## Notes

- If editing `docs/data.json`, keep backups/checkpoints and validate taxonomy/duplicates.
- If `chef_os.py` is running as a service, restart it after manual data/path fixes so stale in-memory state does not overwrite changes.
