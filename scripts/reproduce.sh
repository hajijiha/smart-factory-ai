#!/usr/bin/env bash
# Reproduce dataset, training, evaluations and integrated runtime in dependency order.
set -euo pipefail
cd "$(dirname "$0")/.."
# Avoid a second Gazebo publisher sharing the live ROS2 topics during acquisition.
docker compose stop simulator pdm vision storage controller dashboard
docker compose build
docker compose up -d broker database
docker compose run --rm -e GENERATE_DATA=1 simulator
docker compose run --rm pdm python3 -m factory.pdm.train
docker compose run --rm vision python3 -m factory.vision.train
docker compose run --rm pdm python3 scripts/audit_dataset.py
docker compose run --rm pdm python3 scripts/hardware.py
docker compose run --rm pdm python3 scripts/render_results.py
docker compose run --rm pdm python3 -m pytest tests -q
docker compose up -d
python3 scripts/verify_runtime.py
echo 'Dashboard: http://localhost:8080'
