"""Deproject final UI pixels using ONLY the depth exposure paired with its photo."""
import copy
import math
from types import MappingProxyType
import numpy as np


def _freeze_native_geometry(geometry):
    """Keep original depth-camera measurements; never derive them from align."""
    fields = ('native_depth_m', 'native_rays', 'T_color_depth',
              'native_depth_frame_number', 'aligned_depth_frame_number',
              'native_depth_timestamp_ms', 'aligned_depth_timestamp_ms', 'geometry_frame')
    if geometry is None or any(key not in geometry for key in fields):
        return None  # Older captures remain readable without native evidence.
    if geometry['geometry_frame'] != 'DEPTH_OPTICAL_NATIVE':
        raise ValueError('原生深度坐标系标签无效')
    native = {key: geometry[key] for key in fields}
    for key in ('native_depth_m', 'native_rays', 'T_color_depth'):
        native[key] = np.array(native[key], dtype=float, copy=True)
    depth, rays, transform = (native[key] for key in ('native_depth_m', 'native_rays', 'T_color_depth'))
    if (depth.ndim != 2 or not depth.size or rays.shape != (*depth.shape, 3)
            or not np.isfinite(rays).all() or not np.allclose(rays[..., 2], 1., atol=1e-8, rtol=0)):
        raise ValueError('原生深度与投影射线不匹配')
    if (transform.shape != (4, 4) or not np.isfinite(transform).all()
            or not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-8, rtol=0)
            or not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-6, rtol=0)
            or abs(np.linalg.det(transform[:3, :3])-1) > 1e-6):
        raise ValueError('深度到彩色相机的外参无效')
    numbers = [native[key] for key in ('native_depth_frame_number', 'aligned_depth_frame_number')]
    times = [native[key] for key in ('native_depth_timestamp_ms', 'aligned_depth_timestamp_ms')]
    if (any(isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 0 for value in numbers)
            or numbers[0] != numbers[1]
            or any(isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating))
                   or not math.isfinite(value) for value in times)
            or times[0] != times[1]):
        raise ValueError('原生深度与对齐深度不是同一次曝光')
    for key in ('native_depth_m', 'native_rays', 'T_color_depth'):
        native[key].setflags(write=False)
    return MappingProxyType(native)


class FrozenDepth:
    def __init__(self, depth_m, intrinsics, deproject, known_points, rays=None, native_geometry=None):
        self.depth = np.asarray(depth_m, dtype=float).copy()
        self.depth.setflags(write=False)
        self.intrinsics, self.deproject = intrinsics, deproject
        if rays is not None and (rays.shape != (*self.depth.shape, 3)
                                 or rays.flags.writeable or not np.isfinite(rays).all()):
            raise ValueError('预计算投影射线与冻结深度不匹配')
        self.rays = rays
        self.native_geometry = _freeze_native_geometry(native_geometry)
        self.known = {(p['name'], p['side']) for p in known_points if p['coordinate_valid']}
        from depth_scene import DepthScene
        self.scene = DepthScene(self)

    def resolve(self, points):
        h,w = self.depth.shape
        seen, result = set(), []
        for p in points:
            key = p['name'],p['side']
            x,y = p['x'],p['y']
            if key not in self.known or key in seen:
                raise ValueError('穴位不属于本次有效识别结果或重复')
            seen.add(key)
            if not all(math.isfinite(v) for v in (x,y)) or not (0<=x<w and 0<=y<h):
                raise ValueError('调整位置超出本次照片')
            u,v = min(w-1,int(round(x))),min(h-1,int(round(y)))
            patch = self.depth[max(0,v-2):min(h,v+3),max(0,u-2):min(w,u+3)]
            valid = patch[np.isfinite(patch) & (patch > 0)]
            # Do not fill a hole from a remote depth or project onto a jump edge.
            center = self.depth[v,u]
            if not np.isfinite(center) or center<=0 or len(valid)<max(9, math.ceil(patch.size*.6)):
                raise ValueError(f"{p['name']}调整位置深度无效，请换点或重新扫描")
            if float(np.ptp(valid)) > .02:
                raise ValueError(f"{p['name']}调整位置跨越深度边缘，请重新调整")
            xyz = [float(a)*1000 for a in self.deproject(self.intrinsics,[float(x),float(y)],float(np.median(valid)))]
            if not all(math.isfinite(a) for a in xyz) or xyz[2]<=0:
                raise ValueError('三维坐标无效')
            result.append(dict(copy.deepcopy(p),camera_point_mm=xyz))
        return result
