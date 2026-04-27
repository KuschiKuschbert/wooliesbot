# Stop-at-Stable Operator Checklist (WooliesBot)

Use this at the end of every implementation cycle to prevent endless polish loops.

## 1) Confirm Scope

- Restate requested scope in one sentence.
- Confirm no unapproved feature expansion was added.

## 2) Verify Required Gates

- Functional behavior passes for touched path(s): scraper, dashboard, worker sync, or data flow.
- Required validation/CI checks for touched area are green.
- For data or URL behavior changes, validate required `scripts/e2e_validate.py` layers.
- No known Sev-1/Sev-2 issue, data-loss risk, or security regression remains.

## 3) Enforce Post-Green Budget

- After all gates are green, allow at most 1-2 low-risk polish items.
- Stop after 45 minutes of post-green polish (whichever limit is reached first).
- Defer all other improvements to backlog/new cycle.

## 4) Mandatory Closeout Verdict

End with exactly one of:

- `SHIP: <one-line reason with evidence>`
- `HOLD: <single blocking gate>`
- `BACKLOG: <non-blocking deferred improvement>`

## Default Closeout Prompt

Use this prompt when wrapping up:

`Give final verdict only: SHIP, HOLD, or BACKLOG with one-line reason and evidence.`
