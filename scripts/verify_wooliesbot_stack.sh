#!/usr/bin/env bash
# Compile checks + fast e2e layers (B/C). Optional Layer D (network).
# Usage:
#   ./scripts/verify_wooliesbot_stack.sh
#   RUN_LAYER_D=1 VERIFY_E2E_SAMPLE=25 ./scripts/verify_wooliesbot_stack.sh
# Strict exit codes (fail if any layer prints FAIL):
#   VERIFY_STRICT=1 ./scripts/verify_wooliesbot_stack.sh
#
# Full network e2e (Layers A–D, ~1–2 min). Uses e2e_validate --strict-exit (DIFF/DEAD fail).
#   RUN_FULL_E2E=1 E2E_SAMPLE=25 E2E_SEED=1 ./scripts/verify_wooliesbot_stack.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

strict="${VERIFY_STRICT:-0}"

run_layer() {
  local layer="$1"
  shift
  local out
  out="$(mktemp)"
  set +e
  python3 scripts/e2e_validate.py "$@" | tee "$out"
  local py_exit=$?
  set -e
  if [[ "$py_exit" -ne 0 ]]; then
    rm -f "$out"
    return "$py_exit"
  fi
  if [[ "$strict" == "1" ]] && grep -q "Layer ${layer}: FAIL" "$out"; then
    rm -f "$out"
    echo "VERIFY_STRICT=1: Layer ${layer} reported FAIL — exiting 1." >&2
    return 1
  fi
  rm -f "$out"
  return 0
}

echo "== py_compile discover_variants + chef_os =="
python3 -m py_compile scripts/discover_variants.py chef_os.py

echo "== e2e_validate Layer B (internal consistency) =="
run_layer B --layer B

echo "== e2e_validate Layer C (dashboard eff_price emulation) =="
run_layer C --layer C

if [[ "${RUN_LAYER_D:-0}" == "1" ]]; then
  SAMPLE="${VERIFY_E2E_SAMPLE:-15}"
  echo "== e2e_validate Layer D (URLs, sample=$SAMPLE) — network required =="
  run_layer D --layer D --sample "$SAMPLE"
else
  echo "== Layer D skipped (set RUN_LAYER_D=1 to run link checks) =="
fi

if [[ "${RUN_FULL_E2E:-0}" == "1" ]]; then
  FS="${E2E_SAMPLE:-25}"
  SD="${E2E_SEED:-1}"
  echo "== e2e_validate FULL A+B+C+D (sample=$FS seed=$SD, --strict-exit) — network =="
  python3 scripts/e2e_validate.py --sample "$FS" --seed "$SD" --strict-exit
fi

echo "== verify_wooliesbot_stack: OK =="
