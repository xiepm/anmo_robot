"""Camera ranges in metres, using an exposure transform and registered back ROI."""
import numpy as np
from .frames import inverse,points


def camera_body_distance(depth,k,t_world_camera,t_world_human,encoding='32FC1',stride=4):
    camera=np.asarray(t_world_camera,float);human=np.asarray(t_world_human,float)
    if camera.shape!=(4,4) or human.shape!=(4,4) or not np.isfinite([camera,human]).all():
        raise ValueError('无效的相机 / 人体变换')
    k=np.asarray(k,float).reshape(3,3);depth=np.asarray(depth)
    if depth.ndim!=2 or encoding not in ('32FC1','16UC1') or stride<1:
        raise ValueError('无效深度图')
    if not np.isfinite(k).all() or min(k[0,0],k[1,1])<=0:raise ValueError('无效相机内参')
    result={'origin_distance_m':float(np.linalg.norm(camera[:3,3]-human[:3,3])),
            'camera_world_m':camera[:3,3].tolist(),'human_world_m':human[:3,3].tolist(),
            'surface_range_m':None,'surface_valid':False,'surface_samples':0,
            'surface_reason':'背部不可见、被遮挡或有效深度不足'}
    v,u=np.mgrid[0:depth.shape[0]:stride,0:depth.shape[1]:stride]
    z=depth[::stride,::stride].astype(float)*(.001 if encoding=='16UC1' else 1.)
    valid=np.isfinite(z)&(z>.05)&(z<4.)
    z=z[valid];u=u[valid];v=v[valid]
    cloud=np.column_stack(((u-k[0,2])*z/k[0,0],(v-k[1,2])*z/k[1,1],z))
    body=points(inverse(human)@camera,cloud)
    # Gate against the registered central back, not all scene depths (bed/robot).
    mask=(np.abs(body[:,0])<=.09)&(np.abs(body[:,1])<=.13)&(np.abs(body[:,2])<=.008)
    ranges=np.linalg.norm(cloud[mask],axis=1);result['surface_samples']=int(len(ranges))
    if len(ranges)>=20:
        result.update(surface_valid=True,surface_range_m=float(np.median(ranges)),surface_reason='')
    return result
