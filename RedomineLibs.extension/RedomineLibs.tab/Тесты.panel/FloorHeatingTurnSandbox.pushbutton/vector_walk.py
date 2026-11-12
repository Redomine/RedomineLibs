# -*- coding: utf-8 -*-
"""Diagnostic paired walk with heading and contour-guided turn policies."""
from __future__ import division

import math
import copy

from floor_heating_geometry import (TOLERANCE, _advance, _correct_entry,
                                  _distance, _inside, _on_segment, _offset_polygon,
                                  _ray_boundary_distance)
from floor_heating_pair import _append_branch_segment, _entry_at_depth


def _record(journal, action, **details):
    event = {"step": len(journal) + 1, "action": action}
    event.update(details)
    journal.append(event)


def route_length_mm(branches):
    """Total visible length of both branches, excluding the final bridge."""
    return sum(_distance(first, second)
               for side in ("right", "left")
               for first, second in zip(branches[side]["vertices"],
                                        branches[side]["vertices"][1:]))


def calculate_start_points(polygon, right_start, left_start, normal,
                           offset, spacing):
    """Project the inlet midpoint onto the boundary and clamp the pair."""
    right = (normal[1], -normal[0])
    backward = (-normal[0], -normal[1])
    middle = ((right_start[0] + left_start[0]) / 2.0,
              (right_start[1] + left_start[1]) / 2.0)
    depth = _ray_boundary_distance(middle, backward, polygon)
    on_boundary = any(_on_segment(middle, first, second)
                      for first, second in zip(polygon, polygon[1:] + polygon[:1]))
    if depth <= TOLERANCE and not on_boundary:
        raise ValueError(u"Не найдена граница области под серединой труб")
    center = _advance(middle, backward, depth)
    left_room = _ray_boundary_distance(center, (-right[0], -right[1]), polygon)
    right_room = _ray_boundary_distance(center, right, polygon)
    required = offset + spacing / 2.0
    if left_room + right_room < 2.0 * required - TOLERANCE:
        raise ValueError(u"На границе нет места для двух стартов")
    shift = max(required - left_room, 0.0)
    if right_room - shift < required - TOLERANCE:
        shift = -(required - right_room)
    center = _advance(center, right, shift)
    return (_advance(center, right, spacing / 2.0),
            _advance(center, right, -spacing / 2.0))


def start_at_calculated_entries(route):
    """Remove construction leads; keep the calculated route from each entry."""
    entries = {}
    normal = None
    first_side = None
    spacing = None
    polygon = None
    original_starts = None
    offset = None
    for event in route["journal"]:
        if event["action"] in ("input", "serpentine_input",
                               "single_serpentine_input"):
            normal = event["entry_direction"]
            spacing = event["spacing_mm"]
            polygon = event["polygon"]
            offset = event["offset_mm"]
            original_starts = (event["right_start"], event["left_start"])
            first_side = ("left" if event.get("clockwise", False) else "right")
            if "left_to_right" in event:
                first_side = ("right" if event["left_to_right"] else "left")
        if event["action"] == "entry":
            entries[event["branch"]] = event["corrected"]
        elif event["action"] == "single_serpentine_join":
            first_side = event["first_branch"]
            entries[event["first_branch"]] = event["first_entry"]
            entries[event["second_branch"]] = event["second_entry"]
    for side in ("right", "left"):
        if side not in entries:
            raise ValueError("calculated_entry_missing_for_" + side)
        vertices = route["branches"][side]["vertices"]
        index = next((i for i, point in enumerate(vertices)
                      if _distance(point, entries[side]) <= TOLERANCE), None)
        starts_on_boundary = (original_starts is not None and
                              _distance(vertices[0], original_starts[
                                  0 if side == "right" else 1]) <= TOLERANCE and
                              any(_on_segment(vertices[0], a, b)
                                  for a, b in zip(polygon,
                                                  polygon[1:] + polygon[:1])))
        if starts_on_boundary:
            visible = vertices
        elif index is not None:
            visible = vertices[index:]
        else:
            segment_index = next((i for i, (a, b) in enumerate(zip(
                vertices, vertices[1:]))
                if _on_segment(entries[side], a, b)), None)
            if segment_index is None:
                raise ValueError("calculated_entry_not_on_route_for_" + side)
            visible = [entries[side]] + vertices[segment_index + 1:]
        if normal is not None and polygon is not None:
            backward = (-normal[0], -normal[1])
            distance = _ray_boundary_distance(visible[0], backward,
                                               polygon)
            if distance > TOLERANCE:
                visible = [_advance(visible[0], backward, distance)] + visible
        route["branches"][side]["vertices"] = visible
    if normal is not None and original_starts is not None:
        right = (normal[1], -normal[0])
        target_right, target_left = calculate_start_points(
            polygon, original_starts[0], original_starts[1], normal,
            offset, spacing)
        targets = {"right": target_right, "left": target_left}
        for side in ("right", "left"):
            vertices = route["branches"][side]["vertices"]
            target = targets[side]
            if not _inside(target, polygon):
                raise ValueError(u"Расчётный старт вне области")
            delta = ((target[0] - vertices[0][0]) * right[0] +
                     (target[1] - vertices[0][1]) * right[1])
            vertices[0] = target
            if len(vertices) > 1 and abs((vertices[1][0] - target[0]) * normal[0] +
                                         (vertices[1][1] - target[1]) * normal[1]) > TOLERANCE:
                vertices[1] = _advance(vertices[1], right, delta)
    route["journal"] = [event for event in route["journal"]
                        if event["action"] not in
                        ("entry_segment", "entry_turn", "entry_heading")]
    if route["join"].get("connected") and spacing is not None:
        valid, reason = _valid_join(
            route["branches"]["right"]["vertices"],
            route["branches"]["left"]["vertices"],
            route["join"].get("bridge"), route["contour"], spacing, 0.0)
        if not valid:
            raise ValueError(u"Стартовые позиции нарушают геометрию: " + reason)
    _record(route["journal"], "start_at_calculated_entries",
            right=list(route["branches"]["right"]["vertices"][0]),
            left=list(route["branches"]["left"]["vertices"][0]))
    return route


def _visible_turn_count(vertices, entry_turns):
    directions = []
    for first, second in zip(vertices, vertices[1:]):
        length = _distance(first, second)
        if length > TOLERANCE:
            directions.append(((second[0] - first[0]) / length,
                               (second[1] - first[1]) / length))
    turns = sum(1 for first, second in zip(directions, directions[1:])
                if abs(first[0] * second[1] -
                       first[1] * second[0]) > TOLERANCE)
    return max(0, turns - entry_turns)


def limit_route_length(route, max_total_length_mm):
    """Keep the longest complete paired prefix that fits the length limit."""
    if (math.isnan(max_total_length_mm) or math.isinf(max_total_length_mm) or
            max_total_length_mm <= 0):
        raise ValueError("max_total_length_must_be_positive_and_finite")
    full_length = route_length_mm(route["branches"])
    route["length_limit_mm"] = max_total_length_mm
    if full_length <= max_total_length_mm + TOLERANCE:
        route["length_mm"] = full_length
        route["length_limit_hit"] = False
        return route
    vertices = {side: route["branches"][side]["vertices"]
                for side in ("right", "left")}
    kept = {side: list(vertices[side][:1]) for side in ("right", "left")}
    current_length = 0.0
    rejected_length = 0.0
    for index in range(max(len(vertices["right"]), len(vertices["left"])) - 1):
        pair = []
        for side in ("right", "left"):
            if index + 1 < len(vertices[side]):
                pair.append((side, vertices[side][index + 1],
                             _distance(vertices[side][index],
                                       vertices[side][index + 1])))
        pair_length = sum(item[2] for item in pair)
        if current_length + pair_length > max_total_length_mm + TOLERANCE:
            rejected_length = pair_length
            break
        for side, point, length in pair:
            kept[side].append(point)
        current_length += pair_length
    limited = copy.deepcopy(route)
    entry_turns = {side: sum(1 for event in limited["journal"]
                             if event["action"] == "entry_turn" and
                             event["branch"] == side)
                   for side in ("right", "left")}
    while (_visible_turn_count(kept["right"], entry_turns["right"]) !=
           _visible_turn_count(kept["left"], entry_turns["left"])):
        side = max(("right", "left"),
                   key=lambda name: _visible_turn_count(
                       kept[name], entry_turns[name]))
        if len(kept[side]) <= 1:
            break
        removed = kept[side].pop()
        current_length -= _distance(kept[side][-1], removed)
    for side in ("right", "left"):
        limited["branches"][side]["vertices"] = kept[side]
        limited["branches"][side]["stop_reason"] = "length_limit"
        turns = _visible_turn_count(kept[side], entry_turns[side])
        limited["branches"][side]["turns_used"] = turns
        limited["turns_by_branch"][side] = turns
        limited["stop_reason"][side] = "length_limit"
    limited["turns_used"] = sum(limited["turns_by_branch"].values())
    limited["join"] = {"connected": False, "reason": "length_limit"}
    limited["rollback"] = None
    limited["abandoned_segments"] = []
    limited["length_mm"] = current_length
    limited["full_length_mm"] = full_length
    limited["length_limit_hit"] = True
    _record(limited["journal"], "length_limit_reached",
            limit_mm=max_total_length_mm, built_length_mm=current_length,
            rejected_pair_length_mm=rejected_length,
            full_route_length_mm=full_length)
    return limited


def _right(direction):
    return (direction[1], -direction[0])


def _left(direction):
    return (-direction[1], direction[0])


def _directions(heading, clockwise=False):
    primary = ("left", _left(heading)) if clockwise else ("right", _right(heading))
    secondary = ("right", _right(heading)) if clockwise else ("left", _left(heading))
    return (primary, ("forward", heading), secondary)


def _contour_projection(point, contour, clockwise, previous=None,
                        corner_reach=0.0, force_next=False):
    """Move the normal foot along its edge and across the next corner."""
    area = sum(a[0] * b[1] - b[0] * a[1]
               for a, b in zip(contour, contour[1:] + contour[:1]))
    ordered = (contour if (area < 0) == clockwise else
               list(reversed(contour)))
    lengths = [_distance(a, b) for a, b in
               zip(ordered, ordered[1:] + ordered[:1])]
    perimeter = sum(lengths)
    def on_edge(index):
        length = lengths[index]
        start = ordered[index]
        end = ordered[(index + 1) % len(ordered)]
        direction = ((end[0] - start[0]) / length,
                     (end[1] - start[1]) / length)
        along = max(0.0, min(length,
                    (point[0] - start[0]) * direction[0] +
                    (point[1] - start[1]) * direction[1]))
        foot = _advance(start, direction, along)
        return along, foot, _distance(point, foot)
    if perimeter <= TOLERANCE:
        return None
    if previous is None or "edge" not in previous:
        options = []
        for index, length in enumerate(lengths):
            if length <= TOLERANCE:
                continue
            along, foot, distance = on_edge(index)
            phase = sum(lengths[:index]) + along
            options.append((distance, phase, index, along, foot))
        if not options:
            return None
        distance, phase, index, along, foot = min(options)
        return {"phase": phase, "foot": foot, "distance": distance,
                "edge": index, "along": along, "backward": False}
    index = previous["edge"]
    along, foot, distance = on_edge(index)
    if force_next:
        next_index = (index + 1) % len(ordered)
        if lengths[next_index] > TOLERANCE:
            next_along, next_foot, next_distance = on_edge(next_index)
            return {"phase": previous["phase"] +
                    lengths[index] - previous["along"] + next_along,
                    "foot": next_foot, "distance": next_distance,
                    "edge": next_index, "along": next_along,
                    "backward": False}
    backwards = along < previous["along"] - TOLERANCE
    phase = previous["phase"] + max(0.0, along - previous["along"])
    result = {"phase": phase, "foot": foot, "distance": distance,
              "edge": index, "along": along, "backward": backwards}
    if (not backwards and
            along + corner_reach >= lengths[index] - TOLERANCE):
        next_index = (index + 1) % len(ordered)
        if lengths[next_index] > TOLERANCE:
            next_along, next_foot, next_distance = on_edge(next_index)
            if next_distance < distance - TOLERANCE:
                result = {"phase": phase + lengths[index] - along + next_along,
                          "foot": next_foot, "distance": next_distance,
                          "edge": next_index, "along": next_along,
                          "backward": False}
    return result


def _contour_heading_support(point, direction, contour, clockwise, previous):
    """Find a directed supporting side for an inner pass, without phase credit."""
    area = sum(a[0] * b[1] - b[0] * a[1]
               for a, b in zip(contour, contour[1:] + contour[:1]))
    ordered = contour if (area < 0) == clockwise else list(reversed(contour))
    options = []
    for index, a in enumerate(ordered):
        b = ordered[(index + 1) % len(ordered)]
        length = _distance(a, b)
        if length <= TOLERANCE:
            continue
        tangent = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        if any(abs(tangent[i] - direction[i]) > TOLERANCE for i in (0, 1)):
            continue
        along = sum((point[i] - a[i]) * tangent[i] for i in (0, 1))
        if along < -TOLERANCE or along > length + TOLERANCE:
            continue
        along = max(0.0, min(length, along))
        foot = _advance(a, tangent, along)
        distance = _distance(point, foot)
        # Only support on the interior side of the oriented boundary.
        cross = tangent[0] * (point[1] - foot[1]) - tangent[1] * (point[0] - foot[0])
        if (cross > TOLERANCE if clockwise else cross < -TOLERANCE):
            continue
        if distance > TOLERANCE:
            normal = ((foot[0] - point[0]) / distance,
                      (foot[1] - point[1]) / distance)
            if _ray_boundary_distance(point, normal, contour) < distance - TOLERANCE:
                continue
        options.append((distance, index, along, foot))
    if not options:
        return None
    distance, index, along, foot = min(options)
    return {"edge": index, "along": along, "foot": foot,
            "distance": distance, "phase": previous["phase"],
            "backward": False}


def _contour_tangent(anchor, contour, clockwise, edge_offset=0):
    area = sum(a[0] * b[1] - b[0] * a[1]
               for a, b in zip(contour, contour[1:] + contour[:1]))
    ordered = (contour if (area < 0) == clockwise else
               list(reversed(contour)))
    index = (anchor["edge"] + edge_offset) % len(ordered)
    start = ordered[index]
    end = ordered[(index + 1) % len(ordered)]
    length = _distance(start, end)
    if length <= TOLERANCE:
        return None
    return ((end[0] - start[0]) / length,
            (end[1] - start[1]) / length)


def _next_contour_tangent(anchor, contour, clockwise):
    return _contour_tangent(anchor, contour, clockwise, 1)


def _contour_directions(branch, contour, clockwise, spacing):
    if not branch.get("contour_guided", False):
        return list(_directions(branch["heading"], clockwise))
    point = branch["vertices"][-1]
    anchor = branch.get("contour_anchor")
    if anchor is None:
        anchor = _contour_projection(point, contour, clockwise)
    if anchor is None:
        return list(_directions(branch["heading"], clockwise))
    if (branch.get("rebind_inner_support", False) and
            anchor["distance"] >= 2.0 * spacing - TOLERANCE):
        support = _contour_heading_support(point, branch["heading"], contour, clockwise, anchor)
        if support is not None:
            anchor = support
            branch["contour_anchor"] = support
    next_tangent = _next_contour_tangent(anchor, contour, clockwise)
    next_anchor = _contour_projection(
        point, contour, clockwise, anchor, force_next=True)
    at_corner = (next_anchor["phase"] <= anchor["phase"] + TOLERANCE and
                 anchor["distance"] <= 2.0 * spacing + TOLERANCE)
    ranked = []
    deferred = []
    for index, (name, direction) in enumerate(
            _directions(branch["heading"], clockwise)):
        target = _advance(point, direction, spacing)
        projection = _contour_projection(target, contour, clockwise,
                                         anchor, spacing)
        if projection["backward"]:
            next_projection = _contour_projection(
                target, contour, clockwise, anchor, spacing,
                force_next=True)
            if (next_projection["phase"] + TOLERANCE >= anchor["phase"] and
                    next_projection["distance"] <=
                    anchor["distance"] + spacing + TOLERANCE):
                deferred.append((name, direction))
            continue
        progress = projection["phase"] - anchor["phase"]
        corner_turn = (at_corner and next_tangent is not None and
                       direction == next_tangent)
        ranked.append((0 if corner_turn else 1 if progress > TOLERANCE else 2,
                       -progress, index, name, direction))
    ranked.sort()
    directions = [(item[-2], item[-1]) for item in ranked]
    directions.extend(deferred)
    candidates = branch.get("contour_candidates")
    if candidates is not None:
        alternate = dict(branch)
        alternate["contour_anchor"] = candidates["reserve"]
        alternate["contour_candidates"] = None
        for item in _contour_directions(alternate, contour, clockwise, spacing):
            if item[0] not in [name for name, vector in directions]:
                directions.append(item)
    return directions


def _same_obstacle_segment(first, second):
    if first is None or second is None:
        return False
    return (all(_distance(a, b) <= TOLERANCE for a, b in zip(first, second)) or
            all(_distance(a, b) <= TOLERANCE for a, b in
                zip(first, reversed(second))))


def _can_travel(available, required, min_segment):
    return (available > min_segment + TOLERANCE and
            available + TOLERANCE >= required)


def _axis_interval(segment, transverse, horizontal, clearance):
    first, second = segment
    longitudinal_index = 0 if horizontal else 1
    transverse_index = 1 if horizontal else 0
    low_long = min(first[longitudinal_index], second[longitudinal_index])
    high_long = max(first[longitudinal_index], second[longitudinal_index])
    low_cross = min(first[transverse_index], second[transverse_index])
    high_cross = max(first[transverse_index], second[transverse_index])
    cross_distance = max(low_cross - transverse,
                         transverse - high_cross, 0.0)
    if cross_distance >= clearance - TOLERANCE:
        return None
    extension = math.sqrt(max(0.0, clearance * clearance -
                              cross_distance * cross_distance))
    return (low_long - extension, high_long + extension)


def _distance_to_interval(position, sign, interval):
    low, high = interval
    if sign > 0:
        if high <= position + TOLERANCE:
            return None
        return max(0.0, low - position)
    if low >= position - TOLERANCE:
        return None
    return max(0.0, position - high)


def probe_ray(point, direction, contour, own_segments, other_obstacles,
              spacing_mm):
    """Exact ray limit for an orthogonal contour and orthogonal segments."""
    boundary_limit = _ray_boundary_distance(point, direction, contour)
    if boundary_limit <= TOLERANCE:
        return {"direction": list(direction), "available_mm": 0.0,
                "blocker": "boundary", "boundary_limit_mm": boundary_limit,
                "boundary_hit": list(point)}
    horizontal = abs(direction[0]) > TOLERANCE
    axis_index = 0 if horizontal else 1
    cross_index = 1 if horizontal else 0
    sign = direction[axis_index]
    limit = boundary_limit
    blocker = "boundary"
    blocker_index = None
    blocker_segment = None
    for index, obstacle in enumerate(other_obstacles):
        interval = _axis_interval(obstacle["segment"], point[cross_index],
                                  horizontal, spacing_mm)
        if interval is None:
            continue
        distance = _distance_to_interval(point[axis_index], sign, interval)
        if distance is not None and distance < limit - TOLERANCE:
            limit = distance
            blocker = obstacle["kind"]
            blocker_index = index
            blocker_segment = obstacle["segment"]
    for index, segment in enumerate(own_segments[:-2]):
        interval = _axis_interval(segment, point[cross_index],
                                  horizontal, spacing_mm)
        if interval is None:
            continue
        distance = _distance_to_interval(point[axis_index], sign, interval)
        if distance is not None and distance < limit - TOLERANCE:
            limit = distance
            blocker = "same_branch"
            blocker_index = index
            blocker_segment = segment
    return {"direction": list(direction), "available_mm": max(0.0, limit),
            "blocker": blocker, "blocker_index": blocker_index,
            "blocker_segment": ([list(point) for point in blocker_segment]
                                if blocker_segment is not None else None),
            "boundary_limit_mm": boundary_limit,
            "boundary_hit": list(_advance(point, direction, boundary_limit))}


def _paired_entry_alignment(polygon, contour, starts, normal, first_side,
                            second_side, offset, spacing, clockwise,
                            journal):
    first_entry = _entry_at_depth(polygon, starts[first_side], normal, offset)
    second_entry = _entry_at_depth(
        polygon, starts[second_side], normal, offset + spacing)
    if _inside(first_entry, contour) and _inside(second_entry, contour):
        return {}
    primary = _directions(normal, clockwise)[0][1]
    inward = (-primary[0], -primary[1])
    cross = 0 if abs(normal[1]) > TOLERANCE else 1
    along = 1 - cross
    candidates = []
    for a, b in zip(contour, contour[1:] + contour[:1]):
        if abs(a[cross] - b[cross]) > TOLERANCE:
            continue
        if not (min(a[along], b[along]) - TOLERANCE <=
                first_entry[along] <=
                max(a[along], b[along]) + TOLERANCE):
            continue
        distance = (a[cross] - first_entry[cross]) * inward[cross]
        if distance < -TOLERANCE:
            continue
        point = list(first_entry)
        point[cross] = a[cross]
        if _inside(point, contour):
            candidates.append((max(0.0, distance), tuple(point)))
    if _inside(first_entry, contour):
        first_target = first_entry
    else:
        if not candidates:
            raise ValueError("entry_has_no_offset_contour_on_inward_side")
        first_target = min(candidates, key=lambda item: item[0])[1]
    second_target = list(second_entry)
    second_target[cross] = first_target[cross] + inward[cross] * spacing
    second_target = tuple(second_target)
    if not _inside(second_target, contour):
        raise ValueError("paired_entry_spacing_outside_offset_contour")
    _record(journal, "paired_entry_alignment",
            first_branch=first_side, second_branch=second_side,
            first_raw=list(first_entry), first_target=list(first_target),
            second_raw=list(second_entry), second_target=list(second_target),
            spacing_mm=spacing)
    return {first_side: first_target, second_side: second_target}


def _initial_branch(polygon, contour, start, normal, depth, spacing,
                    side, journal, corrected_override=None):
    entry = _entry_at_depth(polygon, start, normal, depth)
    corrected = (corrected_override if corrected_override is not None else
                 _correct_entry(entry, normal, contour, spacing))
    if not _inside(corrected, contour):
        raise ValueError("{0}_entry_outside_offset_contour".format(side))
    vertices = [start]
    along = ((corrected[0] - start[0]) * normal[0] +
             (corrected[1] - start[1]) * normal[1])
    lateral = (corrected[0] - normal[0] * along,
               corrected[1] - normal[1] * along)
    _record(journal, "entry", branch=side, start=list(start),
            depth_mm=depth, target=list(entry), lateral=list(lateral),
            corrected=list(corrected), correction_mm=_distance(entry, corrected),
            correction_source=("paired_alignment" if
                               corrected_override is not None else "contour"))
    if _distance(vertices[-1], lateral) > TOLERANCE:
        vertices.append(lateral)
    if _distance(vertices[-1], corrected) > TOLERANCE:
        vertices.append(corrected)
    segments = [(vertices[index - 1], vertices[index])
                for index in range(1, len(vertices))]
    for index in range(1, len(segments)):
        previous = segments[index - 1]
        current = segments[index]
        previous_vector = (previous[1][0] - previous[0][0],
                           previous[1][1] - previous[0][1])
        current_vector = (current[1][0] - current[0][0],
                          current[1][1] - current[0][1])
        cross = (previous_vector[0] * current_vector[1] -
                 previous_vector[1] * current_vector[0])
        if abs(cross) > TOLERANCE:
            _record(journal, "entry_turn", branch=side,
                    at=list(current[0]), counted_in_walk=False)
    heading = normal
    initial_turns = 0
    for segment in segments:
        _record(journal, "entry_segment", branch=side,
                start=list(segment[0]), end=list(segment[1]),
                length_mm=_distance(*segment))
    _record(journal, "entry_heading", branch=side,
            direction=list(heading))
    return {"vertices": vertices, "segments": segments,
            "heading": heading, "turns_used": initial_turns,
            "stop_reason": None, "pending_turn": None,
            "contour_candidates": None,
            "last_blocker": None, "last_blocker_segment": None,
            "returning_from_protrusion": None}


def _obstacles(branch, kind):
    return [{"segment": segment, "kind": kind}
            for segment in branch["segments"]]


def _paired_turn_room(branches, contour, spacing, offset, journal,
                      round_index):
    tight = []
    for side, other_side in (("right", "left"), ("left", "right")):
        branch = branches[side]
        point = branch["vertices"][-1]
        obstacles = _obstacles(branches[other_side], "other_branch")
        results = {}
        for name, direction in (("right", _right(branch["heading"])),
                                ("left", _left(branch["heading"]))):
            results[name] = probe_ray(point, direction, contour,
                                      branch["segments"], obstacles,
                                      spacing)
        margins = {name: (offset if results[name]["blocker"] == "boundary"
                          else spacing) for name in ("right", "left")}
        measured = (results["right"]["available_mm"] + margins["right"] +
                    results["left"]["available_mm"] + margins["left"])
        threshold = 3 * spacing + margins["right"] + margins["left"]
        return_heading = branch.get("returning_from_protrusion")
        returning = return_heading is not None
        if returning:
            heading = branch["heading"]
            reversed_course = (heading[0] * return_heading[0] +
                               heading[1] * return_heading[1] < -TOLERANCE)
            if reversed_course and measured + TOLERANCE >= threshold:
                branch["returning_from_protrusion"] = None
                returning = False
                _record(journal, "protrusion_return_complete", round=round_index,
                        branch=side, at=list(point))
        lacks_room = measured + TOLERANCE < threshold and not returning
        _record(journal, "paired_turn_room_probe", round=round_index,
                branch=side, at=list(point), measured_width_mm=measured,
                threshold_mm=threshold, side_margins_mm=margins,
                right=results["right"], left=results["left"],
                returning_from_protrusion=returning,
                lacks_room=lacks_room)
        if lacks_room:
            tight.append(side)
    return tight


def _paired_next_step_available(branches, first_side, second_side,
                                contour, spacing, offset, min_segment,
                                max_turns, clockwise, journal, round_index):
    """Try one complete paired move before treating narrow room as a dead end."""
    trial = copy.deepcopy(branches)
    first = trial[first_side]
    second = trial[second_side]
    probe_log = []
    first_moved = _walk_one(
        first, first_side, contour, _obstacles(second, "other_branch"),
        spacing, offset, min_segment, max_turns, probe_log,
        round_index + 1, clockwise)
    second_moved = False
    if first_moved:
        reservation = _next_reservation(
            first, contour, _obstacles(second, "other_branch"), spacing,
            offset, min_segment, max_turns, probe_log,
            first_side, clockwise)
        obstacles = _obstacles(first, "other_branch")
        if reservation is not None:
            obstacles.append({"segment": reservation,
                              "kind": "first_turn_reservation"})
        second_moved = _walk_one(
            second, second_side, contour, obstacles, spacing, offset,
            min_segment, max_turns, probe_log, round_index + 1,
            clockwise)
    join = {"connected": False, "reason": "incomplete_pair"}
    if first_moved and second_moved:
        join = _finish_route(copy.deepcopy(trial), contour, spacing,
                             min_segment, probe_log, first_side)
    tight_after = (_paired_turn_room(trial, contour, spacing, offset,
                                     probe_log, round_index + 1)
                   if first_moved and second_moved else [])
    moves = [event for event in probe_log if event["action"] == "segment"]
    _record(journal, "pair_next_step_probe", round=round_index + 1,
            first_moved=first_moved, second_moved=second_moved,
            choices={event["branch"]: event["choice"] for event in moves},
            ends={side: list(trial[side]["vertices"][-1])
                  for side in (first_side, second_side)},
            connected=join["connected"], reason=join.get("reason"),
            tight_after=tight_after)
    return first_moved and second_moved and join["connected"] and not tight_after


def _terminal_pair_without_reservation(branches, first_side, second_side,
                                       contour, spacing, offset, min_segment,
                                       max_turns, clockwise, journal,
                                       round_index):
    """Try a final paired move without reserving a turn after the join."""
    trial = copy.deepcopy(branches)
    probe_log = []
    first_moved = _walk_one(
        trial[first_side], first_side, contour,
        _obstacles(trial[second_side], "other_branch"), spacing, offset,
        min_segment, max_turns, probe_log, round_index + 1, clockwise)
    second_moved = False
    if first_moved:
        second_moved = _walk_one(
            trial[second_side], second_side, contour,
            _obstacles(trial[first_side], "other_branch"), spacing, offset,
            min_segment, max_turns, probe_log, round_index + 1, clockwise)
    join = {"connected": False, "reason": "incomplete_pair"}
    equal_turns = (trial[first_side]["turns_used"] ==
                   trial[second_side]["turns_used"])
    tight_after = (_paired_turn_room(trial, contour, spacing, offset,
                                     probe_log, round_index + 1)
                   if first_moved and second_moved else [])
    # A terminal join needs no room for another paired turn. Keep tight_after
    # as diagnostics only; _finish_route validates the actual final geometry.
    if first_moved and second_moved and equal_turns:
        join = _finish_route(trial, contour, spacing, min_segment,
                             probe_log, first_side)
    accepted = (first_moved and second_moved and equal_turns and
                join["connected"])
    _record(journal, "terminal_pair_probe", round=round_index + 1,
            first_moved=first_moved, second_moved=second_moved,
            equal_turns=equal_turns, connected=join["connected"],
            reason=join.get("reason"), accepted=accepted,
            tight_after=tight_after,
            ends={side: list(trial[side]["vertices"][-1])
                  for side in (first_side, second_side)})
    if not accepted:
        return None, None
    for event in probe_log:
        details = dict(event)
        details.pop("step", None)
        _record(journal, details.pop("action"), **details)
    _record(journal, "terminal_pair_selected", round=round_index + 1,
            method=join["method"], bridge=join.get("bridge"))
    return trial, join


def _next_reservation(branch, contour, other_obstacles, spacing, offset,
                      min_segment, max_turns, journal, side, clockwise=False):
    point = branch["vertices"][-1]
    candidates = _contour_directions(branch, contour, clockwise, spacing)
    pending = branch.get("pending_turn")
    if pending is not None:
        candidates.sort(key=lambda item: 0 if item[0] == pending else 1)
    for name, direction in candidates:
        result = probe_ray(point, direction, contour, branch["segments"],
                           other_obstacles, spacing)
        _record(journal, "reservation_probe", branch=side,
                candidate=name, at=list(point), result=result)
        if (name == candidates[0][0] and
                _same_obstacle_segment(
                    result.get("blocker_segment"),
                    branch.get("rejected_primary_obstacle"))):
            _record(journal, "reservation_rejected_dead_end_opening",
                    branch=side, candidate=name, at=list(point))
            continue
        required = offset if result["blocker"] == "boundary" else spacing
        if _can_travel(result["available_mm"], required, min_segment):
            length = min(spacing, result["available_mm"])
            reservation = (point, _advance(point, direction, length))
            _record(journal, "reserve_first_turn", branch=side,
                    candidate=name, start=list(point),
                    end=list(reservation[1]), length_mm=length)
            return reservation
    return None


def _intermediate_checkpoints(point, direction, travel, spacing,
                              min_segment, other_obstacles):
    """Sample the step grid and exact spacing from nearby pipe edges."""
    axis = 0 if abs(direction[0]) > TOLERANCE else 1
    candidates = []
    for obstacle in other_obstacles:
        a, b = obstacle["segment"]
        if abs(a[axis] - b[axis]) > TOLERANCE:
            continue
        for sign in (-1, 1):
            position = a[axis] + sign * spacing
            distance = (position - point[axis]) * direction[axis]
            if (distance + TOLERANCE >= spacing and
                    distance < travel - TOLERANCE):
                candidates.append((distance, "pipe_offset",
                                   obstacle["kind"]))
    checkpoint = spacing
    while checkpoint + min_segment < travel - TOLERANCE:
        candidates.append((checkpoint, "step_grid", None))
        checkpoint += spacing
    candidates.sort(key=lambda item: (item[0],
                                      0 if item[1] == "pipe_offset" else 1))
    unique = []
    for candidate in candidates:
        if not unique or abs(candidate[0] - unique[-1][0]) > TOLERANCE:
            unique.append(candidate)
    return unique


def _turn_preserves_partner_exit(trial_segments, sample, turn_direction,
                                 other_obstacles, contour, spacing, offset,
                                 min_segment):
    """Reject a speculative reservation that closes all existing partner exits."""
    other_segments = [item["segment"] for item in other_obstacles
                      if item["kind"] == "other_branch"]
    if not other_segments:
        return True
    a, point = other_segments[-1]
    length = _distance(a, point)
    if length <= TOLERANCE:
        return True
    heading = ((point[0]-a[0])/length, (point[1]-a[1])/length)
    obstacles = [{"segment": segment, "kind": "other_branch"}
                 for segment in trial_segments]
    reservation = {"segment": (sample, _advance(sample, turn_direction, spacing)),
                   "kind": "first_turn_reservation"}
    had_exit = False
    for name, direction in _directions(heading):
        before = probe_ray(point, direction, contour, other_segments,
                           obstacles, spacing)
        required = offset if before["blocker"] == "boundary" else spacing
        if not _can_travel(before["available_mm"], required, min_segment):
            continue
        had_exit = True
        after = probe_ray(point, direction, contour, other_segments,
                          obstacles + [reservation], spacing)
        required = offset if after["blocker"] == "boundary" else spacing
        if _can_travel(after["available_mm"], required, min_segment):
            return True
    return not had_exit


def _walk_one(branch, side, contour, other_obstacles, spacing, offset,
              min_segment, max_turns, journal, round_index,
              clockwise=False, minimum_turn_run=0.0):
    if branch["stop_reason"] is not None:
        return False
    if branch["turns_used"] >= max_turns:
        branch["stop_reason"] = "turn_limit"
        _record(journal, "branch_stop", branch=side,
                reason="turn_limit", turns_used=branch["turns_used"])
        return False
    point = branch["vertices"][-1]
    directions = _contour_directions(branch, contour, clockwise, spacing)
    # An obsolete outer support must not hide the only geometric exit of an
    # inner pass. Relax its ordering only after all preferred rays are blocked.
    probes = [probe_ray(point, direction, contour, branch["segments"],
                        other_obstacles, spacing) for name, direction in directions]
    if branch.get("contour_guided", False) and not any(
            _can_travel(probe["available_mm"],
                        offset if probe["blocker"] == "boundary" else spacing,
                        min_segment) for probe in probes):
        missing = [item for item in _directions(branch["heading"], clockwise)
                   if item[0] not in [name for name, vector in directions]]
        partner_segments = [item["segment"] for item in other_obstacles
                            if item["kind"] == "other_branch"]
        if partner_segments:
            a, b = partner_segments[-1]
            length = _distance(a, b)
            partner_heading = ((b[0]-a[0])/length, (b[1]-a[1])/length)
            missing = [item for item in missing
                       if _distance(item[1], partner_heading) <= TOLERANCE]
        else:
            missing = []
        directions.extend(missing)
        if missing:
            _record(journal, "contour_blocked_support_fallback", branch=side,
                    round=round_index, candidates=[name for name, vector in missing])
    anchor = branch.get("contour_anchor")
    contour_tangent = (_contour_tangent(anchor, contour, clockwise)
                       if branch.get("contour_guided", False) and
                       anchor is not None else None)
    blocked_tangent = False
    _record(journal, "contour_direction_order", branch=side,
            round=round_index, at=list(point),
            order=[name for name, direction in directions],
            anchor=branch.get("contour_anchor"),
            reserve=(branch["contour_candidates"]["reserve"] if
                     branch.get("contour_candidates") is not None else None))
    pending_turn = branch.get("pending_turn")
    if pending_turn is not None:
        directions.sort(key=lambda item: 0 if item[0] == pending_turn else 1)
        _record(journal, "backtrack_turn_priority", branch=side,
                round=round_index, candidate=pending_turn)
        branch["pending_turn"] = None
    if branch.get("recovery_only_direction") is not None:
        directions = [item for item in directions if
                      item[0] == branch["recovery_only_direction"]]
        branch["recovery_only_direction"] = None
    for name, direction in directions:
        result = probe_ray(point, direction, contour, branch["segments"],
                           other_obstacles, spacing)
        _record(journal, "direction_probe", branch=side,
                round=round_index, candidate=name, at=list(point),
                result=result)
        avoided = branch.get("rejected_primary_obstacle")
        primary_name = directions[0][0] if directions else None
        if name == primary_name and avoided is not None:
            if _same_obstacle_segment(result.get("blocker_segment"), avoided):
                _record(journal, "rejected_dead_end_opening", branch=side,
                        round=round_index, candidate=name,
                        at=list(point), blocker_segment=avoided)
                continue
            branch["rejected_primary_obstacle"] = None
        required = offset if result["blocker"] == "boundary" else spacing
        if not _can_travel(result["available_mm"], required, min_segment):
            if (contour_tangent is not None and
                    abs(direction[0] - contour_tangent[0]) <= TOLERANCE and
                    abs(direction[1] - contour_tangent[1]) <= TOLERANCE and
                    result["blocker"] in
                    ("other_branch", "same_branch",
                     "first_turn_reservation")):
                blocked_tangent = True
            _record(journal, "direction_rejected", branch=side,
                    candidate=name, reason="dictating_distance_at_origin",
                    available_mm=result["available_mm"],
                    required_mm=max(min_segment, required))
            continue
        travel = result["available_mm"]
        last_turn = None
        preferred_selected = False
        checkpoints = _intermediate_checkpoints(
            point, direction, travel, spacing, min_segment,
            other_obstacles)
        for checkpoint, checkpoint_source, checkpoint_kind in checkpoints:
            sample = _advance(point, direction, checkpoint)
            trial_segments = branch["segments"] + [(point, sample)]
            future_turn_allowed = (branch["turns_used"] +
                                   (0 if name == "forward" else 1) < max_turns)
            sample_branch = dict(branch)
            sample_branch["heading"] = direction
            sample_branch["vertices"] = branch["vertices"][:-1] + [sample]
            sample_branch["contour_anchor"] = _contour_projection(
                sample, contour, clockwise,
                branch.get("contour_anchor") or
                _contour_projection(point, contour, clockwise), spacing)
            straight_progress = None
            if branch.get("contour_guided", False):
                straight_probe = probe_ray(
                    sample, direction, contour, trial_segments,
                    other_obstacles, spacing)
                if straight_probe["available_mm"] > min_segment + TOLERANCE:
                    straight_target = _advance(
                        sample, direction,
                        min(spacing, straight_probe["available_mm"]))
                    straight_anchor = _contour_projection(
                        straight_target, contour, clockwise,
                        sample_branch["contour_anchor"], spacing)
                    if not straight_anchor["backward"]:
                        straight_progress = (
                            straight_anchor["phase"] -
                            sample_branch["contour_anchor"]["phase"])
            side_directions = [item for item in _contour_directions(
                sample_branch, contour, clockwise, spacing)
                if item[0] != "forward"]
            primary_name = side_directions[0][0] if side_directions else None
            secondary_name = (side_directions[1][0]
                              if len(side_directions) > 1 else None)
            for turn_name, turn_direction in side_directions:
                side_probe = probe_ray(sample, turn_direction, contour,
                                      trial_segments, other_obstacles,
                                      spacing)
                avoided = branch.get("rejected_primary_obstacle")
                if (name == "forward" and turn_name == primary_name and
                        avoided is not None):
                    if _same_obstacle_segment(
                            side_probe.get("blocker_segment"), avoided):
                        _record(journal, "rejected_dead_end_opening",
                                branch=side, round=round_index,
                                candidate=turn_name, at=list(sample),
                                blocker_segment=avoided)
                        continue
                    branch["rejected_primary_obstacle"] = None
                side_required = (offset if side_probe["blocker"] == "boundary"
                                 else spacing)
                available = _can_travel(side_probe["available_mm"],
                                        side_required, min_segment)
                side_progress = None
                contour_decision = None
                if available and branch.get("contour_guided", False):
                    side_target = _advance(
                        sample, turn_direction,
                        min(spacing, side_probe["available_mm"]))
                    side_anchor = _contour_projection(
                        side_target, contour, clockwise,
                        sample_branch["contour_anchor"], spacing)
                    side_progress = (side_anchor["phase"] -
                                     sample_branch["contour_anchor"]["phase"])
                    sample_tangent = _contour_tangent(
                        sample_branch["contour_anchor"], contour, clockwise)
                    next_tangent = _next_contour_tangent(
                        sample_branch["contour_anchor"], contour, clockwise)
                    following_contour = any(
                        tangent is not None and
                        abs(direction[0] - tangent[0]) <= TOLERANCE and
                        abs(direction[1] - tangent[1]) <= TOLERANCE
                        for tangent in (sample_tangent, next_tangent))
                    sample_next = _next_contour_tangent(
                        sample_branch["contour_anchor"], contour,
                        clockwise)
                    sample_next_anchor = _contour_projection(
                        sample, contour, clockwise,
                        sample_branch["contour_anchor"],
                        force_next=True)
                    corner_turn = (
                        sample_next is not None and
                        turn_direction == sample_next and
                        sample_branch["contour_anchor"]["distance"] <=
                        2.0 * spacing + TOLERANCE and
                        sample_next_anchor["phase"] <=
                        sample_branch["contour_anchor"]["phase"] +
                        TOLERANCE)
                    obstacle_return = (
                        name != "forward" and
                        turn_direction == branch["heading"] and
                        branch.get("last_blocker") in
                        ("other_branch", "same_branch", "first_turn_reservation"))
                    if obstacle_return:
                        contour_decision = "obstacle_return"
                    elif side_anchor["backward"]:
                        contour_decision = "backward"
                        available = False
                    elif corner_turn:
                        contour_decision = "corner_turn"
                    # A preferred straight run may end at an entry pipe.
                    # Keep legal side exits until the endpoint is checked.
                    elif following_contour and straight_progress is not None:
                        contour_decision = "phase_fallback"
                    elif (straight_progress is not None and
                          side_progress <= straight_progress + TOLERANCE):
                        contour_decision = "phase_fallback"
                    else:
                        # Phase gain is a preference, not a geometric reason
                        # to interrupt a clear pass. Keep this turn only as
                        # an escape if the full endpoint has no continuation.
                        contour_decision = "phase_fallback"
                if (available and branch.get("contour_guided", False) and
                        not _turn_preserves_partner_exit(
                            trial_segments, sample, turn_direction,
                            other_obstacles, contour, spacing, offset,
                            min_segment)):
                    available = False
                    contour_decision = "partner_exit_blocked"
                    _record(journal, "intermediate_turn_pair_rejected",
                            branch=side, round=round_index, at=list(sample),
                            candidate=turn_name, reason=contour_decision)
                _record(journal, "intermediate_turn_probe", branch=side,
                        round=round_index, distance_mm=checkpoint,
                        at=list(sample), candidate=turn_name,
                        checkpoint_source=checkpoint_source,
                        checkpoint_obstacle_kind=checkpoint_kind,
                        result=side_probe, turn_available=available,
                        contour_decision=contour_decision,
                        contour_progress_mm=side_progress,
                        straight_progress_mm=straight_progress,
                        within_turn_limit=future_turn_allowed)
                run_ready = checkpoint + TOLERANCE >= minimum_turn_run
                if available and future_turn_allowed and run_ready:
                    if (contour_decision == "phase_fallback" or
                            (turn_name == secondary_name and
                             contour_decision != "obstacle_return")):
                        if (last_turn is None or
                                checkpoint > last_turn[0] + TOLERANCE):
                            last_turn = (checkpoint, turn_name)
                        _record(journal, "backtrack_candidate", branch=side,
                                round=round_index, distance_mm=checkpoint,
                                next_direction=turn_name)
                    else:
                        travel = checkpoint
                        preferred_selected = True
                        if contour_decision == "obstacle_return":
                            branch["pending_turn"] = turn_name
                        _record(journal, "intermediate_turn_selected",
                                branch=side, round=round_index,
                                distance_mm=checkpoint,
                                checkpoint_source=checkpoint_source,
                                next_direction=turn_name)
                    break
            if preferred_selected:
                break
        endpoint_has_exit = False
        if not preferred_selected and last_turn is not None:
            endpoint = _advance(point, direction, result["available_mm"])
            trial_segments = branch["segments"] + [(point, endpoint)]
            endpoint_branch = dict(branch)
            endpoint_branch["heading"] = direction
            endpoint_branch["vertices"] = branch["vertices"][:-1] + [endpoint]
            endpoint_branch["contour_anchor"] = _contour_projection(
                endpoint, contour, clockwise,
                branch.get("contour_anchor") or
                _contour_projection(point, contour, clockwise), spacing)
            for exit_name, exit_direction in _contour_directions(
                    endpoint_branch, contour, clockwise, spacing):
                exit_probe = probe_ray(endpoint, exit_direction, contour,
                                       trial_segments, other_obstacles, spacing)
                exit_required = (offset if exit_probe["blocker"] == "boundary"
                                 else spacing)
                if _can_travel(exit_probe["available_mm"],
                               exit_required, min_segment):
                    endpoint_has_exit = True
                    break
            _record(journal, "endpoint_exit_check", branch=side,
                    round=round_index, at=list(endpoint),
                    has_exit=endpoint_has_exit,
                    fallback_distance_mm=last_turn[0])
        if not preferred_selected and last_turn is not None and not endpoint_has_exit:
            travel = last_turn[0]
            branch["pending_turn"] = last_turn[1]
            _record(journal, "backtrack_turn_selected", branch=side,
                    round=round_index, from_distance_mm=result["available_mm"],
                    to_distance_mm=travel, next_direction=last_turn[1])
        target = _advance(point, direction, travel)
        active_candidates = branch.get("contour_candidates")
        current_anchor = (branch.get("contour_anchor") or
                          _contour_projection(point, contour, clockwise))
        current_target = _contour_projection(
            target, contour, clockwise, current_anchor, spacing)
        if (branch.get("contour_guided", False) and
                current_target["backward"] and
                active_candidates is None):
            next_edge_target = _contour_projection(
                target, contour, clockwise, current_anchor, spacing,
                force_next=True)
            if (next_edge_target["phase"] + TOLERANCE >=
                    current_anchor["phase"] and
                    next_edge_target["distance"] <=
                    current_anchor["distance"] + spacing + TOLERANCE):
                current_target = next_edge_target
                _record(journal, "contour_next_edge_candidate",
                        branch=side, round=round_index,
                        candidate=name, at=list(target),
                        edge=next_edge_target["edge"])
            else:
                # An inner pass can outlive a short side of the original
                # contour. Rebind its support, never shorten free geometry
                # solely to keep that obsolete projection monotone.
                replacement = _contour_heading_support(
                    target, direction, contour, clockwise, current_anchor)
                if replacement is not None:
                    current_target = replacement
                    _record(journal, "contour_support_rebound",
                            branch=side, round=round_index,
                            from_edge=current_anchor["edge"],
                            to_edge=replacement["edge"], at=list(target),
                            reason="obsolete_support", length_mm=travel)
        reserve_target = None
        if active_candidates is not None:
            reserve_target = _contour_projection(
                target, contour, clockwise,
                active_candidates["reserve"], spacing)
        if (branch.get("contour_guided", False) and
                current_target["backward"] and
                (reserve_target is None or reserve_target["backward"])):
            _record(journal, "contour_backward_segment_rejected",
                    branch=side, round=round_index,
                    candidate=name, at=list(target))
            continue
        selected_candidate = None
        if active_candidates is not None:
            return_direction = active_candidates["return_direction"]
            if (abs(direction[0] - return_direction[0]) <= TOLERANCE and
                    abs(direction[1] - return_direction[1]) <= TOLERANCE and
                    not current_target["backward"]):
                selected_candidate = "current"
            elif (name == ("right" if clockwise else "left") and
                  not reserve_target["backward"]):
                selected_candidate = "reserve"
            elif current_target["backward"]:
                selected_candidate = "reserve"
            elif reserve_target["backward"] or name != "forward":
                selected_candidate = "current"
            if selected_candidate == "reserve":
                branch["contour_anchor"] = active_candidates["reserve"]
                current_target = reserve_target
            if selected_candidate is not None:
                branch["contour_candidates"] = None
                _record(journal, "contour_candidate_selected",
                        branch=side, round=round_index,
                        selected=selected_candidate, at=list(target),
                        selected_target=current_target)
        previous_anchor = (branch.get("contour_anchor") or
                           _contour_projection(point, contour, clockwise))
        next_tangent = (_next_contour_tangent(
            previous_anchor, contour, clockwise)
            if branch.get("contour_guided", False) and
            previous_anchor is not None else None)
        corner_candidate = (name != "forward" and next_tangent is not None and
                       branch.get("last_blocker") in
                       ("other_branch", "same_branch",
                        "first_turn_reservation") and
                       abs(direction[0] - next_tangent[0]) <= TOLERANCE and
                       abs(direction[1] - next_tangent[1]) <= TOLERANCE)
        deferred_corner = (corner_candidate and blocked_tangent and
                           preferred_selected and active_candidates is None)
        pipe_corner = corner_candidate and not deferred_corner
        branch["contour_anchor"] = (
            current_target if selected_candidate is not None or
            current_target["edge"] != previous_anchor["edge"] else _contour_projection(
                target, contour, clockwise, previous_anchor, spacing,
                force_next=pipe_corner))
        if pipe_corner:
            _record(journal, "contour_pipe_corner_transition",
                    branch=side, round=round_index,
                    blocker=branch["last_blocker"],
                    blocker_segment=branch.get("last_blocker_segment"),
                    from_edge=previous_anchor["edge"],
                    to_edge=branch["contour_anchor"]["edge"],
                    at=list(target))
        if deferred_corner:
            branch["contour_candidates"] = {
                "return_direction": contour_tangent,
                "reserve": _contour_projection(
                    target, contour, clockwise, previous_anchor,
                    spacing, force_next=True)}
            _record(journal, "contour_candidates_created",
                    branch=side, round=round_index, at=list(target),
                    current=branch["contour_anchor"],
                    reserve=branch["contour_candidates"]["reserve"])
        elif active_candidates is not None and branch.get(
                "contour_candidates") is not None:
            branch["contour_candidates"]["reserve"] = reserve_target
        merged = _append_branch_segment(branch, target)
        branch["heading"] = direction
        branch["last_blocker"] = (result["blocker"] if
                                  abs(travel - result["available_mm"]) <= TOLERANCE
                                  else "intermediate_turn")
        branch["last_blocker_segment"] = (
            result.get("blocker_segment") if
            abs(travel - result["available_mm"]) <= TOLERANCE else None)
        if name != "forward":
            branch["turns_used"] += 1
        _record(journal, "segment", branch=side, round=round_index,
                choice=name, start=list(point), end=list(target),
                direction=list(direction), length_mm=travel,
                blocker=result["blocker"],
                merged_with_previous=merged,
                turns_used=branch["turns_used"])
        if branch["turns_used"] >= max_turns:
            branch["stop_reason"] = "turn_limit"
            _record(journal, "branch_stop", branch=side,
                    reason="turn_limit", turns_used=branch["turns_used"])
        return True
    branch["stop_reason"] = "all_directions_blocked"
    _record(journal, "branch_stop", branch=side,
            reason="all_directions_blocked", at=list(point))
    return False


def _segment_clearance(first, second):
    a, b = first
    c, d = second
    dx = max(min(a[0], b[0]) - max(c[0], d[0]),
             min(c[0], d[0]) - max(a[0], b[0]), 0.0)
    dy = max(min(a[1], b[1]) - max(c[1], d[1]),
             min(c[1], d[1]) - max(a[1], b[1]), 0.0)
    return math.sqrt(dx * dx + dy * dy)


def _sequential_jog_without_parallel_overlap(first, second):
    a, b = first
    c, d = second
    first_horizontal = abs(a[1] - b[1]) <= TOLERANCE
    second_horizontal = abs(c[1] - d[1]) <= TOLERANCE
    if first_horizontal != second_horizontal:
        return False
    axis = 0 if first_horizontal else 1
    first_low, first_high = sorted((a[axis], b[axis]))
    second_low, second_high = sorted((c[axis], d[axis]))
    return (first_high <= second_low + TOLERANCE or
            second_high <= first_low + TOLERANCE)


def _valid_join(right, left, bridge, contour, spacing, min_segment):
    segments = []
    for side, vertices in (("right", right), ("left", left)):
        for index in range(1, len(vertices)):
            segment = (vertices[index - 1], vertices[index])
            length = _distance(*segment)
            if length < min_segment - TOLERANCE:
                return False, "short_segment"
            direction = ((segment[1][0] - segment[0][0]) / length,
                         (segment[1][1] - segment[0][1]) / length)
            if abs(direction[0]) > TOLERANCE and abs(direction[1]) > TOLERANCE:
                return False, "nonorthogonal_segment"
            if index == len(vertices) - 1 and (
                    not _inside(segment[1], contour) or
                    length > _ray_boundary_distance(segment[0], direction,
                                                    contour) + TOLERANCE):
                return False, "outside_contour"
            segments.append((side, index - 1, segment))
    if bridge is not None:
        length = _distance(*bridge)
        if length < min_segment - TOLERANCE:
            return False, "short_bridge"
        direction = ((bridge[1][0] - bridge[0][0]) / length,
                     (bridge[1][1] - bridge[0][1]) / length)
        if length > _ray_boundary_distance(bridge[0], direction,
                                           contour) + TOLERANCE:
            return False, "bridge_outside_contour"
        segments.append(("bridge", 0, bridge))
    for index, (side_a, number_a, segment_a) in enumerate(segments):
        for side_b, number_b, segment_b in segments[index + 1:]:
            adjacent = (side_a == side_b and
                        abs(number_a - number_b) == 1)
            sequential_jog = (side_a == side_b and
                              abs(number_a - number_b) == 2 and
                              _sequential_jog_without_parallel_overlap(
                                  segment_a, segment_b))
            joins = (side_a == "bridge" and side_b != "bridge" and
                     number_b == len(right if side_b == "right" else left) - 2)
            joins = joins or (side_b == "bridge" and side_a != "bridge" and
                              number_a == len(right if side_a == "right" else left) - 2)
            direct_join = (bridge is None and side_a != side_b and
                           number_a == len(right if side_a == "right" else left) - 2 and
                           number_b == len(right if side_b == "right" else left) - 2)
            if adjacent or sequential_jog or joins or direct_join:
                continue
            if _segment_clearance(segment_a, segment_b) < spacing - TOLERANCE:
                return False, "pipe_clearance"
    return True, None


def _finish_route(branches, contour, spacing, min_segment, journal,
                  first_side="right"):
    right = list(branches["right"]["vertices"])
    left = list(branches["left"]["vertices"])
    if len(right) < 2 or len(left) < 2:
        return {"connected": False, "reason": "missing_last_segment"}
    rp, re = right[-2:]
    lp, le = left[-2:]
    right_horizontal = abs(rp[1] - re[1]) <= TOLERANCE
    left_horizontal = abs(lp[1] - le[1]) <= TOLERANCE
    candidates = []
    if right_horizontal != left_horizontal:
        meeting = ((le[0], re[1]) if right_horizontal else (re[0], le[1]))
        candidates.append(("intersect_last_lines", meeting, meeting, None))
    elif right_horizontal:
        if abs(re[1] - le[1]) > TOLERANCE:
            candidates.extend((("align_to_first", re, (re[0], le[1]),
                                (re, (re[0], le[1]))),
                               ("align_to_second", (le[0], re[1]), le,
                                ((le[0], re[1]), le))))
    elif abs(re[0] - le[0]) > TOLERANCE:
        candidates.extend((("align_to_first", re, (le[0], re[1]),
                            (re, (le[0], re[1]))),
                           ("align_to_second", (re[0], le[1]), le,
                            ((re[0], le[1]), le))))
    preferred_alignment = ("align_to_first" if first_side == "right" else
                           "align_to_second")
    candidates.sort(key=lambda item: (_distance(re, item[1]) +
                                      _distance(le, item[2]) +
                                      (_distance(*item[3]) if item[3] else 0.0),
                                      0 if item[0] == preferred_alignment else 1))
    for name, right_end, left_end, bridge in candidates:
        right_run = ((right_end[0] - rp[0]) * (re[0] - rp[0]) +
                     (right_end[1] - rp[1]) * (re[1] - rp[1]))
        left_run = ((left_end[0] - lp[0]) * (le[0] - lp[0]) +
                    (left_end[1] - lp[1]) * (le[1] - lp[1]))
        if right_run <= TOLERANCE or left_run <= TOLERANCE:
            _record(journal, "join_probe", candidate=name,
                    right_end=list(right_end), left_end=list(left_end),
                    accepted=False, reason="reversed_last_segment")
            continue
        proposed_right = right[:-1] + [right_end]
        proposed_left = left[:-1] + [left_end]
        valid, reason = _valid_join(proposed_right, proposed_left, bridge,
                                    contour, spacing, min_segment)
        _record(journal, "join_probe", candidate=name,
                right_end=list(right_end), left_end=list(left_end),
                bridge=[list(point) for point in bridge] if bridge else None,
                accepted=valid, reason=reason)
        if valid:
            branches["right"]["vertices"] = proposed_right
            branches["left"]["vertices"] = proposed_left
            return {"connected": True, "method": name,
                    "bridge": [list(point) for point in bridge] if bridge else None}
    return {"connected": False, "reason": "no_valid_orthogonal_join"}


def _simplify_terminal_route(branches, join, contour, spacing,
                             min_segment, journal):
    """Delay a terminal corner across a redundant monotone stair step.

    Compare equal-length orthogonal paths with the same endpoints. Removing
    two bends is allowed only when the replacement stays inside the contour
    and the complete joined route still meets pipe clearance.
    """
    if not join.get("connected"):
        return
    for side, other in (("right", "left"), ("left", "right")):
        branch = branches[side]
        while len(branch["vertices"]) >= 5:
            vertices = branch["vertices"]
            tail = vertices[-5:]
            vectors = [(b[0] - a[0], b[1] - a[1])
                       for a, b in zip(tail, tail[1:])]
            directions = []
            for dx, dy in vectors:
                if abs(dx) <= TOLERANCE and abs(dy) > TOLERANCE:
                    directions.append((0, 1 if dy > 0 else -1))
                elif abs(dy) <= TOLERANCE and abs(dx) > TOLERANCE:
                    directions.append((1 if dx > 0 else -1, 0))
                else:
                    break
            if (len(directions) != 4 or directions[0] != directions[2] or
                    directions[1] != directions[3] or
                    directions[0][0] * directions[1][0] +
                    directions[0][1] * directions[1][1] != 0):
                break
            corner = (tail[1][0] + vectors[2][0],
                      tail[1][1] + vectors[2][1])
            proposed = vertices[:-4] + [corner, tail[-1]]
            valid, reason = True, None
            # _valid_join normally checks only the resized final segment.
            # Here the preceding segment moves too, so check both new runs.
            for a, b in zip(proposed[-3:], proposed[-2:]):
                length = _distance(a, b)
                direction = ((b[0] - a[0]) / length,
                             (b[1] - a[1]) / length)
                if (not _inside(a, contour) or not _inside(b, contour) or
                        length > _ray_boundary_distance(a, direction, contour)
                        + TOLERANCE):
                    valid, reason = False, "outside_contour"
                    break
            if valid:
                right = proposed if side == "right" else branches[other]["vertices"]
                left = proposed if side == "left" else branches[other]["vertices"]
                valid, reason = _valid_join(right, left, join.get("bridge"),
                                            contour, spacing, min_segment)
            _record(journal, "terminal_corner_probe", branch=side,
                    old_corner=list(tail[1]), new_corner=list(corner),
                    delay_mm=_distance(tail[1], corner), accepted=valid,
                    reason=reason)
            if not valid:
                break
            branch["vertices"] = proposed
            branch["segments"] = list(zip(proposed, proposed[1:]))
            branch["heading"] = directions[-1]
            branch["turns_used"] -= 2
            _record(journal, "terminal_corner_delayed", branch=side,
                    old_tail=[list(p) for p in tail],
                    new_tail=[list(p) for p in proposed[-3:]],
                    delay_mm=_distance(tail[1], corner), removed_turns=2)


def _segment_inside_contour(a, b, contour):
    """Check every interval of an orthogonal run, including tangent vertices."""
    if not _inside(a, contour) or not _inside(b, contour):
        return False
    axis = 0 if abs(b[0] - a[0]) > TOLERANCE else 1
    if abs(b[1-axis] - a[1-axis]) > TOLERANCE:
        return False
    low, high = sorted((a[axis], b[axis]))
    cuts = sorted(set([low, high] + [p[axis] for p in contour
                                   if low < p[axis] < high]))
    for start, end in zip(cuts, cuts[1:]):
        sample = list(a)
        sample[axis] = (start + end) / 2.0
        if not _inside(sample, contour):
            return False
    return True


def _simplify_pair_route(branches, join, contour, spacing, min_segment, journal,
                         first_side="left"):
    """Remove unpaired pockets and sub-spacing stairs from a completed pair.

    A north-west-north-east detour can be replaced by north-east only if
    the *whole pair* remains clear. A follower entering the same pocket
    blocks that shortcut. Monotone stairs are considered only when their
    lateral correction is less than the requested pipe spacing.
    Each accepted change removes two vertices, so convergence is finite.
    """
    if not join.get("connected"):
        return
    changed = True
    while changed:
        changed = False
        second_side = "right" if first_side == "left" else "left"
        for side, other in ((first_side, second_side), (second_side, first_side)):
            branch = branches[side]
            vertices = branch["vertices"]
            # Preserve the entry correction and the terminal connection.
            for index in range(1, len(vertices) - 4):
                tail = vertices[index:index + 5]
                vectors = [(b[0]-a[0], b[1]-a[1])
                           for a, b in zip(tail, tail[1:])]
                lengths = [_distance(a, b) for a, b in zip(tail, tail[1:])]
                if min(lengths) <= TOLERANCE:
                    continue
                directions = [(v[0]/length, v[1]/length)
                              for v, length in zip(vectors, lengths)]
                if (_distance(directions[0], directions[2]) > TOLERANCE or
                        abs(directions[0][0]*directions[1][0] +
                            directions[0][1]*directions[1][1]) > TOLERANCE):
                    continue
                pocket = _distance(directions[1],
                                   (-directions[3][0], -directions[3][1])) <= TOLERANCE
                stair = (_distance(directions[1], directions[3]) <= TOLERANCE
                         and lengths[2] < spacing - TOLERANCE)
                if not (pocket or stair):
                    continue
                if pocket:
                    # Keep a real paired loop even if its inner branch alone
                    # could be shortened without a collision.
                    axis = 0 if abs(directions[1][0]) > TOLERANCE else 1
                    paired = False
                    partner = branches[other]["vertices"]
                    for a, b in zip(partner, partner[1:]):
                        delta = (b[0]-a[0], b[1]-a[1])
                        if (abs(delta[1-axis]) > TOLERANCE or
                                delta[axis]*directions[1][axis] <= TOLERANCE):
                            continue
                        overlap = (min(max(a[axis], b[axis]),
                                       max(tail[1][axis], tail[2][axis])) -
                                   max(min(a[axis], b[axis]),
                                       min(tail[1][axis], tail[2][axis])))
                        if (overlap > TOLERANCE and
                                _segment_clearance((a, b), (tail[1], tail[2]))
                                <= spacing + TOLERANCE):
                            paired = True
                            break
                    if paired:
                        continue
                corner = (tail[1][0] + vectors[2][0],
                          tail[1][1] + vectors[2][1])
                # The shortcut must keep the direction of the final run.
                if ((tail[-1][0]-corner[0])*directions[3][0] +
                        (tail[-1][1]-corner[1])*directions[3][1]
                        < min_segment - TOLERANCE):
                    continue
                replacement = [tail[0], corner, tail[-1]]
                if not all(_segment_inside_contour(a, b, contour)
                           for a, b in zip(replacement, replacement[1:])):
                    continue
                proposed = vertices[:index] + replacement + vertices[index + 5:]
                right = proposed if side == "right" else branches[other]["vertices"]
                left = proposed if side == "left" else branches[other]["vertices"]
                valid, reason = _valid_join(right, left, join.get("bridge"),
                                            contour, spacing, min_segment)
                if not valid:
                    continue
                branch["vertices"] = proposed
                branch["segments"] = list(zip(proposed, proposed[1:]))
                branch["turns_used"] -= 2
                _record(journal, "pair_corner_simplified", branch=side,
                        reason="unpaired_pocket" if pocket else "subspacing_stair",
                        old_path=[list(p) for p in tail],
                        new_path=[list(p) for p in replacement], removed_turns=2)
                changed = True
                break
            if changed:
                # Recheck the leader before shortening the follower: moving
                # its corner can free the follower's exact spacing line.
                break


def _abandoned_segments(journal, branches, join, min_segment):
    """Visible portions of built segments absent from the final route."""
    # These events describe the current final geometry, not route history.
    # A later completion can retain formerly abandoned portions, so replace
    # the derived events rather than appending a second diagnostic snapshot.
    journal[:] = [event for event in journal if event["action"] not in
                  ("abandoned_segment", "abandoned_segment_skipped")]
    kept = []
    for side in ("right", "left"):
        vertices = branches[side]["vertices"]
        kept.extend(zip(vertices, vertices[1:]))
    if join.get("bridge") is not None:
        kept.append(tuple(tuple(point) for point in join["bridge"]))
    abandoned = []
    history = []
    for event in list(journal):
        if event["action"] == "entry":
            points = [event["start"], event.get("lateral", event["target"]),
                      event["corrected"]]
            history.extend((event["step"], event["branch"],
                            (points[index - 1], points[index]))
                           for index in (1, 2))
        elif event["action"] in ("segment", "recovery_segment"):
            history.append((event["step"], event["branch"],
                            (event["start"], event["end"])))
    for source_step, side, segment in history:
        start, end = segment
        if _distance(start, end) <= TOLERANCE:
            continue
        axis = 0 if abs(start[1] - end[1]) <= TOLERANCE else 1
        cross = 1 - axis
        cross_value = start[cross]
        remaining = [(min(start[axis], end[axis]),
                      max(start[axis], end[axis]))]
        for occupied in kept + abandoned:
            occupied_start, occupied_end = occupied
            if (abs(occupied_start[cross] - cross_value) > TOLERANCE or
                    abs(occupied_end[cross] - cross_value) > TOLERANCE):
                continue
            low = min(occupied_start[axis], occupied_end[axis])
            high = max(occupied_start[axis], occupied_end[axis])
            pieces = []
            for first, second in remaining:
                if high <= first + TOLERANCE or low >= second - TOLERANCE:
                    pieces.append((first, second))
                else:
                    if low > first + TOLERANCE:
                        pieces.append((first, low))
                    if high < second - TOLERANCE:
                        pieces.append((high, second))
            remaining = pieces
            if not remaining:
                break
        for first, second in remaining:
            if second - first <= min_segment + TOLERANCE:
                _record(journal, "abandoned_segment_skipped",
                        source_step=source_step, branch=side,
                        length_mm=second - first,
                        reason="short_curve_tolerance")
                continue
            if axis == 0:
                visible = ((first, cross_value), (second, cross_value))
            else:
                visible = ((cross_value, first), (cross_value, second))
            if start[axis] > end[axis]:
                visible = (visible[1], visible[0])
            abandoned.append(visible)
            _record(journal, "abandoned_segment", source_step=source_step,
                    branch=side,
                    start=list(visible[0]), end=list(visible[1]),
                    length_mm=second - first)
    return [[list(first), list(second)] for first, second in abandoned]


def arrowhead_segments(start, end, spacing, min_segment):
    """Two 45-degree wings at the travel endpoint of a diagnostic segment."""
    length = _distance(start, end)
    if length <= TOLERANCE:
        return []
    depth = min(spacing / 4.0, length / 3.0)
    if depth * math.sqrt(2.0) <= min_segment + TOLERANCE:
        return []
    forward = ((end[0] - start[0]) / length,
               (end[1] - start[1]) / length)
    side = (-forward[1], forward[0])
    return [([end[0] - forward[0] * depth + sign * side[0] * depth,
              end[1] - forward[1] * depth + sign * side[1] * depth],
             list(end)) for sign in (-1, 1)]


def _recovery_distance_allowed(distance, length, blocker, min_segment):
    if distance <= min_segment + TOLERANCE:
        return False
    if blocker == "boundary":
        return distance <= length + TOLERANCE
    return distance < length - min_segment - TOLERANCE


def _recovery_checkpoints(original, first_side, contour, spacing,
                          side_order):
    """Anchor return turns to the transverse edges at the U entrance."""
    first = original[first_side]
    start = first["segments"][-1][0]
    heading = first["heading"]
    axis = 0 if abs(heading[0]) > TOLERANCE else 1
    cross = 1 - axis
    sources = []
    for side in ("right", "left"):
        sources.extend(("pipe_edge", side, segment)
                       for segment in original[side]["segments"][:-1])
    sources.extend(("contour_edge", None, segment)
                   for segment in zip(contour, contour[1:] + contour[:1]))
    anchors = []
    for turn_priority, (turn_name, direction) in enumerate(
            item for item in side_order if item[0] != "forward"):
        for kind, side, segment in sources:
            a, b = segment
            if abs(a[axis] - b[axis]) > TOLERANCE:
                continue
            side_reach = max((a[cross] - start[cross]) * direction[cross],
                             (b[cross] - start[cross]) * direction[cross])
            if side_reach <= TOLERANCE:
                continue
            clearance = spacing if kind == "pipe_edge" else 0.0
            distance = ((a[axis] - heading[axis] * clearance -
                         start[axis]) * heading[axis])
            anchors.append({"distance_mm": distance,
                            "source": kind, "source_branch": side,
                            "source_segment": [list(a), list(b)],
                            "turn": turn_name,
                            "turn_priority": turn_priority})
    anchors.sort(key=lambda item: (-item["distance_mm"],
                                   item["turn_priority"]))
    unique = []
    for candidate in anchors:
        if not any(item["turn"] == candidate["turn"] and
                   abs(item["distance_mm"] - candidate["distance_mm"]) <= TOLERANCE
                   for item in unique):
            unique.append(candidate)
    first_length = _distance(*first["segments"][-1])
    boundary_end = first.get("last_blocker") == "boundary"
    limit = (first_length + TOLERANCE if boundary_end else
             first_length - TOLERANCE)
    for step in range(int(limit / spacing), 0, -1):
        unique.append({"distance_mm": step * spacing,
                       "source": "segment_grid", "source_branch": None,
                       "source_segment": None, "turn": None,
                       "turn_priority": None})
    return unique


def _recover_pair_side(snapshots, first_side, second_side, contour,
                       spacing, offset, min_segment, max_turns,
                       clockwise, journal, allowed_turn,
                       minimum_turn_room=None, excluded=None):
    for snapshot in reversed(snapshots):
        original = snapshot["branches"]
        first_segment = original[first_side]["segments"][-1]
        second_segment = original[second_side]["segments"][-1]
        first_length = _distance(*first_segment)
        second_length = _distance(*second_segment)
        first_heading = original[first_side]["heading"]
        second_heading = original[second_side]["heading"]
        if (abs(first_heading[0] - second_heading[0]) > TOLERANCE or
                abs(first_heading[1] - second_heading[1]) > TOLERANCE):
            continue
        if (original[first_side]["turns_used"] >= max_turns or
                original[second_side]["turns_used"] >= max_turns):
            continue
        index = 0 if abs(first_heading[0]) > TOLERANCE else 1
        side_order = _contour_directions(
            original[first_side], contour, clockwise, spacing)
        checkpoints = _recovery_checkpoints(
            original, first_side, contour, spacing, side_order)
        for checkpoint in checkpoints:
            first_distance = checkpoint["distance_mm"]
            if not _recovery_distance_allowed(
                    first_distance, first_length,
                    original[first_side].get("last_blocker"), min_segment):
                continue
            first_point = _advance(first_segment[0], first_heading,
                                   first_distance)
            second_coordinate = first_point[index] - first_heading[index] * spacing
            second_distance = ((second_coordinate - second_segment[0][index]) *
                               second_heading[index])
            if not _recovery_distance_allowed(
                    second_distance, second_length,
                    original[second_side].get("last_blocker"), min_segment):
                continue
            second_point = _advance(second_segment[0], second_heading,
                                    second_distance)
            for turn_name, turn_direction in side_order:
                if turn_name == "forward":
                    continue
                if turn_name != allowed_turn:
                    continue
                if (checkpoint["turn"] is not None and
                        checkpoint["turn"] != turn_name):
                    continue
                _record(journal, "pair_recovery_checkpoint_probe",
                        round=snapshot["round"], candidate=turn_name,
                        first_distance_mm=first_distance,
                        second_distance_mm=second_distance,
                        source=checkpoint["source"],
                        source_branch=checkpoint["source_branch"],
                        source_segment=checkpoint["source_segment"])
                key = (snapshot["round"], first_point, second_point,
                       turn_name)
                if excluded is not None and key in excluded:
                    continue
                trial = copy.deepcopy(original)
                for side, point in ((first_side, first_point),
                                    (second_side, second_point)):
                    branch = trial[side]
                    branch["vertices"][-1] = point
                    branch["segments"][-1] = (branch["segments"][-1][0], point)
                    branch["contour_anchor"] = _contour_projection(
                        point, contour, clockwise)
                    branch["stop_reason"] = None
                    branch["pending_turn"] = turn_name
                if minimum_turn_room is not None:
                    enough_room = True
                    for side, other_side in ((first_side, second_side),
                                             (second_side, first_side)):
                        branch = trial[side]
                        result = probe_ray(
                            branch["vertices"][-1], turn_direction,
                            contour, branch["segments"],
                            _obstacles(trial[other_side], "other_branch"),
                            spacing)
                        _record(journal, "pair_recovery_room_probe",
                                round=snapshot["round"], branch=side,
                                candidate=turn_name,
                                threshold_mm=minimum_turn_room,
                                result=result)
                        if result["available_mm"] + TOLERANCE < minimum_turn_room:
                            enough_room = False
                    if not enough_room:
                        continue
                probe_log = []
                first = trial[first_side]
                second = trial[second_side]
                first_moved = _walk_one(first, first_side, contour,
                                        _obstacles(second, "other_branch"),
                                        spacing, offset, min_segment,
                                        max_turns, probe_log,
                                        snapshot["round"] + 1, clockwise,
                                        minimum_turn_room or 0.0)
                if not first_moved:
                    continue
                reservation = _next_reservation(
                    first, contour, _obstacles(second, "other_branch"),
                    spacing, offset, min_segment, max_turns, probe_log,
                    first_side, clockwise)
                obstacles = _obstacles(first, "other_branch")
                if reservation is not None:
                    obstacles.append({"segment": reservation,
                                      "kind": "first_turn_reservation"})
                second_moved = _walk_one(
                    second, second_side, contour, obstacles, spacing,
                    offset, min_segment, max_turns, probe_log,
                    snapshot["round"] + 1, clockwise,
                    minimum_turn_room or 0.0)
                moves = [event for event in probe_log
                         if event["action"] == "segment"]
                if (not second_moved or len(moves) != 2 or
                        any(event["choice"] != turn_name for event in moves)):
                    continue
                join_trial = copy.deepcopy(trial)
                join = _finish_route(join_trial, contour, spacing,
                                     min_segment, probe_log, first_side)
                if not join["connected"]:
                    continue
                details = {"from_round": snapshot["round"],
                           "to_round": snapshot["round"] + 1,
                           "first_checkpoint_mm": first_distance,
                           "second_checkpoint_mm": second_distance,
                           "first_limit_kind": original[first_side].get("last_blocker"),
                           "second_limit_kind": original[second_side].get("last_blocker"),
                           "turn": turn_name,
                           "checkpoint_source": checkpoint["source"],
                           "anchor_branch": checkpoint["source_branch"],
                           "anchor_segment": checkpoint["source_segment"],
                           "reason": "paired_turn_recovery"}
                _record(journal, "pair_turn_recovery", **details)
                if excluded is not None:
                    excluded.add(key)
                for event in probe_log:
                    event = dict(event)
                    event.pop("step", None)
                    _record(journal, "recovery_" + event.pop("action"),
                            **event)
                return trial, join, details
    _record(journal, "pair_turn_recovery_unavailable")
    return None, None, None


def _recover_pair_forward(snapshot, first_side, second_side, contour,
                          spacing, offset, min_segment, max_turns,
                          clockwise, journal, excluded):
    key = (snapshot["round"], "forward")
    if excluded is not None and key in excluded:
        return None, None, None
    trial = copy.deepcopy(snapshot["branches"])
    first = trial[first_side]
    second = trial[second_side]
    if (first["turns_used"] >= max_turns or
            second["turns_used"] >= max_turns):
        return None, None, None
    first["stop_reason"] = None
    second["stop_reason"] = None
    first["pending_turn"] = "forward"
    second["pending_turn"] = "forward"
    probe_log = []
    first_moved = _walk_one(
        first, first_side, contour, _obstacles(second, "other_branch"),
        spacing, offset, min_segment, max_turns, probe_log,
        snapshot["round"] + 1, clockwise)
    second_moved = False
    if first_moved:
        reservation = _next_reservation(
            first, contour, _obstacles(second, "other_branch"),
            spacing, offset, min_segment, max_turns, probe_log,
            first_side, clockwise)
        obstacles = _obstacles(first, "other_branch")
        if reservation is not None:
            obstacles.append({"segment": reservation,
                              "kind": "first_turn_reservation"})
        second_moved = _walk_one(
            second, second_side, contour, obstacles, spacing, offset,
            min_segment, max_turns, probe_log,
            snapshot["round"] + 1, clockwise)
    moves = [event for event in probe_log if event["action"] == "segment"]
    parallel_forward = (first_moved and second_moved and len(moves) == 2 and
                        all(event["choice"] == "forward" for event in moves))
    join = {"connected": False, "reason": "incomplete_pair"}
    if parallel_forward:
        join = _finish_route(copy.deepcopy(trial), contour, spacing,
                             min_segment, probe_log, first_side)
    _record(journal, "pair_forward_recovery_probe",
            from_round=snapshot["round"],
            first_moved=first_moved, second_moved=second_moved,
            choices={event["branch"]: event["choice"] for event in moves},
            connected=join["connected"])
    if not parallel_forward:
        return None, None, None
    details = {"from_round": snapshot["round"],
               "to_round": snapshot["round"] + 1,
               "turn": "forward", "reason": "paired_forward_recovery"}
    for side, other_side in ((first_side, second_side),
                             (second_side, first_side)):
        previous = snapshot["branches"][side]
        prior_obstacles = _obstacles(snapshot["branches"][other_side],
                                    "other_branch")
        ordered = _contour_directions(previous, contour, clockwise, spacing)
        if not ordered:
            continue
        primary_name, primary_direction = ordered[0]
        rejected = probe_ray(previous["vertices"][-1], primary_direction,
                             contour, previous["segments"], prior_obstacles,
                             spacing)
        trial[side]["rejected_primary_obstacle"] = rejected.get(
            "blocker_segment")
        _record(journal, "rejected_opening_saved", branch=side,
                candidate=primary_name,
                at=list(previous["vertices"][-1]), result=rejected)
    _record(journal, "pair_forward_recovery", **details)
    if excluded is not None:
        excluded.add(key)
    for event in probe_log:
        event = dict(event)
        event.pop("step", None)
        _record(journal, "recovery_" + event.pop("action"), **event)
    return trial, join, details


def _recover_pair_turn(snapshots, first_side, second_side, contour,
                       spacing, offset, min_segment, max_turns,
                       clockwise, journal, minimum_turn_room=None,
                       excluded=None, require_join=True):
    if not snapshots:
        return None, None, None
    for snapshot in reversed(snapshots):
        order = [item[0] for item in _contour_directions(
            snapshot["branches"][first_side], contour, clockwise, spacing)]
        rejected = set(event["choice"] for event in journal
                       if event["action"] == "segment" and
                       event.get("round") == snapshot["round"] + 1 and
                       event.get("branch") == first_side)
        # The follower can leave a pocket on a different side from the leader.
        partner_order = [None] + [item[0] for item in _contour_directions(
            snapshot["branches"][second_side], contour, clockwise, spacing)]
        for choice, partner_choice in ((a, b) for a in order for b in partner_order):
            if choice in rejected:
                continue
            key = (snapshot["round"], "vertex", choice, partner_choice)
            if excluded is not None and key in excluded:
                continue
            trial = copy.deepcopy(snapshot["branches"])
            first = trial[first_side]
            second = trial[second_side]
            first["stop_reason"] = None
            second["stop_reason"] = None
            if partner_choice is not None:
                second["recovery_only_direction"] = partner_choice
            first["pending_turn"] = choice
            first["recovery_only_direction"] = choice
            probe_log = []
            first_moved = _walk_one(
                first, first_side, contour,
                _obstacles(second, "other_branch"), spacing, offset,
                min_segment, max_turns, probe_log,
                snapshot["round"] + 1, clockwise)
            moves = [event for event in probe_log
                     if event["action"] == "segment"]
            _record(journal, "pair_vertex_probe", round=snapshot["round"],
                    candidate=choice, first_moved=first_moved,
                    first_choice=(moves[0]["choice"] if moves else None))
            if not first_moved or not moves or moves[0]["choice"] != choice:
                continue
            reservation = _next_reservation(
                first, contour, _obstacles(second, "other_branch"),
                spacing, offset, min_segment, max_turns, probe_log,
                first_side, clockwise)
            obstacles = _obstacles(first, "other_branch")
            if reservation is not None:
                obstacles.append({"segment": reservation,
                                  "kind": "first_turn_reservation"})
            second_moved = _walk_one(
                second, second_side, contour, obstacles, spacing, offset,
                min_segment, max_turns, probe_log,
                snapshot["round"] + 1, clockwise)
            _record(journal, "pair_vertex_second_probe",
                    round=snapshot["round"], candidate=choice,
                    second_moved=second_moved)
            if not second_moved:
                continue
            joined = _finish_route(copy.deepcopy(trial), contour, spacing,
                                   min_segment, probe_log, first_side)
            _record(journal, "pair_vertex_join_probe",
                    round=snapshot["round"], candidate=choice,
                    connected=joined["connected"],
                    reason=joined.get("reason"))
            # Speculative continuations are validated as complete routes;
            # they need not be connectable immediately after this one step.
            if require_join and not joined["connected"]:
                continue
            details = {"from_round": snapshot["round"],
                       "to_round": snapshot["round"] + 1,
                       "turn": choice,
                       "reason": "paired_vertex_recovery",
                       "partner_turn": partner_choice}
            _record(journal, "pair_vertex_recovery", **details)
            if excluded is not None:
                excluded.add(key)
            for event in probe_log:
                event = dict(event)
                event.pop("step", None)
                _record(journal, "recovery_" + event.pop("action"),
                        **event)
            return trial, joined, details
        for choice in order:
            if choice == "forward":
                result = _recover_pair_forward(
                    snapshot, first_side, second_side, contour, spacing,
                    offset, min_segment, max_turns, clockwise, journal,
                    excluded)
            else:
                result = _recover_pair_side(
                    [snapshot], first_side, second_side, contour, spacing,
                    offset, min_segment, max_turns, clockwise, journal,
                    choice, minimum_turn_room, excluded)
            if result[0] is not None:
                return result
    return None, None, None


def _lead_then_catch(branches, first_side, second_side, contour,
                     spacing, offset, min_segment, max_turns,
                     clockwise, round_index):
    """Let the first branch clear the exit, then let the second catch up."""
    trial = copy.deepcopy(branches)
    log = []
    first = trial[first_side]
    second = trial[second_side]
    first_obstacles = _obstacles(second, "other_branch")
    probe = probe_ray(first["vertices"][-1], first["heading"],
                      contour, first["segments"], first_obstacles, spacing)
    required = offset if probe["blocker"] == "boundary" else spacing
    _record(log, "recovery_lead_forward_probe", branch=first_side,
            round=round_index, result=probe, required_mm=required)
    if not _can_travel(probe["available_mm"], required, min_segment):
        return None, None, log
    first["pending_turn"] = "forward"
    first_moved = _walk_one(
        first, first_side, contour, first_obstacles, spacing,
        offset, min_segment, max_turns, log, round_index,
        clockwise, probe["available_mm"])
    if not first_moved or not any(event["action"] == "segment" and
                                  event["branch"] == first_side and
                                  event["choice"] == "forward"
                                  for event in log):
        return None, None, log
    reservation = _next_reservation(
        first, contour, _obstacles(second, "other_branch"), spacing,
        offset, min_segment, max_turns, log, first_side, clockwise)
    obstacles = _obstacles(first, "other_branch")
    if reservation is not None:
        obstacles.append({"segment": reservation,
                          "kind": "first_turn_reservation"})
    probe = probe_ray(second["vertices"][-1], second["heading"],
                      contour, second["segments"], obstacles, spacing)
    required = offset if probe["blocker"] == "boundary" else spacing
    _record(log, "recovery_follow_forward_probe", branch=second_side,
            round=round_index, result=probe, required_mm=required)
    if not _can_travel(probe["available_mm"], required, min_segment):
        return None, None, log
    second["pending_turn"] = "forward"
    second_moved = _walk_one(
        second, second_side, contour, obstacles, spacing, offset,
        min_segment, max_turns, log, round_index, clockwise,
        probe["available_mm"])
    if not second_moved or not any(event["action"] == "segment" and
                                   event["branch"] == second_side and
                                   event["choice"] == "forward"
                                   for event in log):
        return None, None, log
    paired_snapshot = copy.deepcopy(trial)
    _record(log, "recovery_lead_skip_first", branch=first_side,
            round=round_index + 1)
    reservation = _next_reservation(
        first, contour, _obstacles(second, "other_branch"), spacing,
        offset, min_segment, max_turns, log, first_side, clockwise)
    obstacles = _obstacles(first, "other_branch")
    if reservation is not None:
        obstacles.append({"segment": reservation,
                          "kind": "first_turn_reservation"})
    caught_up = _walk_one(second, second_side, contour, obstacles,
                          spacing, offset, min_segment, max_turns, log,
                          round_index + 1, clockwise)
    if not caught_up:
        return None, None, log
    _record(log, "recovery_follower_caught_up", branch=second_side,
            round=round_index + 1)
    return trial, paired_snapshot, log


def _search_pair_completion(branches, snapshots, first_side, second_side,
                            contour, spacing, offset, min_segment, max_turns,
                            max_rounds, clockwise, round_index, journal,
                            search_budget=None):
    """Keep a safe finish while testing independent recovery continuations.

    At fixed spacing, extra valid route length represents additional coverage.
    A connectable recovery alone is not progress: compare its completed route
    with the saved finish, including the bridge, before committing anything.
    """
    def length(candidate, join):
        return (route_length_mm(candidate) +
                (_distance(*join["bridge"]) if join.get("bridge") else 0.0))

    best = copy.deepcopy(branches)
    best_log = []
    best_join = _finish_route(best, contour, spacing, min_segment,
                              best_log, first_side)
    best_length = length(best, best_join) if best_join["connected"] else -1.0
    terminal_log = []
    terminal, terminal_join = _terminal_pair_without_reservation(
        branches, first_side, second_side, contour, spacing, offset,
        min_segment, max_turns, clockwise, terminal_log, round_index)
    if terminal is not None and length(terminal, terminal_join) >= best_length:
        best, best_join, best_log = terminal, terminal_join, terminal_log
        best_length = length(best, best_join)
    excluded = set()
    selected_details = None
    attempt = 0
    # Bound speculative walking by the same caller-supplied round limit as
    # normal routing. Nested recovery searches share the same budget.
    if search_budget is None:
        search_budget = [max_rounds]
    while search_budget[0] > 0:
        search_budget[0] -= 1
        # Trial events must not affect rejection filters of later candidates.
        trial_log = list(journal)
        log_start = len(trial_log)
        recovered, recovered_join, details = _recover_pair_turn(
            snapshots, first_side, second_side, contour, spacing, offset,
            min_segment, max_turns, clockwise, trial_log, excluded=excluded,
            require_join=False)
        if recovered is None:
            break
        attempt += 1
        if details["turn"] != "forward":
            source = next(s["branches"] for s in snapshots
                          if s["round"] == details["from_round"])
            for side in (first_side, second_side):
                recovered[side]["returning_from_protrusion"] = source[side]["heading"]
        completed_join, later_details = _continue_after_recovery(
            recovered, first_side, second_side, contour, spacing, offset,
            min_segment, max_turns, max_rounds, clockwise,
            details["to_round"] + 1, trial_log, search_alternatives=True,
            search_budget=search_budget)
        valid, reason = (False, completed_join.get("reason"))
        if completed_join["connected"]:
            valid, reason = _valid_join(
                recovered["right"]["vertices"], recovered["left"]["vertices"],
                completed_join.get("bridge"), contour, spacing, min_segment)
        completed_length = length(recovered, completed_join) if valid else 0.0
        improves = valid and completed_length > best_length + TOLERANCE
        _record(journal, "recovery_completion_probe", attempt=attempt,
                from_round=details["from_round"], candidate=details,
                connected=completed_join["connected"], valid=valid,
                reason=(reason if not valid else
                        None if improves else "no_additional_coverage"),
                length_mm=completed_length,
                fallback_length_mm=best_length, accepted=improves)
        if improves:
            best, best_join = recovered, completed_join
            best_length = completed_length
            best_log = trial_log[log_start:]
            selected_details = later_details or details
            # A rollback is only a safe fallback, not proof of full coverage.
            if not any(event["action"] == "recovery_continuation_rollback"
                       for event in best_log):
                break
    if search_budget[0] <= 0:
        _record(journal, "recovery_search_limit", max_rounds=max_rounds,
                attempts=attempt)
    if not best_join["connected"]:
        return None, None, None
    for event in best_log:
        event = dict(event)
        event.pop("step", None)
        _record(journal, event.pop("action"), **event)
    _record(journal, "pair_completion_selected", round=round_index,
            method=("recovery_continuation" if selected_details else "saved_finish"),
            attempts=attempt, length_mm=length(best, best_join))
    return best, best_join, selected_details


def _continue_after_recovery(branches, first_side, second_side, contour,
                             spacing, offset, min_segment, max_turns,
                             max_rounds, clockwise, start_round, journal,
                             search_alternatives=True, search_budget=None):
    snapshots = [{"round": start_round - 1, "branches": copy.deepcopy(branches)}]
    used_recoveries = set()
    latest_recovery = None
    joined_trial = copy.deepcopy(branches)
    last_join = _finish_route(joined_trial, contour, spacing,
                              min_segment, journal, first_side)
    last_vertices = {side: list(joined_trial[side]["vertices"])
                     for side in ("right", "left")}
    last_turns = {side: branches[side]["turns_used"]
                  for side in ("right", "left")}
    last_anchors = {side: branches[side].get("contour_anchor")
                    for side in ("right", "left")}
    pair_diverged = False
    continuation_round = start_round
    if (start_round < max_rounds and
            branches[first_side].get("returning_from_protrusion") is not None and
            branches[second_side].get("returning_from_protrusion") is not None):
        lead, paired_snapshot, trial_log = _lead_then_catch(
            branches, first_side, second_side, contour, spacing, offset,
            min_segment, max_turns, clockwise, start_round)
        if lead is not None:
            branches.clear()
            branches.update(lead)
            snapshots.append({"round": start_round,
                              "branches": paired_snapshot})
            for event in trial_log:
                event = dict(event)
                event.pop("step", None)
                _record(journal, event.pop("action"), **event)
            joined_trial = copy.deepcopy(branches)
            candidate_join = _finish_route(joined_trial, contour, spacing,
                                           min_segment, journal, first_side)
            if candidate_join["connected"]:
                last_join = candidate_join
                last_vertices = {side: list(joined_trial[side]["vertices"])
                                 for side in ("right", "left")}
                last_turns = {side: branches[side]["turns_used"]
                              for side in ("right", "left")}
                last_anchors = {side: branches[side].get("contour_anchor")
                                for side in ("right", "left")}
            continuation_round += 2
        else:
            _record(journal, "recovery_lead_unavailable",
                    round=start_round,
                    probes=[event for event in trial_log if
                            event["action"] in ("recovery_lead_forward_probe",
                                                "recovery_follow_forward_probe")])
    for round_index in range(continuation_round, max_rounds + 1):
        if search_budget is not None:
            if search_budget[0] <= 0:
                break
            search_budget[0] -= 1
        first = branches[first_side]
        second = branches[second_side]
        first_moved = _walk_one(first, first_side, contour,
                                _obstacles(second, "other_branch"),
                                spacing, offset, min_segment, max_turns,
                                journal, round_index, clockwise)
        reservation = _next_reservation(
            first, contour, _obstacles(second, "other_branch"),
            spacing, offset, min_segment, max_turns, journal,
            first_side, clockwise)
        obstacles = _obstacles(first, "other_branch")
        if reservation is not None:
            obstacles.append({"segment": reservation,
                              "kind": "first_turn_reservation"})
        second_moved = _walk_one(second, second_side, contour,
                                 obstacles, spacing, offset, min_segment,
                                 max_turns, journal, round_index, clockwise)
        if first_moved and second_moved:
            snapshots.append({"round": round_index,
                              "branches": copy.deepcopy(branches)})
        trial = copy.deepcopy(branches)
        candidate_join = _finish_route(trial, contour, spacing,
                                       min_segment, journal, first_side)
        _record(journal, "recovery_continuation", round=round_index,
                first_moved=first_moved, second_moved=second_moved,
                connected=candidate_join["connected"])
        if (candidate_join["connected"] and
                first_moved != second_moved and
                branches[first_side]["turns_used"] ==
                branches[second_side]["turns_used"] and
                not _walk_one(
                    copy.deepcopy(branches[first_side if first_moved else
                                           second_side]),
                    first_side if first_moved else second_side,
                    contour,
                    _obstacles(branches[second_side if first_moved else
                                        first_side], "other_branch"),
                    spacing, offset, min_segment, max_turns, [],
                    round_index + 1, clockwise)):
            first_vertices = trial[first_side]["vertices"]
            second_end = trial[second_side]["vertices"][-1]
            turn_direction = (_right(branches[first_side]["heading"])
                              if clockwise else
                              _left(branches[first_side]["heading"]))
            turn_run = ((second_end[0] - first_vertices[-1][0]) *
                        turn_direction[0] +
                        (second_end[1] - first_vertices[-1][1]) *
                        turn_direction[1])
            if (turn_run > min_segment + TOLERANCE and
                    _distance(_advance(first_vertices[-1],
                                       turn_direction, turn_run),
                              second_end) <= TOLERANCE):
                direct = copy.deepcopy(trial)
                direct[first_side]["vertices"].append(second_end)
                valid, reason = _valid_join(
                    direct["right"]["vertices"],
                    direct["left"]["vertices"], None, contour,
                    spacing, min_segment)
                if valid:
                    direct[first_side]["turns_used"] += 1
                    trial = direct
                    candidate_join = {"connected": True,
                                      "method": "terminal_turn",
                                      "bridge": None}
            branches.clear()
            branches.update(trial)
            _record(journal, "recovery_terminal_pair_complete",
                    round=round_index, method=candidate_join["method"])
            return candidate_join, latest_recovery
        if (first_moved or second_moved) and candidate_join["connected"]:
            last_join = candidate_join
            last_vertices = {side: list(trial[side]["vertices"])
                             for side in ("right", "left")}
            last_turns = {side: branches[side]["turns_used"]
                          for side in ("right", "left")}
            last_anchors = {side: branches[side].get("contour_anchor")
                            for side in ("right", "left")}
        tight_sides = []
        if (first_moved and second_moved and
                first["turns_used"] < max_turns and
                second["turns_used"] < max_turns):
            tight_sides = _paired_turn_room(
                branches, contour, spacing, offset, journal, round_index)
        if tight_sides or not (first_moved and second_moved):
            if _paired_next_step_available(
                    branches, first_side, second_side, contour,
                    spacing, offset, min_segment, max_turns,
                    clockwise, journal, round_index):
                _record(journal, "paired_turn_room_recovery_deferred",
                        round=round_index, tight_branches=tight_sides)
                tight_sides = []
            else:
                if search_alternatives and (search_budget is None or
                                            not (first_moved and second_moved)):
                    completed, completed_join, details = _search_pair_completion(
                        branches, snapshots, first_side, second_side, contour,
                        spacing, offset, min_segment, max_turns, max_rounds,
                        clockwise, round_index, journal, search_budget=search_budget)
                    if completed is not None:
                        branches.clear()
                        branches.update(completed)
                        return completed_join, details or latest_recovery
                terminal, terminal_join = _terminal_pair_without_reservation(
                    branches, first_side, second_side, contour, spacing,
                    offset, min_segment, max_turns, clockwise, journal,
                    round_index)
                if terminal is not None:
                    branches.clear()
                    branches.update(terminal)
                    return terminal_join, latest_recovery
        if tight_sides:
            inbound_headings = {side: branches[side]["heading"]
                                for side in ("right", "left")}
            recovered, recovered_join, details = _recover_pair_turn(
                snapshots, first_side, second_side, contour,
                spacing, offset, min_segment, max_turns, clockwise,
                journal, excluded=used_recoveries)
            if recovered is not None:
                if details["turn"] != "forward":
                    for side in ("right", "left"):
                        recovered[side]["returning_from_protrusion"] = inbound_headings[side]
                branches.clear()
                branches.update(recovered)
                snapshots = []
                joined_trial = copy.deepcopy(branches)
                candidate_join = _finish_route(
                    joined_trial, contour, spacing, min_segment,
                    journal, first_side)
                if candidate_join["connected"]:
                    last_join = candidate_join
                    last_vertices = {side: list(joined_trial[side]["vertices"])
                                     for side in ("right", "left")}
                    last_turns = {side: branches[side]["turns_used"]
                                  for side in ("right", "left")}
                    last_anchors = {side: branches[side].get("contour_anchor")
                                    for side in ("right", "left")}
                _record(journal, "paired_turn_room_recovery_selected",
                        round=round_index, tight_branches=tight_sides,
                        inbound_headings=inbound_headings, **details)
                latest_recovery = details
                continue
        if ((first_moved and not second_moved) or
                (not first_moved and not second_moved) or
                (first["stop_reason"] is not None and
                 second["stop_reason"] is not None)):
            if first_moved != second_moved:
                pair_diverged = True
                _record(journal, "pair_progress_diverged",
                        round=round_index,
                        first_moved=first_moved,
                        second_moved=second_moved)
            break
    final_join = _finish_route(branches, contour, spacing,
                               min_segment, journal, first_side)
    if final_join["connected"] and not pair_diverged:
        return final_join, latest_recovery
    if last_join["connected"]:
        for side in ("right", "left"):
            branches[side]["vertices"] = last_vertices[side]
            branches[side]["turns_used"] = last_turns[side]
            branches[side]["contour_anchor"] = last_anchors[side]
            branches[side]["stop_reason"] = "pair_rollback"
        _record(journal, "recovery_continuation_rollback",
                turns_by_branch=last_turns,
                reason=("unpaired_step" if pair_diverged else
                        "no_valid_final_join"))
        return last_join, latest_recovery
    if pair_diverged:
        return {"connected": False, "reason": "unpaired_step"}, latest_recovery
    return final_join, latest_recovery


def _build_vector_route_once(polygon, right_start, left_start,
                             entry_direction, max_turns, offset_mm=200.0,
                             spacing_mm=150.0, min_segment_mm=1.0,
                             max_rounds=500, clockwise=False,
                             contour_guided=False, rebind_inner_support=False):
    """Alternate branches; try right, forward, and left in that order."""
    if isinstance(max_turns, bool) or int(max_turns) != max_turns or max_turns < 0:
        raise ValueError("max_turns_must_be_nonnegative_integer")
    if offset_mm < 0 or spacing_mm <= 0 or min_segment_mm < 0:
        raise ValueError("invalid_geometry_parameters")
    polygon = [tuple(map(float, point)) for point in polygon]
    right_start = tuple(map(float, right_start))
    left_start = tuple(map(float, left_start))
    normal = tuple(map(float, entry_direction))
    if normal not in ((1.0, 0.0), (-1.0, 0.0),
                      (0.0, 1.0), (0.0, -1.0)):
        raise ValueError("entry_direction_must_be_orthogonal_unit_vector")
    if len(polygon) < 4:
        raise ValueError("orthogonal_polygon_requires_four_vertices")
    contour = _offset_polygon(polygon, float(offset_mm))
    journal = []
    _record(journal, "input", polygon=[list(point) for point in polygon],
            right_start=list(right_start), left_start=list(left_start),
            entry_direction=list(normal), offset_mm=offset_mm,
            spacing_mm=spacing_mm, max_turns=max_turns,
            min_segment_mm=min_segment_mm,
            clockwise=clockwise,
            contour_guided=contour_guided,
            direction_policy=("contour_progress" if contour_guided else
                              "heading_priority"),
            direction_order=(["left", "forward", "right"] if clockwise else
                             ["right", "forward", "left"]))
    _record(journal, "offset_contour",
            vertices=[list(point) for point in contour])
    first_side = "left" if clockwise else "right"
    second_side = "right" if clockwise else "left"
    starts = {"right": right_start, "left": left_start}
    entry_targets = _paired_entry_alignment(
        polygon, contour, starts, normal, first_side, second_side,
        offset_mm, spacing_mm, clockwise, journal)
    if contour_guided:
        entry = entry_targets.get(first_side)
        if entry is None:
            entry = _correct_entry(
                _entry_at_depth(polygon, starts[first_side], normal,
                                offset_mm), normal, contour, spacing_mm)
        anchor = _contour_projection(entry, contour, clockwise)
        tangent = _contour_tangent(anchor, contour, clockwise)
        if tangent is not None and anchor["distance"] <= TOLERANCE:
            distance = _ray_boundary_distance(entry, tangent, contour)
            if min_segment_mm + TOLERANCE < distance < spacing_mm - TOLERANCE:
                corner = _advance(entry, tangent, distance)
                next_anchor = _contour_projection(
                    corner, contour, clockwise, anchor, spacing_mm,
                    force_next=True)
                if (next_anchor["along"] <= TOLERANCE and
                        next_anchor["distance"] <= TOLERANCE):
                    starts = {side: _advance(start, tangent, distance)
                              for side, start in starts.items()}
                    journal[0]["right_start"] = list(starts["right"])
                    journal[0]["left_start"] = list(starts["left"])
                    _record(journal, "start_pair_shift", distance_mm=distance,
                            direction=list(tangent),
                            right=list(starts["right"]),
                            left=list(starts["left"]))
                    entry_targets = _paired_entry_alignment(
                        polygon, contour, starts, normal, first_side,
                        second_side, offset_mm, spacing_mm, clockwise,
                        journal)
    branches = {}
    for side, depth in ((first_side, offset_mm),
                        (second_side, offset_mm + spacing_mm)):
        branches[side] = _initial_branch(polygon, contour, starts[side],
                                         normal, depth, spacing_mm,
                                         side, journal,
                                         entry_targets.get(side))
        branches[side]["contour_guided"] = contour_guided
        branches[side]["rebind_inner_support"] = rebind_inner_support
        if contour_guided:
            branches[side]["contour_anchor"] = _contour_projection(
                branches[side]["vertices"][-1], contour, clockwise)
            _record(journal, "contour_anchor_start", branch=side,
                    at=list(branches[side]["vertices"][-1]),
                    anchor=branches[side]["contour_anchor"])
    last_pair_checkpoint = None
    pair_snapshots = []
    resolved_completion = None
    for round_index in range(1, max_rounds + 1):
        first = branches[first_side]
        second = branches[second_side]
        first_moved = _walk_one(first, first_side, contour,
                                _obstacles(second, "other_branch"),
                                spacing_mm, offset_mm,
                                min_segment_mm, max_turns,
                                journal, round_index, clockwise)
        reservation = _next_reservation(first, contour,
                                        _obstacles(second, "other_branch"),
                                        spacing_mm, offset_mm,
                                        min_segment_mm,
                                        max_turns, journal, first_side,
                                        clockwise)
        second_obstacles = _obstacles(first, "other_branch")
        if reservation is not None:
            second_obstacles.append({"segment": reservation,
                                     "kind": "first_turn_reservation"})
        if (contour_guided and reservation is not None and
                first.get("pending_turn") is None):
            reserved_direction = (
                (reservation[1][0] - reservation[0][0]) /
                _distance(*reservation),
                (reservation[1][1] - reservation[0][1]) /
                _distance(*reservation))
            for name, direction in _contour_directions(
                    first, contour, clockwise, spacing_mm):
                if name == "forward" or direction == reserved_direction:
                    continue
                alternative = probe_ray(
                    first["vertices"][-1], direction, contour,
                    first["segments"], _obstacles(second, "other_branch"),
                    spacing_mm)
                required = (offset_mm if alternative["blocker"] ==
                            "boundary" else spacing_mm)
                if _can_travel(alternative["available_mm"], required,
                               min_segment_mm):
                    end = _advance(first["vertices"][-1], direction,
                                   min(spacing_mm,
                                       alternative["available_mm"]))
                    second_obstacles.append({
                        "segment": (first["vertices"][-1], end),
                        "kind": "first_turn_reservation"})
                    _record(journal, "reserve_alternative_turn",
                            branch=first_side, candidate=name,
                            start=list(first["vertices"][-1]),
                            end=list(end))
        second_moved = _walk_one(second, second_side, contour,
                                 second_obstacles, spacing_mm,
                                 offset_mm,
                                 min_segment_mm, max_turns,
                                 journal, round_index, clockwise)
        if first_moved and second_moved:
            pair_snapshots.append({"round": round_index,
                                   "branches": copy.deepcopy(branches)})
        trial = {side: {"vertices": list(branches[side]["vertices"])}
                 for side in ("right", "left")}
        checkpoint_join = _finish_route(trial, contour, spacing_mm,
                                        min_segment_mm, journal, first_side)
        _record(journal, "pair_checkpoint_probe", round=round_index,
                first_moved=first_moved, second_moved=second_moved,
                connected=checkpoint_join["connected"],
                turns_by_branch={side: branches[side]["turns_used"]
                                 for side in ("right", "left")})
        if (first_moved and second_moved and
                checkpoint_join["connected"]):
            last_pair_checkpoint = {
                "round": round_index, "join": checkpoint_join,
                "vertices": {side: list(trial[side]["vertices"])
                             for side in ("right", "left")},
                "turns": {side: branches[side]["turns_used"]
                          for side in ("right", "left")},
                "anchors": {side: branches[side].get("contour_anchor")
                            for side in ("right", "left")}}
            _record(journal, "pair_checkpoint_saved", round=round_index,
                    turns_by_branch=last_pair_checkpoint["turns"])
        tight_sides = []
        if (first_moved and second_moved and
                first["turns_used"] < max_turns and
                second["turns_used"] < max_turns):
            tight_sides = _paired_turn_room(
                branches, contour, spacing_mm, offset_mm,
                journal, round_index)
        if tight_sides:
            if _paired_next_step_available(
                    branches, first_side, second_side, contour,
                    spacing_mm, offset_mm, min_segment_mm, max_turns,
                    clockwise, journal, round_index):
                _record(journal, "paired_turn_room_recovery_deferred",
                        round=round_index, tight_branches=tight_sides)
                tight_sides = []
            else:
                completed, completed_join, details = _search_pair_completion(
                    branches, pair_snapshots, first_side, second_side, contour,
                    spacing_mm, offset_mm, min_segment_mm, max_turns,
                    max_rounds, clockwise, round_index, journal)
                if completed is not None:
                    branches = completed
                    resolved_completion = (completed_join, details)
                    break
        if first_moved != second_moved:
            _record(journal, "pair_progress_diverged", round=round_index,
                    first_moved=first_moved, second_moved=second_moved)
            break
        if not first_moved and not second_moved:
            break
        if (first["stop_reason"] is not None and
                second["stop_reason"] is not None):
            break
    else:
        for side in ("right", "left"):
            if branches[side]["stop_reason"] is None:
                branches[side]["stop_reason"] = "round_limit"
                _record(journal, "branch_stop", branch=side,
                        reason="round_limit", max_rounds=max_rounds)
    rollback = None
    if resolved_completion is not None:
        join, rollback = resolved_completion
    else:
        join = _finish_route(branches, contour, spacing_mm,
                             min_segment_mm, journal, first_side)
    if not join["connected"]:
        recovered, recovered_join, rollback = _recover_pair_turn(
            pair_snapshots, first_side, second_side, contour, spacing_mm,
            offset_mm, min_segment_mm, max_turns, clockwise, journal)
        if recovered is not None:
            branches = recovered
            join, continued_rollback = _continue_after_recovery(
                branches, first_side, second_side, contour, spacing_mm,
                offset_mm, min_segment_mm, max_turns, max_rounds,
                clockwise, rollback["to_round"] + 1, journal)
            if continued_rollback is not None:
                rollback = continued_rollback
    if not join["connected"] and last_pair_checkpoint is not None:
        rollback = {"from_round": round_index,
                    "to_round": last_pair_checkpoint["round"],
                    "reason": "last_connectable_pair"}
        for side in ("right", "left"):
            branches[side]["vertices"] = last_pair_checkpoint["vertices"][side]
            branches[side]["turns_used"] = last_pair_checkpoint["turns"][side]
            branches[side]["contour_anchor"] = last_pair_checkpoint["anchors"][side]
            branches[side]["stop_reason"] = "pair_rollback"
        join = last_pair_checkpoint["join"]
        _record(journal, "pair_rollback", **rollback)
    _simplify_terminal_route(branches, join, contour, spacing_mm,
                             min_segment_mm, journal)
    _simplify_pair_route(branches, join, contour, spacing_mm,
                         min_segment_mm, journal, first_side)
    abandoned = (_abandoned_segments(journal, branches, join,
                                     min_segment_mm)
                 if rollback is not None else [])
    _record(journal, "join_result", **join)
    turns = {side: branches[side]["turns_used"] for side in ("right", "left")}
    reasons = {side: branches[side]["stop_reason"] for side in ("right", "left")}
    _record(journal, "stop", turns_by_branch=turns,
            reasons=reasons)
    return {"branches": {side: {"vertices": branches[side]["vertices"],
                               "turns_used": branches[side]["turns_used"],
                               "stop_reason": branches[side]["stop_reason"]}
                         for side in ("right", "left")},
            "contour": contour, "journal": journal,
            "turns_by_branch": turns,
            "turns_used": turns["right"] + turns["left"],
            "stop_reason": reasons, "join": join,
            "rollback": rollback, "abandoned_segments": abandoned,
            "validated": False}


def _boundary_coverage(candidate, spacing_mm):
    coverage = []
    for a, b in zip(candidate["contour"], candidate["contour"][1:] + candidate["contour"][:1]):
        axis = 0 if abs(b[0]-a[0]) > TOLERANCE else 1
        low, high = sorted((a[axis], b[axis]))
        intervals = []
        for branch in candidate["branches"].values():
            for c, d in zip(branch["vertices"], branch["vertices"][1:]):
                if (abs(c[1-axis]-d[1-axis]) > TOLERANCE or
                        abs(c[1-axis]-a[1-axis]) > spacing_mm/2.0 + TOLERANCE):
                    continue
                start, end = max(low, min(c[axis], d[axis])), min(high, max(c[axis], d[axis]))
                if end > start:
                    intervals.append((start, end))
        total, end = 0.0, low
        for start, stop in sorted(intervals):
            total += max(0.0, stop-max(end, start))
            end = max(end, stop)
        coverage.append(total)
    return coverage


def _complete_inward_bypass(route, spacing, offset, minimum,
                            max_turns, max_rounds, clockwise):
    """Try paired inward detours before an outward turn at an obstacle.

    A follower must turn one spacing before the leader, otherwise its
    transverse leg crosses the leader's resumed pass. Search lengths from
    geometry on the spacing grid, then validate the complete pair.
    """
    first, second = ("left", "right") if clockwise else ("right", "left")
    original = copy.deepcopy(route["branches"])
    # Reopen simplified bypasses only in the search copy. Keep the accepted
    # route unless a complete, clear continuation improves it.
    for event in reversed(route["journal"]):
        if event["action"] != "pair_corner_simplified":
            continue
        vertices = original[event["branch"]]["vertices"]
        new_path = event["new_path"]
        for start in range(len(vertices)-len(new_path)+1):
            if all(_distance(a, b) <= TOLERANCE for a, b in
                   zip(vertices[start:start+len(new_path)], new_path)):
                vertices[start:start+len(new_path)] = [tuple(p) for p in event["old_path"]]
                break
    best = route
    best_length = route_length_mm(route["branches"])
    budget = [max_rounds]
    fv, sv = original[first]["vertices"], original[second]["vertices"]
    for index in range(2, len(fv)-1):
        a, b, c = fv[index-1:index+2]
        length = _distance(a, b)
        if length <= minimum:
            continue
        heading = tuple((b[k]-a[k])/length for k in (0, 1))
        inward = (heading[1], -heading[0]) if clockwise else (-heading[1], heading[0])
        outgoing_length = _distance(b, c)
        if outgoing_length <= minimum:
            continue
        outgoing = tuple((c[k]-b[k])/outgoing_length for k in (0, 1))
        # Only revisit outward corners; ordinary inner turns stay intact.
        if _distance(outgoing, (-inward[0], -inward[1])) > TOLERANCE:
            continue
        # At least three clockwise quarter turns distinguish the return
        # after the outer sweep from a first visit to a boundary protrusion.
        winding = 0.0
        for aa, bb, cc in zip(fv[:index], fv[1:index+1], fv[2:index+1]):
            ux, uy = bb[0]-aa[0], bb[1]-aa[1]
            vx, vy = cc[0]-bb[0], cc[1]-bb[1]
            winding += math.atan2(ux*vy-uy*vx, ux*vx+uy*vy)
        if winding * (-1 if clockwise else 1) < 1.5*math.pi - TOLERANCE:
            continue
        for other_index in range(2, len(sv)):
            u, v = sv[other_index-1:other_index+1]
            other_length = _distance(u, v)
            if other_length <= minimum:
                continue
            other_heading = tuple((v[k]-u[k])/other_length for k in (0, 1))
            lateral = sum((u[k]-b[k])*inward[k] for k in (0, 1))
            if (_distance(heading, other_heading) > TOLERANCE or
                    abs(lateral-spacing) > TOLERANCE):
                continue
            follower_end = tuple(b[k]-heading[k]*spacing+inward[k]*spacing for k in (0, 1))
            if not _on_segment(follower_end, u, v):
                continue
            prefix = copy.deepcopy(original)
            prefix[first]["vertices"] = list(fv[:index+1])
            prefix[second]["vertices"] = list(sv[:other_index]) + [follower_end]
            for branch in prefix.values():
                branch["segments"] = list(zip(branch["vertices"], branch["vertices"][1:]))
            forward = probe_ray(b, heading, route["contour"],
                prefix[first]["segments"], _obstacles(prefix[second], "other_branch"), spacing)
            # Only bypass a pipe within the width of a paired lane;
            # an unobstructed run must continue to its own contour corner.
            if (forward["blocker"] == "boundary" or
                    forward["available_mm"] > 2*spacing + TOLERANCE):
                continue
            available = min(probe_ray(prefix[side]["vertices"][-1], inward,
                route["contour"], prefix[side]["segments"],
                _obstacles(prefix[second if side == first else first], "other_branch"),
                spacing)["available_mm"] for side in (first, second))
            for step in range(1, int((available+TOLERANCE)/spacing)+1):
                if budget[0] <= 0:
                    return best
                budget[0] -= 1
                trial = copy.deepcopy(prefix)
                for side in (first, second):
                    branch = trial[side]
                    start = branch["vertices"][-1]
                    branch["vertices"].append(_advance(start, inward, step*spacing))
                    branch["segments"] = list(zip(branch["vertices"], branch["vertices"][1:]))
                    branch["heading"] = inward
                    branch["turns_used"] = sum(1 for aa, bb in zip(
                        branch["segments"], branch["segments"][1:])
                        if abs((aa[1][0]-aa[0][0])*(bb[1][1]-bb[0][1])-
                               (aa[1][1]-aa[0][1])*(bb[1][0]-bb[0][0])) > TOLERANCE)
                    choice = next(name for name, direction in _directions(inward, clockwise)
                                  if _distance(direction, heading) <= TOLERANCE)
                    branch.update(stop_reason=None, pending_turn=choice,
                        recovery_only_direction=choice, contour_guided=True,
                        contour_anchor=_contour_projection(branch["vertices"][-1],
                            route["contour"], clockwise),
                        returning_from_protrusion=None, rejected_primary_obstacle=None)
                if any(branch["turns_used"] >= max_turns for branch in trial.values()):
                    continue
                # Prefix clearance matters too, before any continuation runs.
                if not _valid_join(trial["right"]["vertices"],
                    trial["left"]["vertices"], None, route["contour"], spacing, minimum)[0]:
                    continue
                journal = []
                for side in (first, second):
                    start, end = trial[side]["segments"][-1]
                    _record(journal, "inward_bypass_segment", branch=side,
                            start=list(start), end=list(end), length_mm=step*spacing)
                join, details = _continue_after_recovery(trial, first, second,
                    route["contour"], spacing, offset, minimum, max_turns,
                    max_rounds, clockwise, 1, journal, search_alternatives=False,
                    search_budget=budget)
                if not join.get("connected"):
                    continue
                valid, reason = _valid_join(trial["right"]["vertices"],
                    trial["left"]["vertices"], join.get("bridge"),
                    route["contour"], spacing, minimum)
                length_mm = route_length_mm(trial)
                preserves_boundary = all(actual + TOLERANCE >= required
                    for actual, required in zip(_boundary_coverage(
                        {"contour": route["contour"], "branches": trial}, spacing),
                        _boundary_coverage({"contour": route["contour"], "branches": prefix}, spacing)))
                if valid and preserves_boundary and length_mm > best_length + 2*spacing + TOLERANCE:
                    best = copy.deepcopy(route)
                    best["branches"], best["join"] = trial, join
                    best["turns_by_branch"] = {side: trial[side]["turns_used"] for side in trial}
                    best["turns_used"] = sum(best["turns_by_branch"].values())
                    best["stop_reason"] = {side: trial[side]["stop_reason"] for side in trial}
                    for event in journal:
                        event = dict(event)
                        event.pop("step", None)
                        _record(best["journal"], event.pop("action"), **event)
                    _record(best["journal"], "inward_bypass_completed",
                            first_segment=index-1, second_segment=other_index-1,
                            depth_mm=step*spacing, length_mm=length_mm)
                    best["abandoned_segments"] = _abandoned_segments(best["journal"], trial, join, minimum)
                    best_length = length_mm
    return best


def _complete_entry_bypass(route, normal, spacing, offset, minimum,
                            max_turns, max_rounds, clockwise):
    """Resume the incoming direction after a short paired entry bypass.

    The follower can need an earlier corner while the leader clears the
    entry. Test such corners on the step grid, keeping the leader's return
    direction. Collinear checkpoint vertices must not become false corners.
    """
    first, second = ("left", "right") if clockwise else ("right", "left")
    original = copy.deepcopy(route["branches"])
    for branch in original.values():
        vertices = []
        for point in branch["vertices"]:
            while len(vertices) >= 2:
                a, b = vertices[-2:]
                u, v = (b[0]-a[0], b[1]-a[1]), (point[0]-b[0], point[1]-b[1])
                if (abs(u[0]*v[1]-u[1]*v[0]) > TOLERANCE or
                        u[0]*v[0]+u[1]*v[1] <= 0):
                    break
                vertices.pop()
            vertices.append(point)
        if len(vertices) < 3:
            return route
        branch["vertices"] = vertices
    vertices = original[first]["vertices"]
    a, b, c = vertices[-3:]
    length, incoming = _distance(b, c), _distance(a, b)
    heading = ((c[0]-b[0])/length, (c[1]-b[1])/length)
    resume = ((b[0]-a[0])/incoming, (b[1]-a[1])/incoming)
    if (length > 2*spacing + TOLERANCE or _distance(heading, normal) > TOLERANCE
            or abs(sum(heading[k]*resume[k] for k in (0, 1))) > TOLERANCE):
        return route
    best = route
    best_length = route_length_mm(route["branches"]) + (
        _distance(*route["join"]["bridge"]) if route["join"].get("bridge") else 0)
    follower = original[second]["vertices"]
    a, b = follower[-2:]
    length = _distance(a, b)
    heading = ((b[0]-a[0])/length, (b[1]-a[1])/length)
    budget = [max_rounds]
    for step in range(1, int((length-minimum)/spacing) + 1):
        if budget[0] <= 0:
            break
        trial = copy.deepcopy(original)
        trial[second]["vertices"][-1] = _advance(b, heading, -step*spacing)
        possible = True
        for side in (first, second):
            branch = trial[side]
            branch["segments"] = list(zip(branch["vertices"], branch["vertices"][1:]))
            start, end = branch["segments"][-1]
            distance = _distance(start, end)
            branch["heading"] = ((end[0]-start[0])/distance, (end[1]-start[1])/distance)
            choices = [name for name, direction in _directions(branch["heading"], clockwise)
                       if _distance(direction, resume) <= TOLERANCE]
            if not choices:
                possible = False
                break
            branch.update(stop_reason=None, pending_turn=choices[0],
                          recovery_only_direction=choices[0], contour_guided=False)
        if not possible:
            continue
        log = []
        join, details = _continue_after_recovery(trial, first, second,
            route["contour"], spacing, offset, minimum, max_turns, max_rounds,
            clockwise, 1, log, search_alternatives=False, search_budget=budget)
        if not join.get("connected"):
            continue
        _simplify_pair_route(trial, join, route["contour"], spacing, minimum, log, first)
        valid, reason = _valid_join(trial["right"]["vertices"], trial["left"]["vertices"],
                                    join.get("bridge"), route["contour"], spacing, minimum)
        length_mm = route_length_mm(trial) + (_distance(*join["bridge"]) if join.get("bridge") else 0)
        if valid and length_mm > best_length + 2*spacing + TOLERANCE:
            best = copy.deepcopy(route)
            best["branches"], best["join"] = trial, join
            best["turns_by_branch"] = {side: trial[side]["turns_used"] for side in trial}
            best["turns_used"] = sum(best["turns_by_branch"].values())
            best["stop_reason"] = {side: trial[side]["stop_reason"] for side in trial}
            for event in log:
                event = dict(event)
                event.pop("step", None)
                _record(best["journal"], event.pop("action"), **event)
            _record(best["journal"], "entry_bypass_completed", follower=second,
                    trim_mm=step*spacing, resume=list(resume), length_mm=length_mm)
            best["abandoned_segments"] = _abandoned_segments(best["journal"], trial, join, minimum)
            best_length = length_mm
            break
    return best


def _complete_terminal_pair(route, spacing, minimum, max_turns, clockwise):
    """Replace a premature, one-sided terminal jog with paired inner turns."""
    if not route["join"].get("connected"):
        return route
    branches = route["branches"]
    bridge = route["join"].get("bridge")
    # A one-spacing cap already closes a regular paired pass.
    if bridge is not None and abs(_distance(*bridge)-spacing) <= TOLERANCE:
        return route
    if bridge is None:
        for side, other in (("left", "right"), ("right", "left")):
            v, u = branches[side]["vertices"], branches[other]["vertices"]
            if (len(v) >= 3 and _distance(v[-1], u[-1]) <= TOLERANCE and
                    abs(_distance(v[-2], v[-1])-spacing) <= TOLERANCE and
                    abs(_segment_clearance((v[-3], v[-2]), (u[-2], u[-1]))-spacing) <= TOLERANCE):
                return route
    first, second = ("left", "right") if clockwise else ("right", "left")
    fv, sv = branches[first]["vertices"], branches[second]["vertices"]
    # Search the most recent paired pass first. Earlier passes remain intact.
    for i in range(len(fv)-2, 1, -1):
        a, b = fv[i-1:i+1]
        length = _distance(a, b)
        if length <= minimum:
            continue
        heading = tuple((b[k]-a[k])/length for k in (0, 1))
        inward = (heading[1], -heading[0]) if clockwise else (-heading[1], heading[0])
        for j in range(len(sv)-2, 1, -1):
            c, d = sv[j-1:j+1]
            other_length = _distance(c, d)
            if other_length <= minimum:
                continue
            other_heading = tuple((d[k]-c[k])/other_length for k in (0, 1))
            if (_distance(heading, other_heading) > TOLERANCE or
                    abs(sum((c[k]-a[k])*inward[k] for k in (0, 1))-spacing) > TOLERANCE or
                    abs(_segment_clearance((a, b), (c, d))-spacing) > TOLERANCE):
                continue
            trial = copy.deepcopy(branches)
            trial[first]["vertices"] = list(fv[:i])
            trial[second]["vertices"] = list(sv[:j])
            best = None
            best_length = -1
            log = []
            for turn in range(max_turns + 1):
                for branch in trial.values():
                    vs = branch["vertices"]
                    branch["segments"] = list(zip(vs, vs[1:]))
                starts = {side: trial[side]["vertices"][-1] for side in (first, second)}
                delta = sum((starts[second][k]-starts[first][k])*heading[k] for k in (0, 1))
                limits = {side: probe_ray(starts[side], heading, route["contour"],
                    trial[side]["segments"], _obstacles(trial[second if side == first else first],
                    "other_branch"), spacing)["available_mm"] for side in (first, second)}
                distance = min(limits[first], limits[second]+spacing+delta)
                distances = {first: distance, second: distance-spacing-delta}
                if min(distances.values()) < max(spacing, minimum)-TOLERANCE:
                    break
                for side in (first, second):
                    trial[side]["vertices"].append(_advance(starts[side], heading, distances[side]))
                if not _valid_join(trial["right"]["vertices"], trial["left"]["vertices"],
                                   None, route["contour"], spacing, minimum)[0]:
                    break
                closed = copy.deepcopy(trial)
                join = _finish_route(closed, route["contour"], spacing, minimum, log, first)
                if join.get("connected"):
                    for branch in closed.values():
                        vs = branch["vertices"]
                        branch["segments"] = list(zip(vs, vs[1:]))
                        branch["turns_used"] = sum(1 for aa, bb, cc in zip(vs, vs[1:], vs[2:])
                            if abs((bb[0]-aa[0])*(cc[1]-bb[1])-(bb[1]-aa[1])*(cc[0]-bb[0])) > TOLERANCE)
                        branch["heading"] = heading
                        branch["stop_reason"] = "paired_terminal_cap"
                    total = route_length_mm(closed) + (_distance(*join["bridge"]) if join.get("bridge") else 0)
                    if max(branch["turns_used"] for branch in closed.values()) <= max_turns and total > best_length:
                        best, best_length = (closed, join, turn), total
                heading = inward
                inward = (heading[1], -heading[0]) if clockwise else (-heading[1], heading[0])
            if best is not None:
                closed, join, turns = best
                if any(actual + TOLERANCE < required for actual, required in zip(
                        _boundary_coverage({"contour": route["contour"], "branches": closed}, spacing),
                        _boundary_coverage(route, spacing))):
                    continue
                result = copy.deepcopy(route)
                result["branches"], result["join"], turns = best
                result["turns_by_branch"] = {side: result["branches"][side]["turns_used"] for side in branches}
                result["turns_used"] = sum(result["turns_by_branch"].values())
                result["stop_reason"] = {side: "paired_terminal_cap" for side in branches}
                if "length_mm" in result:
                    result["length_mm"] = route_length_mm(result["branches"])
                _record(result["journal"], "terminal_pair_completed", first_segment=i-1,
                        second_segment=j-1, paired_turns=turns, length_mm=best_length,
                        old_tail={first: fv[i-1:], second: sv[j-1:]},
                        new_tail={first: closed[first]["vertices"][i-1:],
                                  second: closed[second]["vertices"][j-1:]})
                result["abandoned_segments"] = _abandoned_segments(result["journal"],
                    result["branches"], result["join"], minimum)
                return result
    return route


def build_vector_route(polygon, right_start, left_start, entry_direction,
                       max_turns, offset_mm=200.0, spacing_mm=150.0,
                       min_segment_mm=1.0, max_rounds=500,
                       clockwise=False, contour_guided=False):
    route = _build_vector_route(polygon, right_start, left_start, entry_direction,
        max_turns, offset_mm, spacing_mm, min_segment_mm, max_rounds,
        clockwise, contour_guided)
    return _complete_terminal_pair(route, spacing_mm, min_segment_mm, max_turns, clockwise)


def _build_vector_route(polygon, right_start, left_start, entry_direction,
                       max_turns, offset_mm=200.0, spacing_mm=150.0,
                       min_segment_mm=1.0, max_rounds=500,
                       clockwise=False, contour_guided=False):
    arguments = (polygon, right_start, left_start, entry_direction,
                 max_turns, offset_mm, spacing_mm, min_segment_mm,
                 max_rounds, clockwise)
    route = _build_vector_route_once(*(arguments + (contour_guided,)))
    if not contour_guided:
        return route
    failure = None
    if not route["join"]["connected"]:
        failure = route["join"].get("reason", "no_join")
    else:
        try:
            start_at_calculated_entries(copy.deepcopy(route))
        except ValueError as error:
            failure = error.args[0] if error.args else "invalid_entry"
    if failure is None:
        # A valid join alone does not prove coverage. Compare independent
        # completions after entry trimming and pair simplification. Require
        # at least one full paired advance (one spacing on each branch).
        checked = start_at_calculated_entries(copy.deepcopy(route))
        best_length = route_length_mm(checked["branches"]) + (
            _distance(*checked["join"]["bridge"]) if checked["join"].get("bridge") else 0)
        required_coverage = _boundary_coverage(checked, spacing_mm)
        for guided, rebind in ((True, True), (False, False)):
            candidate = _build_vector_route_once(*(arguments + (guided, rebind)))
            if not candidate["join"]["connected"]:
                continue
            try:
                checked = start_at_calculated_entries(copy.deepcopy(candidate))
            except ValueError:
                continue
            valid, reason = _valid_join(checked["branches"]["right"]["vertices"],
                checked["branches"]["left"]["vertices"], checked["join"].get("bridge"),
                checked["contour"], spacing_mm, min_segment_mm)
            if contour_guided and not guided:
                # A heading-only completion must retain the paired order.
                # Extra length from mismatched turns is not spiral coverage.
                paired = True
                lv = checked["branches"]["left"]["vertices"]
                rv = checked["branches"]["right"]["vertices"]
                for index in range(1, min(len(lv), len(rv))-2):
                    a, b = lv[index:index+2]
                    c, d = rv[index:index+2]
                    u = tuple((b[k]-a[k])/_distance(a, b) for k in (0, 1))
                    v = tuple((d[k]-c[k])/_distance(c, d) for k in (0, 1))
                    if (_distance(u, v) > TOLERANCE or
                            abs(_segment_clearance((a, b), (c, d))-spacing_mm) > TOLERANCE):
                        paired = False
                        break
                if not paired:
                    continue
            length = route_length_mm(checked["branches"]) + (
                _distance(*checked["join"]["bridge"]) if checked["join"].get("bridge") else 0)
            preserves_boundary = all(actual + TOLERANCE >= required
                for actual, required in zip(_boundary_coverage(checked, spacing_mm), required_coverage))
            if valid and preserves_boundary and length > best_length + 2.0 * spacing_mm + TOLERANCE:
                _record(candidate["journal"], "coverage_completion_selected",
                        policy="inner_support" if guided else "heading_priority",
                        previous_length_mm=best_length, length_mm=length)
                route, best_length = candidate, length
        route = _complete_inward_bypass(route, spacing_mm, offset_mm,
            min_segment_mm, max_turns, max_rounds, clockwise)
        return _complete_entry_bypass(route, entry_direction, spacing_mm,
            offset_mm, min_segment_mm, max_turns, max_rounds, clockwise)
    established = _build_vector_route_once(*(arguments + (False,)))
    if established["join"]["connected"]:
        try:
            start_at_calculated_entries(copy.deepcopy(established))
        except ValueError:
            pass
        else:
            _record(established["journal"], "contour_route_fallback",
                    reason=failure)
            return established
    return route
