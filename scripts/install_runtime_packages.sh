#!/usr/bin/env bash
# Run inside the user-owned Jammy runtime using runtime.sh --install.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends \
  build-essential cmake git python3-colcon-common-extensions python3-pytest \
  python3-opencv python3-scipy python3-pip cython3 python3-cairo python3-numpy python3-yaml python3-pyqt5 fonts-noto-cjk \
  ros-humble-ros-base ros-humble-gazebo-ros-pkgs ros-humble-gazebo-ros2-control \
  ros-humble-ros2-controllers ros-humble-xacro \
  ros-humble-cv-bridge ros-humble-message-filters ros-humble-tf2-geometry-msgs \
  ros-humble-rviz2 ros-humble-ros2controlcli \
  ros-humble-rqt ros-humble-rqt-common-plugins xvfb xauth mesa-utils libgl1-mesa-dri

python3 -m pip install --no-deps python-fcl==0.7.0.8
