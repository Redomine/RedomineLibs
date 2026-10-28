# -*- coding: utf-8 -*-
from __future__ import division

import math

TOLERANCE = 0.001

def _distance(first, second):
    return math.hypot(first[0] - second[0], first[1] - second[1])


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


