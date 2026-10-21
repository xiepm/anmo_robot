#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec scripts/runtime.sh colcon build --base-paths src --symlink-install --executor sequential \
  --packages-select elfin_description elfin5_l_ros2_gazebo elfin_massage_sim massage_hardware \
  --cmake-args -DBUILD_TESTING=OFF
