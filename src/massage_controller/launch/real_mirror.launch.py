"""Read-only RViz mirror. No Gazebo, trajectory controller or synthetic joint publisher."""
from pathlib import Path
import xml.etree.ElementTree as ET
from ament_index_python.packages import get_package_share_directory as share
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import xacro


def mirror_description():
    source = Path(share('elfin5_l_ros2_gazebo'))/'urdf/elfin5_l.urdf.xacro'
    root = ET.fromstring(xacro.process_file(str(source), mappings={
        'use_real_hardware':'false','use_fake_hardware':'false'}).toxml())
    for child in list(root):
        if child.tag not in ('link','joint','material'):
            root.remove(child)
    # Display frames are deliberately separate from unverified robot base/TCP frames.
    for link in root.findall('link'): link.set('name', 'mirror_'+link.get('name'))
    for joint in root.findall('joint'):
        for endpoint in ('parent','child'):
            e=joint.find(endpoint);e.set('link','mirror_'+e.get('link'))
    return ET.tostring(root, encoding='unicode')


def setup(context):
    result=[Node(package='robot_state_publisher', executable='robot_state_publisher',
        name='robot_state_publisher', namespace='robot_mirror',
        parameters=[{'robot_description':mirror_description(),'use_sim_time':False,'publish_frequency':30.}],
        remappings=[('joint_states','/robot/joint_states'),('/tf','/robot_mirror/tf'),('/tf_static','/robot_mirror/tf_static')],
        output='screen')]
    if LaunchConfiguration('rviz').perform(context).lower()=='true':
        result.append(Node(package='rviz2', executable='rviz2', name='rviz', namespace='robot_mirror',
            arguments=['-d',str(Path(share('massage_controller'))/'config/real_mirror.rviz')],
            parameters=[{'use_sim_time':False}],
            remappings=[('/tf','/robot_mirror/tf'),('/tf_static','/robot_mirror/tf_static')]))
    return result


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('rviz',default_value='true'),OpaqueFunction(function=setup)])
