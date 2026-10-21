import copy
import json
import unittest
from pathlib import Path
from jsonschema import validate
import test_noncontact
from massage_controller.noncontact_plan import automatic_profile, fingerprint, profile_errors


PROFILE = test_noncontact.PROFILE

class AutomaticSceneTests(unittest.TestCase):
    request = test_noncontact.NoncontactControlTests.request
    result = test_noncontact.NoncontactControlTests.result
    send = test_noncontact.NoncontactControlTests.send
    authorized_fixture = test_noncontact.NoncontactControlTests.authorized_fixture

    def setUp(self):
        test_noncontact.NoncontactControlTests.setUp(self)
        self.cache.value.update(configured_tool_pose_mm_deg=[0.]*6, configured_user_pose_mm_deg=[0.]*6)
        self.device.poll(self.now,None,False)
        self.path.write_text(json.dumps(dict(PROFILE,geometry_source='FROZEN_DEPTH',
            workspace_base_mm=None,surface_ceiling_base_mm=None,safe_transfer_height_base_mm=None)))

    def analyze(self, unknown=0, mismatch=False):
        self.request('CLEAR_POINTS')
        self.assertTrue(self.request('CONFIRM_POINTS',self.data)['ok'])
        ctx=self.core.outbox[-1]['data']['scene_context']
        root=Path(__file__).resolve().parents[3]/'config/rehab_protocol'
        validate(self.core.outbox[-1],json.loads((root/'vision_request.schema.json').read_text()))
        scene=dict(version=1,source='FROZEN_ALIGNED_DEPTH',context_sha256=fingerprint(ctx),
            **{k:ctx[k] for k in ('revision','frame_number','received_monotonic_s','calibration_sha256')},
            state='PARTIAL' if unknown else 'OBSERVED',cell_size_mm=10,corridor_radius_mm=50,
            image_pixels=10000,valid_depth_pixels=9999,corridor_depth_points=100,
            corridor_cells=10,unknown_cells=unknown,unknown_cell_examples_base_xy_mm=[],
            observed_bounds_base_mm={'min':[-650,-300,200],'max':[-500,-100,260]},surface_ceiling_base_mm=260,
            collision_checked=False,execution_eligible=False,note='synthetic test')
        if mismatch:scene['context_sha256']='f'*64
        reply=self.result();reply['data']['scene']=scene
        self.send(reply)
        return ctx,scene

    def test_scene_generates_numbers_without_manual_workspace_or_height(self):
        ctx,scene=self.analyze()
        self.assertEqual(self.core.confirmation.value['state'],'CONFIRMED')
        p=self.nc.effective_profile()
        self.assertFalse(profile_errors(p))
        self.assertEqual(p['surface_ceiling_base_mm'],260)
        self.assertGreaterEqual(p['safe_transfer_height_base_mm'],300)
        self.authorized_fixture()
        self.assertIn('整臂碰撞','；'.join(self.nc.blockers()))
        self.assertFalse(self.request('PREPARE_NONCONTACT',self.command)['ok'])
        self.assertFalse(self.worker.calls)
        root=Path(__file__).resolve().parents[3]/'config/rehab_protocol'
        status=self.core.status()
        validate(status,json.loads((root/'ui_response.schema.json').read_text()))
        self.assertEqual(status['data']['noncontact']['scene_analysis']['workspace_base_mm'],p['workspace_base_mm'])

    def test_unknown_depth_and_late_or_mismatched_scene_do_not_authorize_motion(self):
        self.analyze(unknown=3)
        self.assertIn('未知','；'.join(self.nc.blockers()))
        self.request('CLEAR_POINTS')
        self.assertIsNone(self.core.confirmation.scene)
        self.analyze(mismatch=True)
        self.assertEqual(self.core.confirmation.value['state'],'FAILED')
        self.assertIsNone(self.core.confirmation.scene)

    def test_final_pixel_change_and_feedback_motion_retire_scene(self):
        self.analyze()
        self.cache.value['joint_positions_deg'][0]=1
        self.device.poll(self.now,None,False);self.core.confirmation.tick()
        self.assertIsNone(self.core.confirmation.scene)
        self.assertNotIn('scene_analysis',self.nc.status())

    def test_missing_scene_never_reuses_manual_values(self):
        self.assertIn('确认穴位','；'.join(self.nc.blockers()))
        self.assertFalse(self.request('PREPARE_NONCONTACT',self.command)['ok'])
        self.assertFalse(self.worker.calls)

    def test_nonzero_tcp_is_not_mislabelled_as_flange(self):
        self.request('CLEAR_POINTS')
        self.cache.value['configured_tool_pose_mm_deg'][2]=20
        self.device.poll(self.now,None,False)
        response=self.request('CONFIRM_POINTS',self.data)
        self.assertFalse(response['ok'])
        self.assertIn('TCP/UCS',response['message'])
        self.assertIsNone(self.core.confirmation.value)
