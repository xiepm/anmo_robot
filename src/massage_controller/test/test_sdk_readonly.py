import socket
import threading
import time
import unittest
from collections import deque
from massage_controller.sdk_readonly import ReadOnlyReader, parse_actual_position


class ReadOnlyTests(unittest.TestCase):
    def reader(self):
        r=ReadOnlyReader.__new__(ReadOnlyReader)
        r.timeout_s=.08;r.connected=True;r.audit=deque()
        return r

    def test_parse_joint_tcp_tool_user_fields(self):
        p=parse_actual_position([str(i) for i in range(24)])
        self.assertEqual(p['joint_positions_deg'],[0,1,2,3,4,5])
        self.assertEqual(p['tcp_pose']['xyz_mm'],[6,7,8])
        self.assertEqual(p['tcp_pose']['rxyz_deg'],[9,10,11])
        self.assertEqual(p['configured_tool_pose_mm_deg'],[12,13,14,15,16,17])
        self.assertEqual(p['configured_user_pose_mm_deg'],[18,19,20,21,22,23])

    def test_reject_bad_position(self):
        for raw in (['0']*23,['0']*25,['NaN']*24,['Inf']*24,['bad']*24):
            with self.assertRaises(ValueError):parse_actual_position(raw)

    def test_write_commands_never_reach_socket(self):
        r=self.reader()
        for name in ('MoveL','GrpEnable','GrpStop','SetSimulation','SetBoxDO'):
            with self.assertRaises(ValueError):r.exchange(None,name+',0,;')
        self.assertEqual(len(r.audit),0)

    def test_fragmented_response(self):
        left,right=socket.socketpair();r=self.reader()
        def peer():
            right.recv(4096)
            right.sendall(b'ReadActPos,OK,1,')
            time.sleep(.01);right.sendall(b'2,;')
        thread=threading.Thread(target=peer);thread.start()
        try:self.assertEqual(r.exchange(left,'ReadActPos,0,;'),'ReadActPos,OK,1,2,;')
        finally:thread.join();left.close();right.close()

    def test_deadline_closes_socket_and_invalidates(self):
        left,right=socket.socketpair();r=self.reader();began=time.monotonic()
        try:
            with self.assertRaises(TimeoutError):r.exchange(left,'ReadActPos,0,;')
            self.assertLess(time.monotonic()-began,.2)
            self.assertFalse(r.connected);self.assertEqual(left.fileno(),-1)
        finally:left.close();right.close()
