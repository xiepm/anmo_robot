"""Explicit opt-in live test: read telemetry only; no commands are published."""
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String

out=Path('/workspace/src/massage_controller/reports/robot_readonly')
rclpy.init();node=Node('readonly_telemetry_check')
joints=[];telemetry=[]
node.create_subscription(JointState,'/robot_readonly/joint_states',lambda m:joints.append(list(m.position)),10)
node.create_subscription(String,'/robot_readonly/telemetry',lambda m:telemetry.append(json.loads(m.data)),10)
with (out/'ros_readonly.log').open('w') as log:
    p=subprocess.Popen([sys.executable,'-m','massage_controller.sdk_readonly',
        '--host','192.168.0.10','--port','10003',
        '--sdk','/workspace/SDK/HUAYAN/python API接口/HuayanRobotPythonSDK_V1.1.1.1/CPS.py',
        '--output',str(out/'latest.json'),'--audit',str(out/'ros_commands.json'),
        '--jsonl',str(out/'ros_samples.jsonl'),'--count','40','--ros','--quiet'],stdout=log,stderr=log)
    try:
        deadline=time.monotonic()+12
        while p.poll() is None and time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.05)
        assert p.poll()==0,'reader failed or did not exit'
        assert len(joints)>=10 and len(telemetry)>=10
        assert all(t['valid'] and t['controller_simulation'] is False for t in telemetry)
        # Separate ROS topics are independently queued; compare the first static
        # sample set only to demonstrate units, without asserting exact pairing.
        expected=[math.radians(v) for v in telemetry[-1]['joint_positions_deg']]
        assert all(abs(a-b)<math.radians(.1) for a,b in zip(joints[-1],expected))
        result=dict(passed=True,joint_messages=len(joints),telemetry_messages=len(telemetry),
                    last_joint_positions_rad=joints[-1],last_telemetry=telemetry[-1])
        (out/'ros_readonly_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(passed=True,joint_messages=len(joints),telemetry_messages=len(telemetry))))
    finally:
        if p.poll() is None:p.terminate();p.wait(timeout=5)
        node.destroy_node();rclpy.shutdown()
