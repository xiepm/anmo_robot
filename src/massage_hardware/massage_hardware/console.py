"""Read-only commissioning console, intentionally separate from simulator motion controls."""
import json,time
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image,CameraInfo,JointState
from std_msgs.msg import String
from cv_bridge import CvBridge
from PyQt5.QtCore import Qt,QTimer
from PyQt5.QtGui import QImage,QPixmap
from PyQt5.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QPlainTextEdit,QHBoxLayout
from .profile import load_profile,ordered_positions,readiness_gaps

class HardwareMonitor(Node):
    def __init__(self):
        super().__init__('massage_hardware_monitor');self.declare_parameter('profile','')
        self.profile=load_profile(self.get_parameter('profile').value)
        self.rgb=None;self.depth=None;self.info=None;self.joints=None;self.io=None;self.arrivals={};self.bridge=CvBridge()
        c,r,io=self.profile['camera'],self.profile['robot'],self.profile['io']
        self.create_subscription(Image,c['rgb_topic'],lambda m:self.receive('rgb',m),qos_profile_sensor_data)
        self.create_subscription(Image,c['depth_topic'],lambda m:self.receive('depth',m),qos_profile_sensor_data)
        self.create_subscription(CameraInfo,c['info_topic'],lambda m:self.receive('info',m),qos_profile_sensor_data)
        self.create_subscription(JointState,r['joint_states_topic'],self.receive_joints,qos_profile_sensor_data)
        self.create_subscription(String,io['state_topic'],self.receive_io,10)
    def receive(self,name,message):setattr(self,name,message);self.arrivals[name]=time.monotonic()
    def receive_joints(self,message):
        try:self.receive('joints',ordered_positions(self.profile['robot']['joint_names'],message.name,message.position))
        except ValueError as exc:self.get_logger().warning(str(exc))
    def receive_io(self,message):
        try:
            value=json.loads(message.data)
            if not isinstance(value,dict) or any(type(value.get(k)) is not bool for k in ('start','stop','reset_request','safety_ok_feedback')):
                raise ValueError('Expected four boolean IO states')
            self.receive('io',value)
        except (ValueError,TypeError):self.get_logger().warning('Invalid IO feedback; discarded')
    def fresh(self,key,age=.7):return time.monotonic()-self.arrivals.get(key,0)<age

class Console(QWidget):
    def __init__(self,node):
        super().__init__();self.node=node;self.last_rgb=None
        self.setWindowTitle('按摩机器人 · 实机设备接入');self.resize(1080,720)
        self.setStyleSheet('QWidget{background:#14212b;color:#e0eaf1;font-size:15px;} QPlainTextEdit{background:#1c303e;}')
        layout=QVBoxLayout(self);title=QLabel('实机设备接入  /  D435i · 机械臂 · 按钮 IO');title.setStyleSheet('font-size:24px;padding:10px;');layout.addWidget(title)
        layout.addWidget(QLabel('只读联调：不使能机械臂、不回零、不执行运动；相机外参和控制柜接口待确认。'))
        row=QHBoxLayout();layout.addLayout(row,1);self.image=QLabel('等待 D435i 彩色图像');self.image.setAlignment(Qt.AlignCenter);self.image.setMinimumSize(540,400);row.addWidget(self.image,3)
        self.state=QPlainTextEdit();self.state.setReadOnly(True);row.addWidget(self.state,2)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(200)
        self.spin_timer=QTimer(self);self.spin_timer.timeout.connect(self.spin);self.spin_timer.start(10)
    def spin(self):
        if not rclpy.ok():QApplication.quit();return
        end=time.monotonic()+.008
        for _ in range(50):
            rclpy.spin_once(self.node,timeout_sec=0)
            if time.monotonic()>end:break
    def refresh(self):
        n=self.node;c=n.profile['camera'];r=n.profile['robot'];lines=['D435i / USB','彩色：'+('收到' if n.fresh('rgb') else '未连接 / 过期'),
            '对齐深度：'+('收到' if n.fresh('depth') else '未连接 / 过期'),'内参：'+('收到' if n.fresh('info') else '未连接 / 过期')]
        if n.fresh('rgb') and n.rgb is not self.last_rgb:
            try:
                data=n.bridge.imgmsg_to_cv2(n.rgb,'rgb8');im=QImage(data.data,data.shape[1],data.shape[0],data.strides[0],QImage.Format_RGB888).copy()
                self.image.setPixmap(QPixmap.fromImage(im).scaled(self.image.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation));self.last_rgb=n.rgb
            except Exception as exc:self.image.setText('图像格式错误：'+str(exc))
        elif not n.fresh('rgb'):self.image.clear();self.image.setText('等待 D435i 彩色图像（未连接或过期）');self.last_rgb=None
        if n.fresh('depth') and n.fresh('info'):
            aligned=(n.depth.width,n.depth.height)==(n.info.width,n.info.height) and n.depth.header.frame_id==n.info.header.frame_id
            lines+=['深度 / 内参坐标检查：'+('匹配' if aligned else '不匹配'),'光学坐标：'+n.depth.header.frame_id]
        lines+=['','机械臂：'+r['model'],'驱动：'+('待确认控制柜协议' if r['adapter']=='pending_vendor_confirmation' else r['adapter']),'关节反馈：'+('收到（rad / m）' if n.fresh('joints') else '未连接 / 过期')]
        if n.fresh('joints'):lines+=['  '+name+': %.4f'%q for name,q in zip(r['joint_names'],n.joints)]
        lines+=['','按钮 IO：'+('收到' if n.fresh('io',n.profile['io']['stale_after_s']) else '未接入 / 过期')]
        if n.fresh('io',n.profile['io']['stale_after_s']):lines.append(json.dumps(n.io,ensure_ascii=False))
        lines+=['','接入待办']+['• '+s for s in readiness_gaps(n.profile)]
        lines+=['','运动状态：未启用（本界面没有运动发送接口）']
        self.state.setPlainText('\n'.join(lines))

def main(args=None):
    rclpy.init(args=args);app=QApplication([]);node=HardwareMonitor();window=Console(node);window.show()
    try:app.exec_()
    finally:window.timer.stop();window.spin_timer.stop();node.destroy_node();rclpy.shutdown()
