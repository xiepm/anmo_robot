#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Separate DDS domain from simulation (57). Console is read-only.
exec scripts/runtime.sh env ROS_DOMAIN_ID=68 ROS_LOCALHOST_ONLY=1 \
  ros2 launch massage_hardware commissioning.launch.py "$@"
