"""Gazebo, ros2_control, standalone IK and point selection. Simulation only."""
from pathlib import Path
from ament_index_python.packages import get_package_share_directory as share
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch.conditions import IfCondition
from launch_ros.actions import Node
from elfin_massage_sim.model import description


def launch_setup(context):
    hmi = LaunchConfiguration('hmi').perform(context).lower() == 'true'
    demo = hmi or LaunchConfiguration('demo').perform(context).lower() == 'true'
    sensing=LaunchConfiguration('sensing_mode').perform(context)
    human_pose=tuple(float(LaunchConfiguration(n).perform(context)) for n in ('human_x','human_y','human_z','human_yaw'))
    urdf = description('wrist' if demo else LaunchConfiguration('camera_mode').perform(context), demo,sensing,human_pose)
    camera_params = {'use_sim_time':True}
    if demo:
        camera_params.update(scene='human', camera_namespace='/camera' if sensing=='wrist' else '/global_camera', clearance=0.02,
                             auto_demo=LaunchConfiguration('auto_demo').perform(context).lower()=='true',
                             record_path=LaunchConfiguration('record_path').perform(context))
    if hmi:camera_params['sensing_mode']=sensing
    if demo and not hmi and sensing=='wrist':
        raise ValueError('仅末端模式请使用 start_hmi.sh；旧 Demo 窗口只支持双相机')
    camera_executable='massage_hmi' if hmi else ('demo_camera' if demo else 'click_camera')
    params = {'robot_description': urdf, 'use_sim_time': True}
    spawn = Node(package='gazebo_ros', executable='spawn_entity.py',
                 arguments=['-topic','robot_description','-entity','elfin5_l','-z','0'], output='screen')
    controller = Node(package='controller_manager', executable='spawner',
                      arguments=['joint_state_broadcaster','elfin_arm_controller','--controller-manager-timeout','120'], output='screen')
    return [
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(Path(share('gazebo_ros'))/'launch/gazebo.launch.py')),
            launch_arguments={'gui':LaunchConfiguration('gazebo_gui'),
                              'world':str(Path(share('elfin_massage_sim'))/'worlds/massage.world')}.items()),
        Node(package='robot_state_publisher', executable='robot_state_publisher', parameters=[params]),
        RegisterEventHandler(OnProcessExit(target_action=spawn, on_exit=[controller])),
        RegisterEventHandler(OnProcessExit(target_action=controller, on_exit=[
            Node(package='elfin_massage_sim', executable=camera_executable, parameters=[camera_params],
                 output='screen', condition=IfCondition(LaunchConfiguration('click_gui')))])),
        spawn,
        Node(package='rviz2', executable='rviz2', parameters=[params],
             arguments=['-d',str(Path(share('elfin_massage_sim'))/('config/demo.rviz' if demo else 'config/view.rviz'))],
             condition=IfCondition(LaunchConfiguration('rviz'))),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('camera_mode', default_value='global', choices=['global','wrist']),
        DeclareLaunchArgument('demo', default_value='false'),
        DeclareLaunchArgument('hmi', default_value='false'),
        DeclareLaunchArgument('sensing_mode', default_value='dual',choices=['dual','wrist']),
        DeclareLaunchArgument('human_x',default_value='0.6'),
        DeclareLaunchArgument('human_y',default_value='0.0'),
        DeclareLaunchArgument('human_z',default_value='0.4'),
        DeclareLaunchArgument('human_yaw',default_value='0.0'),
        DeclareLaunchArgument('auto_demo', default_value='false'),
        DeclareLaunchArgument('record_path', default_value=''),
        DeclareLaunchArgument('rviz', default_value='true'),
        DeclareLaunchArgument('gazebo_gui', default_value='true'),
        DeclareLaunchArgument('click_gui', default_value='true'),
        OpaqueFunction(function=launch_setup),
    ])
