"""Explicit T_target_source convention; metres, radians, optical +Z forward."""
import numpy as np
from scipy.spatial.transform import Rotation
from .geometry import deproject

HUMAN_FRAME='human_frame'

def matrix(transform):
    t=transform.transform if hasattr(transform,'transform') else transform
    q=t.rotation;p=t.translation
    result=np.eye(4)
    result[:3,:3]=Rotation.from_quat([q.x,q.y,q.z,q.w]).as_matrix()
    result[:3,3]=[p.x,p.y,p.z]
    return result

def inverse(t):
    result=np.eye(4);result[:3,:3]=t[:3,:3].T;result[:3,3]=-t[:3,:3].T@t[:3,3]
    return result

def points(t,p):
    p=np.asarray(p,dtype=float)
    return p@t[:3,:3].T+t[:3,3]

def same_pose(a,b,position_tol=.001,angle_tol=.002):
    return np.linalg.norm(a[:3,3]-b[:3,3])<=position_tol and Rotation.from_matrix(a[:3,:3]@b[:3,:3].T).magnitude()<=angle_tol

def pixel_mapping(u,v,depth,k,width,height,t_world_camera,t_world_human):
    camera=np.asarray(deproject(u,v,depth,k,width,height))
    world=points(t_world_camera,camera)
    human=points(inverse(t_world_human),world)
    return {'pixel':[int(u),int(v)],'camera_m':camera.tolist(),'world_m':world.tolist(),'human_m':human.tolist()}

def validate_body_path(path):
    p=np.asarray(path,dtype=float)
    if p.ndim!=2 or p.shape[1]!=3 or not 2<=len(p)<=256 or not np.isfinite(p).all():
        raise ValueError('轨迹需要 2–256 个有效人体坐标点')
    if np.any(np.abs(p[:,0])>.09) or np.any(np.abs(p[:,1])>.13) or np.any(np.abs(p[:,2])>.008):
        raise ValueError('轨迹超出人体背部中央平面区域')
    if np.any(np.linalg.norm(np.diff(p,axis=0),axis=1)<1e-5):
        raise ValueError('存在重复的相邻轨迹点')
    return p

def template_path(kind,region='背部中央'):
    cx={'背部中央':0.,'背部左侧':-.045,'背部右侧':.045}[region]
    rx=.05 if region=='背部中央' else .03
    if kind=='纵向直线':p=[[cx,-.09,0],[cx,.09,0]]
    elif kind=='横向往返':p=[[cx-rx,-.08,0],[cx+rx,-.08,0],[cx+rx,0,0],[cx-rx,0,0],[cx-rx,.08,0],[cx+rx,.08,0]]
    elif kind=='椭圆轨迹':
        a=np.linspace(0,2*np.pi,49);p=np.c_[cx+rx*np.cos(a),.085*np.sin(a),np.zeros(len(a))]
    else:raise ValueError('请在相机画面中点选至少两个点')
    return validate_body_path(p)
