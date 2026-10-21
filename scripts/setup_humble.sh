#!/usr/bin/env bash
set -euo pipefail
source /etc/os-release
if [[ "$VERSION_ID" != 22.04 ]]; then
  echo 'This script requires Ubuntu 22.04 (native or VM). The current host is not modified.' >&2
  exit 1
fi
if [[ ! -f /opt/ros/humble/setup.bash ]]; then
  echo 'Install ROS 2 Humble desktop first: https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html' >&2
  exit 1
fi
sudo apt-get update
sudo apt-get install -y python3-colcon-common-extensions python3-rosdep python3-opencv python3-scipy python3-pip cython3 python3-cairo python3-pytest \
  ros-humble-gazebo-ros-pkgs ros-humble-gazebo-ros2-control \
  ros-humble-ros2-controllers ros-humble-ros2controlcli ros-humble-xacro \
  ros-humble-cv-bridge ros-humble-message-filters ros-humble-tf2-geometry-msgs python3-pyqt5 fonts-noto-cjk
python3 -m pip install --user --no-deps python-fcl==0.7.0.8
source /opt/ros/humble/setup.bash
cd "$(dirname "$0")/.."
colcon build --symlink-install --packages-select elfin_description elfin5_l_ros2_gazebo elfin_massage_sim --cmake-args -DBUILD_TESTING=OFF
printf 'Build finished. Run: source install/setup.bash\nros2 launch elfin_massage_sim sim.launch.py\n'
