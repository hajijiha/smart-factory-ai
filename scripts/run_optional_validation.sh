#!/usr/bin/env bash
# Generate new Gazebo run-to-failure and paired interventions without replacing published models.
set -euo pipefail
cd "$(dirname "$0")/.."
run="${OPTIONAL_RUN:-optional-$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ ! "$run" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo 'OPTIONAL_RUN must be a simple new directory name.' >&2
  exit 1
fi
for path in "data/$run" "artifacts/$run" "docs/results/$run"; do
  if [[ -e "$path" ]]; then
    echo "Preserve existing output: $path. Choose a new OPTIONAL_RUN." >&2
    exit 1
  fi
done
data="/app/data/$run"
artifacts="/app/artifacts/$run"
reports="/app/docs/results/$run"
dc() { docker compose "$@"; }
runtime() { dc run --rm --no-deps -e DATA_DIR="$data" -e ARTIFACT_DIR="$artifacts" -e REPORT_DIR="$reports" pdm "$@"; }
dc build simulator pdm
dc up -d broker
dc run --rm --no-deps -e GENERATE_RUL=1 -e ROS_DOMAIN_ID=94 -e DATA_DIR="$data" -e RUL_DATA_DIR="$data/rul" simulator
runtime python3 -m factory.pdm.train_rul --reports "$reports"
dc run --rm --no-deps -e ARTIFACT_DIR="$artifacts" -e PSI_REFERENCE_DATA="${PSI_REFERENCE_DATA:-/app/data/sensor/dataset.npz}" pdm python3 -c 'import os; from factory.pdm.drift import fit_reference; from factory.common import CONFIG,ARTIFACTS; fit_reference(os.environ["PSI_REFERENCE_DATA"], CONFIG["drift"], ARTIFACTS/"drift-reference.json")'
dc run --rm --no-deps -e GENERATE_CAUSAL=1 -e ROS_DOMAIN_ID=95 -e DATA_DIR="$data" -e CAUSAL_DATA_DIR="$data/causal" simulator
dc run --rm --no-deps -e DATA_DIR="$data" pdm python3 scripts/verify_root_cause.py --data "$data/causal" --output "$reports/root-cause.json"
runtime python3 scripts/check_quality.py --no-git --output "$reports/code-quality.json"
printf 'Optional training and controlled experiment: docs/results/%s\n' "$run"
