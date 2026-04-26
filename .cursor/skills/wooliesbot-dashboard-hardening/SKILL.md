---
name: wooliesbot-dashboard-hardening
description: Harden dashboard boot, data loading, and stale-shell behavior. Use when editing docs/app.js, docs/sw.js, docs/index.html, or when users report no data, loading loops, or stale UI after deploy.
---

# WooliesBot Dashboard Hardening

## Use This Skill When

- Dashboard shows empty/default state despite populated `data.json`.
- Boot throws runtime errors during first render.
- Service worker or cache-bust behavior causes stale shell issues.

## Operating Standard

1. Inspect `initDashboard` error paths and user-visible status surfaces.
2. Ensure dynamic icon replacement uses guarded helper calls.
3. Verify SW strategy and HTML cache-bust values are coherent.
4. Keep failures diagnosable in both console and UI.

## Command Snippets

```bash
node --check docs/app.js
python3 scripts/e2e_validate.py --layer C
python3 scripts/e2e_validate.py --layer B --item cola
```

```bash
curl -sI "https://kuschikuschbert.github.io/wooliesbot/" | sed -n '1,12p'
curl -s "https://kuschikuschbert.github.io/wooliesbot/data.json" | python3 -m json.tool >/dev/null
```

## Done Criteria

- No uncaught boot exceptions on first load.
- Data-load failures are visible and actionable in UI status surfaces.
- Fresh deploy serves updated shell assets without stale-client confusion.
