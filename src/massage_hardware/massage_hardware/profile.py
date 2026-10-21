"""Explicit robot bindings; no inferred cabinet protocol or simulator calibration."""
from pathlib import Path
import math
import yaml

def load_profile(filename):
    data=yaml.safe_load(Path(filename).read_text())
    if not isinstance(data,dict) or data.get('version')!=1:raise ValueError('Unsupported hardware profile')
    if data.get('mode')!='commissioning_read_only':raise ValueError('This bringup supports read-only commissioning only')
    for section in ('camera','robot','io'):
        if not isinstance(data.get(section),dict):raise ValueError('Missing '+section)
    camera=data['camera'];robot=data['robot'];io=data['io']
    if camera.get('model')!='D435i':raise ValueError('This camera bringup targets D435i')
    if camera.get('mount') not in ('unconfirmed','wrist','fixed'):raise ValueError('Invalid camera mount')
    names=robot.get('joint_names')
    if not isinstance(names,list) or not names or any(not isinstance(n,str) or not n for n in names) or len(set(names))!=len(names):
        raise ValueError('Joint names must be unique, non-empty strings in command order')
    for item,key in [(camera,'rgb_topic'),(camera,'depth_topic'),(camera,'info_topic'),(robot,'joint_states_topic'),(robot,'trajectory_action'),(io,'state_topic')]:
        if not isinstance(item.get(key),str) or not item[key].startswith('/'):raise ValueError('Absolute ROS name required: '+key)
    if not 0<float(io.get('stale_after_s',0))<=5:raise ValueError('Invalid IO timeout')
    q=robot.get('observation_joints',[])
    if q and (len(q)!=len(names) or not all(math.isfinite(float(v)) for v in q)):raise ValueError('Observation pose does not match joint order')
    return data

def ordered_positions(names,message_names,positions):
    if len(message_names)!=len(positions) or len(set(message_names))!=len(message_names):raise ValueError('Invalid JointState layout')
    mapping=dict(zip(message_names,positions))
    if any(n not in mapping for n in names):raise ValueError('JointState does not contain this robot')
    values=[float(mapping[n]) for n in names]
    if not all(math.isfinite(v) for v in values):raise ValueError('Non-finite joint feedback')
    return values

def readiness_gaps(profile):
    c,r,io=profile['camera'],profile['robot'],profile['io'];gaps=[]
    if c['mount']=='unconfirmed':gaps.append('相机安装位置未确认')
    if not c.get('serial'):gaps.append('未绑定相机序列号')
    if not c.get('extrinsic_file'):gaps.append('缺少实测相机外参')
    if r['adapter']=='pending_vendor_confirmation':gaps.append('控制柜协议 / 驱动未确认')
    if not r.get('urdf_file'):gaps.append('缺少确认后的实机 URDF / TCP')
    if not r.get('observation_joints'):gaps.append('未示教并验证实机观察位')
    if io.get('adapter')=='unselected':gaps.append('按钮 IO 模块未选定')
    return gaps
