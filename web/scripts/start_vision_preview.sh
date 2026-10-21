#!/usr/bin/env bash
set -euo pipefail
WEB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${REHAB_VISION_PYTHON:-$WEB_DIR/../zhenjiu/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "缺少视觉 Python 环境：$PYTHON_BIN" >&2
  echo '请先运行 scripts/install_standalone.sh --vision --vision-python /path/to/python3.12，或设置 REHAB_VISION_PYTHON。' >&2
  exit 1
fi
exec "$PYTHON_BIN" -u "$WEB_DIR/scripts/vision_preview.py" "$@"
