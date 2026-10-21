import asyncio
import json
import sys
import threading
import unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'web/scripts'),str(ROOT/'zhenjiu')]
from rehab_vision.client import Session
from rehab_vision.contract import Contract
from live_locate_bridge import LiveLocateBridge
from vision_preview import LatestFrame
from frozen_depth import FrozenDepth
from test_depth_scene import CONTEXT, P, deproject


class SceneTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_snapshot_to_shared_scene_result_and_stale_capture_rejection(self):
        frames=LatestFrame()
        depth=FrozenDepth(np.full((201,201),.6),None,deproject,[P])
        frames.put_snapshot(b'test',1,10.,depth_resolver=depth)
        bridge=LiveLocateBridge();bridge.refine_snapshot=frames.resolve_points
        session=Session(bridge,Contract())
        data={k:CONTEXT[k] for k in ('revision','frame_number','received_monotonic_s')}
        data.update(points=[{k:P[k] for k in ('name','side','x','y')}],scene_context=CONTEXT)
        await session.handle(json.dumps(dict(cmd='REFINE_POINTS',data=data)))
        response=json.loads(session.outgoing.get_nowait())
        self.assertTrue(response['data']['ok'])
        self.assertEqual(response['data']['scene']['surface_ceiling_base_mm'],400)
        self.assertEqual(response['data']['scene']['state'],'OBSERVED')
        frames.put_snapshot(b'new',2,11.,depth_resolver=depth)
        await session.handle(json.dumps(dict(cmd='REFINE_POINTS',data=data)))
        failed=json.loads(session.outgoing.get_nowait())
        self.assertFalse(failed['data']['ok']);self.assertNotIn('scene',failed['data'])

    async def test_heavy_refinement_does_not_hold_camera_health_lock(self):
        started,release=threading.Event(),threading.Event()
        def refine(data):
            started.set();release.wait(2);return []
        bridge=LiveLocateBridge();bridge.refine_snapshot=refine
        session=Session(bridge,Contract())
        data={k:CONTEXT[k] for k in ('revision','frame_number','received_monotonic_s')}
        data['points']=[{k:P[k] for k in ('name','side','x','y')}]
        task=asyncio.create_task(session.handle(json.dumps(dict(cmd='REFINE_POINTS',data=data))))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            health=await asyncio.wait_for(asyncio.to_thread(bridge.health),.2)
            self.assertEqual(health['mode'],'REAL')
        finally:
            release.set();await task

if __name__=='__main__':unittest.main()
