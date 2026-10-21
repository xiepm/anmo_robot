#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec scripts/runtime.sh env ROS_DOMAIN_ID=68 ros2 run massage_hardware hardware_check \
  --profile /workspace/src/massage_hardware/config/workstation.yaml "$@"
