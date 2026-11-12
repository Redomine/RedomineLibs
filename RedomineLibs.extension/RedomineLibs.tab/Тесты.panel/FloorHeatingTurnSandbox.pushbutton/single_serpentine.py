# -*- coding: utf-8 -*-
"""Geometric decisions for the second pipe of a single serpentine."""
from __future__ import division

from build_progress import checkpoint

from floor_heating_geometry import (TOLERANCE, _correct_entry, _distance, _inside,
                                    _offset_polygon, _ray_boundary_distance)
from floor_heating_pair import _entry_at_depth
from vector_walk import _valid_join


def _path_inside(vertices, contour, min_segment_mm):
    for start, end in zip(vertices, vertices[1:]):
        length = _distance(start, end)
        if length < min_segment_mm - TOLERANCE or not _inside(start, contour):
            return False
        direction = ((end[0] - start[0]) / length,
                     (end[1] - start[1]) / length)
        if length > _ray_boundary_distance(start, direction, contour) + TOLERANCE:
            return False
    return _inside(vertices[-1], contour)


def _first_pipe(polygon, contour, start, normal, sweep, spacing,
                offset, second_x, max_turns, min_segment):
    raw = _entry_at_depth(polygon, start, normal, offset)
    entry = _correct_entry(raw, normal, contour, spacing)
    if not _inside(entry, contour):
        raise ValueError(u"Первая труба не может выйти на отступ")
    vertices = [start]
    lateral = (entry[0], start[1])
    if _distance(vertices[-1], lateral) > TOLERANCE:
        vertices.append(lateral)
    if _distance(vertices[-1], entry) > TOLERANCE:
        vertices.append(entry)
    direction = sweep
    for unused in range(max_turns + 2):
        checkpoint()
        point = vertices[-1]
        travel = _ray_boundary_distance(point, direction, contour)
        if direction[0] != sweep[0]:
            corridor_limit = ((point[0] - second_x) * sweep[0] - spacing)
            travel = min(travel, corridor_limit)
        if travel <= min_segment + TOLERANCE:
            break
        vertices.append((point[0] + direction[0] * travel, point[1]))
        point = vertices[-1]
        rise = _ray_boundary_distance(point, normal, contour)
        if rise < spacing - TOLERANCE:
            break
        vertices.append((point[0], point[1] + normal[1] * spacing))
        direction = (-direction[0], 0.0)
    return vertices, entry


def build_single_serpentine_route(polygon, right_start, left_start,
                                  entry_direction, max_turns,
                                  left_to_right=True, offset_mm=200.0,
                                  spacing_mm=150.0, min_segment_mm=1.0):
    """Find a contour approach for pipe two and join it to a snake prefix."""
    first_side = "right" if left_to_right else "left"
    second_side = "left" if left_to_right else "right"
    first_start = right_start if left_to_right else left_start
    second_start = left_start if left_to_right else right_start
    contour = _offset_polygon(polygon, offset_mm)
    raw_entry = _entry_at_depth(polygon, second_start,
                                entry_direction, offset_mm + spacing_mm)
    second_entry = _correct_entry(raw_entry, entry_direction, contour,
                                  spacing_mm)
    if not _inside(second_entry, contour):
        raise ValueError(u"Вторая труба не может выйти на свой отступ")
    sweep = 1.0 if left_to_right else -1.0
    outward = (-sweep, 0.0)
    outward_room = _ray_boundary_distance(second_entry, outward, contour)
    if outward_room < spacing_mm - TOLERANCE:
        second_entry = (second_entry[0] - sweep * outward_room,
                        second_entry[1])
    edge_x = second_entry[0] - sweep * _ray_boundary_distance(
        second_entry, outward, contour)
    entry_path = [second_start]
    lateral = (second_entry[0], second_start[1])
    if _distance(entry_path[-1], lateral) > TOLERANCE:
        entry_path.append(lateral)
    if _distance(entry_path[-1], second_entry) > TOLERANCE:
        entry_path.append(second_entry)
    first_vertices, first_entry = _first_pipe(
        polygon, contour, first_start, entry_direction, (sweep, 0.0),
        spacing_mm, offset_mm, second_entry[0], max_turns, min_segment_mm)
    # Candidate joins are tried from the longest complete first-pipe prefix.
    for length in range(len(first_vertices), 1, -1):
        checkpoint()
        first = first_vertices[:length]
        target = first[-1]
        if (target[1] - second_entry[1]) * entry_direction[1] <= min_segment_mm:
            continue
        bottom_y = second_entry[1]
        candidates = []
        if (edge_x - second_entry[0]) * sweep < -max(
                min_segment_mm, spacing_mm) - TOLERANCE:
            candidates.append(entry_path + [(edge_x, bottom_y),
                               (edge_x, target[1]), target])
        candidates.append(entry_path + [(second_entry[0], target[1]), target])
        for path in candidates:
            second = [path[0]]
            for point in path[1:]:
                if _distance(second[-1], point) > TOLERANCE:
                    second.append(point)
            if len(second) < 2 or second[-1] != target:
                continue
            inside_part = second[second.index(second_entry):]
            if not _path_inside(inside_part, contour, min_segment_mm):
                continue
            right = first if first_side == "right" else second
            left = second if first_side == "right" else first
            valid, reason = _valid_join(right, left, None, contour,
                                        spacing_mm, min_segment_mm)
            if not valid:
                continue
            turns = {}
            branches = {}
            for side, vertices in ((first_side, first),
                                   (second_side, second)):
                count = sum(1 for a, b, c in zip(vertices, vertices[1:],
                                                vertices[2:])
                            if abs((b[0] - a[0]) * (c[1] - b[1]) -
                                   (b[1] - a[1]) * (c[0] - b[0])) > TOLERANCE)
                turns[side] = count
                branches[side] = {"vertices": vertices,
                                  "turns_used": count,
                                  "stop_reason": "connected"}
            if max(turns.values()) > max_turns:
                continue
            result = {"branches": branches, "contour": contour,
                      "turns_by_branch": turns,
                      "turns_used": sum(turns.values()),
                      "stop_reason": {"right": "connected", "left": "connected"},
                      "join": {"connected": True,
                               "method": "contour_approach", "bridge": None},
                      "rollback": None, "abandoned_segments": [],
                      "validated": False, "journal": [{
                          "step": 1, "action": "single_serpentine_input",
                          "polygon": [list(p) for p in polygon],
                          "right_start": list(right_start),
                          "left_start": list(left_start),
                          "entry_direction": list(entry_direction),
                          "left_to_right": bool(left_to_right),
                          "offset_mm": offset_mm,
                          "spacing_mm": spacing_mm}]}
            result["journal"].append({"step": 2,
                                      "action": "single_serpentine_join",
                                      "first_branch": first_side,
                                      "second_branch": second_side,
                                      "first_entry": list(first_entry),
                                      "second_entry": list(second_entry),
                                      "second_approach": ("along_bottom" if
                                                          len(path) == len(entry_path) + 3
                                                          else "upward"),
                                      "second_path": [list(p) for p in second]})
            return result
    raise ValueError(u"Змейку построить нельзя: нет свободного хода второй трубы")
