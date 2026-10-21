"""Camera-to-Base coordinate preview for the empirically verified wrist mount.

Requires a capture-matched Base/reference pose; this module never commands motion.
The reference is vendor FK(q1,q2,q3,q4,q5,0), zero TCP/UCS, NOT actual output flange.
"""
import numpy as np

REFERENCE_FRAME = 'virtual_flange_with_joint6_zero'


def rigid(value):
    T=np.asarray(value,dtype=float)
    if (T.shape!=(4,4) or not np.isfinite(T).all()
            or not np.allclose(T[3],[0,0,0,1],atol=1e-8)
            or not np.allclose(T[:3,:3].T@T[:3,:3],np.eye(3),atol=1e-6)
            or abs(np.linalg.det(T[:3,:3])-1)>1e-6):
        raise ValueError('Invalid rigid transform')
    return T


def camera_to_base_mm(camera_point_mm, T_base_reference, calibration, *,
                      reference_frame, camera_serial):
    if reference_frame!=REFERENCE_FRAME or calibration.get('reference_frame')!=REFERENCE_FRAME:
        raise ValueError('Actual flange pose is incompatible with this camera mounting reference')
    if calibration.get('units')!='m' or calibration.get('camera_serial')!=camera_serial:
        raise ValueError('Calibration units/camera identity mismatch')
    p=np.asarray(camera_point_mm,dtype=float)
    if p.shape!=(3,) or not np.isfinite(p).all() or p[2]<=0:
        raise ValueError('Need a valid original color optical point, in millimeters')
    T=rigid(T_base_reference)@rigid(calibration['T_reference_camera'])
    return (T[:3,:3]@p+T[:3,3]*1000).tolist()
