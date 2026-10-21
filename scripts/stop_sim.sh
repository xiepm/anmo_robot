#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .runtime/sim-launch.pid ]]; then
  echo 'No recorded simulation process.'
  exit 0
fi
read -r SIM_PID < .runtime/sim-launch.pid
if [[ ! "$SIM_PID" =~ ^[0-9]+$ || ! -r /proc/$SIM_PID/cmdline ]]; then
  echo 'Simulation is not running.'
  exit 0
fi
SIM_COMMAND="$(tr '\0' ' ' < "/proc/$SIM_PID/cmdline")"
if [[ "$SIM_COMMAND" != *'/opt/ros/humble/bin/ros2 launch elfin_massage_sim sim.launch.py'* ]]; then
  echo 'Recorded PID belongs to another command; no process was stopped.' >&2
  exit 1
fi
if [[ "${1:-}" == --status ]]; then
  echo "Simulation is running (PID $SIM_PID)."
else
  kill -INT "$SIM_PID"
  echo 'Waiting for simulation shutdown...'
  for ((attempt=0; attempt<120; attempt++)); do
    SIM_COMMAND="$(tr '\0' ' ' 2>/dev/null < "/proc/$SIM_PID/cmdline" || true)"
    if [[ "$SIM_COMMAND" != *'/opt/ros/humble/bin/ros2 launch elfin_massage_sim sim.launch.py'* ]]; then
      echo 'Simulation stopped.'
      exit 0
    fi
    sleep 0.25
  done
  echo 'Shutdown is still pending; inspect the launch log before restarting.' >&2
  exit 1
fi
