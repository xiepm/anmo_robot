#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLAN_PATH="${1:?用法: scripts/waypoint_test.sh config/commissioning/waypoints.json [--execute]}"
if [[ "$PLAN_PATH" == "$PROJECT_DIR/"* ]]; then
  PLAN_PATH="/workspace/${PLAN_PATH#"$PROJECT_DIR/"}"
fi
exec "$PROJECT_DIR/scripts/start_control.sh" --waypoint-plan "$PLAN_PATH" "${@:2}"
