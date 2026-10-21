#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$PROJECT_DIR/scripts/controller_runtime.sh" python3 -m massage_controller.sdk_readonly \
  --host 192.168.0.10 --port 10003 --format compact \
  --sdk '/workspace/SDK/HUAYAN/python API接口/HuayanRobotPythonSDK_V1.1.1.1/CPS.py' \
  --output /workspace/src/massage_controller/reports/robot_readonly/latest.json "$@"
