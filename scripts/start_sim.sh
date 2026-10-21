#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .runtime/sim-launch.pid ]]; then
  read -r SIM_PID < .runtime/sim-launch.pid
  if [[ "$SIM_PID" =~ ^[0-9]+$ && -r /proc/$SIM_PID/cmdline ]]; then
    SIM_COMMAND="$(tr '\0' ' ' < "/proc/$SIM_PID/cmdline")"
    if [[ "$SIM_COMMAND" == *'/opt/ros/humble/bin/ros2 launch elfin_massage_sim sim.launch.py'* ]]; then
      echo 'Simulation is already running. Stop it with ./scripts/stop_sim.sh first.' >&2
      exit 1
    fi
  fi
fi
exec scripts/runtime.sh /bin/bash -c 'echo $$ > /workspace/.runtime/sim-launch.pid; exec ros2 launch elfin_massage_sim sim.launch.py "$@"' bash "$@"
