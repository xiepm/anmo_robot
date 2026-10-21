import json
import sys
import unittest
from pathlib import Path
import numpy as np
from jsonschema import Draft202012Validator
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from frozen_depth import FrozenDepth
from depth_scene import context_digest, pixel_rays

P = dict(name='大椎', side='MIDLINE', x=110., y=100., coordinate_valid=True)
CONTEXT = dict(revision='12345678-1234-1234-1234-123456789abc', frame_number=1,
    received_monotonic_s=10., calibration_sha256='a'*64,
    T_base_camera=[[1,0,0,0],[0,-1,0,0],[0,0,-1,1],[0,0,0,1]],
    start_flange_mm=[0,0,500], corridor_radius_mm=50, cell_size_mm=10)

def deproject(_, pixel, d):
    return [(pixel[0]-100)*d/200, (pixel[1]-100)*d/200, d]

class SceneTests(unittest.TestCase):
    def test_precomputed_rays_match_sdk_path_and_are_immutable(self):
        rays=pixel_rays(None, deproject, 201, 201)
        depth=np.full((201,201), .6)
        f=FrozenDepth(depth, None, deproject, [P], rays=rays)
        points=f.resolve([{k:P[k] for k in ('name','side','x','y')}])
        self.assertEqual(f.scene.analyze(CONTEXT, points), self.analyze(depth)[1])
        self.assertIs(f.scene.rays, rays)
        with self.assertRaises(ValueError): rays[0,0]=0
        with self.assertRaises(ValueError): FrozenDepth(depth, None, deproject, [P], rays=rays[:1])

    def analyze(self, depth, point=P, context=CONTEXT):
        frozen=FrozenDepth(depth, None, deproject, [point])
        points=frozen.resolve([{k:point[k] for k in ('name','side','x','y')}])
        return frozen, frozen.scene.analyze(context, points)

    def test_full_plane_has_measured_coverage_and_no_execution_approval(self):
        _, s=self.analyze(np.full((201,201), .6))
        self.assertEqual(s['state'], 'OBSERVED')
        self.assertEqual(s['unknown_cells'], 0)
        self.assertEqual(s['coverage_diagnostics']['incomplete_quadrant_cells'],0)
        self.assertEqual(s['coverage_diagnostics']['invalid_ray_cells'],0)
        self.assertEqual(s['surface_ceiling_base_mm'], 400)
        self.assertFalse(s['collision_checked']);self.assertFalse(s['execution_eligible'])
        self.assertEqual(s['context_sha256'], context_digest(CONTEXT))

    def test_dense_obstacle_between_points_is_not_lost_or_percentile_filtered(self):
        depth=np.full((201,201), .6)
        depth[102,105]=.55  # Not an acupoint: a single higher valid sample counts.
        depth[180,180]=.2  # Outside corridor, does not set this route's height.
        _,s=self.analyze(depth)
        self.assertEqual(s['surface_ceiling_base_mm'], 450)

    def test_holes_and_out_of_view_remain_unknown(self):
        depth=np.full((201,201), .6);depth[95:99,95:99]=0
        _,s=self.analyze(depth)
        self.assertEqual(s['state'],'PARTIAL');self.assertGreater(s['unknown_cells'],0)
        self.assertTrue(s['unknown_cell_examples_base_xy_mm'])
        counts=s['coverage_diagnostics']
        self.assertGreater(counts['invalid_ray_cells'],0)
        self.assertEqual(s['unknown_cells'],counts['incomplete_quadrant_cells']+
                         counts['invalid_ray_cells']-counts['overlapping_cause_cells'])
        _,edge=self.analyze(np.full((201,201), .6),dict(P,x=198.))
        self.assertEqual(edge['state'],'PARTIAL')
        self.assertGreater(edge['coverage_diagnostics']['incomplete_quadrant_cells'],0)
        self.assertEqual(edge['coverage_diagnostics']['invalid_ray_cells'],0)

    def test_frozen_depth_is_used_and_context_changes_invalidate_digest(self):
        depth=np.full((201,201), .6)
        f,s=self.analyze(depth);depth[:]=.1
        points=f.resolve([{k:P[k] for k in ('name','side','x','y')}])
        again=f.scene.analyze(dict(CONTEXT, revision='22345678-1234-1234-1234-123456789abc'),points)
        self.assertEqual(again['surface_ceiling_base_mm'],400)
        self.assertNotEqual(again['context_sha256'],s['context_sha256'])

    def test_invalid_transform_or_excessive_extent_fails(self):
        for ctx in (dict(CONTEXT,T_base_camera=np.zeros((4,4)).tolist()),
                    dict(CONTEXT,start_flange_mm=[1e8,0,500])):
            with self.assertRaises(ValueError): self.analyze(np.full((201,201),.6),context=ctx)

    def test_all_unknown_cells_survive_shared_scene_protocol_and_route_check(self):
        root = Path(__file__).resolve().parents[2]
        context = dict(CONTEXT, start_flange_mm=[800,0,500])
        frozen, scene = self.analyze(np.full((201,201),.6), context=context)
        cells = scene['unknown_cell_examples_base_xy_mm']
        self.assertGreater(scene['unknown_cells'], 64)
        self.assertEqual(len(cells), scene['unknown_cells'])
        self.assertEqual(len(set(map(tuple, cells))), scene['unknown_cells'])
        points = frozen.resolve([{k:P[k] for k in ('name','side','x','y')}])
        data = {k: context[k] for k in ('revision','frame_number','received_monotonic_s')}
        data.update(ok=True, error='', points=points, scene=scene)
        message = dict(type='REFINE_RESULT', data=data)
        encoded = json.dumps(message, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.assertLessEqual(len(encoded), 65536)
        self.assertLessEqual(len(json.dumps(scene, ensure_ascii=False).encode('utf-8')), 32768)
        schema = json.loads((root/'config/rehab_protocol/vision_message.schema.json').read_text())
        Draft202012Validator(schema).validate(json.loads(encoded))

        def scene_schemas(value):
            if isinstance(value, dict):
                if 'unknown_cell_examples_base_xy_mm' in value.get('properties', {}):
                    yield value
                for item in value.values(): yield from scene_schemas(item)
            elif isinstance(value, list):
                for item in value: yield from scene_schemas(item)
        ui = json.loads((root/'config/rehab_protocol/ui_response.schema.json').read_text())
        matches = list(scene_schemas(ui))
        self.assertEqual(len(matches), 1)
        Draft202012Validator(matches[0]).validate(dict(
            scene, workspace_base_mm=None, safe_transfer_height_base_mm=None))

        sys.path.insert(0, str(root/'src/massage_controller'))
        from massage_controller.route_coverage import unknown_clearance
        plan = dict(start_pose=dict(x_mm=800, y_mm=0),
                    points=[dict(pose=dict(x_mm=30, y_mm=0))])
        coverage = unknown_clearance(plan, scene, [0,0,0])
        self.assertTrue(coverage['all_unknown_cells_included'])
        self.assertEqual(coverage['checked_cells'], scene['unknown_cells'])
        self.assertEqual(coverage['assessment'], 'CENTERLINE_NEAR_UNKNOWN')
        self.assertGreater(coverage['possibly_intersecting_cells'], 0)
        self.assertFalse(coverage['execution_eligible'])
        self.assertFalse(scene['execution_eligible'])

    def test_oversized_complete_scene_fails_instead_of_truncating_or_exceeding_transport(self):
        with self.assertRaisesRegex(ValueError, '64 KiB.*未截断'):
            self.analyze(np.full((201,201),.6),
                         context=dict(CONTEXT, start_flange_mm=[5000,0,500]))

    def test_scene_size_reserves_room_for_ui_status_fields(self):
        with self.assertRaisesRegex(ValueError, '32 KiB.*未截断'):
            self.analyze(np.full((201,201),.6),
                         context=dict(CONTEXT, start_flange_mm=[2000,0,500]))

if __name__=='__main__':unittest.main()
