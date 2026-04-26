---
name: wooliesbot-ci-ops
description: Triage CI and workflow failures quickly and map them to actionable fixes. Use when GitHub Actions runs fail or deployment/guardrail checks regress.
---

# WooliesBot CI Ops

## Use This Skill When

- A workflow fails and root cause is unclear.
- You need to determine if failure is test, guardrail, deploy, or config.

## Fast Triage

1. Identify failing run and failed job/step.
2. Classify failure type (guardrail, lint/syntax, validator, deploy, auth/cors).
3. Confirm if issue is code change vs stale/deployed runtime mismatch.

## Command Snippets

```bash
gh run list --workflow "CI" --limit 10
gh run view <run-id> --log-failed
```

```bash
gh run list --workflow "Deploy Worker (write API)" --limit 10
gh run view <run-id> --json name,status,conclusion,headSha,url,jobs
```

```bash
python3 scripts/check_file_sizes.py
python3 scripts/e2e_validate.py --layer B
python3 scripts/e2e_validate.py --layer C
```

## Common Failure Classes

- File-size ratchet exceeded in hot files.
- Validation DIFF in Layer B/C.
- Worker deploy/environment mismatch.
- Browser preflight/runtime mismatch vs expected headers.

## Done Criteria

- Root cause identified with failing step evidence.
- Fix mapped to one subsystem with a clear verification command.
- Follow-up run status is confirmed (or blocker documented).
