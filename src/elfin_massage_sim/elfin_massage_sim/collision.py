"""Standalone FCL checks on URDF collision meshes; no planning framework."""
from pathlib import Path
from itertools import combinations
import struct
import numpy as np
import fcl
from .kinematics import origin

# Mechanically adjacent/overlapping housings from the manufacturer's Elfin5 L
# model. These are explicit model exclusions, not disabling arm/world collision.
EXCLUDED = [('elfin_base','elfin_link1'), ('elfin_base','elfin_link2'), ('elfin_base','elfin_link3'),
 ('elfin_link1','elfin_link2'), ('elfin_link1','elfin_link3'), ('elfin_link2','elfin_link3'),
 ('elfin_link2','elfin_link4'), ('elfin_link2','elfin_link5'), ('elfin_link2','elfin_link6'),
 ('elfin_link3','elfin_link4'), ('elfin_link3','elfin_link5'), ('elfin_link3','elfin_link6'),
 ('elfin_link4','elfin_link5'), ('elfin_link4','elfin_link6'), ('elfin_link5','elfin_link6'),
 ('massage_tool','elfin_link6')]


def stl_vertices(path):
    data = Path(path).read_bytes()
    count = struct.unpack_from('<I', data, 80)[0] if len(data) >= 84 else 0
    if len(data) == 84 + count * 50:
        dt = np.dtype([('normal','<f4',3),('vertices','<f4',(3,3)),('attr','<u2')])
        return np.frombuffer(data, dtype=dt, offset=84, count=count)['vertices'].reshape(-1,3).astype(float)
    rows = [list(map(float, line.split()[1:])) for line in data.decode().splitlines() if line.strip().startswith('vertex ')]
    return np.asarray(rows, dtype=float)


class CollisionChecker:
    def __init__(self, model):
        self.model = model
        self.objects = []
        for link in model.root.findall('link'):
            name = link.get('name')
            for c in link.findall('collision'):
                g = c.find('geometry')
                if g.find('mesh') is not None:
                    mesh = g.find('mesh')
                    filename = mesh.get('filename').removeprefix('file://')
                    if filename.startswith('package://'):
                        from ament_index_python.packages import get_package_share_directory
                        package, rel = filename[10:].split('/',1)
                        filename = str(Path(get_package_share_directory(package))/rel)
                    v = stl_vertices(filename) * np.fromstring(mesh.get('scale','1 1 1'), sep=' ')
                    geom = fcl.BVHModel()
                    geom.beginModel(len(v)//3,len(v))
                    geom.addSubModel(v, np.arange(len(v)).reshape(-1,3))
                    geom.endModel()
                elif g.find('box') is not None:
                    geom = fcl.Box(*np.fromstring(g.find('box').get('size'), sep=' '))
                elif g.find('sphere') is not None:
                    geom = fcl.Sphere(float(g.find('sphere').get('radius')))
                else:
                    raise ValueError(f'Unsupported collision geometry for {name}')
                self.objects.append((name, origin(c.find('origin')), fcl.CollisionObject(geom)))
        excluded = {frozenset(p) for p in EXCLUDED}
        def moving(link):
            while link in model.joints:
                parent, name, _, _ = model.joints[link]
                if name in model.names:
                    return True
                link = parent
            return False
        self.pairs = [(a,b) for a,b in combinations(range(len(self.objects)),2)
                      if self.objects[a][0] != self.objects[b][0]
                      and frozenset([self.objects[a][0],self.objects[b][0]]) not in excluded
                      and (moving(self.objects[a][0]) or moving(self.objects[b][0]))]

    def check(self, q):
        if np.any(q < self.model.lower) or np.any(q > self.model.upper):
            raise ValueError('Joint limit violation')
        tf = self.model.transforms(q)
        for name, local, obj in self.objects:
            t = tf.get(name, np.eye(4)) @ local
            obj.setTransform(fcl.Transform(t[:3,:3], t[:3,3]))
        for a,b in self.pairs:
            result = fcl.CollisionResult()
            if fcl.collide(self.objects[a][2], self.objects[b][2], fcl.CollisionRequest(), result):
                raise ValueError(f'Collision: {self.objects[a][0]} / {self.objects[b][0]}')
