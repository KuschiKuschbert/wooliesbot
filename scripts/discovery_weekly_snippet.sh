#!/usr/bin/env bash
# Optional Phase 3: run inventory discovery and write a JSON snippet only (never edits data.json).
# Configure via environment (e.g. in .env, export before launchd, or edit below).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=1091
  source "$ROOT/.venv/bin/activate"
fi

# All items in the output get this compare_group until you reassign in the review UI.
COMPARE_GROUP="${WOOLIESBOT_DISCOVERY_GROUP:-pending_review}"
MAX_Q="${WOOLIESBOT_DISCOVERY_MAX_QUERIES:-8}"
SLEEP_SEC="${WOOLIESBOT_DISCOVERY_SLEEP_SEC:-3}"
ONLY_TYPE="${WOOLIESBOT_DISCOVERY_ONLY_TYPE:-}"  # e.g. household; leave empty for all types

OUT="$ROOT/logs/discovery-snippet-$(date +%Y%m%d-%H%M%S).json"
QUERY_LOG="${WOOLIESBOT_DISCOVERY_QUERY_LOG:-$ROOT/logs/discovery-query.log}"

ARGS=(
  "$ROOT/scripts/discover_variants.py"
  --inventory-scan
  --compare-group "$COMPARE_GROUP"
  --max-queries "$MAX_Q"
  --sleep-sec "$SLEEP_SEC"
  --quiet
  --query-log "$QUERY_LOG"
  --write-snippet "$OUT"
)
if [[ -n "$ONLY_TYPE" ]]; then
  ARGS+=( --only-type "$ONLY_TYPE" )
fi

python3 "${ARGS[@]}"
echo "$(date -Iseconds) discovery_weekly_snippet: wrote $OUT" >> "$ROOT/logs/discovery_scheduler.log"
