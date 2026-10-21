#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
ARGS=("$@")
BACKEND=real
for ((i=0; i<${#ARGS[@]}; i++)); do
  if [[ "${ARGS[i]}" == --backend && $((i+1)) -lt ${#ARGS[@]} ]]; then BACKEND=${ARGS[i+1]}; fi
  if [[ "${ARGS[i]}" == --backend=* ]]; then BACKEND=${ARGS[i]#*=}; fi
done
has_option() {
  local item
  for item in "${ARGS[@]}"; do
    [[ "$item" == "$1" || "$item" == "$1="* ]] && return 0
  done
  return 1
}
for setting in 'REHAB_ROBOT_CONFIG:--robot-config' 'REHAB_HAND_EYE:--hand-eye' 'REHAB_NONCONTACT_CONFIG:--noncontact-config' 'REHAB_SDK:--sdk'; do
  variable=${setting%%:*}
  option=${setting#*:}
  if [[ "$BACKEND" != real && "$variable" == REHAB_NONCONTACT_CONFIG ]]; then continue; fi
  if [[ -n "${!variable:-}" ]] && ! has_option "$option"; then
    ARGS+=("$option" "${!variable}")
  fi
done
USE_RUNTIME=false
if [[ -z "${REHAB_CONTROL_PYTHON:-}" && -x "$PROJECT_DIR/.runtime/jammy/bin/bash" ]]; then
  USE_RUNTIME=true
elif ! has_option --standalone; then
  ARGS+=(--standalone)
fi

# Validate local files before a real SDK connection. SIM/help and offline plan
# validation do not require the robot SDK or this installation's calibration.
if ! has_option --help && ! has_option -h && ! has_option --waypoint-plan; then
  python3 - "$PROJECT_DIR" "${ARGS[@]}" <<'PY'
import argparse, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
p = argparse.ArgumentParser(add_help=False)
p.add_argument('--backend', default='real')
p.add_argument('--robot-config', default=str(root/'src/massage_controller/config/real_robot.json'))
p.add_argument('--hand-eye', default=str(root/'zhenjiu/hand_eye/calibration_20261013.json'))
p.add_argument('--sdk')
p.add_argument('--noncontact-config')
a, _ = p.parse_known_args(sys.argv[2:])
if a.backend != 'real':
    sys.exit(0)
def require(path, hint):
    if not host_path(path).is_file():
        p.error(f'缺少配置/资源：{path}。{hint}；参见 docs/INSTALLATION.md')
def host_path(path):
    return root/path[len('/workspace/'):] if str(path).startswith('/workspace/') else pathlib.Path(path)
require(a.robot_config, '按 real_robot.example.json 准备本机配置，或设置 REHAB_ROBOT_CONFIG')
try:
    cfg = json.loads(host_path(a.robot_config).read_text())
    sdk = a.sdk or str(root/cfg['sdk_relative_path'])
except (ValueError, KeyError, TypeError) as exc:
    p.error(f'机械臂配置无效：{exc}')
require(sdk, '从厂家获得匹配的 Python SDK，或设置 REHAB_SDK')
require(a.hand_eye, '准备本机实测手眼标定，或设置 REHAB_HAND_EYE；不能用示例代替实测')
if a.noncontact_config:
    require(a.noncontact_config, '按 noncontact.example.json 准备本机配置，或设置 REHAB_NONCONTACT_CONFIG')
PY
fi

# The isolated runtime sees the repository at /workspace. Translate explicit
# host paths inside it, and reject outside paths instead of silently misreading.
if "$USE_RUNTIME"; then
  for ((i=0; i<${#ARGS[@]}; i++)); do
    case "${ARGS[i]}" in
      --robot-config|--hand-eye|--sdk|--noncontact-config|--config|--waypoint-plan)
        ((i+1<${#ARGS[@]})) || break
        i=$((i+1)); value=${ARGS[i]}; prefix='' ;;
      --robot-config=*|--hand-eye=*|--sdk=*|--noncontact-config=*|--config=*|--waypoint-plan=*)
        prefix="${ARGS[i]%%=*}="; value=${ARGS[i]#*=} ;;
      *) continue ;;
    esac
    case "$value" in
      "$PROJECT_DIR"/*) ARGS[i]="$prefix/workspace/${value#"$PROJECT_DIR"/}" ;;
      /workspace/*) ;;
      /*) echo "隔离环境不能读取仓库外配置：$value。请放入项目目录，或设置 REHAB_CONTROL_PYTHON 使用原生环境。" >&2; exit 2 ;;
    esac
  done
fi
exec "$PROJECT_DIR/scripts/controller_runtime.sh" python3 -m massage_controller.node "${ARGS[@]}"
