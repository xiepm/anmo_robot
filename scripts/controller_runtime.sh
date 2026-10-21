#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Preserve the existing ROS runtime on the commissioned workstation. A clean
# clone can instead use a dedicated native environment, without installing ROS.
if [[ -z "${REHAB_CONTROL_PYTHON:-}" && -x "$PROJECT_DIR/.runtime/jammy/bin/bash" ]]; then
  case "${REHAB_CAPTURE_TIMING:-}" in
    "$PROJECT_DIR"/*) export REHAB_CAPTURE_TIMING="/workspace/${REHAB_CAPTURE_TIMING#"$PROJECT_DIR"/}" ;;
    /workspace/*|'') ;;
    /*) echo '隔离控制环境不能读取仓库外 REHAB_CAPTURE_TIMING；请移入项目或使用原生控制环境。' >&2; exit 2 ;;
  esac
  exec "$PROJECT_DIR/scripts/runtime.sh" bash -c 'export PYTHONPATH="/workspace/src/massage_controller:/workspace/.runtime/controller_deps:${PYTHONPATH:-}"; exec "$@"' bash "$@"
fi
PYTHON_BIN="${REHAB_CONTROL_PYTHON:-$PROJECT_DIR/.venv-control/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "缺少控制 Python 环境：$PYTHON_BIN" >&2
  echo "请先运行：$PROJECT_DIR/scripts/install_standalone.sh；或设置 REHAB_CONTROL_PYTHON 为解释器绝对路径。" >&2
  exit 1
fi
if [[ "${1:-}" != python3 && "${1:-}" != python ]]; then
  echo '原生控制环境只支持 Python 命令；ROS/Gazebo 命令需要另外准备 ROS 2 Humble。' >&2
  exit 2
fi
shift
export PYTHONPATH="$PROJECT_DIR/src/massage_controller${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" "$@"
