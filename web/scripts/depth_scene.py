"""Observed-surface analysis of one frozen, aligned depth image.

No hole filling, plane extrapolation, anatomical inference or collision approval.
The grid describes measured surfaces at its stated resolution, not free volume.
"""
import hashlib
import json
import math
import threading
import numpy as np


def intrinsics_key(intrinsics):
    return (intrinsics.width, intrinsics.height, intrinsics.fx, intrinsics.fy,
            intrinsics.ppx, intrinsics.ppy, str(intrinsics.model), tuple(intrinsics.coeffs))


def pixel_rays(intrinsics, deproject, width, height):
    rays = np.asarray([deproject(intrinsics, [float(x), float(y)], 1.)
                       for y in range(height) for x in range(width)], dtype=float).reshape(height, width, 3)
    rays.setflags(write=False)
    return rays


def context_digest(context):
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


class DepthScene:
    def __init__(self, frozen):
        self.frozen = frozen
        self.lock = threading.Lock()
        self.rays = frozen.rays

    def analyze(self, context, points):
        with self.lock:
            return self._analyze(context, points)

    def _analyze(self, context, points):
        depth = self.frozen.depth
        h, w = depth.shape
        transform = np.asarray(context['T_base_camera'], dtype=float)
        if (transform.shape != (4, 4) or not np.isfinite(transform).all()
                or not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-8)
                or not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-6)
                or abs(np.linalg.det(transform[:3, :3])-1) > 1e-6):
            raise ValueError('场景变换不是有效刚体变换')
        start = np.asarray(context['start_flange_mm'], dtype=float)
        radius, cell = context['corridor_radius_mm'], context['cell_size_mm']
        if (start.shape != (3,) or not np.isfinite(start).all() or radius != 50 or cell != 10
                or not 1 <= len(points) <= 30 or h*w > 2_000_000):
            raise ValueError('场景分析范围或参数无效')
        if self.rays is None:
            # Use the camera SDK's actual distortion model, not an assumed pinhole.
            self.rays = pixel_rays(self.frozen.intrinsics, self.frozen.deproject, w, h)
        rays = self.rays @ transform[:3, :3].T
        origin = transform[:3, 3]*1000
        cloud = rays * depth[..., None]*1000 + origin
        valid = np.isfinite(depth) & (depth > 0) & np.isfinite(cloud).all(axis=2)
        final = np.asarray([p['camera_point_mm'] for p in points]) @ transform[:3, :3].T + origin
        if not np.isfinite(final).all():
            raise ValueError('最终点位无效')
        route = np.vstack([start, final])[:, :2]
        lower = np.floor((route.min(axis=0)-radius-cell)/cell)*cell
        upper = np.ceil((route.max(axis=0)+radius+cell)/cell)*cell
        nx, ny = np.ceil((upper-lower)/cell).astype(int)
        if nx*ny > 100_000:
            raise ValueError('场景范围异常，拒绝分配过大的栅格')
        yy, xx = np.mgrid[:ny, :nx]
        centers = np.stack([lower[0]+(xx+.5)*cell, lower[1]+(yy+.5)*cell], axis=-1)
        distance = np.full((ny, nx), np.inf)
        for a, b in zip(route[:-1], route[1:]):
            delta = b-a
            length = float(delta @ delta)
            fraction = np.clip(((centers-a)*delta).sum(axis=2)/length, 0, 1) if length else 0
            nearest = a + (fraction[..., None]*delta if length else delta)
            distance = np.minimum(distance, np.linalg.norm(centers-nearest, axis=2))
        # Include intersecting boundary cells rather than silently trimming them.
        corridor = distance <= radius + cell/math.sqrt(2)
        observed = cloud[valid]
        xy = (observed[:, :2]-lower)/cell
        index = np.floor(xy).astype(int)
        inside = (index[:, 0]>=0) & (index[:, 0]<nx) & (index[:, 1]>=0) & (index[:, 1]<ny)
        observed, xy, index = observed[inside], xy[inside], index[inside]
        keep = corridor[index[:, 1], index[:, 0]]
        observed, xy, index = observed[keep], xy[keep], index[keep]
        # Every 5 x 5 mm quadrant needs measured depth. Empty quadrants stay unknown.
        quadrants = np.zeros((ny, nx, 4), dtype=bool)
        q = np.minimum(1, ((xy-index)*2).astype(int))
        quadrants[index[:, 1], index[:, 0], q[:, 1]*2+q[:, 0]] = True
        quadrant_unknown = corridor & ~quadrants.all(axis=2)
        ray_unknown = np.zeros_like(corridor)
        # Invalid rays may cross the corridor behind an occluding surface. Mark
        # their intersected cells unknown over the observed height slab as well.
        bad_rays = rays[~valid]
        all_valid = cloud[valid]
        if len(all_valid) and len(bad_rays):
            zlo, zhi = all_valid[:, 2].min(), all_valid[:, 2].max()
            vertical = np.abs(bad_rays[:, 2]) > 1e-6
            if not vertical.all():
                # A horizontal missing ray has no bounded height intersection.
                ray_unknown |= corridor
            bad_rays = bad_rays[vertical]
            if len(bad_rays):
                ta, tb = (zlo-origin[2])/bad_rays[:, 2], (zhi-origin[2])/bad_rays[:, 2]
                ta, tb = np.maximum(0, np.minimum(ta, tb)), np.maximum(ta, tb)
                active = tb >= ta
                bad_rays, ta, tb = bad_rays[active], ta[active], tb[active]
                lengths = np.linalg.norm(bad_rays[:, :2]*(tb-ta)[:, None], axis=1)
                steps = max(1, int(math.ceil(float(lengths.max())/(cell/2)))) if len(lengths) else 1
                if steps > 512:
                    ray_unknown |= corridor
                else:
                    for t in np.linspace(0, 1, steps+1):
                        xy_bad = origin[:2]+bad_rays[:, :2]*(ta+(tb-ta)*t)[:, None]
                        ij = np.floor((xy_bad-lower)/cell).astype(int)
                        good = (ij[:, 0]>=0) & (ij[:, 0]<nx) & (ij[:, 1]>=0) & (ij[:, 1]<ny)
                        ij = ij[good]
                        # Expand one cell to cover gaps between ray samples.
                        for dx in (-1, 0, 1):
                            for dy in (-1, 0, 1):
                                ix, iy = np.clip(ij[:, 0]+dx, 0, nx-1), np.clip(ij[:, 1]+dy, 0, ny-1)
                                ray_unknown[iy, ix] |= corridor[iy, ix]
        unknown = quadrant_unknown | ray_unknown
        total, missing = int(corridor.sum()), int(unknown.sum())
        locations = np.argwhere(unknown)
        scene = dict(version=1, source='FROZEN_ALIGNED_DEPTH', context_sha256=context_digest(context),
            calibration_sha256=context['calibration_sha256'], frame_number=context['frame_number'],
            received_monotonic_s=context['received_monotonic_s'], revision=context['revision'],
            state='NO_DATA' if not len(observed) else 'PARTIAL' if missing else 'OBSERVED',
            cell_size_mm=cell, corridor_radius_mm=radius, image_pixels=int(depth.size),
            valid_depth_pixels=int(valid.sum()), corridor_depth_points=len(observed),
            corridor_cells=total, unknown_cells=missing,
            coverage_diagnostics=dict(
                incomplete_quadrant_cells=int(quadrant_unknown.sum()),
                invalid_ray_cells=int(ray_unknown.sum()),
                overlapping_cause_cells=int((quadrant_unknown & ray_unknown).sum()),
                note='原因计数可能重叠；缺测象限可能来自深度缺失、遮挡或视野边缘，不能仅据此区分'),
            unknown_cell_examples_base_xy_mm=[[float(lower[0]+(x+.5)*cell), float(lower[1]+(y+.5)*cell)]
                                               for y, x in locations],
            observed_bounds_base_mm=None if not len(observed) else dict(
                min=observed.min(axis=0).tolist(), max=observed.max(axis=0).tolist()),
            surface_ceiling_base_mm=None if not len(observed) else float(observed[:, 2].max()),
            collision_checked=False, execution_eligible=False,
            note='仅本次照片可见表面；缺测和视野外区域保持未知，未验证整臂碰撞')
        # The legacy field name is retained for compatibility, but route
        # coverage requires every unknown square, never a sample of them.
        # Match Session.emit's complete REFINE_RESULT encoding and the shared
        # WebSocket limit; oversize scenes fail explicitly instead of silently
        # dropping cells or disconnecting the control/vision transport.
        data = {key: context[key] for key in
                ('revision', 'frame_number', 'received_monotonic_s')}
        data.update(ok=True, error='', points=points, scene=scene)
        payload = json.dumps(dict(type='REFINE_RESULT', data=data),
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(payload) > 65536:
            raise ValueError('完整未知栅格数据超过 64 KiB 消息上限，场景分析失败；未截断未知区域')
        # The same scene is included in UI STATUS beside coordinates, route
        # review and device status. Reserve space for those existing fields.
        if len(json.dumps(scene, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 32768:
            raise ValueError('完整场景数据超过 32 KiB 容量上限，场景分析失败；未截断未知区域')
        return scene
