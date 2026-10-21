"""ROS-independent projection and conservative demo target validation (metres)."""
import math


def deproject(u, v, depth, k, width, height):
    if not (0 <= u < width and 0 <= v < height):
        raise ValueError('Pixel outside image')
    if not all(math.isfinite(x) for x in (depth, *k)) or not 0.05 < depth < 4.0:
        raise ValueError('Invalid depth or intrinsics')
    if k[0] <= 0 or k[4] <= 0:
        raise ValueError('Uncalibrated camera')
    return ((u - k[2]) * depth / k[0], (v - k[5]) * depth / k[4], depth)


def approach(point, clearance, scene='pad'):
    x, y, z = point
    if not all(math.isfinite(v) for v in (*point, clearance)):
        raise ValueError('Nonfinite target')
    # Only the top of the demo pad; do not accept floor/robot/side surfaces.
    if scene == 'human' and not (0.51 <= x <= 0.69 and -0.13 <= y <= 0.13 and abs(z-.4) <= .008):
        raise ValueError('Select the central flat back area of the mannequin')
    if scene != 'human' and not (0.37 <= x <= 0.83 and -0.23 <= y <= 0.23 and abs(z - 0.4) <= 0.015):
        raise ValueError('Select the TOP of the blue pad')
    if not 0.02 <= clearance <= 0.2:
        raise ValueError('Clearance must be 0.02..0.2 m')
    return x, y, z + clearance
