import unittest
import xml.etree.ElementTree as ET
from elfin_massage_sim.model import augment


class ModelTests(unittest.TestCase):
    def model(self, mode):
        urdf = augment('<robot name="elfin5_l"><link name="world"/></robot>', mode)
        return ET.fromstring(urdf)

    def test_mounts(self):
        for mode, parent in (('global', 'world'), ('wrist', 'elfin_end_link')):
            root = self.model(mode)
            self.assertEqual(root.find("joint[@name='camera_mount']/parent").get('link'), parent)
            self.assertIsNotNone(root.find("link[@name='massage_tcp']"))
            self.assertIsNotNone(root.find("link[@name='massage_pad']/collision"))
            self.assertIsNotNone(root.find("link[@name='massage_tool']/collision"))
            plugin = root.find("gazebo[@reference='camera_link']/sensor/plugin")
            self.assertIn('rgbd/depth/image_raw:=depth/image_raw', [e.text for e in plugin.findall('ros/remapping')])

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            self.model('bad')
