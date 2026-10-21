"""Strict UI authorization packets and dispatch; all devices are test doubles."""
import copy
import json
import unittest
from unittest.mock import Mock

from jsonschema import Draft202012Validator
from massage_controller.node import default_config
from massage_controller.protocol import load_contract
import test_real_scan


DATA = dict(revision='00000000-0000-0000-0000-000000000003',
            points=[dict(name='大椎', side='MIDLINE')],
            operator_observed_no_obstacles=True, reuse_existing_calibration=True)


class AuthorizeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.validator = Draft202012Validator(load_contract(default_config())['ui_request.schema'])

    def valid(self, data):
        return self.validator.is_valid(dict(cmd='AUTHORIZE_NONCONTACT', data=data))

    def test_explicit_current_route_and_true_statements_are_required(self):
        self.assertTrue(self.valid(DATA))
        for key in DATA:
            changed = copy.deepcopy(DATA)
            changed.pop(key)
            with self.subTest(missing=key):
                self.assertFalse(self.valid(changed))
        for key in ('operator_observed_no_obstacles', 'reuse_existing_calibration'):
            for value in (False, 1, 'true', None):
                with self.subTest(key=key, value=value):
                    self.assertFalse(self.valid(dict(DATA, **{key: value})))

    def test_uuid_route_and_additional_fields_are_strict(self):
        for value in ('a' * 36, '---000000000000000000000000000000000', '', None):
            with self.subTest(revision=value):
                self.assertFalse(self.valid(dict(DATA, revision=value)))
        for points in ([], [dict(name='大椎', side='MIDDLE')],
                       [dict(name='大椎', side='MIDLINE', x=120)],
                       [dict(name='大椎')], [dict(name='大椎', side='MIDLINE')] * 31):
            with self.subTest(points=points):
                self.assertFalse(self.valid(dict(DATA, points=points)))
        self.assertFalse(self.valid(dict(DATA, path_confirmed=True)))
        self.assertFalse(self.validator.is_valid(dict(cmd='AUTHORIZE_NONCONTACT', data=DATA, extra=True)))


class AuthorizeDispatchTests(unittest.TestCase):
    def setUp(self):
        test_real_scan.RealScanTests.setUp(self)
        self.handler = Mock(spec=['authorize', 'prepare', 'start', 'check_stop', 'active'])
        self.handler.active = False
        self.core.noncontact = self.handler

    def request(self, data):
        return self.core.request(json.dumps(dict(cmd='AUTHORIZE_NONCONTACT', data=data)))

    def test_valid_authorization_dispatches_without_preparing_or_starting(self):
        response = self.request(DATA)
        self.assertTrue(response['ok'])
        self.assertEqual(response['cmd'], 'AUTHORIZE_NONCONTACT')
        self.handler.authorize.assert_called_once_with(DATA)
        self.handler.prepare.assert_not_called()
        self.handler.start.assert_not_called()
        self.assertFalse(self.device.calls)

    def test_invalid_packet_and_handler_rejection_cannot_prepare(self):
        self.assertFalse(self.request(dict(DATA, operator_observed_no_obstacles=False))['ok'])
        self.handler.authorize.assert_not_called()
        self.handler.authorize.side_effect = ValueError('本次照片已过期')
        result = self.request(DATA)
        self.assertFalse(result['ok'])
        self.assertIn('过期', result['message'])
        self.handler.prepare.assert_not_called()
        self.handler.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
