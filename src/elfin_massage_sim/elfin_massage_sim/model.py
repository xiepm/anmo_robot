"""Augment one upstream robot description, shared by Gazebo, TF and independent kinematics."""
import xml.etree.ElementTree as ET


def augment(urdf, mode, demo=False, sensing_mode='dual', human_pose=(.6,0,.4,0)):
    if mode not in ('global', 'wrist'):
        raise ValueError('camera_mode must be global or wrist')
    root = ET.fromstring(urdf)
    parent, xyz, rpy = ('world', '0.6 0 1.5', '0 1.57079632679 0') if mode == 'global' else (
        'elfin_end_link', '0.08 0 0.02', '0 -1.57079632679 0')
    # Camera body convention: +X forward; optical: +Z forward, +X right, +Y down.
    additions = f'''<extra>
      <link name="camera_link"><inertial><mass value="0.1"/><inertia ixx="0.0001" iyy="0.0001" izz="0.0001" ixy="0" ixz="0" iyz="0"/></inertial>
        <visual><geometry><box size="0.04 0.06 0.04"/></geometry></visual>
        <collision><geometry><box size="0.04 0.06 0.04"/></geometry></collision></link>
      <joint name="camera_mount" type="fixed"><parent link="{parent}"/><child link="camera_link"/><origin xyz="{xyz}" rpy="{rpy}"/></joint>
      <link name="camera_optical_frame"/>
      <joint name="camera_optical_joint" type="fixed"><parent link="camera_link"/><child link="camera_optical_frame"/><origin rpy="-1.57079632679 0 -1.57079632679"/></joint>
      <gazebo reference="camera_link"><sensor name="rgbd" type="depth"><always_on>true</always_on><update_rate>15</update_rate>
        <camera><horizontal_fov>1.0471975512</horizontal_fov><image><width>640</width><height>480</height><format>R8G8B8</format></image><clip><near>0.05</near><far>4</far></clip></camera>
        <plugin name="rgbd_ros" filename="libgazebo_ros_camera.so"><ros><namespace>/camera</namespace><remapping>rgbd/image_raw:=image_raw</remapping><remapping>rgbd/camera_info:=camera_info</remapping><remapping>rgbd/depth/image_raw:=depth/image_raw</remapping><remapping>rgbd/depth/camera_info:=depth/camera_info</remapping><remapping>rgbd/points:=points</remapping></ros><camera_name>rgbd</camera_name><frame_name>camera_optical_frame</frame_name><min_depth>0.05</min_depth><max_depth>4.0</max_depth></plugin>
      </sensor></gazebo>
      <link name="massage_tool"><inertial><mass value="0.05"/><inertia ixx="0.0001" iyy="0.0001" izz="0.0001" ixy="0" ixz="0" iyz="0"/></inertial>
        <visual><geometry><sphere radius="0.025"/></geometry></visual>
        <collision><geometry><sphere radius="0.025"/></geometry></collision></link>
      <joint name="tool_mount" type="fixed"><parent link="elfin_end_link"/><child link="massage_tool"/><origin xyz="0 0 0.075"/></joint>
      <link name="massage_tcp"/><joint name="tcp_joint" type="fixed"><parent link="massage_tool"/><child link="massage_tcp"/><origin xyz="0 0 0.025"/></joint>
      <link name="floor_guard"><inertial><mass value="1"/><inertia ixx="1.34" iyy="1.34" izz="2.67" ixy="0" ixz="0" iyz="0"/></inertial><collision><geometry><box size="4 4 0.098"/></geometry></collision></link>
      <joint name="floor_guard_fixed" type="fixed"><parent link="world"/><child link="floor_guard"/><origin xyz="0 0 -0.05"/></joint>
      <link name="massage_pad"><inertial><mass value="10"/><inertia ixx="0.341667" iyy="0.341667" izz="0.416667" ixy="0" ixz="0" iyz="0"/></inertial><visual><geometry><box size="0.5 0.5 0.4"/></geometry><material name="blue"><color rgba="0.1 0.4 0.8 1"/></material></visual>
        <collision><geometry><box size="0.5 0.5 0.4"/></geometry></collision></link>
      <joint name="pad_fixed" type="fixed"><parent link="world"/><child link="massage_pad"/><origin xyz="0.6 0 0.2"/></joint>
      <gazebo reference="massage_pad"><material>Gazebo/Blue</material></gazebo>
    </extra>'''
    root.extend(ET.fromstring(additions))
    if demo:
        from .demo_scene import add_demo_scene
        add_demo_scene(root, sensing_mode, human_pose)
    return ET.tostring(root, encoding='unicode')


def description(mode='global', demo=False, sensing_mode='dual', human_pose=(.6,0,.4,0)):
    from pathlib import Path
    import xacro
    from ament_index_python.packages import get_package_share_directory
    path = Path(get_package_share_directory('elfin5_l_ros2_gazebo')) / 'urdf/elfin5_l.urdf.xacro'
    urdf = xacro.process_file(str(path), mappings={
        'use_real_hardware': 'false', 'use_fake_hardware': 'false'}).toxml()
    return augment(urdf, mode, demo, sensing_mode, human_pose)
