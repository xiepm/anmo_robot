#!/usr/bin/env python3
"""UI preview adapter: runs the existing locator once, publishes its rendered frames.

Uses the vision virtualenv and shared locator. No robot SDK connection, motion
command, second camera, or video recording. HTTP runs in the same process.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

MAX_FRAME_AGE = 1.5


def configure_capture_geometry(capture):
    """Refresh both optical models from the one active pipeline after open."""
    import numpy as np
    from depth_scene import intrinsics_key, pixel_rays
    # Retire every old profile first, including a partially failed reopen.
    capture.scene_rays = capture.scene_intrinsics_key = None
    capture.native_scene_rays = capture.native_scene_intrinsics_key = None
    capture.native_intrinsics = capture.T_color_depth = None
    capture.raw_depth_frame = None
    active = capture.pipeline.get_active_profile()
    color_profile = active.get_stream(capture.rs.stream.color).as_video_stream_profile()
    depth_profile = active.get_stream(capture.rs.stream.depth).as_video_stream_profile()
    color_intrinsics, native_intrinsics = color_profile.intrinsics, depth_profile.intrinsics
    color_rays = pixel_rays(color_intrinsics, capture.rs.rs2_deproject_pixel_to_point,
                            color_intrinsics.width, color_intrinsics.height)
    native_rays = pixel_rays(native_intrinsics, capture.rs.rs2_deproject_pixel_to_point,
                             native_intrinsics.width, native_intrinsics.height)
    extrinsics = depth_profile.get_extrinsics_to(color_profile)
    transform = np.eye(4)
    # rs2_extrinsics.rotation is column-major; translation is in metres.
    transform[:3, :3] = np.asarray(extrinsics.rotation, dtype=float).reshape(3, 3, order='F')
    transform[:3, 3] = extrinsics.translation
    transform.setflags(write=False)
    capture.scene_intrinsics_key = intrinsics_key(color_intrinsics)
    capture.scene_rays = color_rays
    capture.native_scene_intrinsics_key = intrinsics_key(native_intrinsics)
    capture.native_scene_rays = native_rays
    capture.native_intrinsics = native_intrinsics
    capture.T_color_depth = transform


def native_geometry_for_snapshot(capture):
    """Extract only the original depth exposure retained alongside this photo."""
    import numpy as np
    if any(capture.get(key) is None for key in ('native_depth', 'depth', 'native_rays', 'T_color_depth')):
        return None
    native, aligned = capture['native_depth'], capture['depth']
    if native.get_frame_timestamp_domain() != aligned.get_frame_timestamp_domain():
        raise ValueError('原生深度与对齐深度时钟域不一致')
    units = native.get_units()
    if not np.isfinite(units) or units <= 0:
        raise ValueError('原生深度单位无效')
    # FrozenDepth copies these arrays and verifies frame number/timestamp pairs.
    return dict(native_depth_m=np.asanyarray(native.get_data()).astype(float)*units,
                native_rays=capture['native_rays'], T_color_depth=capture['T_color_depth'],
                native_depth_frame_number=native.get_frame_number(),
                aligned_depth_frame_number=aligned.get_frame_number(),
                native_depth_timestamp_ms=native.get_timestamp(),
                aligned_depth_timestamp_ms=aligned.get_timestamp(),
                geometry_frame='DEPTH_OPTICAL_NATIVE')


class LatestFrame:
    def __init__(self):
        self.lock = threading.Lock()
        self.jpeg = None
        self.updated_at = 0.0
        self.sequence = 0
        self.snapshot = None
        self.depth_resolver = None
        self.timing = {}
        self.camera_state = 'WAITING'
        self.camera_error = ''

    def invalidate(self, reason):
        with self.lock:
            self.jpeg = self.snapshot = self.depth_resolver = None
            self.updated_at = 0.
            self.sequence += 1
            self.timing = dict(camera_clock_ready=False,capture_time_valid=False,error=reason)
            self.camera_state, self.camera_error = 'RECONNECTING', reason

    def put(self, jpeg):
        with self.lock:
            self.jpeg = bytes(jpeg)
            self.updated_at = time.monotonic()
            self.sequence += 1
            self.camera_state, self.camera_error = 'STREAMING', ''

    def get(self):
        with self.lock:
            return self.jpeg, time.monotonic() - self.updated_at, self.sequence

    def put_snapshot(self, jpeg, frame_number, received_at, render_selected=None,
                     raw_jpeg=None, editor_data=None, depth_resolver=None):
        with self.lock:
            self.snapshot = (bytes(jpeg), frame_number, received_at, render_selected,
                             bytes(raw_jpeg) if raw_jpeg is not None else None,
                             copy.deepcopy(editor_data))
            self.depth_resolver = depth_resolver

    def resolve_points(self, data):
        with self.lock:
            if (not self.snapshot or self.snapshot[1:3] != (data['frame_number'],data['received_monotonic_s'])
                    or self.depth_resolver is None):
                raise ValueError('确认坐标的照片已失效，请重新扫描')
            resolver = self.depth_resolver
        points = resolver.resolve(data['points'])
        if 'scene_context' in data:
            points = dict(points=points, scene=resolver.scene.analyze(data['scene_context'], points))
        with self.lock:
            if self.depth_resolver is not resolver:
                raise ValueError('相机已断开或照片已更新，请重新扫描')
        return points

    def get_editor(self, frame_number, received_at, raw=False):
        with self.lock:
            snapshot = self.snapshot
            if not snapshot or snapshot[1:3] != (frame_number, received_at):
                return None
            return snapshot[4] if raw else copy.deepcopy(snapshot[5])

    def get_geometry(self, frame_number, received_at):
        with self.lock:
            if (not self.snapshot or self.snapshot[1:3] != (frame_number,received_at)
                    or self.depth_resolver is None):
                return None
            snapshot = self.snapshot
            frozen = self.depth_resolver
        import io
        import numpy as np
        if frozen.rays is None: return None
        stream=io.BytesIO()
        payload = dict(depth_m=frozen.depth, rays=frozen.rays,
                       frame_number=frame_number, received_monotonic_s=received_at)
        native = getattr(frozen, 'native_geometry', None)
        if native is not None:
            payload.update(native)
        np.savez_compressed(stream, **payload)
        with self.lock:
            if self.snapshot is not snapshot or self.depth_resolver is not frozen:
                return None
            return stream.getvalue()

    def get_snapshot(self, frame_number, received_at, selected=None):
        with self.lock:
            snapshot = self.snapshot
            if not snapshot or snapshot[1:3] != (frame_number, received_at):
                return None
        # The renderer owns immutable pixels and measurements from this capture.
        # If unavailable, never silently return a photo of ALL points.
        jpeg = snapshot[0] if selected is None else snapshot[3](selected) if snapshot[3] else None
        with self.lock:
            return jpeg if self.snapshot is snapshot else None


def parse_selection(raw):
    points = json.loads(raw)
    if not isinstance(points, list) or not 1 <= len(points) <= 64:
        raise ValueError('需要 1–64 个方案点')
    selected = []
    for p in points:
        if (not isinstance(p, dict) or set(p) != {'name', 'side'}
                or not isinstance(p['name'], str) or not 1 <= len(p['name']) <= 32
                or p['side'] not in ('LEFT', 'RIGHT', 'MIDLINE')):
            raise ValueError('方案点名称或侧别无效')
        key = (p['name'], {'LEFT':'L', 'RIGHT':'R', 'MIDLINE':None}[p['side']])
        if key in selected:
            raise ValueError('方案点重复')
        selected.append(key)
    return selected


def create_server(frames, port=8767):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.connection.settimeout(2)
            path = urlsplit(self.path).path
            if path not in ('/frame.jpg', '/health', '/snapshot.jpg', '/snapshot/raw.jpg', '/snapshot/points.json', '/snapshot/geometry.npz'):
                self.send_error(404)
                return
            jpeg, age, sequence = frames.get()
            fresh = jpeg is not None and age <= MAX_FRAME_AGE
            editor_data = None
            geometry = None
            if path.startswith('/snapshot'):
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    frame, received = int(query['frame'][0]), float(query['received'][0])
                    if path == '/snapshot.jpg':
                        selected = parse_selection(query['points'][0]) if 'points' in query else None
                        jpeg = frames.get_snapshot(frame, received, selected)
                    elif path == '/snapshot/raw.jpg':
                        jpeg = frames.get_editor(frame, received, raw=True)
                    elif path == '/snapshot/geometry.npz':
                        geometry = frames.get_geometry(frame, received)
                        jpeg = None
                    else:
                        editor_data = frames.get_editor(frame, received)
                        jpeg = None
                except (KeyError, ValueError, IndexError, TypeError):
                    jpeg = None
                fresh = jpeg is not None
            if geometry is not None:
                body, mime, code = geometry, 'application/octet-stream', 200
            elif editor_data is not None:
                body, mime, code = json.dumps(editor_data, ensure_ascii=False, allow_nan=False).encode('utf-8'), 'application/json; charset=utf-8', 200
            elif path in ('/frame.jpg', '/snapshot.jpg', '/snapshot/raw.jpg') and fresh:
                body, mime, code = jpeg, 'image/jpeg', 200
            else:
                with frames.lock:
                    timing = copy.deepcopy(frames.timing)
                    camera_state, camera_error = frames.camera_state, frames.camera_error
                if not fresh and timing:
                    timing.update(camera_clock_ready=False,capture_time_valid=False,error='相机画面已过期')
                body = json.dumps({
                    'mode': 'REAL', 'fresh': fresh, 'sequence': sequence,
                    'frame_age_ms': round(age * 1000) if jpeg is not None else None,
                    'message': '实时穴位画面' if fresh else '等待视觉程序输出新画面',
                    'timing': timing,
                    'camera_state': camera_state, 'camera_error': camera_error,
                }, ensure_ascii=False).encode('utf-8')
                mime, code = 'application/json; charset=utf-8', 200 if path == '/health' else 503
            self.send_response(code)
            origin = self.headers.get('Origin', '')
            parsed = urlsplit(origin)
            if parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '[::1]', '::1'):
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Expose-Headers', 'X-Preview-Sequence')
            self.send_header('X-Preview-Sequence', str(sequence))
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store, max-age=0')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description='在 UI 中预览现有 D435i 穴位定位，不发送运动命令')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--max-frames', type=int, default=0, help='自检处理帧数；默认持续运行')
    parser.add_argument('--manual-rgb-exposure', type=int, default=166, help='D435i 手动 RGB 曝光，UVC 单位 100 us')
    args = parser.parse_args()
    if args.port in (5173, 8765, 8766):
        parser.error('预览端口不能占用网页或公共控制端口')
    root = Path(__file__).resolve().parents[2]
    vision_dir = root / 'zhenjiu'
    sys.path.insert(0, str(vision_dir))
    from rehab_vision.camera_lock import CameraLock
    camera_lock = CameraLock().acquire()
    frames = LatestFrame()
    try:
        server = create_server(frames, args.port)  # Fail before camera open if occupied.
    except BaseException:
        camera_lock.close()
        raise
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f'穴位实时预览 http://127.0.0.1:{args.port}/frame.jpg', flush=True)

    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, terminate)
    original_render = None
    original_project = None
    original_read = None
    original_init = None
    bridge = None
    locator = None
    try:
        import acupoint_locator_refactored as locator
        from rehab_vision.base_client import BaseClient
        from live_locate_bridge import LiveLocateBridge
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        bridge = LiveLocateBridge()
        bridge.refine_snapshot = frames.resolve_points
        original_project = BaseClient.project
        original_read = locator.RealSenseCapture.read
        original_init = locator.RealSenseCapture.__init__
        from camera_recovery import CameraRecovery
        last_capture = {}
        def disconnected(reason):
            last_capture.clear()
            frames.invalidate(reason)
            bridge.camera_disconnected(reason)

        def close_capture(capture):
            # Construction may have failed after pipeline.start(), before the
            # original constructor set opened. Release partial resources too.
            capture.opened = False
            clock = getattr(capture,'capture_clock',None)
            if clock is not None:
                try: clock.restore()
                except (RuntimeError,OSError): pass
            pipeline = getattr(capture,'pipeline',None)
            if pipeline is not None:
                try: pipeline.stop()
                except RuntimeError: pass
            capture.pipeline = None
            capture.capture_clock = None
            capture.depth_frame = capture.color_intrinsics = None
            capture.raw_depth_frame = capture.native_intrinsics = None
            capture.native_scene_rays = capture.native_scene_intrinsics_key = None
            capture.T_color_depth = None
            capture.align = None
            capture.scene_rays = capture.scene_intrinsics_key = None

        def init_with_clock(capture, *a, **kw):
            kw["manual_rgb_exposure"] = args.manual_rgb_exposure
            import pyrealsense2 as rs
            discovery = rs.context()
            recovery_serial = None
            def open_camera():
                nonlocal recovery_serial
                # pipeline.start waits ~15 s for an absent device and holds the
                # Python GIL. Enumerate first so HTTP/health remain responsive
                # throughout the normal USB-unplugged waiting period.
                devices = discovery.query_devices()
                if not devices:
                    raise RuntimeError('未检测到 RealSense 摄像头，等待 USB 重新接入')
                if len(devices) == 1:
                    recovery_serial = devices[0].get_info(rs.camera_info.serial_number)
                original_init(capture, *a, **kw)
                recovery_serial = capture.serial
                # Refresh both optical models after every USB reconnect.
                configure_capture_geometry(capture)
            def reset_camera():
                # CameraRecovery closes the pipeline first. Address only the
                # camera used by this owner, never its hub or USB controller.
                if recovery_serial is None:
                    return False
                for device in discovery.query_devices():
                    if device.get_info(rs.camera_info.serial_number) == recovery_serial:
                        logging.warning('相机连续采集失败，尝试一次设备复位后自动重连')
                        device.hardware_reset()
                        return True
                return False
            capture.recovery = CameraRecovery(open_camera,lambda: original_read(capture),
                                              lambda: close_capture(capture),disconnected,
                                              reset_camera=reset_camera)
            try: capture.recovery.ensure_open()
            except BaseException:
                close_capture(capture)
                raise
        locator.RealSenseCapture.__init__ = init_with_clock

        def read_and_remember(capture):
            while True:
                ok, bgr = capture.recovery.read()
                from depth_scene import intrinsics_key
                if intrinsics_key(capture.color_intrinsics) != capture.scene_intrinsics_key:
                    capture.recovery._lost('相机内参已变化，重新初始化投影射线')
                    continue
                native_intrinsics = capture.raw_depth_frame.profile.as_video_stream_profile().intrinsics
                if intrinsics_key(native_intrinsics) != capture.native_scene_intrinsics_key:
                    capture.recovery._lost('原生深度内参已变化，重新初始化投影射线')
                    continue
                previous=last_capture.get('received')
                if (previous is not None
                        and capture.frame_metadata['color_frame_number'] < last_capture['number']):
                    # A device reset can be hidden by librealsense's internal
                    # reconnect. Its old metadata/clock handles may then stay
                    # invalid even though pixels return. Rebuild the complete
                    # capture session before accepting that reset counter.
                    capture.recovery._lost('相机采集会话已重置，正在重新初始化曝光和深度采集')
                    continue
                if previous is not None and (capture.received_at-previous>MAX_FRAME_AGE
                        or capture.frame_metadata['color_frame_number']<=last_capture['number']):
                    # librealsense can recover internally without returning a
                    # failed read; retire old photos across that gap as well.
                    disconnected('采集曾中断，旧照片已失效')
                    capture.recovery.generation += 1
                last_capture.update(bgr=bgr.copy(), received=capture.received_at,
                                    number=capture.frame_metadata['color_frame_number'],
                                    depth=capture.depth_frame, intrinsics=capture.color_intrinsics,
                                    rs=capture.rs, rays=capture.scene_rays,
                                    native_depth=capture.raw_depth_frame,
                                    native_rays=capture.native_scene_rays, T_color_depth=capture.T_color_depth)
                return ok, bgr

        locator.RealSenseCapture.read = read_and_remember

        def project_and_publish(client, points, metadata, received_at):
            packet = original_project(client, points, metadata, received_at)
            clock = metadata.get('exposure_clock')
            with frames.lock:
                frames.timing = dict(camera_clock_ready=bool(clock), frame_number=metadata['color_frame_number'],
                    capture_time_valid=packet['data'].get('capture_time_valid',False),
                    rgb_exposure_us=clock['color']['actual_exposure_us'] if clock else None,
                    depth_exposure_us=clock['depth']['actual_exposure_us'] if clock else None,
                    diagnostics=metadata.get('exposure_clock_diagnostics'),
                    error=metadata.get('exposure_clock_error') or packet['data'].get('error') or packet['data'].get('capture_time_error',''))
            def capture_snapshot():
                if last_capture.get('received') != received_at or last_capture.get('number') != metadata['color_frame_number']:
                    raise ValueError('照片与测量帧不匹配')
                import copy
                measured = copy.deepcopy(points)
                lookup = {(p['name'], p['side']): p for p in packet['data']['points']}
                for p in measured:
                    side = {'L':'LEFT', 'R':'RIGHT', None:'MIDLINE'}.get(p.get('side'), p.get('side'))
                    base = lookup.get((p['name'], side), {})
                    p.update(base_point_mm=base.get('base_point_mm'), base_coordinate_valid=base.get('base_coordinate_valid', False),
                             coordinate_display_frame='Base')
                rgb = locator.cv2.cvtColor(last_capture['bgr'], locator.cv2.COLOR_BGR2RGB)
                def render_photo(selected=None):
                    # Failed depth/Base points stay missing, with no marker.
                    visible = measured if selected is None else [p for p in measured if p.get('base_coordinate_valid')]
                    annotated = original_render(rgb, visible, upscale=1., font_size=14,
                        show_labels=True, show_coordinates=True, coordinate_font_size=10,
                        back_panel=True, base_requested=True, panel_keys=selected,
                        status_text='本次拍照识别结果 · 比例候选，未作解剖校正 · — 表示未识别')
                    ok, encoded = locator.cv2.imencode('.jpg', locator.cv2.cvtColor(annotated, locator.cv2.COLOR_RGB2BGR))
                    if not ok:
                        raise RuntimeError('本次照片编码失败')
                    return encoded.tobytes()
                ok, raw_encoded = locator.cv2.imencode('.jpg', last_capture['bgr'])
                if not ok:
                    raise RuntimeError('本次原始照片编码失败')
                editor_data = dict(frame_number=metadata['color_frame_number'], received_monotonic_s=received_at,
                    width=rgb.shape[1], height=rgb.shape[0], points=[dict(
                        name=p['name'], side={'L':'LEFT','R':'RIGHT',None:'MIDLINE'}[p['side']],
                        x=p['x'], y=p['y'], coordinate_valid=bool(p.get('base_coordinate_valid')))
                        for p in measured])
                from frozen_depth import FrozenDepth
                depth = last_capture['depth']
                frozen = FrozenDepth(locator.np.asanyarray(depth.get_data()).astype(float)*depth.get_units(),
                    last_capture['intrinsics'],last_capture['rs'].rs2_deproject_pixel_to_point,editor_data['points'],
                    rays=last_capture['rays'], native_geometry=native_geometry_for_snapshot(last_capture))
                frames.put_snapshot(render_photo(), metadata['color_frame_number'], received_at, render_photo,
                                    raw_encoded.tobytes(), editor_data, frozen)
            bridge.publish(points, metadata, received_at, packet, capture_snapshot)
            return packet

        # This hook receives strict, current measurements before display smoothing.
        BaseClient.project = project_and_publish
        bridge.start()
        original_render = locator.render_frame
        last_encoded = 0.0

        def render_and_publish(*values, **options):
            nonlocal last_encoded
            rgb = original_render(*values, **options)
            now = time.monotonic()
            if now - last_encoded >= 0.1:
                bgr = locator.cv2.cvtColor(rgb, locator.cv2.COLOR_RGB2BGR)
                ok, encoded = locator.cv2.imencode('.jpg', bgr, [locator.cv2.IMWRITE_JPEG_QUALITY, 90])
                if ok:
                    frames.put(encoded.tobytes())
                    last_encoded = now
            return rgb

        # Observe the renderer's return value; detection/depth/Base calculations stay original.
        locator.render_frame = render_and_publish
        output = root / 'web' / '.runtime' / 'base_coordinates_latest.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        sys.argv = [str(vision_dir / 'acupoint_locator_refactored.py'),
                    '--pose-backend', 'rtmw-l', '--pose-rotation', '90',
                    '--pose-auto-rotation',
                    '--torso-only', '--view', 'back', '--base-coordinates',
                    '--base-output', str(output), '--no-display',
                    '--max-frames', str(args.max_frames)]
        locator.main_live()
    finally:
        if bridge:
            bridge.close()
        if original_project is not None:
            BaseClient.project = original_project
        if locator is not None and original_read is not None:
            locator.RealSenseCapture.read = original_read
        if locator is not None and original_init is not None:
            locator.RealSenseCapture.__init__ = original_init
        if locator is not None and original_render is not None:
            locator.render_frame = original_render
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        camera_lock.close()


if __name__ == '__main__':
    main()
