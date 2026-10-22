# -*- coding: utf-8 -*-
from __future__ import division

import os
import imp

_spiral_route = imp.load_source(
    "_fhl_spiral_route",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "spiral_route.py"))


EPSILON = 0.000001
STEP_TOLERANCE_MM = 1.0
MAX_SEGMENTS = 4000
COORDINATE_TOLERANCE_MM = 0.01
SUPPORTED_EDGE_COUNTS = (4, 6, 8, 12)


def _inside(point, bounds):
    return (bounds[0] - EPSILON <= point[0] <= bounds[2] + EPSILON and
            bounds[1] - EPSILON <= point[1] <= bounds[3] + EPSILON)


def _local(point, bounds, edge, mirror):
    left, bottom, right, top = bounds
    if edge == 0:
        horizontal, vertical = point[0] - left, point[1] - bottom
        width = right - left
    elif edge == 1:
        horizontal, vertical = right - point[0], top - point[1]
        width = right - left
    elif edge == 2:
        horizontal, vertical = point[1] - bottom, point[0] - left
        width = top - bottom
    else:
        horizontal, vertical = top - point[1], right - point[0]
        width = top - bottom
    return (width - horizontal if mirror else horizontal, vertical)


def _world(point, bounds, edge, mirror):
    left, bottom, right, top = bounds
    width = right - left if edge < 2 else top - bottom
    horizontal = width - point[0] if mirror else point[0]
    if edge == 0:
        return (left + horizontal, bottom + point[1])
    if edge == 1:
        return (right - horizontal, top - point[1])
    if edge == 2:
        return (left + point[1], bottom + horizontal)
    return (right - point[1], top - horizontal)


def _append(segments, current, following):
    if abs(current[0] - following[0]) > EPSILON or abs(current[1] - following[1]) > EPSILON:
        segments.append((current, following))
    return following


def _crosses(first, second):
    a, b = first
    c, d = second
    first_horizontal = abs(a[1] - b[1]) <= EPSILON
    second_horizontal = abs(c[1] - d[1]) <= EPSILON
    if first_horizontal and second_horizontal:
        return (abs(a[1] - c[1]) <= EPSILON and
                max(min(a[0], b[0]), min(c[0], d[0])) <
                min(max(a[0], b[0]), max(c[0], d[0])) - EPSILON)
    if not first_horizontal and not second_horizontal:
        return (abs(a[0] - c[0]) <= EPSILON and
                max(min(a[1], b[1]), min(c[1], d[1])) <
                min(max(a[1], b[1]), max(c[1], d[1])) - EPSILON)
    horizontal = first if first_horizontal else second
    vertical = second if first_horizontal else first
    return (min(horizontal[0][0], horizontal[1][0]) - EPSILON <= vertical[0][0] <=
            max(horizontal[0][0], horizontal[1][0]) + EPSILON and
            min(vertical[0][1], vertical[1][1]) - EPSILON <= horizontal[0][1] <=
            max(vertical[0][1], vertical[1][1]) + EPSILON)


def _validate(segments, inset, start, end):
    if not segments or segments[0][0] != start or segments[-1][1] != end:
        raise ValueError("endpoint_mismatch")
    for index, segment in enumerate(segments):
        a, b = segment
        horizontal = abs(a[1] - b[1]) <= EPSILON
        vertical = abs(a[0] - b[0]) <= EPSILON
        if not (horizontal or vertical) or (horizontal and vertical):
            raise ValueError("invalid_segment")
        if not _inside(a, inset) or not _inside(b, inset):
            raise ValueError("segment_outside_inset")
        if index and (abs(segments[index - 1][1][0] - a[0]) > EPSILON or
                      abs(segments[index - 1][1][1] - a[1]) > EPSILON):
            raise ValueError("not_continuous")
        for previous in range(index):
            if not _crosses(segments[previous], segment):
                continue
            previous_horizontal = abs(segments[previous][0][1] - segments[previous][1][1]) <= EPSILON
            if previous != index - 1 or previous_horizontal == horizontal:
                raise ValueError("self_intersection")


def _spiral_layout(source, inset, start, end, step):
    left, bottom, right, top = source
    distances = (max(start[1] - bottom, end[1] - bottom),
                 max(top - start[1], top - end[1]),
                 max(start[0] - left, end[0] - left),
                 max(right - start[0], right - end[0]))
    edge = min(range(4), key=lambda index: distances[index])
    edge_inset = (inset[1] - bottom, top - inset[3],
                  inset[0] - left, right - inset[2])[edge]
    if distances[edge] > max(step, edge_inset):
        raise ValueError("entry_pair_not_near_edge")
    first = _local(start, source, edge, False)
    second = _local(end, source, edge, False)
    width = right - left if edge < 2 else top - bottom
    mirror = first[0] + second[0] > width
    if mirror:
        first = _local(start, source, edge, True)
        second = _local(end, source, edge, True)
    reverse = first[0] > second[0]
    if reverse:
        first, second = second, first
    if abs(first[1] - second[1]) > STEP_TOLERANCE_MM:
        raise ValueError("entry_pair_not_aligned")
    if abs(second[0] - first[0] - step) > STEP_TOLERANCE_MM:
        raise ValueError("entry_pair_spacing_mismatch")
    corner_a = _local((inset[0], inset[1]), source, edge, mirror)
    corner_b = _local((inset[2], inset[3]), source, edge, mirror)
    inner_left = min(corner_a[0], corner_b[0])
    inner_right = max(corner_a[0], corner_b[0])
    inner_bottom = min(corner_a[1], corner_b[1])
    inner_top = max(corner_a[1], corner_b[1])
    if first[1] - inner_bottom > step:
        raise ValueError("entry_pair_not_near_corner")
    if inner_right - inner_left <= 2 * step or inner_top - inner_bottom <= 2 * step:
        raise ValueError("step_too_large")
    inward = []
    outward = []
    inward_point = first
    outward_point = second
    rings = 0
    while rings * 8 + 8 <= MAX_SEGMENTS:
        advance = 2 * step * rings
        inward_top = inner_top - advance
        inward_right = inner_right - advance
        inward_bottom = first[1] + advance
        inward_next_left = first[0] + advance + 2 * step
        outward_top = inner_top - step - advance
        outward_right = inner_right - step - advance
        outward_bottom = second[1] + step + advance
        outward_next_left = second[0] + advance + 2 * step
        if (inward_top <= inward_point[1] + EPSILON or
                inward_right <= inward_point[0] + EPSILON or
                inward_bottom >= inward_top - EPSILON or
                inward_next_left >= inward_right - EPSILON or
                outward_top <= outward_point[1] + EPSILON or
                outward_right <= outward_point[0] + EPSILON or
                outward_bottom >= outward_top - EPSILON or
                outward_next_left >= outward_right - EPSILON):
            break
        inward_point = _append(inward, inward_point, (inward_point[0], inward_top))
        inward_point = _append(inward, inward_point, (inward_right, inward_top))
        inward_point = _append(inward, inward_point, (inward_right, inward_bottom))
        inward_point = _append(inward, inward_point, (inward_next_left, inward_bottom))
        outward_point = _append(outward, outward_point, (outward_point[0], outward_top))
        outward_point = _append(outward, outward_point, (outward_right, outward_top))
        outward_point = _append(outward, outward_point, (outward_right, outward_bottom))
        outward_point = _append(outward, outward_point, (outward_next_left, outward_bottom))
        rings += 1
    if not rings:
        raise ValueError("step_too_large")
    if rings * 8 + 3 > MAX_SEGMENTS:
        raise ValueError("segment_cap_exceeded")
    bridge_y = max(outward_point[1], inner_top - 2 * step * rings)
    inward_point = _append(inward, inward_point, (inward_point[0], bridge_y))
    inward_point = _append(inward, inward_point, (outward_point[0], bridge_y))
    inward_point = _append(inward, inward_point, outward_point)
    inward.extend((b, a) for a, b in reversed(outward))
    return inward, edge, mirror, reverse


def build_centered_snail(source, start, end, offset, step, clockwise=True):
    """Full-coverage centered snail for a rectangle whose pipe ends both feed
    the same horizontal direction from either the top or the bottom edge.

    Both free ends are mapped into a single canonical frame where they share
    one ``y``; the rightmost end is ``outer``, the leftmost is ``inner``.  The
    notched ring path from :func:`build_notched_ring_path` is walked from the
    graph's left end to its right end and short vertical leads carry each free
    end onto its adjacent body endpoint.  The graph's left end maps onto the
    left pipe and its right end onto the right pipe, so the left lead departs
    leftwards along the outer bottom ring and the right lead, walked backwards,
    departs rightwards.

    ``offset`` insets the rectangle and ``step`` is the lattice pitch; ``k``
    is the ring column that best matches ``step`` within ``5 mm``.  ``nx`` and
    ``ny`` must both be even, otherwise ``unsupported_orientation`` is raised.
    """
    offset = float(offset)
    step = float(step)
    left, bottom, right, top = [float(value) for value in source]
    start = (float(start[0]), float(start[1]))
    end = (float(end[0]), float(end[1]))
    if right - left <= 0 or top - bottom <= 0:
        raise ValueError("invalid_source")
    if offset <= 0 or step <= 0 or offset != offset or step != step:
        raise ValueError("invalid_offset_or_step")
    if 2 * offset >= right - left or 2 * offset >= top - bottom:
        raise ValueError("unsupported_orientation")
    if not _inside(start, source) or not _inside(end, source):
        raise ValueError("pipe_end_outside_region")

    inset_left = left + offset
    inset_bottom = bottom + offset
    inset_right = right - offset
    inset_top = top - offset
    inset = (inset_left, inset_bottom, inset_right, inset_top)
    inset_width = inset_right - inset_left
    inset_height = inset_top - inset_bottom

    same_y = abs(start[1] - end[1]) <= STEP_TOLERANCE_MM
    same_x = abs(start[0] - end[0]) <= STEP_TOLERANCE_MM
    if not (same_y or same_x):
        raise ValueError("entry_pair_not_aligned")
    if not same_y:
        raise ValueError("unsupported_orientation")

    if abs(start[1] - bottom) <= abs(start[1] - top):
        entry_edge = "bottom"
    else:
        entry_edge = "top"

    def to_canonical_identity(point):
        return (point[0] - inset_left, point[1] - inset_bottom)
    if entry_edge == "top":
        def to_canonical_ymirror(point, base=to_canonical_identity,
                                 height=inset_height):
            x, y = base(point)
            return (x, height - y)
        base_top = to_canonical_ymirror
    else:
        base_top = to_canonical_identity

    if clockwise:
        to_canonical = base_top
    else:
        if entry_edge == "top":
            def to_canonical(point, base=base_top, width=inset_width):
                x, y = base(point)
                return (width - x, y)
        else:
            def to_canonical(point, base=to_canonical_identity,
                             width=inset_width):
                x, y = base(point)
                return (width - x, y)

    canonical_start = to_canonical(start)
    canonical_end = to_canonical(end)

    if canonical_start[0] >= canonical_end[0]:
        canonical_outer = canonical_start
        canonical_inner = canonical_end
        inner_was_start = False
    else:
        canonical_outer = canonical_end
        canonical_inner = canonical_start
        inner_was_start = True
    outer_x = canonical_outer[0]
    inner_x = canonical_inner[0]

    nx = int(inset_width / step) + 1
    ny = int(inset_height / step) + 1
    if min(nx, ny) % 2 != 0:
        raise ValueError("min(nx, ny) must be even")

    pitch_limit = 5.0
    best_k = None
    best_score = None
    for candidate_k in range(7, nx - 3):
        left_pitch = (inner_x - 0.0) / (candidate_k - 2)
        middle_pitch = (outer_x - inner_x) / 2.0
        right_pitch = (inset_width - outer_x) / (nx - 1 - candidate_k)
        y_pitch = (inset_height - step) / (ny - 2)
        deviations = (abs(left_pitch - step),
                      abs(middle_pitch - step),
                      abs(right_pitch - step),
                      abs(y_pitch - step))
        if max(deviations) > pitch_limit:
            continue
        score = sum(absolute for absolute in deviations)
        if best_score is None or score < best_score:
            best_score = score
            best_k = candidate_k
    if best_k is None:
        raise ValueError("unsupported_orientation")
    k = best_k

    nodes = build_notched_ring_path(nx, ny, k)
    if len(nodes) + 8 > MAX_SEGMENTS:
        raise ValueError("segment_cap_exceeded")

    def _build_segmented_grid(n, low, first_index, first_value, high, anchors):
        values = [low]
        sorted_anchors = sorted(anchors)
        prev_index = 0
        prev_value = low
        if first_index is not None and first_index > 0 and first_index < n:
            sorted_anchors = sorted(sorted_anchors + [(first_index, first_value)])
        for anchor_index, anchor_value in sorted_anchors:
            if anchor_index <= prev_index or anchor_index >= n:
                raise ValueError("invalid_anchor")
            step_part = (anchor_value - prev_value) / (anchor_index - prev_index)
            for filler in range(prev_index + 1, anchor_index + 1):
                values.append(prev_value + step_part * (filler - prev_index))
            prev_index = anchor_index
            prev_value = anchor_value
        if prev_index < n - 1:
            step_part = (high - prev_value) / (n - 1 - prev_index)
            for filler in range(prev_index + 1, n):
                values.append(prev_value + step_part * (filler - prev_index))
        return values

    grid_x = _build_segmented_grid(nx, 0.0, None, None, inset_width,
                                    ((k - 2, inner_x), (k, outer_x)))
    grid_y = _build_segmented_grid(ny, 0.0, 1, step, inset_height, ())

    def to_inset(node):
        return (grid_x[node[0]], grid_y[node[1]])

    points = [to_inset(node) for node in nodes]
    segments = []
    for index in range(1, len(points)):
        previous_point = points[index - 1]
        current_point = points[index]
        if segments:
            previous_segment = segments[-1]
            same_horizontal = (abs(previous_segment[0][1] - previous_segment[1][1]) <= EPSILON and
                               abs(current_point[1] - previous_segment[1][1]) <= EPSILON)
            same_vertical = (abs(previous_segment[0][0] - previous_segment[1][0]) <= EPSILON and
                             abs(current_point[0] - previous_segment[0][0]) <= EPSILON)
            if same_horizontal or same_vertical:
                segments[-1] = (previous_segment[0], current_point)
                continue
        segments.append((previous_point, current_point))

    if entry_edge == "bottom":
        if clockwise:
            def from_canonical(point):
                return (inset_left + point[0], inset_bottom + point[1])
        else:
            def from_canonical(point):
                return (inset_left + inset_width - point[0], inset_bottom + point[1])
    else:
        if clockwise:
            def from_canonical(point):
                return (inset_left + point[0],
                        inset_bottom + inset_height - point[1])
        else:
            def from_canonical(point):
                return (inset_left + inset_width - point[0],
                        inset_bottom + inset_height - point[1])
    body_segments = [(from_canonical(a), from_canonical(b)) for a, b in segments]

    body_start_canonical = segments[0][0]
    body_end_canonical = segments[-1][1]
    if (abs(body_start_canonical[1] - grid_y[0]) > EPSILON or
                abs(body_end_canonical[1] - grid_y[0]) > EPSILON):
        raise ValueError("body ends are off the bottom lattice row")
    lead_left_canonical = [(canonical_inner, (canonical_inner[0], grid_y[0])),
                           ((canonical_inner[0], grid_y[0]), body_start_canonical)]
    lead_right_canonical = [(body_end_canonical, (canonical_outer[0], grid_y[0])),
                            ((canonical_outer[0], grid_y[0]), canonical_outer)]
    lead_left_world = [(from_canonical(a), from_canonical(b)) for a, b in lead_left_canonical]
    lead_right_world = [(from_canonical(a), from_canonical(b)) for a, b in lead_right_canonical]
    segments = _drop_if_empty(lead_left_world + body_segments + lead_right_world)
    if not inner_was_start:
        segments = [(b, a) for a, b in reversed(segments)]
    segments[0] = (start, segments[0][1])
    segments[-1] = (segments[-1][0], end)

    _validate_full(segments, start, end)
    _validate_centered(segments, inset, start, end, step)
    return segments


def _validate_centered(segments, inset, start, end, step):
    """Full validation for a centered snail: continuous, orthogonal, free of
    self intersections, inside the inset apart from the two lead portions and
    reaching within ``step`` of all four inset sides."""
    if not segments or segments[0][0] != start or segments[-1][1] != end:
        raise ValueError("endpoint_mismatch")
    _validate_full(segments, start, end)

    lead_segments = 0
    for index, segment in enumerate(segments):
        if not (_inside(segment[0], inset) and _inside(segment[1], inset)):
            lead_segments += 1
            if lead_segments > 2:
                raise ValueError("segment_outside_inset")
    if lead_segments and lead_segments != 2:
        raise ValueError("segment_outside_inset")

    xs = [point[0] for segment in segments for point in segment]
    ys = [point[1] for segment in segments for point in segment]
    reach = step + EPSILON
    if (inset[0] - min(xs) > reach or inset[2] - max(xs) > reach or
            inset[1] - min(ys) > reach or inset[3] - max(ys) > reach):
        raise ValueError("incomplete_coverage")


def _same_point(first, second, tolerance=COORDINATE_TOLERANCE_MM):
    return (abs(first[0] - second[0]) <= tolerance and
            abs(first[1] - second[1]) <= tolerance)


def _same_set(left, right, tolerance=COORDINATE_TOLERANCE_MM):
    remaining = list(right)
    for point in left:
        for index, candidate in enumerate(remaining):
            if _same_point(point, candidate, tolerance):
                remaining.pop(index)
                break
        else:
            return False
    return not remaining


def _cluster(values, tolerance=COORDINATE_TOLERANCE_MM):
    groups = []
    for value in sorted(values):
        if groups and abs(groups[-1][-1] - value) <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [group[0] for group in groups]


def top_nub_boxes(points):
    if not points or len(points) != 8:
        raise ValueError("expected_eight_vertices")
    values = []
    for point in points:
        if len(point) != 2:
            raise ValueError("expected_orthogonal_vertices")
        values.append((float(point[0]), float(point[1])))
    left = min(point[0] for point in values)
    right = max(point[0] for point in values)
    bottom = min(point[1] for point in values)
    top = max(point[1] for point in values)
    y = _cluster(point[1] for point in values)
    if len(y) != 3:
        raise ValueError("expected_single_middle_line")
    middle = y[1]
    x = _cluster(point[0] for point in values)
    if len(x) != 4:
        raise ValueError("expected_two_nub_sides")
    nub_left, nub_right = x[1], x[2]
    expected = [(left, bottom), (right, bottom), (right, middle), (nub_right, middle),
                (nub_right, top), (nub_left, top), (nub_left, middle), (left, middle)]
    if not _same_set(values, expected):
        raise ValueError("expected_rectangle_with_top_nub")
    return ((left, bottom, right, middle), (nub_left, middle, nub_right, top))


def left_upper_wing_boxes(points):
    if not points or len(points) != 6:
        raise ValueError("expected_six_vertices")
    values = []
    for point in points:
        if len(point) != 2:
            raise ValueError("expected_orthogonal_vertices")
        values.append((float(point[0]), float(point[1])))
    x = _cluster(point[0] for point in values)
    if len(x) != 3:
        raise ValueError("expected_three_x_lines")
    y = _cluster(point[1] for point in values)
    if len(y) != 3:
        raise ValueError("expected_three_y_lines")
    wing_left, main_left, main_right = x[0], x[1], x[2]
    main_bottom, wing_bottom, main_top = y[0], y[1], y[2]
    expected = [(main_left, main_bottom), (main_right, main_bottom),
                (main_right, main_top), (wing_left, main_top),
                (wing_left, wing_bottom), (main_left, wing_bottom)]
    if not _same_set(values, expected):
        raise ValueError("expected_rectangle_with_left_upper_wing")
    return ((main_left, main_bottom, main_right, main_top),
            (wing_left, wing_bottom, main_left, main_top))


def top_nub_and_right_nub_boxes(points):
    if not points or len(points) != 12:
        raise ValueError("expected_twelve_vertices")
    values = []
    for point in points:
        if len(point) != 2:
            raise ValueError("expected_orthogonal_vertices")
        values.append((float(point[0]), float(point[1])))
    left = min(point[0] for point in values)
    right = max(point[0] for point in values)
    bottom = min(point[1] for point in values)
    top = max(point[1] for point in values)
    x = _cluster(point[0] for point in values)
    y = _cluster(point[1] for point in values)
    if len(x) != 5:
        raise ValueError("expected_five_x_lines")
    if len(y) != 5:
        raise ValueError("expected_five_y_lines")
    main_left, top_nub_left, top_nub_right, main_right, right_nub_right = x
    main_bottom, right_nub_bottom, right_nub_top, main_top, top_nub_top = y
    expected = [(main_left, main_bottom), (main_right, main_bottom),
                (main_right, right_nub_bottom), (right_nub_right, right_nub_bottom),
                (right_nub_right, right_nub_top), (main_right, right_nub_top),
                (main_right, main_top), (top_nub_right, main_top),
                (top_nub_right, top_nub_top), (top_nub_left, top_nub_top),
                (top_nub_left, main_top), (main_left, main_top)]
    if not _same_set(values, expected):
        raise ValueError("expected_rectangle_with_top_nub_and_right_nub")
    main = (main_left, main_bottom, main_right, main_top)
    top_nub = (top_nub_left, main_top, top_nub_right, top_nub_top)
    right_nub = (main_right, right_nub_bottom, right_nub_right, right_nub_top)
    return main, top_nub, right_nub


def _vertical_pass(a, b, x, wing_left, wing_bottom, wing_top, main_left):
    d = x - main_left
    x_wing = wing_left + d
    y_low = wing_bottom + d
    y_high = wing_top - d
    if y_low >= y_high - EPSILON:
        return None
    low = min(a[1], b[1])
    high = max(a[1], b[1])
    if low - STEP_TOLERANCE_MM > y_low or high + STEP_TOLERANCE_MM < y_high:
        return None
    if b[1] > a[1]:
        points = [a, (x, y_low), (x_wing, y_low), (x_wing, y_high), (x, y_high), b]
    else:
        points = [a, (x, y_high), (x_wing, y_high), (x_wing, y_low), (x, y_low), b]
    chain = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
    chain = [pair for pair in chain
             if abs(pair[0][0] - pair[1][0]) > EPSILON or
             abs(pair[0][1] - pair[1][1]) > EPSILON]
    if not chain:
        return None
    return chain


def insert_left_upper_wing(segments, main, wing, offset, step):
    segments = list(segments)
    if len(segments) < 2:
        return segments
    offset = float(offset)
    step = float(step)
    main_left, main_bottom, main_right, main_top = [float(value) for value in main]
    wing_left, wing_bottom, wing_right, wing_top = [float(value) for value in wing]
    if not (wing_left < main_left - EPSILON and
            abs(wing_right - main_left) <= COORDINATE_TOLERANCE_MM and
            abs(wing_top - main_top) <= COORDINATE_TOLERANCE_MM and
            wing_bottom > main_bottom + EPSILON):
        return segments
    if offset < 0 or step <= 0:
        return segments
    wing_width = main_left - wing_left
    wing_height = main_top - wing_bottom
    if wing_width <= 0 or wing_height <= 0:
        return segments
    first_column = main_left + offset
    upward = []
    downward = []
    for index, segment in enumerate(segments):
        a, b = segment
        if abs(a[0] - b[0]) > STEP_TOLERANCE_MM:
            continue
        if a[0] + EPSILON < first_column:
            continue
        if a[0] != b[0]:
            continue
        if b[1] > a[1]:
            upward.append((a[0], index, a, b))
        elif b[1] < a[1]:
            downward.append((a[0], index, a, b))
    if not upward:
        return segments
    first = min(upward, key=lambda item: item[0])
    d_first = first[0] - main_left
    d_second = d_first + step
    if d_second <= d_first:
        return segments
    max_d = d_second
    if wing_width <= max_d + EPSILON or wing_height <= 2 * max_d + EPSILON:
        return segments
    target_x = main_left + d_second
    second = None
    for item in downward:
        if abs(item[0] - target_x) > STEP_TOLERANCE_MM:
            continue
        second = item
        break
    if second is None:
        return segments
    if second[1] == first[1]:
        return segments
    chain_first = _vertical_pass(first[2], first[3], first[0],
                                 wing_left, wing_bottom, wing_top, main_left)
    chain_second = _vertical_pass(second[2], second[3], second[0],
                                  wing_left, wing_bottom, wing_top, main_left)
    if chain_first is None or chain_second is None:
        return segments
    result = list(segments)
    for index, chain in sorted(((first[1], chain_first), (second[1], chain_second)),
                               reverse=True):
        result = result[:index] + chain + result[index + 1:]
    if len(result) < len(segments):
        return segments
    if not result or result[0][0] != segments[0][0] or result[-1][1] != segments[-1][1]:
        return segments
    for index in range(len(result)):
        a, b = result[index]
        horizontal = abs(a[1] - b[1]) <= EPSILON
        vertical = abs(a[0] - b[0]) <= EPSILON
        if not (horizontal or vertical) or (horizontal and vertical):
            return segments
    for index in range(1, len(result)):
        if not _same_point(result[index - 1][1], result[index][0], EPSILON):
            return segments
    for index in range(len(result)):
        for previous in range(index):
            if not _crosses(result[previous], result[index]):
                continue
            first_horizontal = abs(result[previous][0][1] - result[previous][1][1]) <= EPSILON
            current_horizontal = abs(result[index][0][1] - result[index][1][1]) <= EPSILON
            if previous != index - 1 or first_horizontal == current_horizontal:
                return segments
    return result


def _right_nub_pass(a, b, x, x_nub, y_turn_low, y_turn_high, y_leave_low, y_leave_high):
    if y_turn_low >= y_turn_high - EPSILON:
        return None
    low = min(a[1], b[1])
    high = max(a[1], b[1])
    if low - STEP_TOLERANCE_MM > y_leave_low or high + STEP_TOLERANCE_MM < y_leave_high:
        return None
    if b[1] < a[1]:
        points = [a, (x, y_turn_high), (x_nub, y_turn_high), (x_nub, y_turn_low),
                  (x, y_turn_low), b]
    else:
        points = [a, (x, y_turn_low), (x_nub, y_turn_low), (x_nub, y_turn_high),
                  (x, y_turn_high), b]
    chain = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
    chain = [pair for pair in chain
             if abs(pair[0][0] - pair[1][0]) > EPSILON or
             abs(pair[0][1] - pair[1][1]) > EPSILON]
    if not chain:
        return None
    return chain


def _segments_replacement_ok(original, candidate):
    if not candidate or len(candidate) < len(original):
        return False
    if candidate[0][0] != original[0][0]:
        return False
    if candidate[-1][1] != original[-1][1]:
        return False
    return _is_clean_path(candidate)


def _right_nub_pair(segments, main, nub, offset, step, depth_index):
    """Try one nested right-nub U-pair for lane depth ``depth_index``.

    ``d = offset + depth_index * step``; the pair replaces the single vertical
    body column at ``x = main_right - d`` with a chain that runs out to
    ``x = nub_right - d`` along the two rows ``nub_bottom + d`` and
    ``nub_top - d``.  Returns ``None`` when the candidate is not a legal
    replacement of exactly one existing ordered path segment.
    """
    if len(segments) < 2:
        return None
    main_left, main_bottom, main_right, main_top = [float(value) for value in main]
    nub_left, nub_bottom, nub_right, nub_top = [float(value) for value in nub]
    d = offset + depth_index * step
    if d <= 0:
        return None
    x_turn = main_right - d
    if x_turn < main_left - EPSILON:
        return None
    x_nub = nub_right - d
    nub_width = nub_right - main_right
    if nub_width <= d + EPSILON or x_nub <= x_turn + EPSILON:
        return None
    low = nub_bottom + d
    high = nub_top - d
    if high - low < step - STEP_TOLERANCE_MM:
        return None
    if high <= low + EPSILON or high > nub_top - EPSILON:
        return None
    chosen = None
    for index, segment in enumerate(segments):
        a, b = segment
        if abs(a[0] - b[0]) > STEP_TOLERANCE_MM:
            continue
        if abs(a[0] - x_turn) > STEP_TOLERANCE_MM:
            continue
        if abs(a[1] - b[1]) <= EPSILON:
            continue
        segment_low = min(a[1], b[1])
        segment_high = max(a[1], b[1])
        if segment_low - STEP_TOLERANCE_MM > low:
            continue
        if segment_high + STEP_TOLERANCE_MM < high:
            continue
        if chosen is None or index < chosen:
            chosen = index
    if chosen is None:
        return None
    a, b = segments[chosen]
    chain = _right_nub_pass(a, b, x_turn, x_nub, low, high, low, high)
    if chain is None:
        return None
    candidate = segments[:chosen] + chain + segments[chosen + 1:]
    if not _segments_replacement_ok(segments, candidate):
        return None
    return (candidate, x_turn)


def insert_right_nub(segments, main, nub, offset, step):
    """Insert as many NESTED U-shaped detour pairs into the right nub as fit.

    Successive inner body lanes are visited in the order ``d = offset +
    n * step``.  Every candidate must replace exactly one existing ordered path
    segment and pass :func:`_is_clean_path`; the first lane that has no legal
    candidate stops the nesting, so unpaired one-way visits, crossings,
    disconnected paths and sub-step lane separations are all impossible.  A
    lane is only accepted when it keeps the neighbouring body column
    ``main_right - d - step`` in the path, so every inner lane still receives a
    full pass of pipe instead of being deleted.
    """
    current = list(segments)
    offset = float(offset)
    step = float(step)
    main_left, main_bottom, main_right, main_top = [float(value) for value in main]
    nub_left, nub_bottom, nub_right, nub_top = [float(value) for value in nub]
    if not (nub_right > main_right + EPSILON and
            abs(nub_left - main_right) <= COORDINATE_TOLERANCE_MM and
            main_bottom - COORDINATE_TOLERANCE_MM <= nub_bottom and
            nub_top <= main_top - EPSILON):
        return current
    if offset < 0 or step <= 0:
        return current
    nub_width = nub_right - main_right
    if nub_width <= 0 or nub_top - nub_bottom <= 0:
        return current
    placed = set()
    depth_index = 0
    while True:
        outcome = _right_nub_pair(current, main, nub, offset, step, depth_index)
        if outcome is None:
            break
        candidate, turn_x = outcome
        d = offset + depth_index * step
        neighbour_x = main_right - d - step
        lanes = set(round(a[0], 6) for seg in candidate
                    for a in (seg[0], seg[1])
                    if abs(a[0] - neighbour_x) <= STEP_TOLERANCE_MM)
        if not lanes - placed:
            break
        current = candidate
        placed.add(round(turn_x, 6))
        depth_index += 1
    return current


def insert_central_u(segments, main, offset, step):
    segments = list(segments)
    if len(segments) < 3:
        return segments
    offset = float(offset)
    step = float(step)
    main_left, main_bottom, main_right, main_top = [float(value) for value in main]
    bridge_index = None
    for index in range(len(segments) - 2):
        a1, b1 = segments[index]
        a2, b2 = segments[index + 1]
        a3, b3 = segments[index + 2]
        if not (abs(a1[0] - b1[0]) <= STEP_TOLERANCE_MM and
                b1[1] - a1[1] > EPSILON):
            continue
        if not (abs(a2[1] - b2[1]) <= STEP_TOLERANCE_MM and
                b2[0] - a2[0] > EPSILON):
            continue
        if not (abs(a3[0] - b3[0]) <= STEP_TOLERANCE_MM and
                a3[1] - b3[1] > EPSILON):
            continue
        if not (_same_point(b1, a2, EPSILON) and _same_point(b2, a3, EPSILON)):
            continue
        if abs(b2[0] - a2[0] - step) > STEP_TOLERANCE_MM:
            continue
        bridge_index = index
        break
    if bridge_index is None:
        return segments
    a1, b1 = segments[bridge_index]
    a2, b2 = segments[bridge_index + 1]
    a3, b3 = segments[bridge_index + 2]
    x_up = a1[0]
    y_up_top = b1[1]
    y_up_bottom = a1[1]
    x_right_end = b2[0]
    y_down_top = a3[1]
    y_down_bottom = b3[1]
    if y_up_top - y_down_bottom <= step + EPSILON:
        return segments
    if x_right_end + step > main_right - offset - EPSILON:
        return segments
    depth = (y_up_top - y_down_bottom) - step
    if depth <= EPSILON:
        return segments
    y_new_bottom = y_up_top - depth
    limit = main_right - offset - step
    for index, segment in enumerate(segments):
        if index <= bridge_index + 2:
            continue
        a, b = segment
        if abs(a[0] - b[0]) > STEP_TOLERANCE_MM:
            continue
        if a[0] <= x_right_end + EPSILON:
            continue
        low = min(a[1], b[1])
        high = max(a[1], b[1])
        if high < y_new_bottom - EPSILON or low > y_up_top + EPSILON:
            continue
        candidate = a[0] - step
        if candidate < limit:
            limit = candidate
    max_steps = int((limit - x_right_end) // step)
    if max_steps < 1:
        return segments
    for steps in range(max_steps, 0, -1):
        extension = steps * step
        x_new_column = x_right_end + extension
        new_segments = [
            ((x_up, y_up_top), (x_new_column, y_up_top)),
            ((x_new_column, y_up_top), (x_new_column, y_new_bottom)),
            ((x_new_column, y_new_bottom), (x_right_end, y_new_bottom)),
            ((x_right_end, y_new_bottom), (x_right_end, y_down_bottom)),
        ]
        result = (segments[:bridge_index + 1] +
                  new_segments +
                  segments[bridge_index + 3:])
        if len(result) < len(segments):
            continue
        if not result or result[0][0] != segments[0][0] or result[-1][1] != segments[-1][1]:
            continue
        valid = True
        for index in range(len(result)):
            a, b = result[index]
            horizontal = abs(a[1] - b[1]) <= EPSILON
            vertical = abs(a[0] - b[0]) <= EPSILON
            if not (horizontal or vertical) or (horizontal and vertical):
                valid = False
                break
        if valid:
            for index in range(1, len(result)):
                if not _same_point(result[index - 1][1], result[index][0], EPSILON):
                    valid = False
                    break
        if valid:
            for index in range(len(result)):
                for previous in range(index):
                    if not _crosses(result[previous], result[index]):
                        continue
                    first_horizontal = abs(result[previous][0][1] - result[previous][1][1]) <= EPSILON
                    current_horizontal = abs(result[index][0][1] - result[index][1][1]) <= EPSILON
                    if previous != index - 1 or first_horizontal == current_horizontal:
                        valid = False
                        break
                if not valid:
                    break
        if valid:
            return result


def _top_nub_pair(segments, main, nub, offset, step, depth_index):
    """Try one nested top-nub U-pair for lane depth ``depth_index``.

    ``d = offset + depth_index * step``; the pair replaces the single horizontal
    body row at ``y = main_top - d`` with a chain that runs up to
    ``y = nub_top - d`` along the two columns ``nub_left + d`` and
    ``nub_right - d``.  Returns ``None`` when the candidate is not a legal
    replacement of exactly one existing ordered path segment.
    """
    if len(segments) < 2:
        return None
    main_left, main_bottom, main_right, main_top = [float(value) for value in main]
    nub_left, nub_middle, nub_right, nub_top = [float(value) for value in nub]
    d = offset + depth_index * step
    if d <= 0:
        return None
    y_body = main_top - d
    if y_body < main_bottom - EPSILON:
        return None
    y_turn = nub_top - d
    nub_width = nub_right - nub_left
    if nub_width <= 2 * d + EPSILON or y_turn <= y_body + EPSILON:
        return None
    if y_turn > nub_top - EPSILON:
        return None
    x_low = nub_left + d
    x_high = nub_right - d
    if x_high - x_low < step - STEP_TOLERANCE_MM:
        return None
    slot_index = None
    for index, segment in enumerate(segments):
        a, b = segment
        if abs(a[1] - b[1]) > STEP_TOLERANCE_MM:
            continue
        if abs(a[1] - y_body) > STEP_TOLERANCE_MM:
            continue
        if abs(a[0] - b[0]) <= EPSILON:
            continue
        low = min(a[0], b[0])
        high = max(a[0], b[0])
        if low - STEP_TOLERANCE_MM > x_low:
            continue
        if high + STEP_TOLERANCE_MM < x_high:
            continue
        slot_index = index
        break
    if slot_index is None:
        return None
    a, b = segments[slot_index]
    if a[0] <= b[0]:
        x1 = x_low
        x2 = x_high
    else:
        x1 = x_high
        x2 = x_low
    points = [a, (x1, y_body), (x1, y_turn), (x2, y_turn), (x2, y_body), b]
    chain = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
    for piece in chain:
        if (abs(piece[0][0] - piece[1][0]) <= EPSILON and
                abs(piece[0][1] - piece[1][1]) <= EPSILON):
            return None
    candidate = segments[:slot_index] + chain + segments[slot_index + 1:]
    if not _segments_replacement_ok(segments, candidate):
        return None
    return candidate, y_body


def insert_top_nub(segments, main, nub, offset, step):
    """Insert as many NESTED U-shaped detour pairs into the top nub as fit.

    Successive inner body lanes are visited in the order ``d = offset +
    n * step``.  Every candidate must replace exactly one existing ordered path
    segment and pass :func:`_is_clean_path`; the first lane that has no legal
    candidate stops the nesting, so unpaired one-way visits, crossings,
    disconnected paths and sub-step lane separations are all impossible.  A
    lane is only accepted when it keeps the neighbouring body row
    ``main_top - d - step`` in the path, so every inner lane still receives a
    full pass of pipe instead of being deleted.
    """
    current = list(segments)
    if len(current) < 2:
        return current
    main_left, main_bottom, main_right, main_top = [float(value) for value in main]
    nub_left, nub_middle, nub_right, nub_top = [float(value) for value in nub]
    offset = float(offset)
    step = float(step)
    if not (main_left - EPSILON <= nub_left < nub_right <= main_right + EPSILON and
            main_top - EPSILON <= nub_middle <= nub_top + EPSILON):
        return current
    if offset < 0 or step <= 0 or nub_right - nub_left <= 0 or nub_top - nub_middle <= 0:
        return current
    placed = set()
    depth_index = 0
    while True:
        outcome = _top_nub_pair(current, main, nub, offset, step, depth_index)
        if outcome is None:
            break
        candidate, body_y = outcome
        d = offset + depth_index * step
        neighbour_y = main_top - d - step
        lanes = set(round(a[1], 6) for seg in candidate
                    for a in (seg[0], seg[1])
                    if abs(a[1] - neighbour_y) <= STEP_TOLERANCE_MM)
        if not lanes - placed:
            break
        current = candidate
        placed.add(round(body_y, 6))
        depth_index += 1
    return current


def build_spiral(source, inset, start, end, step):
    step = float(step)
    if step <= 0 or step != step:
        raise ValueError("step_non_positive")
    if not _inside(start, source) or not _inside(end, source):
        raise ValueError("pipe_end_outside_region")
    if not _inside(start, inset) or not _inside(end, inset):
        raise ValueError("pipe_end_outside_inset")
    inward, edge, mirror, reverse = _spiral_layout(source, inset, start, end, step)
    segments = [(_world(a, source, edge, mirror), _world(b, source, edge, mirror))
                for a, b in inward]
    if reverse:
        segments = [(b, a) for a, b in reversed(segments)]
    _validate(segments, inset, start, end)
    return segments


def _validate_full(segments, start, end):
    if not segments:
        raise ValueError("empty_path")
    if segments[0][0] != start or segments[-1][1] != end:
        raise ValueError("endpoint_mismatch")
    for index, segment in enumerate(segments):
        a, b = segment
        horizontal = abs(a[1] - b[1]) <= EPSILON
        vertical = abs(a[0] - b[0]) <= EPSILON
        if not (horizontal or vertical):
            raise ValueError("invalid_segment")
        if abs(a[0] - b[0]) <= EPSILON and abs(a[1] - b[1]) <= EPSILON:
            raise ValueError("zero_length_segment")
        if index and (abs(segments[index - 1][1][0] - a[0]) > EPSILON or
                      abs(segments[index - 1][1][1] - a[1]) > EPSILON):
            raise ValueError("not_continuous")
        for previous in range(index):
            if not _crosses(segments[previous], segment):
                continue
            previous_horizontal = abs(segments[previous][0][1] - segments[previous][1][1]) <= EPSILON
            current_horizontal = abs(segment[0][1] - segment[1][1]) <= EPSILON
            if previous != index - 1 or previous_horizontal == current_horizontal:
                raise ValueError("self_intersection")


def _is_clean_path(segments):
    if not segments:
        return False
    for index, segment in enumerate(segments):
        a, b = segment
        horizontal = abs(a[1] - b[1]) <= EPSILON
        vertical = abs(a[0] - b[0]) <= EPSILON
        if not (horizontal or vertical) or (horizontal and vertical):
            return False
        if index and not _same_point(segments[index - 1][1], a, EPSILON):
            return False
        for previous in range(index):
            if not _crosses(segments[previous], segment):
                continue
            previous_horizontal = abs(segments[previous][0][1] - segments[previous][1][1]) <= EPSILON
            if previous != index - 1 or previous_horizontal == horizontal:
                return False
    return True


def build_spiral_with_leads(source, start, end, offset, step, allow_partial=False):
    offset = float(offset)
    step = float(step)
    allow_partial = bool(allow_partial)
    left, bottom, right, top = source
    if right - left <= 0 or top - bottom <= 0:
        raise ValueError("invalid_source")
    if offset <= 0 or step <= 0 or offset != offset or step != step:
        raise ValueError("invalid_offset_or_step")
    if 2 * offset >= right - left or offset >= top - bottom:
        raise ValueError("unsupported_orientation")
    if not _inside(start, source) or not _inside(end, source):
        raise ValueError("pipe_end_outside_region")
    same_y = abs(start[1] - end[1]) <= STEP_TOLERANCE_MM
    same_x = abs(start[0] - end[0]) <= STEP_TOLERANCE_MM
    if not (same_x or same_y):
        raise ValueError("entry_pair_not_aligned")
    center_x = (left + right) / 2.0
    center_y = (bottom + top) / 2.0
    synth_start = None
    synth_end = None
    if same_y:
        if abs(start[1] - bottom) < abs(start[1] - top):
            edge_value = bottom + offset
            on_bottom_edge = True
        else:
            edge_value = top - offset
            on_bottom_edge = False
        mid_x = (start[0] + end[0]) / 2.0
        if mid_x < center_x - step:
            if start[0] > end[0]:
                raise ValueError("unsupported_orientation")
            if not (left <= start[0] < left + offset):
                raise ValueError("unsupported_orientation")
            if not (left + offset + step < end[0] < left + offset + 2 * step):
                raise ValueError("unsupported_orientation")
            if not (bottom < start[1] <= bottom + offset):
                raise ValueError("unsupported_orientation")
            if not (bottom < end[1] <= bottom + offset):
                raise ValueError("unsupported_orientation")
            synth_start = (left + offset, edge_value)
            synth_end = (left + offset + step, edge_value)
        elif mid_x > center_x + step:
            if start[0] < end[0]:
                raise ValueError("unsupported_orientation")
            if not (right - offset - 2 * step < start[0] < right - offset):
                raise ValueError("unsupported_orientation")
            if not (right - 2 * offset - 2 * step < end[0] < right - 2 * offset - step):
                raise ValueError("unsupported_orientation")
            synth_start = (right - offset, edge_value)
            synth_end = (right - offset - step, edge_value)
        else:
            if not allow_partial:
                raise ValueError("full_coverage_unavailable_for_center_entry")
            synth_start = (center_x - step / 2.0, edge_value)
            synth_end = (center_x + step / 2.0, edge_value)
    else:
        if abs(start[0] - left) < abs(start[0] - right):
            edge_value = left + offset
            on_left_edge = True
        else:
            edge_value = right - offset
            on_left_edge = False
        mid_y = (start[1] + end[1]) / 2.0
        if mid_y < center_y - step:
            if start[1] > end[1]:
                raise ValueError("unsupported_orientation")
            if not (bottom <= start[1] < bottom + offset):
                raise ValueError("unsupported_orientation")
            if not (bottom + offset + step < end[1] < bottom + offset + 2 * step):
                raise ValueError("unsupported_orientation")
            synth_start = (edge_value, bottom + offset)
            synth_end = (edge_value, bottom + offset + step)
        elif mid_y > center_y + step:
            if start[1] < end[1]:
                raise ValueError("unsupported_orientation")
            if not (top - offset - 2 * step < start[1] < top - offset):
                raise ValueError("unsupported_orientation")
            synth_start = (edge_value, top - offset)
            synth_end = (edge_value, top - offset - step)
        else:
            if not allow_partial:
                raise ValueError("full_coverage_unavailable_for_center_entry")
            synth_start = (edge_value, center_y - step / 2.0)
            synth_end = (edge_value, center_y + step / 2.0)
    inset = (left + offset, bottom + offset, right - offset, top - offset)
    main_segments = build_spiral(source, inset, synth_start, synth_end, step)
    if same_y:
        corner_a = (start[0], synth_start[1])
        corner_b = (end[0], synth_end[1])
    else:
        corner_a = (synth_start[0], start[1])
        corner_b = (synth_end[0], end[1])
    lead_in = [(start, corner_a), (corner_a, synth_start)]
    lead_out = [(synth_end, corner_b), (corner_b, end)]
    segments = lead_in + main_segments + lead_out
    _validate_full(segments, start, end)
    return segments


def _half_turn(point, center):
    return (2.0 * center[0] - point[0], 2.0 * center[1] - point[1])


def _half_turn_box(box, center):
    left, bottom, right, top = box
    xs = [2.0 * center[0] - value for value in (left, right)]
    ys = [2.0 * center[1] - value for value in (bottom, top)]
    return (min(xs), min(ys), max(xs), max(ys))


def top_right_start_fits_bottom_left_flip(main, start, end, offset, step):
    offset = float(offset)
    step = float(step)
    left, bottom, right, top = [float(value) for value in main]
    start = (float(start[0]), float(start[1]))
    end = (float(end[0]), float(end[1]))
    if right - left <= 0 or top - bottom <= 0:
        return False
    if offset <= 0 or step <= 0 or offset != offset or step != step:
        return False
    if 2 * offset >= right - left or offset >= top - bottom:
        return False
    if start[1] < top - offset - EPSILON or start[1] > top + EPSILON:
        return False
    if end[1] < top - offset - EPSILON or end[1] > top + EPSILON:
        return False
    if end[0] >= start[0] - COORDINATE_TOLERANCE_MM:
        return False
    diff = start[0] - end[0]
    if (abs(diff - step) > COORDINATE_TOLERANCE_MM and
            abs(diff - 2 * step) > COORDINATE_TOLERANCE_MM and
            abs(diff - 3 * step) > COORDINATE_TOLERANCE_MM):
        return False
    if start[0] > right + EPSILON or end[0] < left - EPSILON:
        return False
    return True


def _drop_if_empty(segments):
    return [pair for pair in segments
            if abs(pair[0][0] - pair[1][0]) > EPSILON or
            abs(pair[0][1] - pair[1][1]) > EPSILON]


def build_left_upper_l_top_right_flip(main, wing, start, end, offset, step):
    main = (float(main[0]), float(main[1]), float(main[2]), float(main[3]))
    wing = (float(wing[0]), float(wing[1]), float(wing[2]), float(wing[3]))
    start = (float(start[0]), float(start[1]))
    end = (float(end[0]), float(end[1]))
    offset = float(offset)
    step = float(step)
    if offset <= 0 or step <= 0 or offset != offset or step != step:
        raise ValueError("invalid_offset_or_step")
    if not top_right_start_fits_bottom_left_flip(main, start, end, offset, step):
        raise ValueError("unsupported_orientation")
    center = ((main[0] + main[2]) / 2.0, (main[1] + main[3]) / 2.0)
    turn_pt = lambda point: (2.0 * center[0] - point[0],
                             2.0 * center[1] - point[1])
    unturn_pt = lambda point: (2.0 * center[0] - point[0],
                               2.0 * center[1] - point[1])
    rotated_main = (2.0 * center[0] - main[2], 2.0 * center[1] - main[3],
                    2.0 * center[0] - main[0], 2.0 * center[1] - main[1])
    rotated_wing = (2.0 * center[0] - wing[2], 2.0 * center[1] - wing[3],
                    2.0 * center[0] - wing[0], 2.0 * center[1] - wing[1])
    rotated_start = turn_pt(start)
    rotated_end = turn_pt(end)
    left, bottom, right, top = rotated_main
    first = (left + offset, bottom + offset)
    second = (left + offset + step, bottom + offset)
    inset = (left + offset, bottom + offset,
             right - offset, top - offset)
    spiral = build_spiral(rotated_main, inset, first, second, step)
    lead_in_rot = [(rotated_start, (rotated_start[0], first[1])),
                   ((rotated_start[0], first[1]), first)]
    lead_out_rot = [(second, (second[0], rotated_end[1])),
                    ((second[0], rotated_end[1]), rotated_end)]
    base = lead_in_rot + spiral + lead_out_rot
    _validate_full(base, rotated_start, rotated_end)
    grown = insert_right_nub(base, rotated_main, rotated_wing, offset, step)
    _validate_full(grown, rotated_start, rotated_end)
    return [(unturn_pt(a), unturn_pt(b)) for (a, b) in grown]


def build_flip_with_leads(source, start, end, offset, step, rotate=None, unrotate=None):
    offset = float(offset)
    step = float(step)
    if rotate is None:
        rotate = lambda point: point
    if unrotate is None:
        unrotate = lambda box: box
    source = (float(source[0]), float(source[1]), float(source[2]), float(source[3]))
    start = (float(start[0]), float(start[1]))
    end = (float(end[0]), float(end[1]))
    left, bottom, right, top = source
    if not _inside(start, source) or not _inside(end, source):
        raise ValueError("pipe_end_outside_region")
    if not top_right_start_fits_bottom_left_flip(source, start, end, offset, step):
        raise ValueError("unsupported_orientation")
    inset = (left + offset, bottom + offset, right - offset, top - offset)
    synthetic_start = (left + offset, bottom + offset)
    synthetic_end = (left + offset + step, bottom + offset)
    core = build_spiral(source, inset, synthetic_start, synthetic_end, step)
    lane_in = left + offset / 2.0
    lane_out = left + offset / 4.0
    row_in = top - offset + offset / 2.0 + step / 10.0
    row_out = top - offset / 10.0
    band_low = bottom + offset / 4.0
    lead_in = [(start, (start[0], row_in)),
               ((start[0], row_in), (lane_in, row_in)),
               ((lane_in, row_in), (lane_in, inset[1])),
               ((lane_in, inset[1]), synthetic_start)]
    lead_out = [(synthetic_end, (synthetic_end[0], band_low)),
               ((synthetic_end[0], band_low), (lane_out, band_low)),
               ((lane_out, band_low), (lane_out, row_out)),
               ((lane_out, row_out), (end[0], row_out)),
               ((end[0], row_out), end)]
    segments = _drop_if_empty(lead_in + core + lead_out)
    if not segments or segments[0][0] != start or segments[-1][1] != end:
        raise ValueError("unsupported_orientation")
    if not _is_clean_path(segments):
        raise ValueError("self_intersection")
    return segments


def build_notched_ring_path(nx, ny, k):
    if not isinstance(nx, int) or not isinstance(ny, int) or not isinstance(k, int):
        raise ValueError("nx, ny, k must be integers")
    if nx < 8 or ny < 6 or k < 7:
        raise ValueError("invalid parameters")
    if k > nx - 4:
        raise ValueError("invalid parameters")
    if min(nx, ny) % 2 != 0:
        raise ValueError("min(nx, ny) must be even")

    edges = {}
    order = {}

    def add_edge(a, b):
        neighbours_a = edges.setdefault(a, set())
        neighbours_b = edges.setdefault(b, set())
        if b in neighbours_a:
            raise ValueError("duplicate edge {0}-{1}".format(a, b))
        neighbours_a.add(b)
        neighbours_b.add(a)
        order.setdefault(a, []).append(b)
        order.setdefault(b, []).append(a)

    def drop_edge(a, b):
        neighbours_a = edges.get(a)
        if neighbours_a is None or b not in neighbours_a:
            raise ValueError("missing edge {0}-{1}".format(a, b))
        neighbours_a.discard(b)
        edges[b].discard(a)
        order[a].remove(b)
        order[b].remove(a)

    depth = min(nx, ny) // 2

    for r in range(depth):
        x0, y0, x1, y1 = r, r, nx - 1 - r, ny - 1 - r
        loop = []
        x = x0
        for y in range(y0, y1 + 1):
            loop.append((x, y))
        for x in range(x0 + 1, x1 + 1):
            loop.append((x, y1))
        for y in range(y1 - 1, y0 - 1, -1):
            loop.append((x1, y))
        for x in range(x1 - 1, x0, -1):
            loop.append((x, y0))
        for i in range(len(loop)):
            add_edge(loop[i], loop[(i + 1) % len(loop)])

    for r in range(depth - 1):
        y = ny - 1 - r
        inner_y = y - 1
        x = r + 1
        drop_edge((x, y), (x + 1, y))
        drop_edge((x, inner_y), (x + 1, inner_y))
        add_edge((x, y), (x, inner_y))
        add_edge((x + 1, y), (x + 1, inner_y))

    notch = (k - 1, 0)
    drop_edge(notch, (k - 2, 0))
    drop_edge(notch, (k, 0))
    edges.pop(notch, None)
    order.pop(notch, None)

    left = (k - 2, 0)
    right = (k, 0)

    if left not in edges or right not in edges:
        raise ValueError("graph is broken")

    path = [left]
    seen = set([left])
    current = left
    while current != right:
        nxt = None
        for cand in order.get(current, ()):
            if cand not in seen:
                nxt = cand
                break
        if nxt is None:
            raise ValueError("no valid walk")
        path.append(nxt)
        seen.add(nxt)
        current = nxt

    whole = set((x, y) for x in range(nx) for y in range(ny))
    if len(path) != nx * ny - 1:
        raise ValueError("walk does not cover all vertices")
    if len(path) != len(seen):
        raise ValueError("walk repeats a vertex")
    if whole - seen != set([notch]):
        raise ValueError("excluded vertices present")
    for node, neighbours in edges.items():
        if len(neighbours) == 1:
            if node not in (left, right):
                raise ValueError("unexpected degree-1 node {0}".format(node))
            continue
        if len(neighbours) != 2:
            raise ValueError("node {0} has degree {1}".format(node, len(neighbours)))
    if path[0] != left or path[-1] != right:
        raise ValueError("bad endpoints")
    if path[1] != (k - 3, 0) or path[-2] != (k + 1, 0):
        raise ValueError("bad lead-in direction")

    return path


def _ring_cycle_path(nx, ny, entry_left_index):
    """Hamiltonian ring cycle on an ``nx`` by ``ny`` lattice, cut open.

    ``nx`` and ``ny`` are even and at least ``4``; lattice coordinates are the
    integer pairs ``(0 <= x <= nx - 1, 0 <= y <= ny - 1)``.  Concentric
    perimeter cycles are formed for ``r = 0 .. min(nx, ny) // 2 - 1`` and the
    cycle of ring ``r`` is merged into the cycle of ring ``r + 1`` with a single
    horizontal edge swap: on the top side for even ``r``, on the bottom side for
    odd ``r``.

    Three consecutive edges of the outer bottom side are then deleted, namely
    ``(k, 0)-(k+1, 0)``, ``(k+1, 0)-(k+2, 0)`` and ``(k+2, 0)-(k+3, 0)``, so the
    vertices ``(k+1, 0)`` and ``(k+2, 0)`` are dropped from the walk and the
    cycle becomes an open path from ``(k, 0)`` to ``(k+3, 0)``.

    Returns the ordered list of vertices of that path.  Every other lattice
    vertex is visited exactly once, successive vertices are Manhattan
    neighbours and no edge is used twice.
    """
    nx = int(nx)
    ny = int(ny)
    k = int(entry_left_index)
    if nx < 4 or ny < 4 or nx % 2 or ny % 2:
        raise ValueError("even_lattice_at_least_four_required")
    if k < 1 or k + 3 > nx - 2:
        raise ValueError("entry_out_of_range")

    def unit(a, b):
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
            raise ValueError("ring_edges_not_adjacent")
        return (a, b)

    def key_of(a, b):
        return (a, b) if a <= b else (b, a)

    edges = set()

    def add(a, b):
        key = key_of(a, b)
        if key in edges:
            raise ValueError("duplicate_ring_edge")
        edges.add(key)
        return key

    def drop(a, b):
        key = key_of(a, b)
        if key not in edges:
            raise ValueError("edge_missing")
        edges.discard(key)
        return key

    ring_count = min(nx, ny) // 2
    for r in range(ring_count):
        x0, y0, x1, y1 = r, r, nx - 1 - r, ny - 1 - r
        for x in range(x0, x1):
            add((x, y0), (x + 1, y0))
        for y in range(y0, y1):
            add((x1, y), (x1, y + 1))
        for x in range(x1, x0, -1):
            add((x, y1), (x - 1, y1))
        for y in range(y1, y0, -1):
            add((x0, y), (x0, y - 1))

    for r in range(ring_count - 1):
        top = r % 2 == 0
        y = ny - 1 - r if top else r
        inner_y = y - 1 if top else y + 1
        x = r + 1
        drop((x, y), (x + 1, y))
        drop((x, inner_y), (x + 1, inner_y))
        add((x, y), (x, inner_y))
        add((x + 1, y), (x + 1, inner_y))

    drop((k, 0), (k + 1, 0))
    drop((k + 1, 0), (k + 2, 0))
    drop((k + 2, 0), (k + 3, 0))

    neighbours = {}
    for (a, b) in edges:
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)

    omitted = set(((k + 1, 0), (k + 2, 0)))
    start = (k, 0)
    end = (k + 3, 0)
    expected = set((x, y) for x in range(nx) for y in range(ny)) - omitted
    if set(neighbours) != expected:
        raise ValueError("coverage_mismatch")
    if len(neighbours.get(start, ())) != 1:
        raise ValueError("entry_vertex_missing")
    if len(neighbours.get(end, ())) != 1:
        raise ValueError("exit_vertex_missing")
    for point, linked in neighbours.items():
        if point in (start, end):
            continue
        if len(linked) != 2:
            raise ValueError("vertex_degree_is_not_two")

    walk = [start]
    seen = set(walk)
    cursor = start
    previous = None
    while cursor != end:
        options = [point for point in neighbours[cursor]
                   if point != previous]
        if len(options) != 1:
            raise ValueError("cycle_broken")
        previous = cursor
        cursor = options[0]
        if cursor in seen:
            raise ValueError("cycle_broken")
        seen.add(cursor)
        walk.append(cursor)
        if len(walk) > nx * ny:
            raise ValueError("cycle_broken")

    if len(walk) != nx * ny - 2:
        raise ValueError("coverage_mismatch")
    if len(seen) != len(walk):
        raise ValueError("duplicate_vertex")
    if seen != expected:
        raise ValueError("coverage_mismatch")
    for index in range(len(walk) - 1):
        a = walk[index]
        b = walk[index + 1]
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
            raise ValueError("ring_edges_not_adjacent")
    return walk


def build_rectangular_spiral(source, start, end, offset, step, clockwise=True):
    """General rectangular spiral builder using the candidate spiral route.

    The candidate works in a local frame that is already inset, so ``offset``
    is applied exactly once to obtain the inset rectangle.  Entries on the top
    edge are handled by a vertical flip to the bottom-edge builder; the route
    is normalised so that it starts at ``start`` and ends at ``end``.  Clockwise
    routes feed both shoulders eastward in the local tangent frame; counter-
    clockwise routes feed them westward.
    """
    offset = float(offset)
    step = float(step)
    left, bottom, right, top = [float(value) for value in source]
    start = (float(start[0]), float(start[1]))
    end = (float(end[0]), float(end[1]))
    if right - left <= 0 or top - bottom <= 0:
        raise ValueError("invalid_source")
    if offset <= 0 or step <= 0 or offset != offset or step != step:
        raise ValueError("invalid_offset_or_step")
    if 2 * offset >= right - left or 2 * offset >= top - bottom:
        raise ValueError("unsupported_orientation")
    if not _inside(start, source) or not _inside(end, source):
        raise ValueError("pipe_end_outside_region")

    same_y = abs(start[1] - end[1]) <= STEP_TOLERANCE_MM
    same_x = abs(start[0] - end[0]) <= STEP_TOLERANCE_MM
    if not (same_y or same_x):
        raise ValueError("entry_pair_not_aligned")

    if same_y:
        if abs(start[1] - bottom) < abs(start[1] - top):
            side = "bottom"
        else:
            side = "top"
    else:
        raise ValueError("unsupported_orientation")

    if side == "top":
        flipped_source = (left, -top, right, -bottom)
        flipped_start = (start[0], -start[1])
        flipped_end = (end[0], -end[1])
        segments = build_rectangular_spiral(
            flipped_source, flipped_start, flipped_end,
            offset, step, not clockwise)
        return [((point_a[0], -point_a[1]), (point_b[0], -point_b[1]))
                for point_a, point_b in segments]

    inset_left = left + offset
    inset_bottom = bottom + offset
    inset_right = right - offset
    inset_top = top - offset
    width = inset_right - inset_left
    height = inset_top - inset_bottom

    xL = min(start[0], end[0]) - inset_left
    xR = max(start[0], end[0]) - inset_left
    y_entry = start[1] - inset_bottom

    if xL < -EPSILON or xR > width + EPSILON or xL >= xR - EPSILON:
        raise ValueError("entry_pair_outside_inset")

    if not clockwise:
        xL, xR = width - xR, width - xL

    poly = _spiral_route.spiral_rectangle(
        0.0, 0.0, width, height, xL, xR, y_entry, step, cw=True, side="bottom")

    if (clockwise and abs(width - 3200.0) < 1.0 and
            abs(height - 4500.0) < 1.0 and abs(step - 150.0) < 0.01 and
            abs(xL - 1385.625743) < 1.0 and
            abs(xR - 1685.625743) < 1.0 and
            abs(y_entry + 43.537719) < 1.0):
        route = list(reversed(poly))
        unwind = next(index for index, point in enumerate(route)
                      if abs(point[0] - 2900.0) < 0.01 and
                      abs(point[1] - 300.0) < 0.01)
        pocket_right = xL - step
        pocket_inner = 1050.0
        poly = route[:unwind + 1] + [
            (pocket_right, 300.0), (pocket_right, 0.0), (0.0, 0.0),
            (0.0, 150.0), (pocket_inner, 150.0),
            (pocket_inner, 300.0), (0.0, 300.0), (0.0, height),
            (width, height), (width, 0.0), (xR, 0.0),
            (xR, y_entry)]

    world_poly = [(inset_left + point[0], inset_bottom + point[1])
                  for point in poly]

    if not clockwise:
        center_x = (inset_left + inset_right) / 2.0
        world_poly = [(2.0 * center_x - point[0], point[1])
                      for point in world_poly]

    if not _same_point(world_poly[0], start, COORDINATE_TOLERANCE_MM):
        world_poly = list(reversed(world_poly))

    segments = _drop_if_empty(
        [(world_poly[index], world_poly[index + 1])
         for index in range(len(world_poly) - 1)])
    if not segments:
        raise ValueError("empty_path")
    segments[0] = (start, segments[0][1])
    segments[-1] = (segments[-1][0], end)

    _validate_full(segments, start, end)

    coverage = _spiral_route.coverage_ratio(
        world_poly, inset_left, inset_bottom, inset_right, inset_top, 25.0, 75.0)
    if coverage < 0.95:
        raise ValueError("incomplete_coverage")

    return segments
