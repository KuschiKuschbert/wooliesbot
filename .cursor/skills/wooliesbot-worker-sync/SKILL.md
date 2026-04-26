---
name: wooliesbot-worker-sync
description: Maintain shopping sync correctness in the write Worker, including CORS, auth mode compatibility, and merge semantics. Use for workers/wooliesbot-write changes or shopping_list sync incidents.
---

# WooliesBot Worker Sync

## Use This Skill When

- `shopping_list` sync fails in browser or CI.
- CORS/preflight behavior changes are needed.
- Auth mode is transitioning (credentials <-> token).

## Operating Standard

- Keep `OPTIONS`, `GET`, `POST` headers consistent.
- Preserve credential-mode compatibility unless explicitly removed.
- Keep merge behavior deterministic and idempotent.

## Command Snippets

```bash
python3 scripts/simulate_shopping_list_dual_device.py
WOOLIESBOT_WRITE_API_TOKEN=testtoken python3 scripts/simulate_shopping_list_dual_device.py
```

```bash
curl -i -X OPTIONS "https://wooliesbot-write.wooliesbot.workers.dev/shopping_list" \
  -H "Origin: https://kuschikuschbert.github.io" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: x-wooliesbot-device"
```

```bash
curl -s "https://wooliesbot-write.wooliesbot.workers.dev/health" | python3 -m json.tool
```

## Done Criteria

- Browser preflight passes with headers matching request mode.
- Sync pull/push path works in both credential and token modes.
- Health metadata reflects deployed auth/cors intent.
