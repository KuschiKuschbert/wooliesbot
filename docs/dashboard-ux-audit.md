# Dashboard + Insights Audit Baseline

This audit captures the current UI structure, duplication risks, and the regression checklist used during the revamp.

## UI Inventory

### Dashboard (`deals` tab)
- Hero: freshness/status and value proposition.
- Stats strip: list size, specials countdown, budget, estimated savings.
- Priority modules:
  - `Buy Now` (low stock + at/under deal price).
  - `Top 5 This Week`.
  - Mobile priority rail mirrors both modules.
- Discovery modules:
  - Restock predictions.
  - Near misses.
  - Specials grid + pagination + category/search/store filters.
- Utility modules:
  - Essentials checklist.
  - Cola head-to-head.
  - Master tracklist table/cards.

### Insights (`analytics` tab)
- Savings headline metrics.
- Live gauge + weekly wins.
- Spend trend and category charts.
- Category inflation + store/category heatmap.
- Smart tips + volatility + best month to buy.
- Pantry health + target-confidence intelligence.
- Advanced cards (shopping time and compare-group diagnostics).

## Double-Ups / Complexity Risks

- Priority derivation duplicated in multiple functions:
  - `renderBuyNow()`
  - `renderMobilePriorityRail()`
- Top deal ranking duplicated in:
  - `renderTop5Deals()`
  - `renderMobilePriorityRail()`
- Similar savings math appears in stats + analytics widgets.
- Inline styling and inline `onclick` usage in dynamic card markup increase drift risk.
- Large responsive surface area (`29` media queries in `style.css`) and many hard overrides (`!important`).

## No-Regression Checklist

Run this checklist after each phase:

1. Deals tab
   - Search filters list correctly.
   - Store/category filters and sort pills work.
   - Specials grid and pagination update correctly.
2. Shopping list and trip flows
   - Add/remove item works from every card entry point.
   - Drawer open/close and mobile bottom-nav toggle behave.
   - Shopping trip mode (`Go shopping`, `Done shopping`, `Clear completed`) is stable.
3. Write/sync paths
   - Stock modal save path works (local + cloud write path unchanged).
   - Settings modal values persist and cloud cart load still works.
4. Insights tab
   - All analytics cards render without console errors.
   - Spending/category charts render on desktop and mobile.
   - Advanced diagnostics card remains collapsible.
5. Automation scripts
   - `scripts/e2e_validate.py`
   - `scripts/e2e_mobile.py`
   - `scripts/audit_mobile.py`
