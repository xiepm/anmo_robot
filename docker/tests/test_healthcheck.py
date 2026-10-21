import contextlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('demo_healthcheck', Path(__file__).resolve().parents[1]/'healthcheck.py')
healthcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healthcheck)


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ready = Path(self.directory.name)/'ready.json'
        self.data = dict(state='ready', mode='SIMULATION', children=dict(control=1, vision=2, nginx=3))
        self.ready.write_text(json.dumps(self.data))
        self.connections, self.requests, self.pids = [], [], []
        self.dead = None
        self.http_status = 200
        self.refuse_port = None

    def alive(self, pid):
        self.pids.append(pid)
        return pid != self.dead

    def connect(self, address, *, timeout):
        self.connections.append((address, timeout))
        if address[1] == self.refuse_port: raise ConnectionRefusedError('fake port unavailable')
        # No send/recv methods: probing must never make an application request.
        return contextlib.nullcontext(object())

    def request(self, uri, *, timeout):
        self.requests.append((uri, timeout))
        response = type('Response', (), {'status': self.http_status})()
        return contextlib.nullcontext(response)

    def check(self):
        return healthcheck.check_health(ready_file=self.ready, alive=self.alive,
                                       connect=self.connect, request=self.request)

    def test_ready_checks_children_loopback_ports_and_nginx_without_websocket(self):
        self.assertTrue(self.check())
        self.assertEqual(self.pids, [1, 2, 3])
        self.assertEqual(self.connections, [(('127.0.0.1', 8765), .5), (('127.0.0.1', 8766), .5)])
        self.assertEqual(self.requests, [('http://127.0.0.1:8080/healthz', .5)])

    def test_no_marker_or_invalid_marker_is_not_ready(self):
        self.ready.unlink()
        with self.assertRaises(OSError): self.check()
        for data in ([], {}, dict(self.data, mode='REAL'), dict(self.data, children={'control': 1}),
                     dict(self.data, children=dict(control=True, vision=2, nginx=3))):
            with self.subTest(data=data):
                self.ready.write_text(json.dumps(data))
                with self.assertRaises(ValueError): self.check()

    def test_exited_child_is_unhealthy_before_any_network_probe(self):
        self.dead = 2
        with self.assertRaises(ValueError): self.check()
        self.assertEqual(self.connections, [])
        self.assertEqual(self.requests, [])

    def test_missing_control_port_is_unhealthy(self):
        for port in (8765, 8766):
            with self.subTest(port=port):
                self.refuse_port = port
                with self.assertRaises(OSError): self.check()

    def test_failed_nginx_is_unhealthy(self):
        self.http_status = 503
        with self.assertRaises(ValueError): self.check()


if __name__ == '__main__':
    unittest.main()
