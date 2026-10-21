"""Pure coordinate primitives and an explicitly synthetic, obstacle-free fixture.
This module does NOT certify anatomical accuracy, real collision paths or hand-eye data.
"""
import math


def rigid(matrix):
    if len(matrix)!=4 or any(len(row)!=4 for row in matrix):raise ValueError('变换必须为4x4')
    if any(type(x) not in (int,float) or not math.isfinite(x) for row in matrix for x in row):raise ValueError('变换含非有限值')
    if any(abs(matrix[3][i]-[0,0,0,1][i])>1e-8 for i in range(4)):raise ValueError('齐次末行错误')
    r=matrix
    for i in range(3):
        for j in range(3):
            if abs(sum(r[k][i]*r[k][j] for k in range(3))-(1 if i==j else 0))>1e-6:
                raise ValueError('旋转不正交')
    det=(r[0][0]*(r[1][1]*r[2][2]-r[1][2]*r[2][1])
         -r[0][1]*(r[1][0]*r[2][2]-r[1][2]*r[2][0])
         +r[0][2]*(r[1][0]*r[2][1]-r[1][1]*r[2][0]))
    if abs(det-1)>1e-6:raise ValueError('旋转行列式错误')
    return matrix


def compose(a,b):
    rigid(a);rigid(b)
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def camera_to_base_m(camera_point_mm, base_flange, flange_camera):
    if len(camera_point_mm)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in camera_point_mm):
        raise ValueError('相机点必须为三个有限数')
    chain=compose(base_flange,flange_camera)
    p=[x/1000 for x in camera_point_mm]+[1]
    return tuple(sum(chain[i][j]*p[j] for j in range(4)) for i in range(3))


class SimulationGeometry:
    """Synthetic identity frames, +Z surface normal, empty +/-2m workspace.
    No calibration file is generated. This class is unavailable in REAL mode.
    """
    def __init__(self,mode):
        if mode!='SIMULATION':raise ValueError('模拟工装不可用于真实运动')
        self.identity=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
        self.base_points={}
        self.targets={}

    def locate(self,points):
        self.base_points={}
        for p in points:
            if p['coordinate_valid']:
                target=camera_to_base_m(p['camera_point_mm'],self.identity,self.identity)
                self.check(target)
                self.base_points[(p['name'],p['side'])]=target

    @staticmethod
    def check(p):
        if any(not -2<=x<=2 for x in p):raise ValueError('点或路径超出模拟工装范围')

    def plan(self,plan):
        targets={}
        for p in plan['points']:
            key=(p['name'],p['side'])
            if key not in self.base_points:raise ValueError('缺少本次基座坐标')
            x,y,z=self.base_points[key]
            gap=plan.get('gap_mm',0)/1000
            near=(x,y,z+gap);lift=(x,y,z+gap+.05)
            self.check(near);self.check(lift)
            targets[key]={'APPROACH':near,'RETRACT':lift,'TRANSFER':lift}
        # This fixture is convex and obstacle-free: endpoint bounds cover every
        # linear segment. It is not a collision planner for a robot or a person.
        self.targets=targets

    def target(self,point,step):
        return self.targets[(point['name'],point['side'])][step]
