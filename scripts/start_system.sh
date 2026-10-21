#!/usr/bin/env bash
# Manual, repeatable startup after login. Never enables or starts robot motion.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/web/reports/services"
UNITS=(rehabilitation-control rehabilitation-vision-preview rehabilitation-web)
SERVICE_ENV=()
# User services do not inherit variables exported in this terminal by default.
for variable in REHAB_CONTROL_PYTHON REHAB_VISION_PYTHON REHAB_ROBOT_CONFIG REHAB_HAND_EYE REHAB_NONCONTACT_CONFIG REHAB_SDK REHAB_CAPTURE_TIMING; do
  if [[ -n "${!variable:-}" ]]; then SERVICE_ENV+=("--setenv=$variable=${!variable}"); fi
done

show_status() {
  for unit in "${UNITS[@]}"; do
    state=$(systemctl --user is-active "$unit.service" 2>/dev/null || true)
    printf '%-38s %s\n' "$unit" "${state:-未启动}"
  done
  python3 - "$PROJECT_DIR" <<'PY'
import json, pathlib, sys, time, urllib.request
for label,url in [('网页','http://127.0.0.1:5173/'),('相机预览','http://127.0.0.1:8767/health')]:
    try:
        with urllib.request.urlopen(url,timeout=2) as response:
            if label=='网页': print('网页：可以打开 http://127.0.0.1:5173/')
            else:
                d=json.load(response)
                print('相机预览：'+('已有实时画面' if d.get('fresh') else d.get('camera_error') or d.get('message','等待相机')))
    except Exception as exc: print(label+'：尚未就绪（'+str(exc)+'）')
try:
    d=json.loads((pathlib.Path(sys.argv[1])/'web/.runtime/base_coordinates_latest.json').read_text())['data']
    boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    fresh=d.get('host_boot_id')==boot and 0<=time.monotonic()-d['generated_monotonic_s']<2
    if fresh and d.get('feedback_window') and not d.get('error'):
        print('机械臂反馈与相机坐标通信：正常')
    else: print('机械臂坐标通信：等待新鲜反馈，请查看页面右上角设备状态')
except (OSError,ValueError,KeyError):
    print('机械臂坐标通信：尚无新鲜反馈，请查看页面右上角设备状态')
PY
  printf '日志目录：%s\n' "$LOG_DIR"
}

case "${1:-}" in
  --status) show_status; exit 0 ;;
  --help|-h)
    echo '用法：启动系统.sh [--status]'
    echo '不带参数：依次启动控制、相机和网页；重复运行复用已有服务。'
    echo '--status：只查看状态。启动服务不会恢复旧路线或触发运动。'
    exit 0 ;;
  '') ;;
  *) echo '未知参数；使用 --help 查看说明。' >&2; exit 2 ;;
esac

for command in systemctl systemd-run flock ss python3 node; do
  command -v "$command" >/dev/null || { echo "缺少命令：$command" >&2; exit 1; }
done
systemctl --user show-environment >/dev/null || { echo '请登录桌面后以普通用户运行，不要 sudo 启动系统。' >&2; exit 1; }
mkdir -p "$PROJECT_DIR/.runtime/services" "$LOG_DIR"
exec 9>"$PROJECT_DIR/.runtime/services/start.lock"
flock -n 9 || { echo '另一份启动脚本正在执行，请稍候。'; exit 1; }

start_service() {
  local unit="$1" port="$2" restart="$3" logfile="$4"
  shift 4
  local state
  state=$(systemctl --user is-active "$unit.service" 2>/dev/null || true)
  if [[ "$state" == active || "$state" == activating ]]; then
    echo "$unit 已在运行，保持现有进程。"
    return 0
  fi
  if [[ "$state" == deactivating ]]; then
    echo "$unit 正在退出；请等待退出完成后重新运行。" >&2
    return 1
  fi
  for occupied_port in ${port//,/ }; do
    if [[ -n "$(ss -H -ltn "sport = :$occupied_port")" ]]; then
      echo "端口 $occupied_port 被其他进程占用，未启动重复服务：$unit" >&2
      return 1
    fi
  done
  systemctl --user reset-failed "$unit.service" 2>/dev/null || true
  # --collect removes stopped transient definitions. This script recreates them
  # on the next login/reboot; it does not enable unattended boot-time control.
  systemd-run --user --collect --unit="$unit" "${SERVICE_ENV[@]}" \
    --description="Rehabilitation $unit" --working-directory="$PROJECT_DIR" \
    --property="Restart=$restart" --property=RestartSec=2 \
    --property=TimeoutStopSec=12 \
    --property="StandardOutput=append:$LOG_DIR/$logfile" --property=StandardError=inherit \
    "$@"
}

result=0
start_service rehabilitation-control 8765,8766 no control.log \
  "$PROJECT_DIR/scripts/start_control.sh" --backend real --standalone --enable-real-scan \
  --noncontact-config "${REHAB_NONCONTACT_CONFIG:-config/commissioning/noncontact.json}" || result=1
start_service rehabilitation-vision-preview 8767 always vision.log \
  "$PROJECT_DIR/web/scripts/start_vision_preview.sh" || result=1
start_service rehabilitation-web 5173 on-failure web.log \
  "$PROJECT_DIR/scripts/start_web.sh" || result=1

# Bounded readiness wait; a missing camera must not prevent opening the UI.
python3 - <<'PY' || result=1
import sys, time, urllib.request
for attempt in range(40):
    try:
        with urllib.request.urlopen('http://127.0.0.1:5173/',timeout=.5): break
    except OSError: time.sleep(.25)
else:
    print('网页启动未完成，请查看 web/reports/services/web.log',file=sys.stderr)
    sys.exit(1)
PY
show_status
echo '首次加载识别模型需要稍等；可再次运行 --status 查看实时画面是否就绪。'
echo '可以关闭此终端，服务由当前用户的 systemd 管理。'
exit "$result"
