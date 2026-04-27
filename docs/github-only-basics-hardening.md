# GitHub-Only Basics Hardening Checklist

This is the minimal hardening baseline for WooliesBot now that scheduled automation runs in GitHub Actions.

## 1) Main branch protection (minimal + compatible)

Use repository branch protection/rulesets for `main` with:

- Require pull requests for normal code changes.
- Require CI status checks (at least the `CI` workflow’s `validate` job, which runs `e2e_validate` layers B and C on `docs/data.json`).
- Block force push and branch deletion.

Compatibility note for scheduled data commits:

- The scrape workflows currently commit `docs/data.json` and `docs/heartbeat.json` directly.
- Allow an approved automation exception for GitHub Actions bot writes, or use a ruleset bypass actor policy for Actions.

## 2) Workflow permissions (least privilege)

- `scrape.yml`: `contents: write` (required for commit/push)
- `weekly-notify.yml`: `contents: write` (required for commit/push)
- `ci.yml`: `contents: read`
- `deploy-worker.yml`: `contents: read`

Keep write permissions only where a workflow actually pushes repository changes.

## 3) Action pinning (implemented)

All active workflows now pin third-party actions to immutable SHAs:

- `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5`
- `actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065`
- `actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020`
- `actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02`

Maintenance policy:

1. Review pinned SHAs quarterly (or sooner for security advisories).
2. Update SHAs in workflow files and validate with manual dispatch.
3. Keep a short changelog note whenever SHAs are rotated.

## 4) Secret scope + rotation basics

Telegram secrets (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`) should be available only to:

- `scrape.yml` failure notification step
- `weekly-notify.yml` success/failure notification steps

Rotation baseline:

- Owner: repository admin/on-call maintainer
- Cadence: every 90 days (or immediately after potential exposure)
- Procedure: update repository secrets, manually dispatch both workflows, verify Telegram delivery

## 5) Reliability guardrails

- Keep heartbeat staleness guard active in scheduled workflows.
- Keep Layer D JSON audit artifact upload active (`e2e-validate-links-full`).
- Keep Layer A spot-check + artifact path active (`Layer A live price spot-check`, `e2e-validate-layer-a`).
- If Layer A fails, follow triage/recovery steps in `docs/github-cutover-runbook.md` (Layer A failure section).
- If two consecutive scheduled runs fail, follow rollback steps in `docs/github-cutover-runbook.md`.
