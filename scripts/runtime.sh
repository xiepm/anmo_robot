#!/usr/bin/env bash
# User-owned Ubuntu 22.04 environment. No host root or system package changes.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOTFS="$PROJECT_DIR/.runtime/jammy"
if [[ ! -x "$ROOTFS/bin/bash" ]]; then
  echo 'Runtime is not installed yet.' >&2
  exit 1
fi
# PRoot is used only for package installation (emulates package ownership).
if [[ "${1:-}" == --install ]]; then
  shift
  export PROOT_NO_SECCOMP=1
  exec "$PROJECT_DIR/.runtime/tools/usr/bin/proot" -0 -r "$ROOTFS" -b /dev -b /proc -b /sys \
    -b "$PROJECT_DIR:/workspace" -w /workspace /usr/bin/env HOME=/root DEBIAN_FRONTEND=noninteractive "$@"
fi
# Normal simulation runs at native speed in a user/mount namespace.
DISPLAY_BINDS=()
if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then
  DISPLAY_BINDS=(--ro-bind "$XAUTHORITY" /root/.Xauthority --setenv XAUTHORITY /root/.Xauthority)
fi
exec bwrap --unshare-user --uid 0 --gid 0 --bind "$ROOTFS" / "${DISPLAY_BINDS[@]}" \
  --dev-bind /dev /dev --proc /proc --ro-bind /sys /sys --bind /tmp /tmp \
  --bind "$PROJECT_DIR" /workspace --chdir /workspace \
  --setenv HOME /root --setenv QT_X11_NO_MITSHM 1 --setenv ROS_LOCALHOST_ONLY 1 \
  --setenv ROS_DOMAIN_ID 57 --setenv LIBGL_ALWAYS_SOFTWARE 1 \
  /bin/bash -c 'source /opt/ros/humble/setup.bash; if [[ -f /workspace/install/setup.bash ]]; then source /workspace/install/setup.bash; fi; exec "$@"' bash "$@"
