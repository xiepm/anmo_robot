"""Exact diagnostic XY distance from a tool-tip path to unknown depth squares.

This does not certify free space. A positive lower bound only separates the
mathematical centerline from the reported squares in XY; it excludes neither
unobserved volume nor collisions of the physical tool, links or accessories.
"""
import math


def _point_segment_distance(point, start, end):
    dx, dy = end[0]-start[0], end[1]-start[1]
    length = dx*dx+dy*dy
    t = max(0., min(1., ((point[0]-start[0])*dx+(point[1]-start[1])*dy)/length)) if length else 0.
    return math.hypot(point[0]-start[0]-t*dx, point[1]-start[1]-t*dy)


def _segment_square_distance(start, end, center, half_size):
    """Continuous segment versus a closed square, including boundary contact."""
    lower = [coordinate-half_size for coordinate in center]
    upper = [coordinate+half_size for coordinate in center]
    entry, leave = 0., 1.
    for axis in range(2):
        delta = end[axis]-start[axis]
        if delta == 0:
            if not lower[axis] <= start[axis] <= upper[axis]:
                break
        else:
            first, last = sorted(((lower[axis]-start[axis])/delta,
                                  (upper[axis]-start[axis])/delta))
            entry, leave = max(entry, first), min(leave, last)
            if entry > leave:
                break
    else:
        return 0.
    # For disjoint line segments, a closest pair contains an endpoint of at
    # least one segment. Check both route endpoints against the square, and
    # each square corner against the route, without replacing it by a circle.
    def point_square_distance(point):
        return math.hypot(*(max(lower[i]-point[i], 0., point[i]-upper[i]) for i in range(2)))

    return min(point_square_distance(start), point_square_distance(end),
               *(_point_segment_distance([x, y], start, end)
                 for x in (lower[0], upper[0]) for y in (lower[1], upper[1])))


def unknown_clearance(plan, scene, offset):
    count = scene['unknown_cells']
    examples = scene.get('unknown_cell_examples_base_xy_mm', [])
    complete = len(examples) == count and len({tuple(p) for p in examples}) == count
    report = dict(assessment='INCOMPLETE', unknown_cells=count,
                  checked_cells=len(examples), all_unknown_cells_included=complete,
                  minimum_xy_distance_lower_bound_mm=None,
                  nearest_cell_base_xy_mm=None, possibly_intersecting_cells=None,
                  execution_eligible=False)
    if not complete:
        return report  # Never treat a truncated list of examples as all cells.
    if not count:
        report.update(assessment='NO_UNKNOWN_REPORTED', possibly_intersecting_cells=0)
        return report
    half_size = scene['cell_size_mm'] / 2
    vertices = [plan['start_pose']] + [point['pose'] for point in plan['points']]
    route = [[p['x_mm'] + offset[0], p['y_mm'] + offset[1]] for p in vertices]
    if len(route) < 2:
        return report
    distances = []
    for center in examples:
        distances.append(min(_segment_square_distance(a, b, center, half_size)
                             for a, b in zip(route, route[1:])))
    index = min(range(count), key=distances.__getitem__)
    possible = sum(distance <= 1e-9 for distance in distances)
    report.update(assessment='CENTERLINE_NEAR_UNKNOWN' if possible else 'UNKNOWN_OFF_CENTERLINE',
                  minimum_xy_distance_lower_bound_mm=distances[index],
                  nearest_cell_base_xy_mm=list(examples[index]),
                  possibly_intersecting_cells=possible)
    return report
