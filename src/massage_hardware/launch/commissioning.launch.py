"""D435i + read-only desktop console. Never loads a robot hardware driver."""
from pathlib import Path
from ament_index_python.packages import get_package_share_directory as share
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument,OpaqueFunction,IncludeLaunchDescription,GroupAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from massage_hardware.profile import load_profile

def setup(context):
    filename=LaunchConfiguration('profile').perform(context);p=load_profile(filename);c=p['camera'];actions=[]
    if LaunchConfiguration('camera').perform(context).lower()=='true':
        args={'camera_namespace':c['namespace'].strip('/'),'camera_name':c['name'],
              'device_type':'d435i','enable_color':'true','enable_depth':'true',
              'align_depth.enable':'true','enable_sync':'true','enable_gyro':'false','enable_accel':'false',
              'pointcloud.enable':'false','depth_module.depth_profile':c['depth_profile'],
              'rgb_camera.color_profile':c['color_profile'],'publish_tf':'true'}
        if c['serial']:args['serial_no']='_'+str(c['serial']).lstrip('_')
        actions.append(GroupAction(scoped=True,forwarding=False,actions=[IncludeLaunchDescription(PythonLaunchDescriptionSource(str(Path(share('realsense2_camera'))/'launch/rs_launch.py')),launch_arguments=args.items())]))
    if LaunchConfiguration('ui').perform(context).lower()=='true':
        actions.append(Node(package='massage_hardware',executable='hardware_console',parameters=[{'profile':filename,'use_sim_time':False}],output='screen'))
    return actions

def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('profile',default_value=str(Path(share('massage_hardware'))/'config/workstation.yaml')),
        DeclareLaunchArgument('camera',default_value='true',choices=['true','false']),
        DeclareLaunchArgument('ui',default_value='true',choices=['true','false']),OpaqueFunction(function=setup)])
