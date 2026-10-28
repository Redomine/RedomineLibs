# -*- coding: utf-8 -*-
from __future__ import division

from floor_heating_geometry import TOLERANCE, _advance

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


