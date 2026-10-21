import json
import time
import unittest
from test_real_device import Cache, sample
from massage_controller.core import Controller
from massage_controller.node import default_config
from massage_controller.protocol import load_contract
from massage_controller.real_device import RealDevices
from robot_fixture import robot_config


class RealScanTests(unittest.TestCase):
    def setUp(self):
        self.now = time.monotonic()
        self.cache = Cache(sample(self.now))
        self.cache.value['robot_state'].update(paused=0, in_position=1, blending_done=1)
        self.device = RealDevices(self.cache, robot_config(), scan_enabled=True)
        self.core = Controller(load_contract(default_config()), lambda: self.now, self.device)
        self.core.vision_connected = True
        self.core.vision_message(json.dumps(dict(type='VISION_STATUS', data=dict(
            mode='REAL', camera_connected=True, model_ready=True, worker_state='IDLE',
            last_frame_age_ms=10, error=''))))

    def request(self, cmd, data=None):
        return self.core.request(json.dumps(dict(cmd=cmd, data=data or {})))

    def test_scan_dispatches_without_enabling_motion(self):
        self.assertTrue(self.request('SCAN', dict(region='BACK'))['ok'])
        self.assertEqual(self.core.outbox, [dict(cmd='LOCATE', data=dict(region='BACK', view='back'))])
        self.assertFalse(self.device.motion_enabled)
        self.assertFalse(self.request('START')['ok'])
        self.assertFalse(self.device.calls)

    def test_cancel_and_disconnect_invalidate_locate(self):
        self.request('SCAN', dict(region='BACK'))
        self.assertTrue(self.request('STOP')['ok'])
        self.assertEqual(self.core.outbox[-1]['cmd'], 'CANCEL')
        self.assertNotEqual(self.core.result, 'STOPPED')
        self.assertFalse(self.core.status()['data']['positioning_valid'])
        self.assertFalse(self.request('SCAN', dict(region='BACK'))['ok'])

    def test_motion_or_unsettled_feedback_blocks_scan(self):
        for key, value in [('moving', 1), ('paused', 1), ('in_position', 0), ('blending_done', 0)]:
            self.setUp()
            self.cache.value['robot_state'][key] = value
            self.device.poll(self.now, None, False)
            self.assertFalse(self.request('SCAN', dict(region='BACK'))['ok'])

    def test_real_result_without_exposure_evidence_is_not_executable(self):
        self.request('SCAN', dict(region='BACK'))
        self.core.vision_message(json.dumps(dict(type='LOCATE_RESULT', data=dict(
            status='SUCCEEDED', region='BACK', view='back', frame='camera_color_optical_frame',
            capture_host_monotonic_ms=None, capture_time_valid=False,
            points=[dict(name='大椎', side='MIDLINE', camera_point_mm=[0, 0, 600],
                         coordinate_valid=True, normal_camera=None, normal_valid=False)], message=''))))
        self.assertFalse(self.core.status()['data']['positioning_valid'])
        self.assertIn('本控制进程', self.core.error)
        self.assertFalse(self.request('START')['ok'])

    def test_verified_new_photo_reaches_confirmation_without_motion_permission(self):
        from test_base_coordinates import BaseProjector, CAL, fixture
        self.request('SCAN', dict(region='BACK'))
        request, history = fixture(self.now + .1)
        request['data']['points'][0].update(name='大椎', side='MIDLINE')
        self.now += .2
        packet = BaseProjector(CAL).project(request, history, self.now)
        self.assertEqual(packet['data']['error'], '')
        self.core.snapshot_verified = lambda p: p == packet
        p = packet['data']['points'][0]
        self.core.vision_message(json.dumps(dict(type='LOCATE_RESULT', data=dict(
            status='SUCCEEDED', region='BACK', view='back', frame='camera_color_optical_frame',
            capture_host_monotonic_ms=None, capture_time_valid=False,
            points=[dict(name=p['name'], side=p['side'], camera_point_mm=p['camera_point_mm'],
                         coordinate_valid=True, normal_camera=None, normal_valid=False)],
            base_preview=packet, message=''))))
        state = self.core.status()['data']
        self.assertEqual(state['state'], 'IDLE')
        self.assertEqual(state['error'], '')
        self.assertEqual(state['scan_snapshot']['available_points'], [dict(name='大椎', side='MIDLINE')])
        self.assertFalse(state['positioning_valid'])
        self.assertFalse(state['scan_snapshot']['execution_eligible'])
        self.assertFalse(self.request('START')['ok'])
        self.core.vision_lost()
        self.assertNotIn('scan_snapshot', self.core.status()['data'])

if __name__ == '__main__':
    unittest.main()
