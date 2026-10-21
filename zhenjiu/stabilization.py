"""Display-only temporal stabilization. Missing measurements are never held valid."""
from dataclasses import dataclass
import math
import time
import numpy as np
import copy


class TorsoDisplayTracker:
    """Confidence hysteresis for display only; never changes model confidence.

    Acquire from 3 consistent frames with recent >=.5 evidence; retain >=.4 observations
only while spatially continuous. No extrapolation, held points or stale XYZ.
"""
    indices = (11,12,23,24)

    def __init__(self, acquire=.5, retain=.4, jump_px=25., max_gap=.35):
        self.acquire,self.retain,self.jump_px,self.max_gap=acquire,retain,jump_px,max_gap
        self.reset()

    def reset(self):
        self.previous=None
        self.hits=0
        self.at=0.
        self.strong_at=None
        self.state='LOST'

    def update(self, landmarks, shape, at=None):
        at=time.monotonic() if at is None else at
        if not landmarks:
            self.reset()
            return landmarks
        out=copy.deepcopy(landmarks)
        h,w=shape[:2]
        xy=np.array([[landmarks[i].x,landmarks[i].y] for i in self.indices])
        scores=np.array([getattr(landmarks[i],'raw_visibility',landmarks[i].visibility) for i in self.indices])
        if (not np.isfinite(xy).all() or not np.isfinite(scores).all()
                or not ((xy>=.01)&(xy<.99)).all() or scores.min()<self.retain):
            self.reset()
            return out
        continuous=(self.previous is not None and 0<=at-self.at<=self.max_gap
                    and np.linalg.norm((xy-self.previous)*[w,h],axis=1).max()<=self.jump_px)
        if not continuous:
            self.hits=0
            self.state='LOST'
            self.strong_at=None
        strong=bool(scores.min()>=self.acquire)
        if strong:
            self.strong_at=at
        self.hits+=1
        if self.state.startswith('TRACKING') and continuous:
            self.state='TRACKING' if strong else 'TRACKING_WEAK'
        elif self.strong_at is not None and at-self.strong_at<=.6:
            self.state=('TRACKING' if strong else 'TRACKING_WEAK') if self.hits>=3 else 'ACQUIRING'
        else:
            self.state='LOST'
        self.previous,self.at=xy.copy(),at
        if self.state.startswith('TRACKING'):
            for i,score in zip(self.indices,scores):
                out[i].display_tracking_valid=True
                out[i].display_tracking_score=float(score)
        return out


class BaseDisplayFilter:
    """Smooth the table only; the caller must export raw measurements first."""
    def __init__(self):
        self.filter=AdaptiveFilter(cutoff=.35,beta=.002,jump=40.,confirm=3)

    def update(self, points, at=None):
        at=time.monotonic() if at is None else at
        self.filter.prune(at)
        result=[]
        valid_keys={(p['name'],p['side']) for p in points if p.get('base_coordinate_valid')}
        self.filter.states={k:v for k,v in self.filter.states.items() if k in valid_keys}
        for p in points:
            q=dict(p)
            if p.get('base_coordinate_valid'):
                xyz=self.filter.update((p['name'],p['side']),p['base_point_mm'],at)
                q.update(base_coordinate_valid=xyz is not None,
                         base_point_mm=np.round(xyz,1).tolist() if xyz is not None else None,
                         base_display_smoothed=True)
            result.append(q)
        return result


@dataclass
class Track:
    value: np.ndarray
    raw: np.ndarray
    velocity: np.ndarray
    at: float
    hits: int = 1
    candidate: object = None
    candidate_hits: int = 0


class AdaptiveFilter:
    def __init__(self, cutoff=0.6, beta=0.012, jump=60., max_gap=0.3, confirm=3):
        self.cutoff, self.beta, self.jump = cutoff, beta, jump
        self.max_gap, self.confirm = max_gap, confirm
        self.states = {}

    @staticmethod
    def alpha(cutoff, dt):
        return 1 - math.exp(-2 * math.pi * cutoff * dt)

    def update(self, key, value, at):
        value = np.asarray(value, dtype=float)
        if not np.isfinite(value).all():
            self.states.pop(key, None)
            return None
        old = self.states.get(key)
        if old is None or at - old.at > self.max_gap:
            self.states[key] = Track(value.copy(), value.copy(), np.zeros_like(value), at)
            return value.copy() if self.confirm <= 1 else None
        dt = max(1e-3, at - old.at)
        if np.linalg.norm(value - old.raw) > self.jump:
            if old.candidate is not None and np.linalg.norm(value - old.candidate) < self.jump / 3:
                old.candidate_hits += 1
            else:
                old.candidate_hits = 1
            old.candidate = value.copy()
            old.at = at
            if old.candidate_hits < self.confirm:
                return None  # Hide the point rather than freeze a stale coordinate.
            self.states[key] = Track(value.copy(), value.copy(), np.zeros_like(value), at, self.confirm)
            return value.copy()
        old.candidate, old.candidate_hits = None, 0
        speed = (value - old.raw) / dt
        ad = self.alpha(1.0, dt)
        old.velocity += ad * (speed - old.velocity)
        a = self.alpha(self.cutoff + self.beta * float(np.linalg.norm(old.velocity)), dt)
        old.value += a * (value - old.value)
        old.raw, old.at, old.hits = value.copy(), at, old.hits + 1
        return old.value.copy() if old.hits >= self.confirm else None

    def prune(self, at):
        self.states = {k: v for k, v in self.states.items() if at - v.at <= self.max_gap}


class StablePoints:
    """XY is filtered before current-frame depth sampling; XYZ after it, for display."""
    def __init__(self):
        self.xy = AdaptiveFilter()
        self.xyz = AdaptiveFilter(cutoff=0.8, beta=0.008, jump=100., confirm=3)

    def update(self, points, at=None):
        at = time.monotonic() if at is None else at
        self.xy.prune(at)
        result = []
        for p in points:
            xy = self.xy.update((p['name'], p['side']), [p['x'], p['y']], at)
            if xy is not None:
                result.append(dict(p, x=round(xy[0], 1), y=round(xy[1], 1)))
        return result

    def depth(self, points, at=None):
        at = time.monotonic() if at is None else at
        self.xyz.prune(at)
        result = []
        for p in points:
            key = (p['name'], p['side'])
            q = dict(p)
            if not p.get('coordinate_valid'):
                self.xyz.states.pop(key, None)
            else:
                xyz = self.xyz.update(key, p['camera_point_mm'], at)
                q.update(coordinate_valid=xyz is not None,
                         camera_point_mm=np.round(xyz, 1).tolist() if xyz is not None else None,
                         depth_mm=round(float(xyz[2]), 1) if xyz is not None else None)
            result.append(q)
        return result
