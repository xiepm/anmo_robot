#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Current rig: the mannequin's head is on the left of the camera image.
# Explicit flags passed after defaults can override model or orientation.
exec "$APP_DIR/run_live.sh" --pose-backend rtmw-l --pose-rotation 90 --pose-auto-rotation --torso-only --view back "$@"
