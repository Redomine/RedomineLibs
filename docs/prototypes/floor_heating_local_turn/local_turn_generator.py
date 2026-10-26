# -*- coding: utf-8 -*-
"""Diagnostic local-turn routing, independent of the ring-based prototypes.

This is deliberately not a validated floor-heating layout. Each body leg is
extended toward the first real boundary or previously laid segment, then the
next turn is chosen from the geometry at that point. No rectangular ring or
offset-contour family supplies target vertices.
"""
from __future__ import division

import math


TOLERANCE = 0.001
DIRECTIONS = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))


def _distance(first, second):
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _segment_distance(point, segment):
    first, second = segment
    delta_x = second[0] - first[0]
    delta_y = second[1] - first[1]
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared <= TOLERANCE * TOLERANCE:
        return _distance(point, first)
    factor = ((point[0] - first[0]) * delta_x +
              (point[1] - first[1]) * delta_y) / length_squared
    factor = max(0.0, min(1.0, factor))
    projection = (first[0] + factor * delta_x, first[1] + factor * delta_y)
    return _distance(point, projection)


def _on_segment(point, first, second):
    if abs(first[0] - second[0]) <= TOLERANCE:
        return (abs(point[0] - first[0]) <= TOLERANCE and
                min(first[1], second[1]) - TOLERANCE <= point[1] <=
                max(first[1], second[1]) + TOLERANCE)
    return (abs(point[1] - first[1]) <= TOLERANCE and
            min(first[0], second[0]) - TOLERANCE <= point[0] <=
            max(first[0], second[0]) + TOLERANCE)


def _inside(point, polygon):
    contained = False
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if _on_segment(point, first, second):
            return True
        if (first[1] > point[1]) != (second[1] > point[1]):
            crossing = (first[0] + (point[1] - first[1]) *
                        (second[0] - first[0]) / (second[1] - first[1]))
            if crossing > point[0]:
                contained = not contained
    return contained


def _offset_polygon(polygon, offset):
    area = 0.0
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        area += first[0] * second[1] - second[0] * first[1]
    orientation = 1.0 if area > 0.0 else -1.0
    shifted = []
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        delta_x = second[0] - first[0]
        delta_y = second[1] - first[1]
        length = math.hypot(delta_x, delta_y)
        if length <= TOLERANCE:
            raise ValueError("zero_boundary_edge")
        shift_x = -orientation * delta_y / length * offset
        shift_y = orientation * delta_x / length * offset
        shifted.append(((first[0] + shift_x, first[1] + shift_y),
                        (second[0] + shift_x, second[1] + shift_y)))
    result = []
    for index, current in enumerate(shifted):
        previous = shifted[index - 1]
        if abs(previous[0][0] - previous[1][0]) <= TOLERANCE:
            if abs(current[0][1] - current[1][1]) <= TOLERANCE:
                result.append((previous[0][0], current[0][1]))
            else:
                result.append(current[0])
        elif abs(current[0][0] - current[1][0]) <= TOLERANCE:
            result.append((current[0][0], previous[0][1]))
        else:
            result.append(current[0])
    return result


def _ray_boundary_distance(origin, direction, contour):
    distances = []
    for index, first in enumerate(contour):
        second = contour[(index + 1) % len(contour)]
        if abs(direction[0]) > TOLERANCE:
            if abs(first[0] - second[0]) > TOLERANCE:
                continue
            if not min(first[1], second[1]) - TOLERANCE <= origin[1] <= max(first[1], second[1]) + TOLERANCE:
                continue
            distance = (first[0] - origin[0]) / direction[0]
        else:
            if abs(first[1] - second[1]) > TOLERANCE:
                continue
            if not min(first[0], second[0]) - TOLERANCE <= origin[0] <= max(first[0], second[0]) + TOLERANCE:
                continue
            distance = (first[1] - origin[1]) / direction[1]
        if distance > TOLERANCE:
            distances.append(distance)
    return min(distances) if distances else 0.0


def _advance(point, direction, length):
    return (point[0] + direction[0] * length,
            point[1] + direction[1] * length)


def _entry_direction(polygon, start):
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    sides = ((abs(start[1] - min(ys)), (0.0, 1.0)),
             (abs(start[1] - max(ys)), (0.0, -1.0)),
             (abs(start[0] - min(xs)), (1.0, 0.0)),
             (abs(start[0] - max(xs)), (-1.0, 0.0)))
    return min(sides, key=lambda entry: entry[0])[1]


def _append(vertices, point):
    if _distance(vertices[-1], point) > TOLERANCE:
        vertices.append(point)


def _correct_entry(entry, normal, contour, spacing):
    if _inside(entry, contour):
        return entry
    tangent = (-normal[1], normal[0])
    candidates = []
    step = max(10.0, min(25.0, spacing / 6.0))
    for multiplier in range(1, 401):
        for sign in (-1.0, 1.0):
            point = _advance(entry, tangent, sign * multiplier * step)
            if not _inside(point, contour):
                continue
            left = _ray_boundary_distance(point, tangent, contour)
            right = _ray_boundary_distance(point, (-tangent[0], -tangent[1]),
                                           contour)
            if min(left, right) >= spacing / 2.0:
                candidates.append(point)
        if candidates:
            break
    return min(candidates, key=lambda point: _distance(point, entry)) if candidates else entry


def _available_length(origin, direction, contour, segments, spacing, sample):
    limit = _ray_boundary_distance(origin, direction, contour)
    if limit <= TOLERANCE:
        return 0.0, "boundary"
    distance = min(sample, limit)
    last_valid = 0.0
    while distance <= limit + TOLERANCE:
        point = _advance(origin, direction, min(distance, limit))
        if not _inside(point, contour):
            return last_valid, "boundary"
        if any(_segment_distance(point, segment) < spacing - TOLERANCE
               for segment in segments[:-2]):
            return last_valid, "laid"
        last_valid = min(distance, limit)
        if last_valid >= limit - TOLERANCE:
            return last_valid, "boundary"
        distance = min(distance + sample, limit)
    return last_valid, "boundary"


def build_route(polygon, start, end, offset_mm=200.0, spacing_mm=150.0,
                clockwise=True, max_legs=120):
    """Build a diagnostic route by deciding each turn at the current point.

    The return to the outlet is an explicitly unvalidated Manhattan connection.
    This makes failed layouts visible without presenting them as valid routes.
    """
    polygon = [tuple(map(float, point)) for point in polygon]
    start = tuple(map(float, start))
    end = tuple(map(float, end))
    contour = _offset_polygon(polygon, float(offset_mm))
    normal = _entry_direction(polygon, start)
    if abs(normal[0]) <= TOLERANCE:
        edge = min(point[1] for point in contour) if normal[1] > 0 else max(point[1] for point in contour)
        entry = (start[0], edge + normal[1] * spacing_mm)
    else:
        edge = min(point[0] for point in contour) if normal[0] > 0 else max(point[0] for point in contour)
        entry = (edge + normal[0] * spacing_mm, start[1])
    vertices = [start]
    _append(vertices, entry)
    corrected_entry = _correct_entry(entry, normal, contour, spacing_mm)
    _append(vertices, corrected_entry)
    entry = corrected_entry
    journal = [{"decision": "paired_entry", "at": list(entry)}]
    segments = [(vertices[index - 1], vertices[index])
                for index in range(1, len(vertices))]
    options = ((-normal[1], normal[0]), (normal[1], -normal[0]))
    lengths = [_available_length(entry, direction, contour, segments,
                                 spacing_mm, spacing_mm / 5.0)[0]
               for direction in options]
    if abs(lengths[0] - lengths[1]) > TOLERANCE:
        direction = options[0] if lengths[0] > lengths[1] else options[1]
    else:
        direction = options[0] if clockwise else options[1]
    sample = max(10.0, min(30.0, spacing_mm / 5.0))
    minimum_leg = max(30.0, spacing_mm / 2.0)
    visited = set()
    for index in range(max_legs):
        current = vertices[-1]
        length, reason = _available_length(current, direction, contour,
                                           segments, spacing_mm, sample)
        if length < minimum_leg:
            candidates = ((-direction[1], direction[0]),
                          (direction[1], -direction[0]))
            if not clockwise:
                candidates = tuple(reversed(candidates))
            choices = [(_available_length(current, candidate, contour,
                                           segments, spacing_mm, sample)[0],
                        candidate) for candidate in candidates]
            choices.sort(reverse=True)
            if not choices or choices[0][0] < minimum_leg:
                journal.append({"decision": "stop", "at": list(current),
                                "reason": reason})
                break
            direction = choices[0][1]
            length = choices[0][0]
            reason = "turn"
        target = _advance(current, direction, length)
        key = (round(target[0], 1), round(target[1], 1), direction)
        if key in visited:
            journal.append({"decision": "stop", "at": list(current),
                            "reason": "cycle"})
            break
        visited.add(key)
        _append(vertices, target)
        segments.append((current, target))
        journal.append({"decision": "straight", "from": list(current),
                        "to": list(target), "stop_reason": reason})
        left = (-direction[1], direction[0])
        right = (direction[1], -direction[0])
        preferred = left if clockwise else right
        alternate = right if clockwise else left
        possibilities = []
        for candidate in (preferred, alternate):
            available, blocker = _available_length(target, candidate, contour,
                                                    segments, spacing_mm, sample)
            possibilities.append((available, candidate, blocker))
        chosen = next((entry for entry in possibilities
                       if entry[1] == preferred and entry[0] >= minimum_leg), None)
        if chosen is None:
            chosen = next((entry for entry in possibilities
                           if entry[0] >= minimum_leg), None)
        if chosen is None:
            journal.append({"decision": "stop", "at": list(target),
                            "reason": "no_pair_turn"})
            break
        direction = chosen[1]
        journal.append({"decision": "turn", "at": list(target),
                        "toward": list(direction), "next_limit": chosen[0],
                        "limit_reason": chosen[2]})
    current = vertices[-1]
    via = (end[0], current[1])
    if abs(current[0] - end[0]) > TOLERANCE and abs(current[1] - end[1]) > TOLERANCE:
        _append(vertices, via)
    _append(vertices, end)
    journal.append({"decision": "diagnostic_unvalidated_return",
                    "at": list(end)})
    return {"vertices": vertices, "journal": journal,
            "contour": contour, "validated": False}
