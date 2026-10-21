"""Two-camera demonstration console driven by real Gazebo RGB-D and joint feedback."""
import json
import time
from pathlib import Path
import cv2
import numpy as np
import rclpy
from rclpy.time import Time
from rclpy.clock import Clock, ClockType
from rclpy.qos import qos_profile_sensor_data
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import Image
from geometry_msgs.msg import PointStamped
from tf2_geometry_msgs import do_transform_point
from .click_camera import ClickCamera
from .geometry import deproject, approach
from .demo_scene import DEMO_POINTS

WINDOW='Elfin | Massage demonstration'
BG=(27,23,19); PANEL=(44,38,31); TEXT=(239,234,224); MUTED=(168,155,140); ACCENT=(198,211,72)

class DemoCamera(ClickCamera):
    def __init__(self):
        super().__init__(gui=False)
        self.status='Waiting for both cameras and the trajectory controller'
        self.declare_parameter('auto_demo',False)
        self.declare_parameter('record_path','')
        self.wrist=None
        self.create_subscription(Image,'/camera/image_raw',self.receive_wrist,qos_profile_sensor_data)
        self.demo_active=False;self.auto_attempted=False;self.demo_started=0.
        self.measured=[];self.samples=[];self.recorder=None;self.last_video_frame=0.
        self.last_demo_result='';self.ready_since=None
        cv2.namedWindow(WINDOW,cv2.WINDOW_AUTOSIZE);cv2.setMouseCallback(WINDOW,self.mouse)
        self.create_timer(.05,self.draw,clock=Clock(clock_type=ClockType.STEADY_TIME))

    def receive_wrist(self,msg):
        self.wrist=msg

    def receive(self,rgb,depth,info):
        first=self.latest is None
        super().receive(rgb,depth,info)
        if first and self.latest is not None:
            self.status='Ready. START DEMO runs all three points; click the central back for a single approach.'

    def pixel(self, point, snapshot):
        _,depth,info=snapshot
        p=PointStamped();p.header.frame_id='world';p.header.stamp=depth.header.stamp
        p.point.x,p.point.y,p.point.z=point
        tf=self.tf.lookup_transform(info.header.frame_id,'world',Time.from_msg(depth.header.stamp))
        p=do_transform_point(p,tf).point
        if p.z<=0:raise ValueError('Point behind camera')
        return round(info.k[0]*p.x/p.z+info.k[2]),round(info.k[4]*p.y/p.z+info.k[5])

    def measured_points(self):
        if self.latest is None:raise ValueError('Waiting for global RGB-D')
        snapshot=self.latest
        _,depth,info=snapshot
        age=(self.get_clock().now()-Time.from_msg(depth.header.stamp)).nanoseconds/1e9
        if not -.05 <= age <= .5:raise ValueError('Global RGB-D stale; check Gazebo Play')
        array=self.bridge.imgmsg_to_cv2(depth,'passthrough')
        tf=self.tf.lookup_transform('world',depth.header.frame_id,Time.from_msg(depth.header.stamp))
        measured=[]
        for point in DEMO_POINTS:
            u,v=self.pixel(point,snapshot)
            if not (0<=u<depth.width and 0<=v<depth.height):raise ValueError('Demo point outside global camera')
            z=float(array[v,u])*(.001 if depth.encoding=='16UC1' else 1.)
            p=PointStamped(header=depth.header)
            p.point.x,p.point.y,p.point.z=deproject(u,v,z,info.k,depth.width,depth.height)
            p=do_transform_point(p,tf).point
            actual=(p.x,p.y,p.z)
            approach(actual,.02,'human')
            if np.linalg.norm(np.asarray(actual)-point)>.015:raise ValueError('Back point occluded; return to view pose')
            measured.append(actual)
        return measured

    def start_demo(self):
        if self.motion.busy or self.demo_active:
            self.notice('Motion busy; STOP cancels');return
        try:
            points=self.measured_points()
            if not self.motion.start('demo',points):return
            self.measured=points;self.samples=[];self.demo_active=True
            self.demo_started=time.monotonic();self.last_demo_result=''
            path=self.get_parameter('record_path').value
            if path:
                Path(path).parent.mkdir(parents=True,exist_ok=True)
                self.recorder=cv2.VideoWriter(path,cv2.VideoWriter_fourcc(*'mp4v'),10,(1120,720))
                if not self.recorder.isOpened():
                    self.recorder.release();self.recorder=None
                    self.get_logger().error('Video recorder could not open '+path)
        except Exception as exc:
            self.notice(str(exc))

    def mouse(self,event,x,y,flags,data):
        if event!=cv2.EVENT_LBUTTONDOWN:return
        if 620<=y<=670:
            if 20<=x<=244:self.start_demo()
            elif 258<=x<=462:self.handle_key(ord('v'))
            elif 476<=x<=660:self.motion.cancel()
        elif 20<=x<660 and 115<=y<595:
            self.click(event,x-20,y-115,flags,data)

    def draw(self):
        canvas=np.full((720,1120,3),BG,np.uint8)
        def text(s,x,y,size=.55,color=TEXT):
            cv2.putText(canvas,str(s),(x,y),cv2.FONT_HERSHEY_SIMPLEX,size,color,1,cv2.LINE_AA)
        def box(x,y,w,h,color=PANEL):cv2.rectangle(canvas,(x,y),(x+w,y+h),color,-1)
        text('ELFIN  /  MASSAGE LAB',20,40,.90)
        text('DUAL CAMERA DEMONSTRATION',760,38,.52,ACCENT)
        text('Prone mannequin  /  3 back locations  /  Independent robot motion',20,72,.52,MUTED)
        text('01  GLOBAL RGB-D  -  SELECT THE BACK',20,102,.51,ACCENT)
        text('02  WRIST CAMERA',684,102,.51,ACCENT)
        box(20,115,640,480);box(684,115,416,312)
        if self.latest is not None:
            self.displayed=self.latest
            frame=self.bridge.imgmsg_to_cv2(self.latest[0],'bgr8').copy()
            for i,p in enumerate(DEMO_POINTS,1):
                try:
                    u,v=self.pixel(p,self.latest)
                    cv2.circle(frame,(u,v),9,ACCENT,2,cv2.LINE_AA)
                    cv2.putText(frame,str(i),(u+12,v+4),cv2.FONT_HERSHEY_SIMPLEX,.45,(30,30,30),1,cv2.LINE_AA)
                except Exception:pass
            canvas[115:595,20:660]=frame
        if self.wrist is not None:
            canvas[115:427,684:1100]=cv2.resize(self.bridge.imgmsg_to_cv2(self.wrist,'bgr8'),(416,312))
        box(684,443,416,152)
        text('MOTION STATUS',702,468,.43,MUTED)
        text(self.motion.phase,702,500,.65,ACCENT)
        if self.motion.model is not None and self.motion.q is not None:
            xyz=self.motion.model.fk(self.motion.q)[:3,3]
            text('TCP  x %.3f   y %.3f   z %.3f m'%tuple(xyz),702,529,.43)
        text('20 mm clearance | motion-only demo',702,560,.46,MUTED)
        text('No contact-force control',702,582,.43,MUTED)
        for x,w,label,key in [(20,224,'START DEMO [D]','d'),(258,204,'VIEW POSE [V]','v'),(476,184,'STOP [SPACE]',' ')]:
            box(x,620,w,50,(83,94,39) if key=='d' else PANEL)
            text(label,x+13,651,.55)
        text('Three points / one press cycle per point',684,640,.43,MUTED)
        if self.demo_active:text('Elapsed %.0f s'%(time.monotonic()-self.demo_started),684,667,.5,ACCENT)
        text(self.status[:125],20,702,.45,MUTED)
        cv2.imshow(WINDOW,canvas)
        key=cv2.waitKey(1)&255
        if key in (ord('d'),ord('D')):self.start_demo()
        else:self.handle_key(key)
        now=time.monotonic()
        if self.demo_active and now-self.last_video_frame>=.1:
            self.last_video_frame=now
            if self.motion.q is not None:
                p=self.motion.model.fk(self.motion.q)[:3,3]
                self.samples.append({'t':now-self.demo_started,'stage':self.motion.phase,'tcp':p.tolist()})
            if self.recorder is not None:self.recorder.write(canvas)
        if self.demo_active and not self.motion.busy:
            self.demo_active=False;self.last_demo_result=self.motion.phase
            path=self.get_parameter('record_path').value
            if self.recorder is not None:self.recorder.release();self.recorder=None
            if path:
                result={'passed':self.motion.phase=='COMPLETE','camera_mode':'dual',
                        'measured_surface_points':self.measured,'stages':self.motion.history,
                        'controller_error_code':self.motion.last_result_code,'samples':self.samples}
                Path(path).with_suffix('.json').write_text(json.dumps(result,indent=2))
                cv2.imwrite(str(Path(path).with_suffix('.png')),canvas)
        if self.get_parameter('auto_demo').value and not self.auto_attempted:
            if self.motion.ready() and self.latest is not None and self.wrist is not None:
                if self.ready_since is None:self.ready_since=now
                age=(self.get_clock().now()-Time.from_msg(self.latest[1].header.stamp)).nanoseconds/1e9
                if now-self.ready_since>2. and -.05<=age<=.5:
                    self.auto_attempted=True;self.start_demo()
        if hasattr(self,'quit_at') and now>=self.quit_at and not self.motion.busy:
            rclpy.shutdown()

    def close_demo(self):
        if self.recorder is not None:self.recorder.release()
        self.motion.close()


def main(args=None):
    rclpy.init(args=args);node=DemoCamera()
    try:rclpy.spin(node)
    except (KeyboardInterrupt,ExternalShutdownException):pass
    finally:
        node.close_demo();node.destroy_node();cv2.destroyAllWindows()
        if rclpy.ok():rclpy.shutdown()
