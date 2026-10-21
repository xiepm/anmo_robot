"""Explicit real-device read-only integration: one control process + passive TF display."""
import asyncio
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage
from websockets.legacy.client import connect

out=Path('/workspace/src/massage_controller/reports/integrated_real');out.mkdir(exist_ok=True)
log=(out/'processes.log').open('w')
processes=[]
rclpy.init();node=Node('integrated_real_test')
joints={};telemetry={};tf=[];status=[]
def joint(m):joints[m.header.stamp.sec*10**9+m.header.stamp.nanosec]=dict(names=list(m.name),position=list(m.position))
def tele(m):
    value=json.loads(m.data)
    if value['valid']:telemetry[value['received_unix_ns']]=value
node.create_subscription(JointState,'/robot/joint_states',joint,100)
node.create_subscription(String,'/control_real_test/robot_telemetry',tele,100)
node.create_subscription(String,'/control_real_test/status',lambda m:status.append(json.loads(m.data)),100)
node.create_subscription(TFMessage,'/robot_mirror/tf',lambda m:tf.extend([x.child_frame_id for x in m.transforms]),100)
async def query():
    async with connect('ws://127.0.0.1:18765') as ws:
        async def req(cmd):
            await ws.send(json.dumps(dict(cmd=cmd,data={})))
            while True:
                m=json.loads(await ws.recv())
                if (cmd=='GET_STATUS' and m['type']=='STATUS') or (m['type']=='REPLY' and m['cmd']==cmd):return m
        current=await req('GET_STATUS');started=time.monotonic()
        start=await req('START');stop=await req('STOP')
        return dict(status=current,start_reply=start,stop_reply=stop,commands_elapsed_ms=(time.monotonic()-started)*1000)
try:
    processes.append(subprocess.Popen([sys.executable,'-m','massage_controller.node','--ui-port','18765','--vision-port','18766',
        '--ros-args','-r','__node:=control_real_test'],stdout=log,stderr=log,start_new_session=True))
    processes.append(subprocess.Popen(['ros2','launch','massage_controller','real_mirror.launch.py','rviz:=false'],stdout=log,stderr=log,start_new_session=True))
    end=time.monotonic()+7
    while time.monotonic()<end:
        rclpy.spin_once(node,timeout_sec=.02)
        assert all(p.poll() is None for p in processes),'process exited'
    assert len(joints)>30 and len(telemetry)>30,(len(joints),len(telemetry))
    matched=set(joints)&set(telemetry);assert len(matched)>30
    max_error=0
    for stamp in matched:
        t,j=telemetry[stamp],joints[stamp]
        assert t['controller_simulation'] is False and t['motion_enabled'] is False
        assert j['names']==[f'elfin_joint{i}' for i in range(1,7)]
        error=max(abs(a-math.radians(b)) for a,b in zip(j['position'],t['joint_positions_deg']))
        max_error=max(max_error,error)
    assert max_error<1e-12
    assert 'mirror_elfin_link6' in tf,set(tf)
    result=asyncio.run(query())
    assert result['status']['data']['mode']=='REAL'
    assert result['status']['data']['robot_feedback_valid']
    assert not result['start_reply']['ok'] and not result['stop_reply']['ok']
    assert result['commands_elapsed_ms']<200
    result.update(passed=True,matched_real_joint_samples=len(matched),max_angle_error_rad=max_error,
                  tf_children=sorted(set(tf)),last_telemetry=telemetry[max(telemetry)],
                  joint_publishers=[p.node_name for p in node.get_publishers_info_by_topic('/robot/joint_states')])
    assert result['joint_publishers']==['control_real_test'],result['joint_publishers']
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('passed','matched_real_joint_samples','max_angle_error_rad','commands_elapsed_ms','joint_publishers')}))
finally:
    for p in processes:
        if p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
    for p in processes:
        try:p.wait(timeout=6)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    node.destroy_node();rclpy.shutdown();log.close()
