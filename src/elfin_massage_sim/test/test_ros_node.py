import unittest
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import cv2
import rclpy
from elfin_massage_sim.click_camera import ClickCamera


class RosNodeTests(unittest.TestCase):
    def test_observation_keys_and_mouse_button(self):
        node = SimpleNamespace(motion=Mock(), displayed=(SimpleNamespace(height=480),))
        node.handle_key = lambda key: ClickCamera.handle_key(node, key)
        for key in (ord('v'), ord('V')):
            node.handle_key(key)
        ClickCamera.click(node, cv2.EVENT_LBUTTONDOWN, 80, 505, 0, None)
        self.assertEqual(node.motion.start.call_count, 3)
        node.motion.start.assert_called_with('observe')
        ClickCamera.click(node, cv2.EVENT_LBUTTONDOWN, 480, 505, 0, None)
        node.motion.cancel.assert_called_once()

    def test_direct_controller_client_and_no_planning_imports(self):
        rclpy.init()
        node=ClickCamera(gui=False)
        try:
            self.assertIsNotNone(node.handle)
            self.assertIsNone(node.motion.goal_handle)
            self.assertFalse(node.motion.busy)
            self.assertFalse(any(name.startswith(('moveit','moveit_msgs')) for name in sys.modules))
            node.motion.model = object()
            node.motion.q = [0.] * 6
            node.motion.joint_received = time.monotonic()-2.
            self.assertFalse(node.motion.start('observe'))
            self.assertIn('Gazebo Play', node.status)
            self.assertFalse(node.motion.busy)
        finally:
            node.motion.close();node.destroy_node();rclpy.shutdown()
