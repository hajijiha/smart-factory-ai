#!/usr/bin/env bash
# Candidate acquisition/training/evaluation. Existing baseline models stay intact.
set -euo pipefail
cd "$(dirname "$0")/.."
experiment_project="${BIAS_PROJECT:-smart-factory-bias-review}"
run_name="${BIAS_RUN:-local-bias-v2}"
if [[ ! "$run_name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo 'BIAS_RUN must be a simple directory name.' >&2
  exit 1
fi
mapfile -t configured_paths < <(python3 -c 'import yaml; from pathlib import Path; c=yaml.safe_load(Path("config.bias.yaml").read_text()); print("\n".join(c["paths"][k] for k in ("data","artifacts","reports")))')
configured_data="${configured_paths[0]}"
configured_artifacts="${configured_paths[1]}"
configured_reports="${configured_paths[2]}"
results="${BIAS_REPORT_BASE:-$configured_reports}/$run_name"
data="${BIAS_DATA_BASE:-$configured_data}/$run_name"
artifacts="${BIAS_ARTIFACT_BASE:-$configured_artifacts}/$run_name"
dc() { docker compose -p "$experiment_project" -f compose.yaml -f compose.bias.yaml "$@"; }
runtime() { dc run --rm --no-deps -e DATA_DIR="$data" -e ARTIFACT_DIR="$artifacts" -e REPORT_DIR="$results" pdm "$@"; }
acquire() { dc run --rm --no-deps -e DATA_DIR="$data" -e ARTIFACT_DIR="$artifacts" -e REPORT_DIR="$results" simulator; }
if [[ -f "docs/results/$run_name/candidate-freeze.json" ]]; then
  echo 'A frozen experiment already exists. Preserve it and use another checkout for a new experiment.' >&2
  exit 1
fi
mkdir -p "docs/results/$run_name"
dc build simulator pdm
dc up -d broker
trap 'dc stop broker >/dev/null' EXIT
acquire
runtime python3 -m scripts.bias_audit --data "$data" --output "$results/candidate-data-audit.json"
runtime python3 -m factory.pdm.train --train-only
runtime python3 -m factory.vision.train --train-only
runtime python3 -m factory.vision.train --export-only
runtime python3 -m scripts.bias_evaluate --data "$data" --artifacts "$artifacts" \
  --freeze-out "$results/candidate-freeze.json" --output unused.json
runtime python3 -m factory.pdm.train --evaluate-only
runtime python3 -m scripts.bias_evaluate --data "$data" --artifacts "$artifacts" \
  --phase final --freeze "$results/candidate-freeze.json" --output "$results/candidate-challenge.json"
runtime python3 -m factory.vision.train --evaluate-only
echo "Results: docs/results/$run_name. Candidate weights: artifacts/$run_name; baseline weights were not replaced."
