#!/usr/bin/env bash
# Stop only this verifier's Compose project; preserve its DB and MQTT volumes.
set -euo pipefail
clean_path=/home/gkwlgns02/smart-factory-independent-clean-20261013
logs='/mnt/c/Users/gkwlg/OneDrive/Documents/ChatGPT/smart factory/reports/completion-work-2026-10-12'
project=smart-factory-independent-clean-20261013
cd "$clean_path"
[[ "$(git rev-parse HEAD)" == d200c86db86165e15b5daff4be2e4e6da0a71db6 ]]
[[ -s artifacts/vision.onnx && -s artifacts/vision.export.json ]]
sha256sum artifacts/pdm.pt artifacts/vision.pt artifacts/rul.pt artifacts/vision.onnx > "$logs/independent-clean-artifact-hashes.txt"
docker compose -p "$project" ps --format json > "$logs/independent-clean-services-final.jsonl"
docker compose -p "$project" down > "$logs/independent-clean-cleanup.log" 2>&1
docker volume inspect "${project}_database-data" "${project}_mqtt-data" > "$logs/independent-clean-volumes-preserved.json"
printf 'Independent services stopped. Both named data volumes are preserved.\n'
