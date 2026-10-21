"""URDF forward kinematics and bounded numerical IK, independent of ROS."""
import xml.etree.ElementTree as ET
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

DOWN = np.array([[0., 1., 0.], [1., 0., 0.], [0., 0., -1.]])


def origin(element):
    t = np.eye(4)
    if element is not None:
        t[:3, 3] = np.fromstring(element.get('xyz', '0 0 0'), sep=' ')
        t[:3, :3] = Rotation.from_euler('xyz', np.fromstring(element.get('rpy', '0 0 0'), sep=' ')).as_matrix()
    return t


class Kinematics:
    def __init__(self, urdf, joint_names=None, world_frame='world', tcp_frame='massage_tcp'):
        self.root = ET.fromstring(urdf)
        self.names = list(joint_names) if joint_names is not None else [f'elfin_joint{i}' for i in range(1, 7)]
        if not self.names or len(set(self.names))!=len(self.names):raise ValueError('Joint order must be explicit and unique')
        self.world_frame,self.tcp_frame=world_frame,tcp_frame
        self.joint_types={j.get('name'):j.get('type') for j in self.root.findall('joint')}
        if self.root.find(f"link[@name='{world_frame}']") is None or self.root.find(f"link[@name='{tcp_frame}']") is None:
            raise ValueError('Configured base / TCP link is missing from URDF')
        self.joints = {}
        self.lower, self.upper = [], []
        for j in self.root.findall('joint'):
            self.joints[j.find('child').get('link')] = (
                j.find('parent').get('link'), j.get('name'), origin(j.find('origin')),
                np.fromstring(j.find('axis').get('xyz', '0 0 1'), sep=' ') if j.find('axis') is not None else np.array([0.,0.,1.]))
        for name in self.names:
            if self.joint_types.get(name) not in ('revolute','prismatic'):
                raise ValueError('An actuated joint must have finite revolute / prismatic limits: '+name)
            limit = self.root.find(f"joint[@name='{name}']/limit")
            if limit is None or limit.get('lower') is None or limit.get('upper') is None:
                raise ValueError('Missing finite joint limits: '+name)
            self.lower.append(float(limit.get('lower')))
            self.upper.append(float(limit.get('upper')))
        self.lower, self.upper = np.array(self.lower), np.array(self.upper)
        if not np.isfinite([self.lower,self.upper]).all() or np.any(self.lower>=self.upper):raise ValueError('Invalid joint limits')

    def transforms(self, q):
        q = np.asarray(q, dtype=float)
        if q.shape != (len(self.names),) or not np.isfinite(q).all():
            raise ValueError(f'Expected {len(self.names)} finite joint positions')
        values = dict(zip(self.names, q))
        cache = {self.world_frame: np.eye(4)}
        def visit(link):
            if link not in cache:
                parent, name, fixed, axis = self.joints[link]
                motion = np.eye(4)
                if name in values:
                    if self.joint_types[name]=='prismatic':motion[:3,3]=axis*values[name]
                    else:motion[:3, :3] = Rotation.from_rotvec(axis * values[name]).as_matrix()
                cache[link] = visit(parent) @ fixed @ motion
            return cache[link]
        for child in self.joints:
            visit(child)
        return cache

    def fk(self, q):
        return self.transforms(q)[self.tcp_frame]

    def ik(self, xyz, seed, rotation=DOWN, cancelled=lambda: False):
        target = np.asarray(xyz, dtype=float)
        if target.shape != (3,) or not np.isfinite(target).all():
            raise ValueError('Invalid Cartesian target')
        def residual(q):
            if cancelled():
                raise InterruptedError('Planning cancelled')
            pose = self.fk(q)
            return np.r_[pose[:3,3] - target,
                         0.3 * Rotation.from_matrix(rotation @ pose[:3,:3].T).as_rotvec()]
        result = least_squares(residual, np.clip(seed, self.lower+1e-7, self.upper-1e-7),
                               bounds=(self.lower+1e-7, self.upper-1e-7), max_nfev=120,
                               ftol=1e-9, xtol=1e-9, gtol=1e-9)
        pose = self.fk(result.x)
        if np.linalg.norm(pose[:3,3]-target) > 0.0002 or np.linalg.norm(
                Rotation.from_matrix(rotation @ pose[:3,:3].T).as_rotvec()) > 0.002:
            raise ValueError('No converged IK solution within joint limits')
        return result.x
