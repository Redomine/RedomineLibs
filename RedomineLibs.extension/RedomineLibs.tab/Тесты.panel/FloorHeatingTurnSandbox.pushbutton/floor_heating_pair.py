# -*- coding: utf-8 -*-
from __future__ import division

from itertools import islice

from floor_heating_geometry import TOLERANCE, _advance, _inside, _on_segment

def _entry_at_depth(polygon, start, normal, depth):
    backward = (-normal[0], -normal[1])
    candidates = []
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if abs(normal[0]) > TOLERANCE:
            if abs(first[0] - second[0]) > TOLERANCE:
                continue
            if not min(first[1], second[1]) - TOLERANCE <= start[1] <= max(first[1], second[1]) + TOLERANCE:
                continue
            distance = (first[0] - start[0]) * backward[0]
        else:
            if abs(first[1] - second[1]) > TOLERANCE:
                continue
            if not min(first[0], second[0]) - TOLERANCE <= start[0] <= max(first[0], second[0]) + TOLERANCE:
                continue
            distance = (first[1] - start[1]) * backward[1]
        if distance >= -TOLERANCE:
            candidates.append(max(0.0, distance))
    if not candidates:
        raise ValueError("entry_boundary_not_found_behind_open_end")
    boundary = _advance(start, backward, min(candidates))
    return _advance(boundary, normal, depth)


def _axis(vector):
    dx, dy = vector
    length = (dx * dx + dy * dy) ** 0.5
    if length <= TOLERANCE:
        raise ValueError("pipe_direction_has_zero_view_projection")
    if abs(dx) >= abs(dy):
        if abs(dy) > length * 0.01:
            raise ValueError("pipe_is_not_orthogonal_on_view")
        return (1.0 if dx > 0 else -1.0, 0.0)
    if abs(dx) > length * 0.01:
        raise ValueError("pipe_is_not_orthogonal_on_view")
    return (0.0, 1.0 if dy > 0 else -1.0)


def classify_open_ends(ends):
    """Return (right, left, inward_direction, lateral_spacing)."""
    if len(ends) != 2:
        raise ValueError("two_open_ends_required")
    first_direction = _axis(ends[0]["direction_vector"])
    second_direction = _axis(ends[1]["direction_vector"])
    if first_direction != second_direction:
        raise ValueError("open_ends_point_in_different_directions")
    right = (first_direction[1], -first_direction[0])
    ranked = sorted(ends, key=lambda item:
                    item["point"][0] * right[0] + item["point"][1] * right[1],
                    reverse=True)
    separation = ((ranked[0]["point"][0] - ranked[1]["point"][0]) * right[0] +
                  (ranked[0]["point"][1] - ranked[1]["point"][1]) * right[1])
    if separation <= TOLERANCE:
        raise ValueError("open_ends_have_no_left_right_separation")
    return ranked[0], ranked[1], first_direction, separation


def _entry_faces(polygon):
    # Join collinear pieces of the same wall before assigning the two inlets.
    horizontal = [abs(p[1] - polygon[i - 1][1]) <= TOLERANCE
                  for i, p in enumerate(polygon)]
    corners = [p for i, p in enumerate(polygon)
               if horizontal[i] != horizontal[(i + 1) % len(polygon)]]
    if not corners:
        return []
    origin = corners[0]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    area = sum((a[0] - origin[0]) * (b[1] - origin[1]) -
               (a[1] - origin[1]) * (b[0] - origin[0]) for a, b in edges)
    faces = []
    for a, b in edges:
        axis = 1 if abs(b[1] - a[1]) <= TOLERANCE else 0
        inward = b[0] - a[0] if axis == 1 else a[1] - b[1]
        lo, hi = sorted((a[1-axis], b[1-axis]))
        faces.append((axis, 1 if inward * area > 0 else -1, a[axis], lo, hi))
    return faces


def _pipe_entry(end, faces, polygon):
    point, vector = end["point"], end["direction_vector"]
    try:
        direction = _axis(vector)
    except ValueError:
        return None, False
    face = None
    for i, (axis, inward, position, lo, hi) in enumerate(faces):
        if not direction[axis]:
            continue
        depth = (point[axis] - position) * direction[axis]
        if depth < -TOLERANCE or depth > abs(vector[axis]) + TOLERANCE:
            continue
        cross = point[1-axis] - vector[1-axis] * depth / abs(vector[axis])
        if lo - TOLERANCE <= cross <= hi + TOLERANCE:
            if face is not None or direction[axis] != inward:
                return None, False  # transit through another arm, or an end on an exit face
            face = i
    if face is not None:
        return face, False
    start = (point[0] - vector[0], point[1] - vector[1])
    internal = _inside(start, polygon) and all(
        not _on_segment(p, a, polygon[i-1]) for p in (start, point)
        for i, a in enumerate(polygon))
    return None, internal


def spiral_inlets(ends, polygon, selected_ids=()):
    """Find the two local inlets before passing their ends to the solver."""
    selected = [end for end in ends if end["pipe_id"] in selected_ids]
    if len(set(end["pipe_id"] for end in selected)) >= 2:
        ends = selected
        if len(ends) == 2:
            classify_open_ends(ends)
            return ends
    faces = _entry_faces(polygon)
    by_face = {}
    internal = []
    for end in ends:
        i, wholly_inside = _pipe_entry(end, faces, polygon)
        if i is not None:
            by_face.setdefault(i, []).append(end)
        elif wholly_inside:
            internal.append(end)

    def pairs(group, axis, aligned):
        ordered = sorted(group, key=lambda end: end["point"][axis])
        for i, first in enumerate(ordered):
            for j in range(i + 1, len(ordered)):
                second = ordered[j]
                if aligned and second["point"][axis] - first["point"][axis] > TOLERANCE:
                    break
                if (first["pipe_id"] != second["pipe_id"] and
                        abs(first["point"][1-axis] - second["point"][1-axis]) > TOLERANCE):
                    yield [first, second]

    ambiguous = u"Несколько возможных подводов. Выделите область и две нужные трубы перед запуском."
    # Evaluate all entry walls at the same priority. A staggered pair on
    # another wall must not mask the only pair ending at the same depth.
    for aligned in (True, False):
        candidates = list(islice((pair for i, group in by_face.items()
                                  for pair in pairs(group, faces[i][0], aligned)), 2))
        if len(candidates) > 1:
            raise ValueError(ambiguous)
        if candidates:
            return candidates[0]
    # Keep existing layouts with staggered entry faces or local feed stubs.
    # Through pipes and non-planar ends have already been discarded.
    local = [end for group in by_face.values() for end in group] + internal
    for pair in (local, internal):
        if len(pair) == 2 and pair[0]["pipe_id"] != pair[1]["pipe_id"]:
            classify_open_ends(pair)
            return pair
    raise ValueError(u"Не найдена однозначная пара свободных подводов. "
                     u"Для явного выбора выделите область и две трубы перед запуском.")


def _append_branch_segment(branch, target):
    current = branch["vertices"][-1]
    if branch["segments"]:
        previous = branch["segments"][-1]
        old_vector = (previous[1][0] - previous[0][0],
                      previous[1][1] - previous[0][1])
        new_vector = (target[0] - current[0], target[1] - current[1])
        cross = old_vector[0] * new_vector[1] - old_vector[1] * new_vector[0]
        dot = old_vector[0] * new_vector[0] + old_vector[1] * new_vector[1]
        if abs(cross) <= TOLERANCE and dot > TOLERANCE:
            branch["vertices"][-1] = target
            branch["segments"][-1] = (previous[0], target)
            return True
    branch["vertices"].append(target)
    branch["segments"].append((current, target))
    return False


