---
name: wooliesbot-operations
description: >-
  WooliesBot inventory safety, merge hygiene, variant discovery habits, verification
  (e2e_validate), compare_group conventions, and privacy expectations for
  docs/data.json and chef_os. Use when editing data.json, merging discovery output,
  planning inventory scans, or coordinating scraper + manual edits.
---

# WooliesBot — Operations & inventory safety

## When to use

- Editing [`docs/data.json`](docs/data.json), `compare_group`, or URLs by hand or from scripts.
- Running or designing **variant discovery** / inventory-scan tooling (extra API traffic).
- After **chef_os** or scraper changes — what to verify before trusting prices.

## Always-on rule

Workspace rule **[`.cursor/rules/wooliesbot-data-safety.mdc`](../../rules/wooliesbot-data-safety.mdc)** (`alwaysApply: true`) duplicates the non-negotiables for every Cursor session. This skill expands context and pointers.

## Core habits

1. **Checkpoint before bulk edits:** git commit/branch or file copy of `docs/data.json`.
2. **Merge hygiene:** dedupe names and PDP URLs; stable `compare_group` ids (`snake_case`).
3. **Daemon awareness:** restarting **`chef_os.py`** / launchd when fixing URLs permanently so in-memory state does not overwrite fixes.
4. **Discovery quota:** batch discovery gently; never parallel curl_cffi (see [`wooliesbot-scraper`](../../rules/wooliesbot-scraper.mdc) / `chef_os.py`).
5. **Risky categories:** weighted/deli — weak or null BFF pricing; prefer manual URLs over blind search expansion.
6. **Verify:** [`scripts/e2e_validate.py`](../../../scripts/e2e_validate.py) — B/C always after inventory/scrape changes; D sample when URLs move.
7. **Privacy:** committed `data.json` exposes shopping patterns — mind forks and shares.
8. **UX truth:** group-best $/unit can favor huge packs — informational only.

## Variant discovery CLI

- Script: **[`scripts/discover_variants.py`](../../../scripts/discover_variants.py)** — proposes draft `docs/data.json` rows via sequential Woolworths + Coles search (no writes to `data.json` unless you paste manually). Options: `--compare-group`, `--from-item`, `--inventory-scan`, `--max-queries`, `--exclude-regex`, `--write-snippet`.
- **Phase 2 review UI:** **[`docs/discovery-review.html`](../../../docs/discovery-review.html)** — paste or load discovery JSON, toggle rows, copy/export selected items (also linked from the main dashboard header as “Discovery”).
- **Phase 3 (optional schedule):** **[`scripts/discovery_weekly_snippet.sh`](../../../scripts/discovery_weekly_snippet.sh)** + **[`com.wooliesbot.discovery.plist.example`](../../../com.wooliesbot.discovery.plist.example)** — writes `logs/discovery-snippet-*.json` only; see **[`LAUNCHD_SETUP.md`](../../../LAUNCHD_SETUP.md)**.
- Before merging drafts: backup `data.json`, drop `_discovery_meta` from pasted rows if present (or use the review page’s strip toggle), run **`python scripts/e2e_validate.py`** (layers **B** and **C** minimum).

## Scraper hardening (sibling track)

- Evidence-driven changes only: inspect **`chef_os.log`**, **`logs/scraper_metrics.json`**, stale flags in `data.json`.
- After any **`chef_os.py`** edit: **`e2e_validate.py`** layers B/C (and D sample when URLs change).
- Do not increase parallel curl_cffi concurrency — see wooliesbot-scraper rule.

## Related docs

- Scraper architecture & anti-bot: [`.cursor/rules/wooliesbot-scraper.mdc`](../../rules/wooliesbot-scraper.mdc)
- Variant discovery epic: `.cursor/plans/variant_discovery_workflow_*.plan.md` (user plans dir) if present

## Quick verification commands

```bash
cd "/path/to/Woolies Script"
./scripts/verify_wooliesbot_stack.sh       # py_compile + e2e B/C (Layer D: RUN_LAYER_D=1)
VERIFY_STRICT=1 ./scripts/verify_wooliesbot_stack.sh   # exit 1 if any layer prints FAIL (e.g. Layer B DIFFs)
python scripts/e2e_validate.py --help      # choose layers per need
```
