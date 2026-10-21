"""RGB-D fiducial registration. No world/body ground truth is an estimator input."""
import xml.etree.ElementTree as ET
import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from .frames import points
from .kinematics import Kinematics

VISION_FRAME='human_estimated'
# Calibrated positions on the mannequin, relative to its back origin, in metres.
MARKERS={'orange':(-.05,-.10,.001),'green':(.05,-.02,.001),'blue':(-.04,.11,.001)}
HSV_RANGES={'orange':((5,110,70),(28,255,255)), 'green':((40,100,55),(82,255,255)),
            'blue':((100,110,60),(132,255,255))}


def rigid_fit(local,observed):
    local=np.asarray(local,float);observed=np.asarray(observed,float)
    if local.shape!=observed.shape or local.shape!=(3,3) or not np.isfinite(observed).all():
        raise ValueError('需要三个有效且对应的三维标记点')
    a=local-local.mean(axis=0);b=observed-observed.mean(axis=0)
    if np.linalg.svd(b,compute_uv=False)[1]<.015:raise ValueError('标记几何退化，不能确定人体朝向')
    u,_,vt=np.linalg.svd(a.T@b)
    correction=np.eye(3);correction[-1,-1]=np.linalg.det(vt.T@u.T)
    rotation=vt.T@correction@u.T
    t=np.eye(4);t[:3,:3]=rotation;t[:3,3]=observed.mean(axis=0)-rotation@local.mean(axis=0)
    errors=np.linalg.norm(points(t,local)-observed,axis=1)
    if errors.max()>.006:raise ValueError('标记刚体拟合残差过大，拒绝配准')
    return t,errors


def estimate_rgbd(bgr,depth,k,t_world_camera,encoding='32FC1'):
    if bgr.shape[:2]!=depth.shape or depth.ndim!=2:raise ValueError('RGB 与深度未对齐')
    if encoding not in ('32FC1','16UC1'):raise ValueError('不支持的深度编码')
    k=np.asarray(k,float).reshape(3,3)
    if not np.isfinite(k).all() or k[0,0]<=0 or k[1,1]<=0:raise ValueError('无效相机内参')
    zmap=depth.astype(float)*(.001 if encoding=='16UC1' else 1.)
    hsv=cv2.cvtColor(bgr,cv2.COLOR_BGR2HSV);observed=[];measurements=[]
    for name,xyz in MARKERS.items():
        lo,hi=HSV_RANGES[name];mask=cv2.inRange(hsv,np.array(lo,np.uint8),np.array(hi,np.uint8))
        n,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
        candidates=[i for i in range(1,n) if 12<=stats[i,cv2.CC_STAT_AREA]<=20000]
        if len(candidates)!=1:raise ValueError(f'{name} 标记缺失或存在多个候选；请回观察位使三个标记可见')
        i=candidates[0];u,v=centers[i];area=int(stats[i,cv2.CC_STAT_AREA])
        yy,xx=np.where(labels==i);radius=max(1.,np.sqrt(area/np.pi)*.5)
        central=(xx-u)**2+(yy-v)**2<=radius**2
        values=zmap[yy[central],xx[central]];values=values[np.isfinite(values)&(values>.05)&(values<4.)]
        if len(values)<3:raise ValueError(f'{name} 标记没有有效深度')
        z=float(np.median(values))
        if np.ptp(values)>.02:raise ValueError(f'{name} 标记深度不连续')
        camera=np.array([(u-k[0,2])*z/k[0,0],(v-k[1,2])*z/k[1,1],z])
        observed.append(camera);measurements.append({'id':name,'pixel':[float(u),float(v)],'area_px':area,
            'depth_m':z,'camera_m':camera.tolist(),'body_marker_m':list(xyz)})
    tch,errors=rigid_fit(list(MARKERS.values()),observed)
    if np.dot(tch[:3,2],np.mean(observed,axis=0))>=0:raise ValueError('估计法向背向相机，拒绝配准')
    twh=np.asarray(t_world_camera)@tch
    return {'method':'colored_fiducials_rgbd_rigid_fit','frame_id':VISION_FRAME,
            'T_camera_human':tch.tolist(),'T_world_human':twh.tolist(),
            'fit_rms_mm':float(np.sqrt(np.mean(errors**2))*1000),
            'fit_max_mm':float(errors.max()*1000),'markers':measurements}


def compare_reference(estimated,reference):
    a=np.asarray(estimated);b=np.asarray(reference)
    return {'translation_error_mm':float(np.linalg.norm(a[:3,3]-b[:3,3])*1000),
            'rotation_error_deg':float(np.degrees(Rotation.from_matrix(a[:3,:3]@b[:3,:3].T).magnitude())),
            'translation_delta_mm':((a[:3,3]-b[:3,3])*1000).tolist(),'T_world_reference':b.tolist()}


def registered_model(model,t_world_human):
    root=ET.fromstring(ET.tostring(model.root))
    element=root.find("joint[@name='human_registration']/origin")
    if element is None:raise ValueError('机器人场景缺少人体模型')
    t=np.asarray(t_world_human)
    element.set('xyz',' '.join(map(str,t[:3,3])))
    element.set('rpy',' '.join(map(str,Rotation.from_matrix(t[:3,:3]).as_euler('xyz'))))
    return Kinematics(ET.tostring(root,encoding='unicode'))
