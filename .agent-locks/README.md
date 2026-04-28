# Agent lock files (local coordination)

Optional **local** locks so only one Cursor agent session edits a given set of paths at a time. Locks are **not committed** (see `.gitignore`).

## When to use

- You have **more than one** Composer / agent chat working at once.
- Two sessions might touch the **same files** (e.g. `docs/app.js`, `workers/wooliesbot-write/src/index.js`).

Skip locks if a **single** agent session owns the whole task.

## Lifecycle

1. **Create** a lock **before** editing contested paths (see format below).
2. **Remove** the lock when the task is **done** or **cancelled** (including failed runs—do not leave stale locks).

Stale locks block others; treating missing locks as “free” keeps the honour system workable.

## File naming

- Path: `.agent-locks/<slug>.lock`
- Use a short slug: task id, branch name, or `YYYY-MM-DD-feature-name`.

## Format (plain text)

Each line is either a **comment** (`# …`) or a **path** (repo-relative, POSIX `/`).

Use **globs** only if you intend to cover a whole tree; prefer explicit files when possible.

Example `.agent-locks/2026-04-28-worker-sync.lock`:

```text
# owner: optional label for your session / branch
# task: shopping list merge semantics

workers/wooliesbot-write/src/index.js
scripts/simulate_shopping_list_dual_device.py
scripts/e2e_sync_two_devices.py
```

Directories: list the directory with a trailing slash to mean “anything under here” by convention:

```text
workers/wooliesbot-write/
```

Agents should treat **every path prefix** listed in **any other** `*.lock` file as **read-only** for editing (except deleting their **own** lock after completion).

## Checking for conflicts

From repo root:

```bash
ls .agent-locks/*.lock 2>/dev/null
```

Before editing a path, open each existing `.lock` and ensure your target path is not covered.

## Relation to git

Locks do **not** replace branches or PR review. Use **one branch per coherent task**, merge via PR (`./scripts/shippr.sh`), and rebase or merge `main` before pushing if others landed overlapping changes.

See also **`.cursor/rules/wooliesbot-agent-coordination.mdc`** and **`AGENTS.md`** at the repo root.
