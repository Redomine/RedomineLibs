# -*- coding: utf-8 -*-
"""Diagnostic paired walk: check right, forward, then left at each endpoint."""
from __future__ import division

import math
import copy

from floor_heating_geometry import (TOLERANCE, _advance, _correct_entry,
                                  _distance, _inside, _offset_polygon,
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
            "last_blocker": None, "returning_from_protrusion": None}


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
    moves = [event for event in probe_log if event["action"] == "segment"]
    _record(journal, "pair_next_step_probe", round=round_index + 1,
            first_moved=first_moved, second_moved=second_moved,
            choices={event["branch"]: event["choice"] for event in moves},
            ends={side: list(trial[side]["vertices"][-1])
                  for side in (first_side, second_side)},
            connected=join["connected"], reason=join.get("reason"))
    return first_moved and second_moved and join["connected"]


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
    if first_moved and second_moved and equal_turns:
        join = _finish_route(trial, contour, spacing, min_segment,
                             probe_log, first_side)
    accepted = first_moved and second_moved and equal_turns and join["connected"]
    _record(journal, "terminal_pair_probe", round=round_index + 1,
            first_moved=first_moved, second_moved=second_moved,
            equal_turns=equal_turns, connected=join["connected"],
            reason=join.get("reason"), accepted=accepted,
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
    candidates = _directions(branch["heading"], clockwise)
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
                    distance + min_segment < travel - TOLERANCE):
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
    directions = list(_directions(branch["heading"], clockwise))
    pending_turn = branch.get("pending_turn")
    if pending_turn is not None:
        directions.sort(key=lambda item: 0 if item[0] == pending_turn else 1)
        _record(journal, "backtrack_turn_priority", branch=side,
                round=round_index, candidate=pending_turn)
        branch["pending_turn"] = None
    for name, direction in directions:
        result = probe_ray(point, direction, contour, branch["segments"],
                           other_obstacles, spacing)
        _record(journal, "direction_probe", branch=side,
                round=round_index, candidate=name, at=list(point),
                result=result)
        avoided = branch.get("rejected_primary_obstacle")
        primary_name = _directions(branch["heading"], clockwise)[0][0]
        if name == primary_name and avoided is not None:
            if _same_obstacle_segment(result.get("blocker_segment"), avoided):
                _record(journal, "rejected_dead_end_opening", branch=side,
                        round=round_index, candidate=name,
                        at=list(point), blocker_segment=avoided)
                continue
            branch["rejected_primary_obstacle"] = None
        required = offset if result["blocker"] == "boundary" else spacing
        if not _can_travel(result["available_mm"], required, min_segment):
            _record(journal, "direction_rejected", branch=side,
                    candidate=name, reason="dictating_distance_at_origin",
                    available_mm=result["available_mm"],
                    required_mm=max(min_segment, required))
            continue
        travel = result["available_mm"]
        primary_name, primary_direction = _directions(direction, clockwise)[0]
        secondary_name, secondary_direction = _directions(direction, clockwise)[2]
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
            directions = [(primary_name, primary_direction),
                          (secondary_name, secondary_direction)]
            for turn_name, turn_direction in directions:
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
                _record(journal, "intermediate_turn_probe", branch=side,
                        round=round_index, distance_mm=checkpoint,
                        at=list(sample), candidate=turn_name,
                        checkpoint_source=checkpoint_source,
                        checkpoint_obstacle_kind=checkpoint_kind,
                        result=side_probe, turn_available=available,
                        within_turn_limit=future_turn_allowed)
                run_ready = checkpoint + TOLERANCE >= minimum_turn_run
                if available and future_turn_allowed and run_ready:
                    if turn_name == secondary_name:
                        last_turn = (checkpoint, turn_name)
                        _record(journal, "backtrack_candidate", branch=side,
                                round=round_index, distance_mm=checkpoint,
                                next_direction=turn_name)
                    else:
                        travel = checkpoint
                        preferred_selected = True
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
            for exit_name, exit_direction in _directions(direction, clockwise):
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
        merged = _append_branch_segment(branch, target)
        branch["heading"] = direction
        branch["last_blocker"] = (result["blocker"] if
                                  abs(travel - result["available_mm"]) <= TOLERANCE
                                  else "intermediate_turn")
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


def _abandoned_segments(journal, branches, join, min_segment):
    """Visible portions of built segments absent from the final route."""
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
            history.extend((event["step"], (points[index - 1], points[index]))
                           for index in (1, 2))
        elif event["action"] in ("segment", "recovery_segment"):
            history.append((event["step"], (event["start"], event["end"])))
    for source_step, segment in history:
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
                        source_step=source_step, length_mm=second - first,
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
            (side_order[0], side_order[2])):
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
        side_order = _directions(first_heading, clockwise)
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
            for turn_name, turn_direction in (side_order[0], side_order[2]):
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
    if not parallel_forward or not join["connected"]:
        return None, None, None
    details = {"from_round": snapshot["round"],
               "to_round": snapshot["round"] + 1,
               "turn": "forward", "reason": "paired_forward_recovery"}
    for side, other_side in ((first_side, second_side),
                             (second_side, first_side)):
        previous = snapshot["branches"][side]
        prior_obstacles = _obstacles(snapshot["branches"][other_side],
                                    "other_branch")
        primary_name, primary_direction = _directions(
            previous["heading"], clockwise)[0]
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
                       excluded=None):
    if not snapshots:
        return None, None, None
    heading = snapshots[-1]["branches"][first_side]["heading"]
    order = [item[0] for item in _directions(heading, clockwise)]
    for snapshot in reversed(snapshots):
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


def _continue_after_recovery(branches, first_side, second_side, contour,
                             spacing, offset, min_segment, max_turns,
                             max_rounds, clockwise, start_round, journal):
    snapshots = []
    used_recoveries = set()
    latest_recovery = None
    joined_trial = copy.deepcopy(branches)
    last_join = _finish_route(joined_trial, contour, spacing,
                              min_segment, journal, first_side)
    last_vertices = {side: list(joined_trial[side]["vertices"])
                     for side in ("right", "left")}
    last_turns = {side: branches[side]["turns_used"]
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
            continuation_round += 2
        else:
            _record(journal, "recovery_lead_unavailable",
                    round=start_round,
                    probes=[event for event in trial_log if
                            event["action"] in ("recovery_lead_forward_probe",
                                                "recovery_follow_forward_probe")])
    for round_index in range(continuation_round, max_rounds + 1):
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
        if first_moved and second_moved and candidate_join["connected"]:
            last_join = candidate_join
            last_vertices = {side: list(trial[side]["vertices"])
                             for side in ("right", "left")}
            last_turns = {side: branches[side]["turns_used"]
                          for side in ("right", "left")}
        tight_sides = []
        if (first_moved and second_moved and
                first["turns_used"] < max_turns and
                second["turns_used"] < max_turns):
            tight_sides = _paired_turn_room(
                branches, contour, spacing, offset, journal, round_index)
        if tight_sides:
            if _paired_next_step_available(
                    branches, first_side, second_side, contour,
                    spacing, offset, min_segment, max_turns,
                    clockwise, journal, round_index):
                _record(journal, "paired_turn_room_recovery_deferred",
                        round=round_index, tight_branches=tight_sides)
                tight_sides = []
            else:
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
                last_join = _finish_route(joined_trial, contour, spacing,
                                          min_segment, journal, first_side)
                last_vertices = {side: list(joined_trial[side]["vertices"])
                                 for side in ("right", "left")}
                last_turns = {side: branches[side]["turns_used"]
                              for side in ("right", "left")}
                _record(journal, "paired_turn_room_recovery_selected",
                        round=round_index, tight_branches=tight_sides,
                        inbound_headings=inbound_headings, **details)
                latest_recovery = details
                continue
        if (first_moved != second_moved or
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
            branches[side]["stop_reason"] = "pair_rollback"
        _record(journal, "recovery_continuation_rollback",
                turns_by_branch=last_turns,
                reason=("unpaired_step" if pair_diverged else
                        "no_valid_final_join"))
        return last_join, latest_recovery
    if pair_diverged:
        return {"connected": False, "reason": "unpaired_step"}, latest_recovery
    return final_join, latest_recovery


def build_vector_route(polygon, right_start, left_start, entry_direction,
                       max_turns, offset_mm=200.0, spacing_mm=150.0,
                       min_segment_mm=1.0, max_rounds=500,
                       clockwise=False):
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
    branches = {}
    for side, depth in ((first_side, offset_mm),
                        (second_side, offset_mm + spacing_mm)):
        branches[side] = _initial_branch(polygon, contour, starts[side],
                                         normal, depth, spacing_mm,
                                         side, journal,
                                         entry_targets.get(side))
    last_pair_checkpoint = None
    pair_snapshots = []
    early_recovery = None
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
                terminal, terminal_join = _terminal_pair_without_reservation(
                    branches, first_side, second_side, contour, spacing_mm,
                    offset_mm, min_segment_mm, max_turns, clockwise,
                    journal, round_index)
                if terminal is not None:
                    branches = terminal
                    _record(journal, "terminal_pair_complete",
                            round=round_index + 1,
                            method=terminal_join["method"])
                    break
        if tight_sides:
            inbound_headings = {side: branches[side]["heading"]
                                for side in ("right", "left")}
            recovered, recovered_join, details = _recover_pair_turn(
                pair_snapshots, first_side, second_side, contour,
                spacing_mm, offset_mm, min_segment_mm, max_turns,
                clockwise, journal)
            if recovered is not None:
                if details["turn"] != "forward":
                    for side in ("right", "left"):
                        recovered[side]["returning_from_protrusion"] = inbound_headings[side]
                early_recovery = (recovered, recovered_join, details)
                _record(journal, "paired_turn_room_recovery_selected",
                        round=round_index, tight_branches=tight_sides,
                        inbound_headings=inbound_headings, **details)
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
    if early_recovery is not None:
        branches, recovered_join, rollback = early_recovery
        join, continued_rollback = _continue_after_recovery(
            branches, first_side, second_side, contour, spacing_mm,
            offset_mm, min_segment_mm, max_turns, max_rounds,
            clockwise, rollback["to_round"] + 1, journal)
        if continued_rollback is not None:
            rollback = continued_rollback
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
            branches[side]["stop_reason"] = "pair_rollback"
        join = last_pair_checkpoint["join"]
        _record(journal, "pair_rollback", **rollback)
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
