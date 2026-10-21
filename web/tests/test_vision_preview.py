import importlib.util
import json
from pathlib import Path
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

spec = importlib.util.spec_from_file_location('preview', Path(__file__).parents[1] / 'scripts/vision_preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)

class PreviewTests(unittest.TestCase):
    def test_geometry_export_is_bound_to_frozen_photo(self):
        import io
        from types import SimpleNamespace
        import numpy as np
        depth=np.full((2,3),.6);rays=np.ones((2,3,3))
        self.frames.put_snapshot(b'photo',12,45.5,depth_resolver=SimpleNamespace(depth=depth,rays=rays))
        url=self.url+'/snapshot/geometry.npz?frame=12&received=45.5'
        with urlopen(url) as response:
            with np.load(io.BytesIO(response.read()),allow_pickle=False) as data:
                np.testing.assert_equal(data['depth_m'],depth)
                np.testing.assert_equal(data['rays'],rays)
                self.assertEqual(int(data['frame_number']),12)
                self.assertEqual(float(data['received_monotonic_s']),45.5)
        self.frames.put_snapshot(b'replacement',13,46.)
        with self.assertRaises(HTTPError) as stale: urlopen(url)
        self.assertEqual(stale.exception.code,503);stale.exception.close()

    def setUp(self):
        self.frames = preview.LatestFrame()
        self.server = preview.create_server(self.frames, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_snapshot_is_exact_capture_and_does_not_follow_live_frames(self):
        self.frames.put_snapshot(b'captured photo', 12, 45.5)
        self.frames.put(b'next live frame')
        with urlopen(self.url + '/snapshot.jpg?frame=12&received=45.5') as response:
            self.assertEqual(response.read(), b'captured photo')
        for suffix in ('frame=13&received=45.5', 'frame=12&received=46', ''):
            with self.assertRaises(HTTPError) as wrong:
                urlopen(self.url + '/snapshot.jpg?' + suffix)
            self.assertEqual(wrong.exception.code, 503)
            wrong.exception.close()

    def test_plan_selection_is_ordered_and_bound_to_the_original_capture(self):
        calls = []
        def render(selected):
            calls.append(selected)
            return b'only selected markers'
        self.frames.put_snapshot(b'all markers', 12, 45.5, render)
        selected = [dict(name='肩井',side='RIGHT'),dict(name='大椎',side='MIDLINE')]
        url = self.url + '/snapshot.jpg?' + urlencode(dict(frame=12,received=45.5,points=json.dumps(selected)))
        self.frames.put(b'next live frame')
        with urlopen(url) as response:
            self.assertEqual(response.read(),b'only selected markers')
        self.assertEqual(calls,[[('肩井','R'),('大椎',None)]])
        self.frames.put_snapshot(b'new photo',13,46.)
        with self.assertRaises(HTTPError) as stale:
            urlopen(url)
        self.assertEqual(stale.exception.code,503)
        stale.exception.close()
        self.assertEqual(len(calls),1)

    def test_malformed_selection_cannot_fall_back_to_full_photo(self):
        self.frames.put_snapshot(b'all markers',12,45.5,lambda _: b'filtered')
        for points in [[], {}, [dict(name='肩井',side='L')],
                       [dict(name='大椎',side='MIDLINE')]*2]:
            url = self.url + '/snapshot.jpg?' + urlencode(dict(frame=12,received=45.5,points=json.dumps(points)))
            with self.assertRaises(HTTPError) as invalid:
                urlopen(url)
            self.assertEqual(invalid.exception.code,503)
            invalid.exception.close()

    def test_editable_photo_and_pixels_are_immutable_and_bound_to_one_capture(self):
        data = dict(frame_number=12, received_monotonic_s=45.5, width=640, height=480,
                    points=[dict(name='大椎',side='MIDLINE',x=123.,y=234.,coordinate_valid=True)])
        self.frames.put_snapshot(b'annotated',12,45.5,raw_jpeg=b'raw photo',editor_data=data)
        data['points'][0]['x'] = 999
        self.frames.put(b'next live frame')
        with urlopen(self.url+'/snapshot/raw.jpg?frame=12&received=45.5') as response:
            self.assertEqual(response.read(), b'raw photo')
        with urlopen(self.url+'/snapshot/points.json?frame=12&received=45.5') as response:
            self.assertEqual(json.load(response)['points'][0]['x'],123.)
        for path in ('raw.jpg','points.json'):
            with self.assertRaises(HTTPError) as stale:
                urlopen(self.url+f'/snapshot/{path}?frame=13&received=45.5')
            self.assertEqual(stale.exception.code,503)
            stale.exception.close()
        # Updating the photo retires both resources together.
        self.frames.put_snapshot(b'new',13,46.)
        self.assertIsNone(self.frames.get_editor(12,45.5))
        self.assertIsNone(self.frames.get_editor(12,45.5,raw=True))

    def test_missing_and_stale_frame_never_return_successful_image(self):
        with self.assertRaises(HTTPError) as missing:
            urlopen(self.url + '/frame.jpg')
        self.assertEqual(missing.exception.code, 503)
        missing.exception.close()
        self.frames.put(b'jpeg fixture')
        self.frames.updated_at = time.monotonic() - 2
        with self.assertRaises(HTTPError) as stale:
            urlopen(self.url + '/frame.jpg')
        self.assertEqual(stale.exception.code, 503)
        stale.exception.close()
        with urlopen(self.url + '/health') as response:
            self.assertFalse(json.load(response)['fresh'])

    def test_latest_only_no_cache_local_cors_and_no_write_endpoint(self):
        self.frames.put(b'old frame')
        self.frames.put(b'new frame')
        with urlopen(Request(self.url + '/frame.jpg', headers={'Origin': 'http://127.0.0.1:5173'})) as response:
            self.assertEqual(response.read(), b'new frame')
            self.assertEqual(response.headers['X-Preview-Sequence'], '2')
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'http://127.0.0.1:5173')
        with urlopen(Request(self.url + '/health', headers={'Origin': 'https://example.com'})) as response:
            self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
        with self.assertRaises(HTTPError) as post:
            urlopen(Request(self.url + '/frame.jpg', data=b'command'))
        self.assertEqual(post.exception.code, 501)
        post.exception.close()
        with self.assertRaises(HTTPError) as unknown:
            urlopen(self.url + '/unknown')
        self.assertEqual(unknown.exception.code, 404)
        unknown.exception.close()

    def test_http_stays_available_across_camera_disconnect_and_reconnect(self):
        self.frames.put(b'before unplug')
        self.frames.invalidate('camera removed')
        with urlopen(self.url+'/health') as response:
            value=json.load(response)
            self.assertFalse(value['fresh']);self.assertEqual(value['camera_state'],'RECONNECTING')
        with self.assertRaises(HTTPError) as gone:urlopen(self.url+'/frame.jpg')
        self.assertEqual(gone.exception.code,503);gone.exception.close()
        self.frames.put(b'after replug')
        with urlopen(self.url+'/frame.jpg') as response:
            self.assertEqual(response.read(),b'after replug')

if __name__ == '__main__':
    unittest.main()
