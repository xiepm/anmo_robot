#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./scripts/start_sim.sh hmi:=true rviz:=false "$@"
