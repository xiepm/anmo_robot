#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec scripts/runtime.sh /bin/bash -c '
  set -e
  python3 -c "import rclpy, cv2, xacro; import scipy, fcl; from control_msgs.action import FollowJointTrajectory; print(\"ROS Python interfaces OK\")"
  dpkg-query -W gazebo
  ros2 pkg prefix gazebo_ros2_control
  ros2 pkg prefix joint_trajectory_controller
  glxinfo -B
'
