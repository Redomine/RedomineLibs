# -*- coding: utf-8 -*-
"""
Dual-entry CW spiral routing for rectangular domains.
Python 2.7 compatible, no external packages.
"""
from __future__ import print_function
import math


def dist_point_to_segment(px, py, x1, y1, x2, y2):
    """Distance from point (px,py) to segment (x1,y1)-(x2,y2)."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / float(dx * dx + dy * dy)
    if t < 0:
        return math.hypot(px - x1, py - y1)
    if t > 1:
        return math.hypot(px - x2, py - y2)
    projx = x1 + t * dx
    projy = y1 + t * dy
    return math.hypot(px - projx, py - projy)


def segments_parallel_distance(seg1, seg2):
    """
    Minimum distance between two segments.  If they share an endpoint
    (adjacent in a polyline) return a large number so they are ignored.
    """
    (x1, y1), (x2, y2) = seg1
    (x3, y3), (x4, y4) = seg2
    # Adjacency test: share an endpoint (within 1e-6)
    eps = 1e-6
    p1 = [(x1, y1), (x2, y2)]
    p2 = [(x3, y3), (x4, y4)]
    for a in p1:
        for b in p2:
            if abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps:
                return 1e9
    # General segment distance using point-to-segment both ways.
    d = min(
        dist_point_to_segment(x1, y1, x3, y3, x4, y4),
        dist_point_to_segment(x2, y2, x3, y3, x4, y4),
        dist_point_to_segment(x3, y3, x1, y1, x2, y2),
        dist_point_to_segment(x4, y4, x1, y1, x2, y2),
    )
    return d


def segment_intersect(seg1, seg2):
    """True if two closed segments intersect at a non-shared interior point."""
    (x1, y1), (x2, y2) = seg1
    (x3, y3), (x4, y4) = seg2

    def ccw(Ax, Ay, Bx, By, Cx, Cy):
        return (Cy - Ay) * (Bx - Ax) - (By - Ay) * (Cx - Ax)

    d1 = ccw(x3, y3, x4, y4, x1, y1)
    d2 = ccw(x3, y3, x4, y4, x2, y2)
    d3 = ccw(x1, y1, x2, y2, x3, y3)
    d4 = ccw(x1, y1, x2, y2, x4, y4)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True

    # Collinear cases
    def on_segment(px, py, qx, qy, rx, ry):
        if min(px, qx) - 1e-6 <= rx <= max(px, qx) + 1e-6 and \
           min(py, qy) - 1e-6 <= ry <= max(py, qy) + 1e-6:
            return True
        return False

    if abs(d1) < 1e-9 and on_segment(x3, y3, x4, y4, x1, y1):
        return True
    if abs(d2) < 1e-9 and on_segment(x3, y3, x4, y4, x2, y2):
        return True
    if abs(d3) < 1e-9 and on_segment(x1, y1, x2, y2, x3, y3):
        return True
    if abs(d4) < 1e-9 and on_segment(x1, y1, x2, y2, x4, y4):
        return True
    return False


def check_self_intersections(poly):
    """Return (ok, list of intersecting segment index pairs)."""
    segs = [(poly[i], poly[i + 1]) for i in range(len(poly) - 1)]
    bad = []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            # Adjacent segments are allowed to touch at shared endpoint.
            if j == i + 1:
                continue
            if segment_intersect(segs[i], segs[j]):
                bad.append((i, j))
    return len(bad) == 0, bad


def min_non_adjacent_parallel_distance(poly):
    """Minimum distance between any two non-adjacent parallel segments."""
    segs = [(poly[i], poly[i + 1]) for i in range(len(poly) - 1)]
    dmin = 1e9
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if j == i + 1:
                continue
            d = segments_parallel_distance(segs[i], segs[j])
            if d < dmin:
                dmin = d
    return dmin


def coverage_ratio(poly, L, B, R, T, grid_step, max_dist):
    """Fraction of grid points inside [L,R]x[B,T] within max_dist of polyline."""
    if not poly:
        return 0.0
    points = []
    x = L + grid_step / 2.0
    while x < R:
        y = B + grid_step / 2.0
        while y < T:
            points.append((x, y))
            y += grid_step
        x += grid_step
    if not points:
        return 0.0
    covered = 0
    for px, py in points:
        dmin = 1e9
        for i in range(len(poly) - 1):
            d = dist_point_to_segment(px, py, poly[i][0], poly[i][1],
                                      poly[i + 1][0], poly[i + 1][1])
            if d < dmin:
                dmin = d
        if dmin <= max_dist + 1e-6:
            covered += 1
    return float(covered) / len(points)


def point_in_polygon(px, py, poly):
    """Ray casting for a polygon given as list of (x,y)."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > py) != (y2 > py)):
            xinters = x1 + (py - y1) * (x2 - x1) / float(y2 - y1)
            if px < xinters:
                inside = not inside
    return inside


def coverage_polygon(poly, polygon, grid_step, max_dist):
    """Coverage fraction for points inside an arbitrary polygon."""
    # bounding box of polygon
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    L, R = min(xs), max(xs)
    B, T = min(ys), max(ys)
    points = []
    x = L + grid_step / 2.0
    while x < R:
        y = B + grid_step / 2.0
        while y < T:
            if point_in_polygon(x, y, polygon):
                points.append((x, y))
            y += grid_step
        x += grid_step
    if not points:
        return 0.0
    covered = 0
    for px, py in points:
        dmin = 1e9
        for i in range(len(poly) - 1):
            d = dist_point_to_segment(px, py, poly[i][0], poly[i][1],
                                      poly[i + 1][0], poly[i + 1][1])
            if d < dmin:
                dmin = d
        if dmin <= max_dist + 1e-6:
            covered += 1
    return float(covered) / len(points)


def bridge_orthogonal(a, b):
    """Return L-shaped bridge points between a and b (two orthogonal legs)."""
    if abs(a[0] - b[0]) < 1e-9 or abs(a[1] - b[1]) < 1e-9:
        return [a, b]
    # Prefer horizontal then vertical.
    return [a, (b[0], a[1]), b]


def spiral_rectangle(L, B, R, T, xL, xR, y_entry, s, cw=True, side='bottom'):
    """
    Generate a single continuous polyline for a rectangle [L,R]x[B,T]
    with two entry points (xL,y_entry) and (xR,y_entry).
    Both arms initially go right (east) after a short vertical lead.
    side: which side the entries are on ('bottom','top','left','right').
    cw: True for CW rotation, False for CCW (mirror horizontally).
    Returns list of (x,y) points.
    """
    # Work in a local frame where the entry side is the bottom.
    pts = _spiral_bottom_entries(L, B, R, T, xL, xR, y_entry, s)
    if not cw:
        # Mirror horizontally about the center line.
        cx = (L + R) / 2.0
        pts = [(2 * cx - x, y) for (x, y) in pts]
    if side == 'bottom':
        return pts
    # Rotate to requested side: rotate 90*clockwise_steps and translate.
    if side == 'right':
        return _rotate_points(pts, L, B, R, T, 1)
    if side == 'top':
        return _rotate_points(pts, L, B, R, T, 2)
    if side == 'left':
        return _rotate_points(pts, L, B, R, T, 3)
    raise ValueError('bad side')


def _rotate_points(pts, L, B, R, T, steps):
    """Rotate rectangle coordinates 90*steps degrees clockwise."""
    w = R - L
    h = T - B
    # Use normalized coordinates then rotate and map back.
    out = []
    for x, y in pts:
        nx = (x - L) / float(w)
        ny = (y - B) / float(h)
        if steps == 1:
            # (nx,ny) -> (ny, 1-nx) in normalized, then scale to h x w
            rx = L + ny * h
            ry = B + (1 - nx) * w
        elif steps == 2:
            rx = L + (1 - nx) * w
            ry = B + (1 - ny) * h
        elif steps == 3:
            rx = L + (1 - ny) * h
            ry = B + nx * w
        else:
            rx, ry = x, y
        out.append((rx, ry))
    return out


def _spiral_bottom_entries(L, B, R, T, xL, xR, y_entry, s):
    """Core CW spiral with entries on the bottom side."""
    # Ensure xL < xR.
    if xL > xR:
        xL, xR = xR, xL
    pts = []
    # Right-entry (named "outer" by user) lead + perimeter + bridge.
    pts.append((xR, y_entry))
    pts.append((xR, B))
    pts.append((R, B))
    pts.append((R, T))
    pts.append((L, T))
    pts.append((L, B + s))
    pts.append((xL - s, B + s))
    pts.append((xL - s, B + 2 * s))

    # Right-entry spiral loops, k = 0,1,...
    k = 0
    while True:
        if k == 0:
            start_x = xL - s
        else:
            start_x = L + 2 * k * s
        bottom_y = B + 2 * (k + 1) * s
        right_x = R - 2 * (k + 1) * s
        top_y = T - 2 * (k + 1) * s
        left_x = L + 2 * (k + 1) * s
        next_y = B + 2 * (k + 2) * s

        # Stop if there is not enough room for this loop.
        if right_x <= left_x + s or top_y <= bottom_y + s:
            break
        # bottom segment
        pts.append((right_x, bottom_y))
        # right segment
        pts.append((right_x, top_y))
        # top segment
        pts.append((left_x, top_y))
        # left segment down to next_y
        pts.append((left_x, next_y))
        k += 1

    right_end = pts[-1]

    # Left-entry (named "inner") lead + spiral.
    left_pts = []
    left_pts.append((xL, y_entry))
    left_pts.append((xL, B + s))

    m = 0
    while True:
        if m == 0:
            start_x = xL
        else:
            start_x = L + (2 * m - 1) * s
        bottom_y = B + (2 * m + 1) * s
        right_x = R - (2 * m + 1) * s
        top_y = T - (2 * m + 1) * s
        left_x = L + (2 * m + 1) * s
        next_y = B + (2 * m + 3) * s

        if right_x <= left_x + s or top_y <= bottom_y + s:
            break
        left_pts.append((right_x, bottom_y))
        left_pts.append((right_x, top_y))
        left_pts.append((left_x, top_y))
        left_pts.append((left_x, next_y))
        m += 1

    left_end = left_pts[-1]

    # Align the left-entry endpoint to the right-entry y so the connecting
    # bridge is a horizontal segment in the gap between the two spirals.
    # This avoids the bridge landing on an existing vertical wall.
    left_x = left_end[0]
    aligned_left_end = (left_x, right_end[1])
    if len(left_pts) >= 2 and left_pts[-1][1] <= aligned_left_end[1] <= left_pts[-2][1]:
        # Truncate the last left-side segment; do not go below the bridge y.
        left_pts[-1] = aligned_left_end
        left_end = aligned_left_end

    # Connect the two spirals in the centre with an orthogonal bridge.
    bridge = bridge_orthogonal(right_end, left_end)
    # Append bridge and then traverse left-entry spiral in reverse.
    pts.extend(bridge[1:])  # bridge[0] == right_end already in pts
    skip_first = False
    if bridge and left_pts and bridge[-1] == left_pts[-1]:
        skip_first = True
    for idx, p in enumerate(reversed(left_pts)):
        if skip_first and idx == 0:
            continue
        pts.append(p)

    return pts


def polyline_length(poly):
    total = 0.0
    for i in range(len(poly) - 1):
        total += math.hypot(poly[i + 1][0] - poly[i][0],
                            poly[i + 1][1] - poly[i][1])
    return total


def svg_from_poly(poly, L, B, R, T, filename, stroke='blue'):
    """Write a simple SVG of the polyline inside the given bbox."""
    margin = 50
    w = R - L
    h = T - B
    # SVG coordinate system: y points down.  Flip y.
    def tx(x, y):
        return x - L + margin
    def ty(x, y):
        return (T - y) + margin
    pts_svg = ['{:.3f},{:.3f}'.format(tx(x, y), ty(x, y)) for (x, y) in poly]
    polyline = ' '.join(pts_svg)
    width = w + 2 * margin
    height = h + 2 * margin
    # Draw rectangle boundary.
    rect_x = tx(L, B)
    rect_y = ty(L, T)
    rect_w = w
    rect_h = h
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">\n'
        '  <rect x="{rx}" y="{ry}" width="{rw}" height="{rh}" '
        'fill="none" stroke="black" stroke-width="2"/>\n'
        '  <polyline points="{pts}" fill="none" stroke="{stroke}" stroke-width="3"/>\n'
        '</svg>\n'
    ).format(w=width, h=height, rx=rect_x, ry=rect_y, rw=rect_w, rh=rect_h,
             pts=polyline, stroke=stroke)
    with open(filename, 'w') as f:
        f.write(svg)


def svg_polygon(poly, polygon, filename, stroke='blue'):
    """SVG for an arbitrary polygon boundary."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    L, Rr = min(xs), max(xs)
    Bb, Tt = min(ys), max(ys)
    margin = 50
    w = Rr - L
    h = Tt - Bb
    def tx(x, y):
        return x - L + margin
    def ty(x, y):
        return (Tt - y) + margin
    pts_svg = ['{:.3f},{:.3f}'.format(tx(x, y), ty(x, y)) for (x, y) in poly]
    polyline = ' '.join(pts_svg)
    boundary = ' '.join('{:.3f},{:.3f}'.format(tx(x, y), ty(x, y)) for (x, y) in polygon + [polygon[0]])
    width = w + 2 * margin
    height = h + 2 * margin
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">\n'
        '  <polyline points="{bound}" fill="none" stroke="black" stroke-width="2"/>\n'
        '  <polyline points="{pts}" fill="none" stroke="{stroke}" stroke-width="3"/>\n'
        '</svg>\n'
    ).format(w=width, h=height, bound=boundary, pts=polyline, stroke=stroke)
    with open(filename, 'w') as f:
        f.write(svg)


def u_insert_pocket(poly, pocket_rect, s):
    """
    Very basic pocket filler: insert a U-shaped detour into a rectangular
    pocket.  pocket_rect = (L,B,R,T).  Returns a new polyline.
    This is a prototype helper; real pockets need pocket-aware placement.
    """
    pL, pB, pR, pT = pocket_rect
    # Find the closest segment on poly to the pocket.
    best_i = -1
    best_d = 1e9
    best_point = None
    for i in range(len(poly) - 1):
        x1, y1 = poly[i]
        x2, y2 = poly[i + 1]
        # Project pocket centre onto segment.
        cx = (pL + pR) / 2.0
        cy = (pB + pT) / 2.0
        dx = x2 - x1
        dy = y2 - y1
        seg_len2 = dx * dx + dy * dy
        if seg_len2 == 0:
            t = 0
        else:
            t = max(0.0, min(1.0, ((cx - x1) * dx + (cy - y1) * dy) / seg_len2))
        px = x1 + t * dx
        py = y1 + t * dy
        d = math.hypot(cx - px, cy - py)
        if d < best_d:
            best_d = d
            best_i = i
            best_point = (px, py)
    if best_i < 0:
        return poly
    # Insert a simple U from the segment into the pocket and back.
    # U opens from the nearest side, goes to the far side, and returns.
    # Use a small margin s from pocket walls.
    new_pts = poly[:best_i + 1]
    x1, y1 = poly[best_i]
    x2, y2 = poly[best_i + 1]
    # Determine direction of host segment.
    if abs(y2 - y1) < abs(x2 - x1):
        # horizontal host: enter from top or bottom of pocket
        enter = (best_point[0], pB + s)
        far = (best_point[0], pT - s)
        return_pt = (best_point[0], pB + s)
    else:
        # vertical host: enter from left or right of pocket
        enter = (pL + s, best_point[1])
        far = (pR - s, best_point[1])
        return_pt = (pL + s, best_point[1])
    new_pts.append(enter)
    new_pts.append(far)
    new_pts.append(return_pt)
    new_pts.extend(poly[best_i + 1:])
    return new_pts
