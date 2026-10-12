#!/usr/bin/env bash
# Create one new isolated clone and preserve its database volumes after verification.
set -euo pipefail
source_path='/mnt/c/Users/gkwlg/OneDrive/Documents/ChatGPT/smart factory/smart-factory-ai'
clean_path=/home/gkwlgns02/smart-factory-independent-clean-20261013
logs='/mnt/c/Users/gkwlg/OneDrive/Documents/ChatGPT/smart factory/reports/completion-work-2026-10-12'
project=smart-factory-independent-clean-20261013
if [[ -e "$clean_path" ]]; then
  echo 'Preserve existing independent checkout; select a different explicit path.' >&2
  exit 1
fi
git clone --no-hardlinks -b codex/complete-requirements "$source_path" "$clean_path" > "$logs/independent-clean-clone.log" 2>&1
cd "$clean_path"
[[ "$(git rev-parse HEAD)" == d200c86db86165e15b5daff4be2e4e6da0a71db6 ]]
[[ -z "$(git status --porcelain)" ]]
[[ ! -e data && ! -e artifacts/vision.onnx ]]
[[ -s artifacts/pdm.pt && -s artifacts/vision.pt && -s artifacts/rul.pt && -s artifacts/drift-reference.json ]]
git rev-parse HEAD > "$logs/independent-clean-source.txt"
git ls-files artifacts > "$logs/independent-clean-tracked-artifacts.txt"
export DASHBOARD_PORT=8085
docker compose -p "$project" up -d --build > "$logs/independent-clean-deploy.log" 2>&1
docker compose -p "$project" ps --format json > "$logs/independent-clean-services.jsonl"
printf 'Independent empty-data clone is started at http://localhost:8085\n'
