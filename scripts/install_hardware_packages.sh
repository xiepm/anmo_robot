#!/usr/bin/env bash
# Installs only inside the existing user-owned Jammy runtime.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/runtime.sh --install apt-get update
exec scripts/runtime.sh --install apt-get install -y --no-install-recommends \
  ros-humble-realsense2-camera ros-humble-librealsense2
