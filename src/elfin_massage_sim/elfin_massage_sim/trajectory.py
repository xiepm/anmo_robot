"""Three-stage motion with bounded quintic trajectories and sampled FCL checks."""
from dataclasses import dataclass
import numpy as np
from scipy.interpolate import BPoly
from .kinematics import DOWN
from .geometry import approach

# Fixed branch for the preset (0.6, 0, 0.65 m), tool +Z downward.
OBSERVATION_Q = np.array([1.0503810376335038e-05, -0.42092293498948546, 0.5700084098609655, 3.588295479926913e-06, 2.1506613087422055, 1.5708020915024583])


@dataclass
class Stage:
    name: str
    times: np.ndarray
    positions: np.ndarray
    velocities: np.ndarray
    accelerations: np.ndarray

    def spline(self):
        return BPoly.from_derivatives(self.times, list(zip(self.positions, self.velocities, self.accelerations)))


class Planner:
    def __init__(self, model, collisions, cancelled=lambda: False):
        self.model, self.collisions, self.cancelled = model, collisions, cancelled
        self.max_velocity = 0.35  # rad/s
        self.max_acceleration = 0.5  # rad/s²
        self.scene = 'human' if model.root.find("link[@name='human_torso']") is not None else 'pad'

    def check_cancel(self):
        if self.cancelled():
            raise InterruptedError('Planning cancelled')

    def finish(self, name, times, q):
        q = np.asarray(q)
        velocity = np.gradient(q, times, axis=0, edge_order=2)
        velocity[[0,-1]] = 0.
        acceleration = np.gradient(velocity, times, axis=0, edge_order=2)
        acceleration[[0,-1]] = 0.
        stage = Stage(name, times, q, velocity, acceleration)
        # Verify the same quintic interpolation the controller executes, including
        # points between IK samples. This is discrete validation, not a swept-volume proof.
        sample_times = np.linspace(0, times[-1], max(3, int(np.ceil(times[-1]/0.025))+1))
        spline = stage.spline()
        speed = np.abs(spline(sample_times, 1)).max()
        acc = np.abs(spline(sample_times, 2)).max()
        scale = max(1., speed/self.max_velocity, np.sqrt(acc/self.max_acceleration)) * 1.02
        stage.times = times * scale
        stage.velocities = velocity / scale
        stage.accelerations = acceleration / scale**2
        for configuration in spline(sample_times):
            self.check_cancel()
            self.collisions.check(configuration)
        return stage

    def joint(self, name, start, end):
        self.check_cancel()
        start, end = np.asarray(start), np.asarray(end)
        delta = np.max(np.abs(end-start))
        duration = max(1., 1.875*delta/self.max_velocity, np.sqrt(5.8*delta/self.max_acceleration))
        times = np.linspace(0., duration, max(3,int(np.ceil(duration/0.08))+1))
        u = times/duration
        s = 10*u**3 - 15*u**4 + 6*u**5
        return self.finish(name, times, start[None,:] + s[:,None]*(end-start)[None,:])

    def cartesian(self, name, start, target, speed=0.05, rotation=DOWN):
        self.check_cancel()
        first = self.model.fk(start)
        if np.linalg.norm(first[:3,:3]-rotation) > 0.03:
            raise ValueError('Cartesian approach requires the downward observation orientation')
        target = np.asarray(target)
        duration = max(1., 1.875*np.linalg.norm(target-first[:3,3])/speed)
        times = np.linspace(0., duration, max(3,int(np.ceil(duration/0.08))+1))
        u = times/duration
        s = 10*u**3 - 15*u**4 + 6*u**5
        path = [np.asarray(start)]
        for fraction in s[1:]:
            self.check_cancel()
            point = first[:3,3] + fraction*(target-first[:3,3])
            q = self.model.ik(point, path[-1], rotation=rotation, cancelled=self.cancelled)
            if np.max(np.abs(q-path[-1])) > 0.12:
                raise ValueError('IK branch jump / near singularity; path rejected')
            path.append(q)
        stage = self.finish(name, times, path)
        # Bound actual interpolated Cartesian departure from the intended line.
        spline = stage.spline()
        delta = target-first[:3,3]
        for t in np.linspace(0.,stage.times[-1],max(3,len(times)*2)):
            self.check_cancel()
            point = self.model.fk(spline(t))[:3,3]
            alpha = np.clip(np.dot(point-first[:3,3],delta)/max(np.dot(delta,delta),1e-12),0,1)
            if np.linalg.norm(point-(first[:3,3]+alpha*delta)) > 0.001:
                raise ValueError('Interpolated Cartesian path departs from the line')
        return stage

    def observe(self, current):
        current = np.asarray(current)
        stages = []
        p = self.model.fk(current)
        if p[2,3] < 0.59 and np.linalg.norm(p[:3,:3]-DOWN) < 0.03:
            stage = self.cartesian('RETREAT', current, [p[0,3],p[1,3],0.60], speed=0.04)
            stages.append(stage)
            current = stage.positions[-1]
        stages.append(self.joint('OBSERVE', current, OBSERVATION_Q))
        return stages

    def approach(self, current, surface, clearance=0.04):
        target = np.array(approach(surface, clearance, self.scene))
        if clearance >= 0.15:
            raise ValueError('Final clearance must be below the 0.15 m preapproach height')
        stages = self.observe(current)
        pre = np.array(surface) + [0.,0.,0.15]
        stages.append(self.cartesian('PREAPPROACH', stages[-1].positions[-1], pre))
        stages.append(self.cartesian('NORMAL_APPROACH', stages[-1].positions[-1], target, speed=0.02))
        return stages

    def home(self, current):
        stages = self.observe(current)
        stages.append(self.joint('HOME', stages[-1].positions[-1], np.zeros(6)))
        return stages

    def demo(self, current, surfaces):
        if self.scene != 'human' or len(surfaces) != 3:
            raise ValueError('Demo requires the mannequin and three measured back points')
        targets = [np.asarray(approach(p, .02, 'human')) for p in surfaces]
        stages = self.observe(current)
        for i, target in enumerate(targets, 1):
            above = target + [0., 0., .13]
            def move(label, xyz, speed):
                stages.append(self.cartesian(f'P{i}_{label}', stages[-1].positions[-1], xyz, speed))
            move('PREAPPROACH', above, .05)
            move('NORMAL_APPROACH', target, .025)
            move('RELEASE', target+[0.,0.,.025], .025)
            move('PRESS', target, .025)
            move('RETREAT', above, .05)
        stages.append(self.joint('OBSERVE', stages[-1].positions[-1], OBSERVATION_Q))
        return stages

    def trace(self, current, body_path, clearance=.02, speed=.02):
        from .frames import validate_body_path, points, HUMAN_FRAME
        path=validate_body_path(body_path)
        if not .02<=clearance<=.08 or not .005<=speed<=.04:
            raise ValueError('间隙应为 20–80 mm，循迹速度应为 5–40 mm/s')
        t=self.model.transforms(current)[HUMAN_FRAME]
        rotation=t[:3,:3]@DOWN
        normal=t[:3,2]
        world=points(t,path)+normal*clearance
        stages=self.observe(current)
        above=world[0]+normal*.13
        aligned=self.model.ik(above,stages[-1].positions[-1],rotation=rotation,cancelled=self.cancelled)
        stages.append(self.joint('PREAPPROACH',stages[-1].positions[-1],aligned))
        stages.append(self.cartesian('NORMAL_APPROACH',aligned,world[0],.025,rotation))
        lengths=np.linalg.norm(np.diff(world,axis=0),axis=1)
        arc=np.r_[0.,np.cumsum(lengths)]
        duration=max(1.,1.875*arc[-1]/speed)
        times=np.linspace(0.,duration,max(3,int(np.ceil(duration/.06))+1))
        u=times/duration;dist=arc[-1]*(10*u**3-15*u**4+6*u**5)
        xyz=np.stack([np.interp(dist,arc,world[:,i]) for i in range(3)],axis=1)
        q=[stages[-1].positions[-1]]
        for point in xyz[1:]:
            self.check_cancel()
            next_q=self.model.ik(point,q[-1],rotation=rotation,cancelled=self.cancelled)
            if np.max(np.abs(next_q-q[-1]))>.12:raise ValueError('循迹逆解发生分支跳变')
            q.append(next_q)
        stage=self.finish('TRACE',times,q)
        # Validate the controller's interpolation against the selected polyline.
        segments=np.diff(world,axis=0)
        denominator=np.sum(segments*segments,axis=1)
        spline=stage.spline()
        for moment in np.linspace(0,stage.times[-1],len(times)*2):
            self.check_cancel()
            point=self.model.fk(spline(moment))[:3,3]
            alpha=np.clip(np.sum((point-world[:-1])*segments,axis=1)/denominator,0,1)
            error=np.linalg.norm(world[:-1]+alpha[:,None]*segments-point,axis=1).min()
            if error>.001:raise ValueError('插值轨迹偏离所选路径超过 1 mm')
        stages.append(stage)
        stages.append(self.cartesian('RETREAT',stage.positions[-1],world[-1]+normal*.13,.04,rotation))
        stages.append(self.joint('OBSERVE',stages[-1].positions[-1],OBSERVATION_Q))
        return stages
