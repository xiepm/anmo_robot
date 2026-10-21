"""Click synchronized rendered RGB-D; transform at exposure time and command an independent stage executor."""
import copy
import time
import cv2
import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from rclpy.time import Time
from rclpy.clock import Clock, ClockType
from rclpy.qos import qos_profile_sensor_data
from cv_bridge import CvBridge
from message_filters import Subscriber, ApproximateTimeSynchronizer
from sensor_msgs.msg import Image, CameraInfo
from geometry_msgs.msg import PointStamped
from tf2_ros import Buffer, TransformListener, TransformException
from tf2_geometry_msgs import do_transform_point
from elfin_massage_sim.geometry import deproject, approach
from elfin_massage_sim.direct_motion import DirectMotion

BUTTONS = ((8, 198, 'View pose [V]', ord('v')),
           (208, 398, 'Home [H]', ord('h')),
           (408, 632, 'Cancel [SPACE]', 32))


class ClickCamera(Node):
    def __init__(self, gui=True):
        super().__init__('click_camera')
        self.declare_parameter('clearance', 0.04)
        self.declare_parameter('max_image_age', 0.5)
        self.declare_parameter('camera_namespace', '/camera')
        self.declare_parameter('scene', 'pad')
        namespace = self.get_parameter('camera_namespace').value.rstrip('/')
        self.bridge = CvBridge()
        self.tf = Buffer()
        self.listener = TransformListener(self.tf, self)
        self.surface_pub = self.create_publisher(PointStamped, '/massage/surface_point', 10)
        self.target_pub = self.create_publisher(PointStamped, '/massage/target_point', 10)
        self.subs = [Subscriber(self, typ, topic, qos_profile=qos_profile_sensor_data) for typ, topic in (
            (Image, namespace+'/image_raw'), (Image, namespace+'/depth/image_raw'), (CameraInfo, namespace+'/camera_info'))]
        self.sync = ApproximateTimeSynchronizer(self.subs, queue_size=10, slop=0.04)
        self.sync.registerCallback(self.receive)
        self.latest = self.displayed = None
        self.last_surface = self.last_target = None
        self.status = 'Waiting for RGB-D. Click pad; V: view pose; H: home; SPACE: cancel; Q: quit'
        self.motion = DirectMotion(self)
        if gui:
            cv2.namedWindow('Elfin massage RGB-D', cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback('Elfin massage RGB-D', self.click)
            self.create_timer(0.05, self.render, clock=Clock(clock_type=ClockType.STEADY_TIME))

    def notice(self, text):
        self.status = text
        self.get_logger().info(text)

    def receive(self, rgb, depth, info):
        if (rgb.width, rgb.height) != (depth.width, depth.height) or (info.width, info.height) != (depth.width, depth.height):
            return
        if not rgb.header.frame_id == depth.header.frame_id == info.header.frame_id:
            return
        if depth.encoding not in ('32FC1', '16UC1'):
            return
        # Copy ownership; callbacks must never change the snapshot currently on screen.
        if self.latest is None:
            self.get_logger().info('Synchronized RGB-D stream ready')
            self.status = 'Ready. Click pad; V: view pose; H: home; SPACE: cancel; Q: quit'
        self.latest = (rgb, depth, info)

    def render(self):
        if self.latest is not None:
            self.displayed = self.latest
            try:
                frame = self.bridge.imgmsg_to_cv2(self.displayed[0], 'bgr8').copy()
                cv2.putText(frame, self.status[:90], (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1)
                height = frame.shape[0]
                frame = cv2.copyMakeBorder(frame, 0, 52, 0, 0, cv2.BORDER_CONSTANT, value=(35, 35, 35))
                for left, right, label, _ in BUTTONS:
                    cv2.rectangle(frame, (left, height+8), (right, height+44), (95, 95, 95), -1)
                    cv2.putText(frame, label, (left+8, height+32), cv2.FONT_HERSHEY_SIMPLEX,
                                0.5, (255, 255, 255), 1)
                cv2.imshow('Elfin massage RGB-D', frame)
            except Exception as exc:
                self.notice(f'Image conversion failed: {exc}')
        self.handle_key(cv2.waitKey(1) & 0xff)
        if hasattr(self, 'quit_at') and time.monotonic() >= self.quit_at and not self.motion.busy:
            rclpy.shutdown()

    def handle_key(self, key):
        if ord('A') <= key <= ord('Z'):
            key += ord('a') - ord('A')
        if key == ord('q'):
            self.motion.cancel()
            # Keep spinning to deliver cancellation before shutting down.
            self.quit_at = time.monotonic() + 1.0
        elif key == 32:
            self.motion.cancel()
        elif key == ord('h'):
            self.motion.start('home')
        elif key == ord('v'):
            self.motion.start('observe')

    def click(self, event, u, v, flags, userdata):
        if event != cv2.EVENT_LBUTTONDOWN or self.displayed is None:
            return
        height = self.displayed[0].height
        if height+8 <= v <= height+44:
            for left, right, _, key in BUTTONS:
                if left <= u <= right:
                    self.handle_key(key)
                    return
        if v >= height:
            return
        if self.motion.busy:
            self.notice('Motion busy; SPACE cancels')
            return
        rgb, depth, info = self.displayed
        try:
            age = (self.get_clock().now() - Time.from_msg(depth.header.stamp)).nanoseconds / 1e9
            if not -0.05 <= age <= self.get_parameter('max_image_age').value:
                raise ValueError(f'Stale frame (age={age:.3f}s); wait for live image')
            array = self.bridge.imgmsg_to_cv2(depth, 'passthrough')
            if not (0 <= u < depth.width and 0 <= v < depth.height):
                raise ValueError('Pixel outside image')
            # Use the clicked pixel itself: a neighbourhood median may select the background at edges.
            z = float(array[v, u]) * (0.001 if depth.encoding == '16UC1' else 1.0)
            xyz = deproject(u, v, z, info.k, depth.width, depth.height)
            point = PointStamped(header=depth.header)
            point.point.x, point.point.y, point.point.z = xyz
            transform = self.tf.lookup_transform('world', point.header.frame_id, Time.from_msg(point.header.stamp))
            surface = do_transform_point(point, transform)
            target = approach((surface.point.x, surface.point.y, surface.point.z), self.get_parameter('clearance').value,
                              self.get_parameter('scene').value)
            self.last_surface = surface
            self.last_target = target
            self.surface_pub.publish(surface)
            stamped = copy.deepcopy(surface)
            stamped.point.x, stamped.point.y, stamped.point.z = target
            self.target_pub.publish(stamped)
            self.notice(f'world surface=({surface.point.x:.3f}, {surface.point.y:.3f}, {surface.point.z:.3f}) m')
            self.motion.start('approach', (surface.point.x, surface.point.y, surface.point.z),
                              self.get_parameter('clearance').value)
        except (ValueError, TransformException, IndexError) as exc:
            self.notice(str(exc))


def main(args=None):
    rclpy.init(args=args)
    node = ClickCamera()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        if rclpy.ok():
            node.motion.cancel()
    finally:
        node.motion.close()
        node.destroy_node()
        cv2.destroyAllWindows()
        if rclpy.ok():
            rclpy.shutdown()
