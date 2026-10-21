#!/usr/bin/env bash
# Explicit dependency installation only; never starts services or hardware.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONTROL_PYTHON=python3
VISION_PYTHON=python3.12
INSTALL_VISION=false
INSTALL_WEB=true
usage() {
  cat <<'HELP'
用法：scripts/install_standalone.sh [--python /path/to/python] [--vision] [--vision-python /path/to/python3.12] [--skip-web]
默认：创建/更新 .venv-control（Python >=3.10），npm ci 并构建 web。
--vision：另外创建/更新 zhenjiu/.venv（实测 Python 3.12），安装相机/推理依赖。
--skip-web：只准备 Python 环境。
脚本仅在明确执行后安装依赖；不启动服务，不下载模型/SDK，不修改系统软件包。
HELP
}
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --python|--vision-python)
      (($# >= 2)) || { usage >&2; exit 2; }
      if [[ "$1" == --python ]]; then CONTROL_PYTHON="$2"; else VISION_PYTHON="$2"; fi
      shift 2 ;;
    --vision) INSTALL_VISION=true; shift ;;
    --skip-web) INSTALL_WEB=false; shift ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done
check_python() {
  command -v "$1" >/dev/null || { echo "找不到解释器：$1" >&2; exit 1; }
  "$1" - "$2" <<'PY'
import sys
version = sys.version_info[:2]
if sys.argv[1] == 'vision':
    if version != (3, 12):
        raise SystemExit('视觉固定依赖请使用 Python 3.12，通过 --vision-python 指定。')
elif version < (3, 10):
    raise SystemExit('控制进程需要 Python >=3.10。')
PY
}
check_python "$CONTROL_PYTHON" control
if "$INSTALL_VISION"; then check_python "$VISION_PYTHON" vision; fi
if "$INSTALL_WEB"; then
  command -v npm >/dev/null || { echo '请先安装 Node.js（建议22 LTS，至少22.12）及npm。' >&2; exit 1; }
  node -e 'let [a,b]=process.versions.node.split(".").map(Number); if(!((a===20&&b>=19)||(a===22&&b>=12)||a>22)){console.error("需要Node ^20.19.0或>=22.12.0");process.exit(1)}'
fi
cd "$PROJECT_DIR"
if [[ ! -x .venv-control/bin/python ]]; then "$CONTROL_PYTHON" -m venv .venv-control; fi
check_python "$PROJECT_DIR/.venv-control/bin/python" control
.venv-control/bin/python -m pip install -r src/massage_controller/requirements-standalone.txt
if "$INSTALL_VISION"; then
  if [[ ! -x zhenjiu/.venv/bin/python ]]; then "$VISION_PYTHON" -m venv zhenjiu/.venv; fi
  check_python "$PROJECT_DIR/zhenjiu/.venv/bin/python" vision
  zhenjiu/.venv/bin/python -m pip install -r zhenjiu/requirements.txt
fi
if "$INSTALL_WEB"; then
  (cd web && npm ci && npm run build)
fi
echo '依赖准备完成；没有启动控制进程、相机或机械臂。下一步见 docs/INSTALLATION.md。'
