#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"
if [[ ! -x .venv/bin/python ]]; then
  echo '缺少运行环境，请先按 README.md 创建 .venv 并安装 requirements.txt。' >&2
  exit 1
fi
# Ubuntu Wayland 桌面使用 XWayland 显示 OpenCV 窗口。
export QT_QPA_PLATFORM=xcb
exec .venv/bin/python -u acupoint_locator_refactored.py live "$@"
