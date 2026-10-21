import json
import os
from pathlib import Path
import signal
import subprocess
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

out=Path('/workspace/src/massage_controller/reports')
log=(out/'ros_smoke.log').open('w')
proc=subprocess.Popen(['ros2','run','massage_controller','control_node','--backend','simulation-test','--ui-port','18765','--vision-port','18766'],stdout=log,stderr=log,start_new_session=True)
rclpy.init();node=Node('controller_smoke_test');received=[]
node.create_subscription(String,'/control_node/status',lambda m:received.append(json.loads(m.data)),10)
pub=node.create_publisher(String,'/control_node/stop',10)
try:
    deadline=time.monotonic()+10
    while not received and time.monotonic()<deadline:
        rclpy.spin_once(node,timeout_sec=.1)
        assert proc.poll() is None,'controller exited'
    assert received,'no ROS status'
    assert received[-1]['data']['mode']=='SIMULATION'
    pub.publish(String(data='STOP'))
    while time.monotonic()<deadline:
        rclpy.spin_once(node,timeout_sec=.1)
        if received[-1]['data']['result']=='STOPPED':break
    assert received[-1]['data']['result']=='STOPPED'
    (out/'ros_smoke.json').write_text(json.dumps(dict(passed=True,received_statuses=received),ensure_ascii=False,indent=2)+'\n')
    print('ROS installed entrypoint, status topic and ordinary STOP topic: PASS')
finally:
    node.destroy_node();rclpy.shutdown()
    if proc.poll() is None:os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
    log.close()
