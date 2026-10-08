#!/usr/bin/env bash
# Reproduce dataset, training, evaluations and integrated runtime in dependency order.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose build
docker compose up -d broker database
docker compose run --rm -e GENERATE_DATA=1 simulator
docker compose run --rm pdm python3 -m factory.pdm.train
docker compose run --rm vision python3 -m factory.vision.train
docker compose run --rm pdm python3 -m pytest tests -q
docker compose up -d
echo 'Dashboard: http://localhost:8080'
