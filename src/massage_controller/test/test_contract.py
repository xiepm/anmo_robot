import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator
from massage_controller.node import default_config

class ContractTests(unittest.TestCase):
    def test_all_documented_responses(self):
        root=Path(default_config())
        schema=json.loads((root/'ui_response.schema.json').read_text())
        validator=Draft202012Validator(schema);count=0
        for p in (root.parents[1]/'总规划'/'通讯协议示例'/'完整场景').glob('*/*.json'):
            m=json.loads(p.read_text())
            if m.get('type') in ('STATUS','REPLY'):
                self.assertFalse(list(validator.iter_errors(m)),str(p));count+=1
        self.assertGreater(count,100)

    def test_all_schemas_well_formed(self):
        for p in Path(default_config()).glob('*.schema.json'):
            Draft202012Validator.check_schema(json.loads(p.read_text()))
