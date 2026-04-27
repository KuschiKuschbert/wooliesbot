---
name: wooliesbot-stability-signoff
description: Run a focused stability signoff and return SHIP/HOLD/BACKLOG to avoid circular post-green iteration.
---

# WooliesBot Stability Signoff

## Use This Skill When

- You think the change is done but keep finding additional optional improvements.
- You need a clear release-cycle closeout decision.
- You want a repeatable signoff that aligns with the always-on stop-at-stable rule.

## Inputs

- Requested scope for this cycle.
- Changed subsystem(s): scraper, dashboard, worker, data taxonomy, or CI/workflow.
- Required checks for touched area.

## Procedure

1. Restate scope in one sentence and confirm no scope creep.
2. Check functional behavior for touched path(s) and capture concrete evidence.
3. Verify required CI/validation signals for touched path(s).
4. Identify remaining risks:
   - Sev-1/Sev-2 defects
   - data-loss risk
   - security regression
5. Confirm required evidence by change type:
   - data/inventory/url mapping changes -> validation layers B and C, plus D sample if URL behavior changed
   - dashboard changes -> affected load/data flows verified
   - worker sync changes -> merge/write behavior verified
6. Decide if additional ideas are blockers or backlog:
   - blocker -> `HOLD`
   - non-blocking -> `BACKLOG`
7. Enforce post-green improvement budget:
   - allow up to 1-2 low-risk polish items
   - or at most 45 minutes post-green
   - then defer everything else

## Output Format (required)

Return exactly one status with one-line rationale:

- `SHIP` - all gates green, no blocking risk.
- `HOLD` - at least one gate failed; include the blocker.
- `BACKLOG` - improvement deferred because it is non-blocking.

## Done Criteria

- Status is explicit (`SHIP`, `HOLD`, or `BACKLOG`).
- Rationale is one line and references current scope.
- Next action is unambiguous (ship now, fix blocker, or defer item).
