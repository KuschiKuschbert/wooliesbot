#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Usage:
  ./scripts/shippr.sh <branch-name> <pr-title> <commit-message> [base-branch]

Examples:
  ./scripts/shippr.sh docs/layer-a-playbook "docs: add Layer A failure playbook" "docs: add Layer A failure playbook"
  ./scripts/shippr.sh fix/validator-timeout "fix: handle validator timeout" "fix: handle validator timeout" main

Behavior:
  1) Verifies there are local changes to commit
  2) Creates and checks out the branch
  3) Stages all tracked/untracked files and creates one commit
  4) Pushes with upstream
  5) Opens a PR with gh using --fill
EOF
  exit 0
fi

branch_name="${1:-}"
pr_title="${2:-}"
commit_message="${3:-}"
base_branch="${4:-main}"

if [[ -z "$branch_name" || -z "$pr_title" || -z "$commit_message" ]]; then
  echo "Error: missing required arguments." >&2
  echo "Run ./scripts/shippr.sh --help for usage." >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Error: GitHub CLI (gh) is required." >&2
  exit 2
fi

if [[ -z "$(git status --porcelain)" ]]; then
  echo "Error: no local changes found to commit." >&2
  exit 2
fi

if git show-ref --verify --quiet "refs/heads/${branch_name}"; then
  echo "Error: local branch '${branch_name}' already exists." >&2
  exit 2
fi

if git ls-remote --exit-code --heads origin "${branch_name}" >/dev/null 2>&1; then
  echo "Error: remote branch '${branch_name}' already exists on origin." >&2
  exit 2
fi

git checkout -b "$branch_name"
git add -A
git commit -m "$commit_message"
git push -u origin "$branch_name"
gh pr create --base "$base_branch" --title "$pr_title" --fill
