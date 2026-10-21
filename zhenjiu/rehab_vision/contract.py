"""Read the controller-owned schemas and catalogue without redefining them."""
import json
import math
from pathlib import Path
from jsonschema import Draft202012Validator

DEFAULT_CONTRACT = Path(__file__).resolve().parents[2] / 'config' / 'rehab_protocol'
FRAME = 'camera_color_optical_frame'


def strict_json(raw):
    if not isinstance(raw, str):
        raise ValueError('只接受文本 JSON')
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('重复 JSON 字段')
            result[k] = v
        return result
    def bad(value):
        raise ValueError('非有限数字')
    obj = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
    def finite(v):
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError('非有限数字')
        if isinstance(v, dict):
            for x in v.values(): finite(x)
        elif isinstance(v, list):
            for x in v: finite(x)
    finite(obj)
    return obj


class Contract:
    def __init__(self, directory=DEFAULT_CONTRACT):
        root = Path(directory)
        self.regions = strict_json((root / 'acupoints.json').read_text(encoding='utf-8'))['regions']
        self.request = Draft202012Validator(json.loads((root / 'vision_request.schema.json').read_text(encoding='utf-8')))
        self.message = Draft202012Validator(json.loads((root / 'vision_message.schema.json').read_text(encoding='utf-8')))

    def decode(self, raw):
        obj = strict_json(raw)
        if not self.request.is_valid(obj):
            raise ValueError('字段、类型或枚举不符合公共协议')
        return obj

    def points(self, region, raw_points):
        allowed = {(e['name'], s) for e in self.regions[region] for s in e['sides']}
        out, seen = [], set()
        for p in raw_points:
            side = {'L': 'LEFT', 'R': 'RIGHT', None: 'MIDLINE'}.get(p.get('side'), p.get('side'))
            key = (p['name'], side)
            if key not in allowed:
                continue
            if key in seen:
                raise ValueError('同名同侧重复穴位')
            seen.add(key)
            xyz = p.get('camera_point_mm')
            valid = (p.get('coordinate_valid') is True and isinstance(xyz, (list, tuple))
                     and len(xyz) == 3 and all(type(x) in (int, float) and math.isfinite(x) for x in xyz)
                     and xyz[2] > 0)
            out.append(dict(name=key[0], side=side,
                            camera_point_mm=list(xyz) if valid else None, coordinate_valid=valid,
                            normal_camera=None, normal_valid=False))
        return out


def reply(cmd, ok, message):
    return dict(type='REPLY', cmd=cmd, ok=ok, message=message, data={})


def result(data, status, message, points=None):
    return dict(type='LOCATE_RESULT', data=dict(
        status=status, region=data['region'], view=data['view'],
        capture_host_monotonic_ms=None, capture_time_valid=False,
        frame=FRAME, points=points or [], message=message))
