"""Shared strict wire contract. No network IDs or simulation injection commands."""
import json
import math
from pathlib import Path
from jsonschema import Draft202012Validator


def load_contract(path):
    p = Path(path)
    return {n: json.loads((p / (n + '.json')).read_text(encoding='utf-8-sig')) for n in
            ('acupoints', 'simulation', 'ui_request.schema', 'vision_message.schema')}


def decode(raw, schema):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('重复JSON字段')
            result[k] = v
        return result
    def bad(value):
        raise ValueError('非有限数字')
    if not isinstance(raw, str):
        raise ValueError('只接受文本JSON')
    msg = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
    def finite(v):
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError('非有限数字')
        if isinstance(v, dict):
            for x in v.values(): finite(x)
        if isinstance(v, list):
            for x in v: finite(x)
    finite(msg)
    errors = list(Draft202012Validator(schema).iter_errors(msg))
    if errors:
        raise ValueError('字段、类型或枚举不符合协议')
    return msg


def reply(cmd, ok, message, data=None):
    return dict(type='REPLY', cmd=cmd, ok=ok, message=message, data=data or {})
