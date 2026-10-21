import unittest
from unittest.mock import patch
from rehab_vision.base_client import BaseClient

class BaseReconnectTests(unittest.TestCase):
    def test_continuous_preview_does_not_extend_retry_deadline(self):
        client = BaseClient()
        with patch('rehab_vision.base_client.time.monotonic', return_value=10.) as clock, \
             patch('rehab_vision.base_client.connect', side_effect=OSError('offline')) as connect:
            first = client.project([], {}, 10.)
            self.assertIn('offline', first['data']['error'])
            self.assertEqual(client.retry_at, 11.)
            for now in (10.1, 10.3, 10.6, 10.9):
                clock.return_value = now
                result = client.project([], {}, now)
                self.assertIn('等待控制坐标服务重连', result['data']['error'])
                self.assertEqual(client.retry_at, 11.)
            self.assertEqual(connect.call_count, 1)
            clock.return_value = 11.01
            client.project([], {}, 11.01)
            self.assertEqual(connect.call_count, 2)
            self.assertAlmostEqual(client.retry_at, 12.01)

if __name__ == '__main__': unittest.main()
