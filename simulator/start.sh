#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/humble/setup.bash
set -u
mkdir -p "${DATA_DIR:-/app/data}/live" "${DATA_DIR:-/app/data}/sensor" /app/docs/results
Xvfb :99 -screen 0 1024x768x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
python3 -m simulator.world /tmp/factory.world
gzserver --verbose -s libgazebo_ros_init.so -s libgazebo_ros_factory.so /tmp/factory.world >/tmp/gazebo.log 2>&1 &
GAZEBO_PID=$!
export FACTORY_GAZEBO_PID="$GAZEBO_PID"
trap 'kill "$GAZEBO_PID" 2>/dev/null || true' EXIT
exec python3 -m simulator.bridge
