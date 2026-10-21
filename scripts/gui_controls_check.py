#!/usr/bin/env python3
"""GUI smoke check. Run on a test DISPLAY with click_gui:=false simulation."""
import json
import subprocess
import time
from pathlib import Path
import cv2
import numpy as np
import rclpy
from std_srvs.srv import Empty
from elfin_massage_sim.click_camera import ClickCamera

rclpy.init(args=['--ros-args', '-p', 'use_sim_time:=true'])
node = ClickCamera(gui=True)
report = {'passed': False}
out = Path('/workspace/reports/independent')
def wait(predicate, timeout=45):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        rclpy.spin_once(node, timeout_sec=.02)
        if predicate(): return
    raise AssertionError(node.status)
def spin_for(seconds):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline: rclpy.spin_once(node, timeout_sec=.02)
def physics(name):
    client=node.create_client(Empty, '/'+name+'_physics')
    assert client.wait_for_service(timeout_sec=5)
    future=client.call_async(Empty.Request())
    wait(future.done, 5);future.result();node.destroy_client(client)
def press_button():
    subprocess.run(['xdotool', 'mousemove', '--window', window, '80', '505', 'click', '1'],check=True)
try:
    wait(lambda: node.motion.ready() and node.displayed is not None)
    window=subprocess.check_output(['xdotool','search','--onlyvisible','--name','^Elfin massage RGB-D$'],text=True).splitlines()[-1]
    physics('pause');spin_for(1.3)
    press_button();spin_for(.3)
    assert not node.motion.busy and 'Gazebo Play' in node.status, node.status
    report['paused_click_rejected']=True
    physics('unpause');wait(node.motion.ready)
    press_button();wait(lambda: node.motion.busy,5)
    wait(lambda: not node.motion.busy)
    assert node.motion.phase=='COMPLETE',node.status
    tcp=node.motion.model.fk(node.motion.q)[:3,3]
    assert np.linalg.norm(tcp-[.6,0,.65])<.005
    report['mouse_observation_tcp_m']=tcp.tolist()
    # Exercise real uppercase X keyboard input while the GTK camera has focus.
    subprocess.run(['xdotool','windowfocus','--sync',window,'key','V'],check=True)
    wait(lambda:node.motion.busy,5)
    wait(lambda:not node.motion.busy)
    assert node.motion.phase=='COMPLETE'
    report['uppercase_v_completed']=True
    # Capture the actual rendered window, including toolbar.
    from PyQt5.QtWidgets import QApplication
    app=QApplication([])
    app.primaryScreen().grabWindow(int(window)).save(str(out/'wrist-controls.png'))
    report['passed']=True
finally:
    if node.motion.busy:
        node.motion.cancel();spin_for(2.)
    # Always leave the test simulator running rather than paused.
    physics('unpause')
    (out/'gui-controls.json').write_text(json.dumps(report,indent=2))
    node.motion.close();node.destroy_node();cv2.destroyAllWindows()
    if rclpy.ok():rclpy.shutdown()
print(json.dumps(report,indent=2))
