#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$PROJECT_DIR/scripts/controller_runtime.sh" ros2 launch massage_controller real_mirror.launch.py "$@"
