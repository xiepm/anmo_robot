#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./scripts/start_sim.sh demo:=true camera_mode:=wrist "$@"
