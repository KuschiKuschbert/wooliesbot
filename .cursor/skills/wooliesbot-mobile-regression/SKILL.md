---
name: wooliesbot-mobile-regression
description: Protect mobile dashboard behavior and prevent viewport-specific regressions. Use when editing docs/app.js, docs/index.html, docs/style.css, or mobile e2e scripts.
---

# WooliesBot Mobile Regression

## Use This Skill When

- UI changes affect nav, sticky chrome, drawers, or mobile interactions.
- Reports mention behavior differences between desktop and mobile.

## Operating Standard

1. Validate layout assumptions at narrow viewport widths.
2. Re-check touch and focus interactions for list/drawer/modal flows.
3. Confirm no hidden-overlap regressions in fixed/sticky components.

## Command Snippets

```bash
python3 scripts/e2e_mobile.py
python3 scripts/audit_mobile.py
```

```bash
node --check docs/app.js
```

## Done Criteria

- Mobile flows behave correctly at compact and standard breakpoints.
- No new overlap/clipping in sticky/fixed UI.
- Desktop behavior remains stable after mobile fixes.
