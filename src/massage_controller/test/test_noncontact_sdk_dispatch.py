"""Exercise the shipped SDK serializer with fake I/O and fake robot feedback.

No SDK constructor, socket, device connection or physical command is used.
Dense validation samples must never become the controller's motion queue.
"""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from massage_controller.route_review import candidate_samples
from massage_controller.waypoint_motion import MotionReader, WaypointSession
from massage_controller.waypoint_plan import pose
from test_waypoint import Clock, FakeReader, plan


class FakeWire:
    def __init__(self):
        self.commands = []

    def sendAndRecv(self, command, _result):
        self.commands.append(command)
        return 0


class SDKDispatchReader(FakeReader):
    def __init__(self, clock, sdk):
        super().__init__(clock)
        self.sdk = sdk
        self.feedback_joints = [11., -22., 33., -44., 55., -66.]

    def sample(self):
        sample = super().sample()
        sample['joint_positions_deg'] = self.feedback_joints[:]
        return sample

    def move(self, target, joints, selected_plan, command_id, radius=0):
        # Use the real adapter and vendor string serializer, then let the
        # isolated feedback double acknowledge movement for session validation.
        MotionReader.move(self, target, joints, selected_plan, command_id, radius)
        super().move(target, joints, selected_plan, command_id, radius)


class NoncontactSDKDispatchTests(unittest.TestCase):
    def setUp(self):
        # A test regression must fail rather than accidentally reach hardware.
        guard = patch('socket.socket', side_effect=AssertionError('hardware I/O forbidden in fake test'))
        guard.start()
        self.addCleanup(guard.stop)
        sdk_path = (Path(__file__).resolve().parents[3] /
                    'SDK/HUAYAN/python API接口/HuayanRobotPythonSDK_V1.1.1.1/CPS.py')
        if not sdk_path.is_file():
            self.skipTest('Optional vendor SDK is not installed; see SDK/README.md')
        spec = importlib.util.spec_from_file_location('fake_wire_vendor_sdk', sdk_path)
        vendor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vendor)
        self.wire = FakeWire()
        self.sdk = vendor.CPSClient.__new__(vendor.CPSClient)
        self.sdk.g_clients = [self.wire]

    def execute(self, distance):
        selected = plan()
        selected.update(target_reference='FLANGE', tool_pose_mm_deg=[0, 0, 20, 0, 0, 0],
                        speed=10, acceleration=20, max_segment_mm=500,
                        auto_subdivide=True, move_timeout_s=180,
                        workspace_mm=dict(min=[-10, -10, 80], max=[600, 10, 120]))
        final = dict(name='confirmed_point', pose=dict(selected['start_pose'], x_mm=distance), dwell_s=.2)
        selected['points'] = [final]
        review_samples = list(candidate_samples(selected, step_mm=10.))
        clock = Clock()
        reader = SDKDispatchReader(clock, self.sdk)
        events = []
        session = WaypointSession(selected, reader, events.append, clock, clock.sleep)
        reader.on_sample = lambda: session.request_start() if session.status_snapshot()['state'] == 'READY' else None
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'COMPLETED', session.status_snapshot())
        self.assertTrue(session.status_snapshot()['stop_confirmed'])
        self.assertEqual(reader.moves[-1], pose(final['pose'], 'confirmed_point'))
        self.assertEqual([event['point'] for event in events if event.get('step') == 'DWELL'], ['confirmed_point'])
        for command in self.wire.commands:
            values = command.split(',')
            self.assertEqual(values[0:2], ['WayPointEx', '0'])
            self.assertEqual(list(map(float, values[8:14])), reader.feedback_joints)
            # On the wire UCS precedes TCP, even though the Python arguments
            # take TCP before UCS. Both must remain zero for Base/flange targets.
            self.assertEqual(list(map(float, values[14:26])), [0.] * 12)
            self.assertEqual(list(map(float, values[26:28])), [10., 20.])
            self.assertEqual(values[29:34], ['1', '0', '0', '0', '0'])
            self.assertTrue(values[34].startswith(session.run_id + '_'))
            self.assertEqual(values[35], ';')
        return review_samples, reader

    def test_one_movel_target_is_sent_instead_of_45_validation_samples(self):
        samples, reader = self.execute(450.)
        self.assertEqual(len(samples), 45)
        self.assertEqual(len(self.wire.commands), 1)
        self.assertEqual(reader.moves, [[450., 0, 100, 0, 0, 0]])
        self.assertEqual(reader.radii, [0])
        self.assertEqual(list(map(float, self.wire.commands[0].split(',')[2:8])), reader.moves[0])

    def test_only_overlong_segment_is_split_without_changing_confirmed_endpoint(self):
        samples, reader = self.execute(550.)
        self.assertEqual(len(samples), 55)
        self.assertEqual(len(self.wire.commands), 2)
        self.assertEqual(reader.moves, [[275., 0, 100, 0, 0, 0], [550., 0, 100, 0, 0, 0]])
        self.assertEqual(reader.radii, [5., 0])
        for command, target in zip(self.wire.commands, reader.moves):
            self.assertEqual(list(map(float, command.split(',')[2:8])), target)


if __name__ == '__main__':
    unittest.main()
