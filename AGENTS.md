# Agent coordination (WooliesBot)

## Path ownership

- Prefer **one active task per agent session** for a given area of the repo.
- **Declare** which files or directories you will change before editing (in chat or via a lock file).
- **Do not** edit paths owned by another parallel agent session for the same time window.

## Lock files (optional)

For parallel Cursor agents on the **same machine**, create a **local** lock under `.agent-locks/` so others skip the same paths. Details and examples: **[`.agent-locks/README.md`](.agent-locks/README.md)**.

Lock files are gitignored; remove yours when the task finishes.

## Merge discipline

- Ship through **PRs** to `main`; use **`./scripts/shippr.sh`** when packaging local work ([`.cursor/rules/wooliesbot-shippr-mainline.mdc`](.cursor/rules/wooliesbot-shippr-mainline.mdc)).
- Keep **one cohesive change per branch/PR** where practical.
- If `main` moved underneath you, **rebase or merge** before pushing and resolve conflicts once.

## Canonical rule

Cursor loads **`.cursor/rules/wooliesbot-agent-coordination.mdc`** in every session (`alwaysApply`).
