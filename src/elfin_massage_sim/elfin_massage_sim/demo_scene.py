"""Original prone mannequin and a second, fixed RGB-D camera."""
import xml.etree.ElementTree as ET
from pathlib import Path
from ament_index_python.packages import get_package_share_directory

DEMO_POINTS = ((.55, -.10, .40), (.65, -.02, .40), (.56, .11, .40))


def add_demo_scene(root, sensing_mode='dual', human_pose=(.6,0,.4,0)):
    if sensing_mode not in ('dual','wrist'):raise ValueError('sensing_mode must be dual or wrist')
    mesh_directory=Path(get_package_share_directory('elfin_massage_sim'))/'meshes'
    root.extend(ET.fromstring('''<extra><link name="human_frame"/>
      <joint name="human_registration" type="fixed"><parent link="world"/><child link="human_frame"/>
      <origin xyz="0.6 0 0.4" rpy="0 0 0"/></joint></extra>'''))
    registration=root.find("joint[@name='human_registration']/origin")
    registration.set('xyz',' '.join(map(str,human_pose[:3])))
    registration.set('rpy',f'0 0 {human_pose[3]}')
    tool=root.find("link[@name='massage_tool']")
    for tag in ('visual','collision'):
        tool.append(ET.fromstring(f'<{tag}><origin xyz="0 0 -0.05"/><geometry><box size="0.012 0.012 0.05"/></geometry></{tag}>'))
    # Replace the small test pad with a low treatment couch.
    for tag, name in [('link','massage_pad'),('joint','pad_fixed')]:
        root.remove(root.find(f"{tag}[@name='{name}']"))
    root.remove(root.find("gazebo[@reference='massage_pad']"))
    def part(name, xyz, geometry, material='Grey', rgba='.65 .68 .70 1', collision=True):
        parent='world'
        if name.startswith(('human_','back_')):
            parent='human_frame'
            xyz=tuple(a-b for a,b in zip(xyz,(.6,0,.4)))
        xml=f'''<extra><link name="{name}">
          <inertial><mass value="1"/><inertia ixx="0.01" iyy="0.01" izz="0.01" ixy="0" ixz="0" iyz="0"/></inertial>
          <visual><geometry>{geometry}</geometry><material name="{name}_color"><color rgba="{rgba}"/></material></visual>
          {f'<collision><geometry>{geometry}</geometry></collision>' if collision else ''}</link>
          <joint name="{name}_fixed" type="fixed"><parent link="{parent}"/><child link="{name}"/><origin xyz="{' '.join(map(str,xyz))}"/></joint>
          <gazebo reference="{name}"><material>Gazebo/{material}</material></gazebo></extra>'''
        root.extend(ET.fromstring(xml))
    def oval(name, xyz, scale, material='Grey', rgba='.65 .68 .70 1'):
        part(name,xyz,f'<mesh filename="package://elfin_massage_sim/meshes/ellipsoid.stl" scale="{scale}"/>',material,rgba)
    part('treatment_couch',(.6,.20,.19),'<box size="0.70 2.0 0.08"/>','Turquoise','.10 .60 .62 1')
    for x in (.32,.88):
        for y in (-.65,1.05):
            part(f'couch_leg_{int(x*100)}_{int(y*100)}',(x,y,.075),'<box size="0.045 0.045 0.15"/>','DarkGrey','.18 .22 .26 1')
    part('human_torso',(.6,0,.33),'<mesh filename="package://elfin_massage_sim/meshes/torso.stl"/>','White','.83 .85 .87 1')
    # Visible landmarks on the mannequin's flat back; included in collision geometry.
    part('back_center_line',(.6,0,.40025),'<box size="0.006 0.27 0.0005"/>','Grey','.45 .48 .50 1')
    for i,point in enumerate(DEMO_POINTS,1):
        material,rgba=[('Orange','1 .45 .12 1'),('Green','.1 .8 .1 1'),('Blue','.1 .2 1 1')][i-1]
        oval(f'back_target_{i}',(point[0],point[1],.4005),'.019 .019 .0005',material,rgba)
    oval('human_neck',(.6,-.30,.32),'.055 .085 .065')
    oval('human_head',(.6,-.44,.325),'.10 .125 .09')
    oval('human_pelvis',(.6,.30,.315),'.16 .16 .085','DarkGrey','.22 .27 .32 1')
    for side,x in [('left',.51),('right',.69)]:
        oval('human_'+side+'_thigh',(x,.53,.30),'.072 .23 .070','DarkGrey','.22 .27 .32 1')
        oval('human_'+side+'_calf',(x,.86,.29),'.058 .19 .060','DarkGrey','.22 .27 .32 1')
        oval('human_'+side+'_foot',(x,1.065,.275),'.061 .105 .045')
    for side,x in [('left',.35),('right',.85)]:
        oval('human_'+side+'_upper_arm',(x,-.04,.30),'.050 .20 .060')
        oval('human_'+side+'_forearm',(x,.27,.285),'.044 .18 .045')
        oval('human_'+side+'_hand',(x,.45,.277),'.047 .075 .035')
    # Reuse the original depth sensor definition with independent names, topics and TF.
    camera=root.find("link[@name='camera_link']")
    root.find("gazebo[@reference='camera_link']/sensor/camera/horizontal_fov").text='1.6'
    global_body=ET.fromstring(ET.tostring(camera))
    global_body.set('name','global_camera_link');root.append(global_body)
    root.extend(ET.fromstring('''<extra>
      <joint name="global_camera_mount" type="fixed"><parent link="world"/><child link="global_camera_link"/><origin xyz="0.45 0.20 2.35" rpy="0 1.57079632679 0"/></joint>
      <link name="global_camera_optical_frame"/>
      <joint name="global_camera_optical_joint" type="fixed"><parent link="global_camera_link"/><child link="global_camera_optical_frame"/><origin rpy="-1.57079632679 0 -1.57079632679"/></joint>
    </extra>'''))
    sensor=ET.fromstring(ET.tostring(root.find("gazebo[@reference='camera_link']")))
    sensor.set('reference','global_camera_link')
    sensor.find('sensor').set('name','global_rgbd')
    sensor.find('sensor/camera/horizontal_fov').text='1.25'
    plugin=sensor.find('sensor/plugin');plugin.set('name','global_rgbd_ros')
    plugin.find('ros/namespace').text='/global_camera'
    plugin.find('frame_name').text='global_camera_optical_frame'
    root.append(sensor)
    if sensing_mode=='wrist':
        for tag,name in [('link','global_camera_link'),('link','global_camera_optical_frame'),
                         ('joint','global_camera_mount'),('joint','global_camera_optical_joint')]:
            root.remove(root.find(f"{tag}[@name='{name}']"))
        root.remove(sensor)
    for mesh in root.findall('.//mesh'):
        filename=mesh.get('filename','')
        if filename.startswith('package://elfin_massage_sim/meshes/'):
            mesh.set('filename',(mesh_directory/filename.rsplit('/',1)[1]).as_uri())
