---
name: wooliesbot-release-playbook
description: Execute safe incremental rollouts with feature branches, staged commits, and gated merges. Use for multi-phase changes spanning worker, dashboard, data, and CI.
---

# WooliesBot Release Playbook

## Use This Skill When

- The change set has multiple risky subsystems.
- You need reversible rollout steps and clear merge gates.

## Playbook

1. Split work into focused feature branches by risk domain.
2. Commit each phase with passing targeted checks.
3. Merge in dependency order only after green gates.
4. Push and validate deploy/runtime signals after each merge.

## Command Snippets

```bash
git checkout -b feature/<phase-name>
# implement + validate
git add <files> && git commit -m "<phase commit>"
git checkout main
git merge --no-ff feature/<phase-name> -m "merge: <phase summary>"
```

```bash
git push origin main
gh run list --workflow "CI" --limit 5
gh run list --workflow "Deploy Worker (write API)" --limit 5
```

## Merge Gate Checklist

- Relevant validators pass.
- CI guardrails pass (including file-size ratchet).
- Browser/API smoke checks match expected runtime behavior.
- Rollback path is clear for each phase.

## Done Criteria

- All phase branches merged in planned order.
- `main` is green in CI/deploy workflows.
- Runtime smoke checks confirm expected behavior post-merge.

## Dashboard shell version (static `docs/` site)

When you change `docs/index.html`, `docs/app.js`, `docs/style.css`, or `docs/sw.js` in a way that affects caching or the service worker precache, bump **one** id everywhere in the same commit:

1. `meta name="wooliesbot-shell-version"` in `docs/index.html`
2. `body` `data-ui-surface` and `data-shell-version` (same value)
3. Footer `#footer-ui-stamp` text (same value, for humans)
4. `docs/sw.js` `SHELL_VERSION` (cache bucket name)
5. Query string on static assets in `index.html` (e.g. `style.css?v=…`, `app.js?v=…`, and the same on `discovery-review.html` if it references those files)

Keeping these aligned avoids stale UI, stale SW caches, and confusing version drift. The app reads the meta tag for `checkForStaleShellVersion` in `docs/app.js` reload logic.
