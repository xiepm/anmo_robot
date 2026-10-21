#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$PROJECT_DIR/scripts/controller_runtime.sh" python3 -m massage_controller.vision_sim "$@"
