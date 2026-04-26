---
name: wooliesbot-data-taxonomy
description: Keep docs/data.json taxonomy clean for reliable comparisons and recommendations. Use when editing compare_group, price_mode, or grouped product rows.
---

# WooliesBot Data Taxonomy

## Use This Skill When

- Adding/editing rows in `docs/data.json`.
- Fixing compare-group warnings or odd group winners.
- Normalizing litre/kg/each semantics.

## Operating Standard

- One `compare_group` should map to one compatible `price_mode`.
- For `price_mode=litre`, ensure `pack_litres` is valid.
- Keep grouping stable over time; avoid ad-hoc renames.

## Command Snippets

```bash
python3 scripts/e2e_validate.py --layer B
python3 scripts/e2e_validate.py --layer B --item cola
python3 scripts/e2e_validate.py --layer C --item cola
```

```bash
python3 - <<'PY'
import json
from collections import defaultdict
with open("docs/data.json") as f:
    d=json.load(f)
items=d["items"] if isinstance(d,dict) else d
g=defaultdict(set)
for it in items:
    cg=it.get("compare_group")
    if cg: g[cg].add(it.get("price_mode") or "each")
print({k:sorted(v) for k,v in g.items() if len(v)>1})
PY
```

## Done Criteria

- No mixed `price_mode` inside any `compare_group`.
- Target group changes pass Layer B and Layer C spot checks.
- Group winners/diagnostics are improved or unchanged.
