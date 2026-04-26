---
name: wooliesbot-validation-gates
description: Run and interpret WooliesBot validation layers to gate safe merges. Use when shipping dashboard, data, worker, or scraping changes.
---

# WooliesBot Validation Gates

## Use This Skill When

- A change touches data shape, compare logic, or rendering.
- CI reports DIFF/WARN and triage is needed.
- You need a fast pre-merge confidence pass.

## Gate Sequence

1. Layer B (`--layer B`) for internal data consistency.
2. Layer C (`--layer C`) for dashboard rendering consistency.
3. Layer D sample/full when URL mapping behavior changed.

## Command Snippets

```bash
python3 scripts/e2e_validate.py --layer B
python3 scripts/e2e_validate.py --layer C
python3 scripts/e2e_validate.py --layer D --sample 20
```

```bash
python3 scripts/e2e_validate.py --all --strict-exit
```

## Interpretation

- `DIFF`: block merge until root cause is addressed.
- `WARN`: assess risk; acceptable only with rationale.
- `SKIP`: ensure skip reason is expected (not accidental).

## Done Criteria

- No unexpected `DIFF` in touched areas.
- `WARN` outcomes are understood and documented.
- Validation coverage matches change scope.
