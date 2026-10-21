"""Read-only coordinate RPC to the control process; no SDK import or robot socket."""
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from websockets.sync.client import connect
from .contract import DEFAULT_CONTRACT, strict_json

CONTROL_SOURCE = Path(__file__).resolve().parents[2] / 'src/massage_controller'
if str(CONTROL_SOURCE) not in sys.path:
    sys.path.insert(0, str(CONTROL_SOURCE))
from massage_controller.host_clock import get_host_boot_id


class BaseClient:
    def __init__(self, uri='ws://127.0.0.1:8766/coordinates'):
        parsed = urlsplit(uri)
        if (parsed.scheme != 'ws' or parsed.hostname not in ('localhost','127.0.0.1','::1')
                or parsed.path != '/coordinates' or parsed.query or parsed.fragment):
            raise ValueError('坐标服务只允许本机 ws://.../coordinates')
        self.uri, self.ws, self.retry_at = uri, None, 0.
        self.boot_id, self.clock_error = '', ''
        try:
            self.boot_id = get_host_boot_id()
        except (OSError, RuntimeError, ValueError) as exc:
            # RGB-D preview must survive an unavailable host identity, but no
            # coordinate request may use a guessed or empty boot identifier.
            self.clock_error = f'无法核对本次系统启动标识: {exc}'
        self.validator = Draft202012Validator(json.loads(
            (DEFAULT_CONTRACT/'base_coordinates_response.schema.json').read_text(encoding='utf-8')))

    def project(self, points, metadata, received_at):
        raw = []
        for p in points:
            raw.append(dict(name=p['name'],
                side={'L':'LEFT','R':'RIGHT',None:'MIDLINE'}.get(p.get('side'),p.get('side')),
                coordinate_valid=bool(p.get('coordinate_valid')),
                camera_point_mm=p.get('camera_point_mm') if p.get('coordinate_valid') else None))
        data = dict(frame='camera_color_optical_frame', camera_serial=metadata.get('camera_serial','unknown'),
            host_boot_id=self.boot_id, frame_number=int(metadata.get('color_frame_number',0)),
            frame_received_monotonic_s=received_at, points=raw)
        if metadata.get('exposure_clock'):
            data['exposure_clock']=metadata['exposure_clock']
        attempted = False
        try:
            if self.clock_error:
                raise RuntimeError(self.clock_error)
            if not self.ws:
                if time.monotonic() < self.retry_at:
                    raise ConnectionError('等待控制坐标服务重连')
                attempted = True
                self.ws = connect(self.uri, open_timeout=.5, close_timeout=.1, max_size=65536)
            attempted = True
            self.ws.send(json.dumps(dict(cmd='PROJECT_POINTS', data=data), allow_nan=False))
            answer = strict_json(self.ws.recv(timeout=.8))
            self.validator.validate(answer)
            d = answer['data']
            if (d['frame_number'] != data['frame_number'] or d['host_boot_id'] != self.boot_id
                    or d['camera_serial'] != data['camera_serial']
                    or d['frame_received_monotonic_s'] != received_at
                    or time.monotonic() > d['valid_until_monotonic_s']):
                raise ValueError('Base 响应不属于本帧或已过期')
            return answer
        except Exception as exc:
            # Preview frames during backoff must not keep pushing its deadline away.
            if attempted:
                self.close()
                self.retry_at = time.monotonic()+1
            return dict(type='BASE_COORDINATES', data=dict(
                frame='robot_base', units='mm', camera_serial=data['camera_serial'],
                frame_number=data['frame_number'], frame_received_monotonic_s=received_at,
                host_boot_id=self.boot_id, generated_monotonic_s=time.monotonic(),
                valid_until_monotonic_s=received_at, capture_time_valid=False, motion_eligible=False,
                pairing_method='stationary_host_receipt_preview', reference_frame='virtual_flange_with_joint6_zero',
                calibration_sha256=None, feedback_window=None,
                points=[dict(p,base_point_mm=None,base_coordinate_valid=False) for p in raw],
                error=f'Base 坐标不可用: {exc}'))

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None


def write_latest(path, packet):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(path)


def invalidate(packet, reason):
    # Never leave a seemingly live point on disk after pause/exit/lost connection.
    packet['data']['error'] = reason
    packet['data']['valid_until_monotonic_s'] = 0.
    for p in packet['data']['points']:
        p.update(base_point_mm=None, base_coordinate_valid=False)
    return packet
