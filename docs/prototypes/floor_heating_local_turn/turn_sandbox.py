# -*- coding: utf-8 -*-
"""A deliberately diagnostic, turn-limited walk inside one orthogonal region."""
from __future__ import division

from local_turn_generator import (TOLERANCE, _advance, _available_length,
                                  _correct_entry, _distance, _entry_direction,
                                  _inside, _offset_polygon,
                                  _ray_boundary_distance, _segment_distance)


def _record(journal, action, **details):
    event = {"step": len(journal) + 1, "action": action}
    event.update(details)
    journal.append(event)


def _probe(point, direction, contour, segments, spacing, sample):
    length, blocker = _available_length(point, direction, contour, segments,
                                         spacing, sample)
    return {"direction": list(direction), "available_mm": length,
            "blocker": blocker}


def build_sandbox_route(polygon, start, max_turns, offset_mm=200.0,
                        spacing_mm=150.0, clockwise=True, max_legs=500):
    """Return the partial route and an ordered explanation of every decision.

    There is no automatic connection to an outlet and no validity guarantee.
    """
    if isinstance(max_turns, bool) or int(max_turns) != max_turns or max_turns < 0:
        raise ValueError("max_turns_must_be_nonnegative_integer")
    if offset_mm < 0 or spacing_mm <= 0 or max_legs < 1:
        raise ValueError("invalid_geometry_parameters")
    polygon = [tuple(map(float, point)) for point in polygon]
    start = tuple(map(float, start))
    if len(polygon) < 4:
        raise ValueError("orthogonal_polygon_requires_four_vertices")
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        dx, dy = abs(first[0] - second[0]), abs(first[1] - second[1])
        if (dx <= TOLERANCE and dy <= TOLERANCE) or (dx > TOLERANCE and dy > TOLERANCE):
            raise ValueError("polygon_must_have_nonzero_orthogonal_edges")
    journal = []
    _record(journal, "input", polygon=[list(point) for point in polygon],
            start=list(start), max_turns=max_turns, offset_mm=offset_mm,
            spacing_mm=spacing_mm, clockwise=bool(clockwise), max_legs=max_legs,
            turn_convention="prototype_clockwise_prefers_left_relative_to_travel")
    contour = _offset_polygon(polygon, float(offset_mm))
    _record(journal, "offset_contour", vertices=[list(point) for point in contour])
    normal = _entry_direction(polygon, start)
    _record(journal, "entry_normal", direction=list(normal),
            reason="nearest_side_of_region_bounds")
    if abs(normal[0]) <= TOLERANCE:
        edge = min(point[1] for point in contour) if normal[1] > 0 else max(point[1] for point in contour)
        entry = (start[0], edge + normal[1] * spacing_mm)
    else:
        edge = min(point[0] for point in contour) if normal[0] > 0 else max(point[0] for point in contour)
        entry = (edge + normal[0] * spacing_mm, start[1])
    _record(journal, "entry_target", point=list(entry),
            inside_offset_contour=_inside(entry, contour))
    corrected = _correct_entry(entry, normal, contour, spacing_mm)
    _record(journal, "entry_correction", before=list(entry), after=list(corrected),
            moved_mm=_distance(entry, corrected),
            inside_offset_contour=_inside(corrected, contour))
    if not _inside(corrected, contour):
        _record(journal, "stop", reason="entry_outside_offset_contour")
        return {"vertices": [start], "contour": contour, "journal": journal,
                "turns_used": 0, "stop_reason": "entry_outside_offset_contour",
                "validated": False}
    vertices = [start]
    if _distance(start, entry) > TOLERANCE:
        vertices.append(entry)
    if _distance(vertices[-1], corrected) > TOLERANCE:
        vertices.append(corrected)
    segments = [(vertices[index - 1], vertices[index])
                for index in range(1, len(vertices))]
    sample = max(10.0, min(30.0, spacing_mm / 5.0))
    minimum_leg = max(30.0, spacing_mm / 2.0)
    _record(journal, "walk_thresholds", sample_mm=sample,
            minimum_leg_mm=minimum_leg)
    options = ((-normal[1], normal[0]), (normal[1], -normal[0]))
    probes = [_probe(corrected, direction, contour, segments, spacing_mm, sample)
              for direction in options]
    _record(journal, "initial_direction_candidates", candidates=probes)
    if abs(probes[0]["available_mm"] - probes[1]["available_mm"]) > TOLERANCE:
        chosen_index = 0 if probes[0]["available_mm"] > probes[1]["available_mm"] else 1
        why = "longer_available_leg"
    else:
        chosen_index = 0 if clockwise else 1
        why = "equal_lengths_orientation_tiebreak"
    direction = options[chosen_index]
    _record(journal, "initial_direction", direction=list(direction), reason=why)
    turns = 0
    visited = set()
    stop_reason = "leg_limit"
    for leg_index in range(max_legs):
        current = vertices[-1]
        forward = _probe(current, direction, contour, segments, spacing_mm, sample)
        _record(journal, "forward_probe", leg=leg_index + 1,
                at=list(current), result=forward)
        length = forward["available_mm"]
        if length < minimum_leg:
            stop_reason = "forward_leg_too_short"
            _record(journal, "stop", reason=stop_reason, at=list(current),
                    available_mm=length, required_mm=minimum_leg)
            break
        target = _advance(current, direction, length)
        key = (round(target[0], 1), round(target[1], 1), direction)
        if key in visited:
            stop_reason = "cycle"
            _record(journal, "stop", reason=stop_reason, at=list(current),
                    attempted_target=list(target))
            break
        visited.add(key)
        vertices.append(target)
        segments.append((current, target))
        _record(journal, "straight", leg=leg_index + 1, start=list(current),
                end=list(target), direction=list(direction), length_mm=length,
                blocker=forward["blocker"])
        left = (-direction[1], direction[0])
        right = (direction[1], -direction[0])
        preferred = left if clockwise else right
        alternate = right if clockwise else left
        candidates = [_probe(target, candidate, contour, segments, spacing_mm, sample)
                      for candidate in (preferred, alternate)]
        _record(journal, "turn_candidates", at=list(target),
                preferred=list(preferred), candidates=candidates)
        eligible = [entry for entry in candidates
                    if entry["available_mm"] >= minimum_leg]
        if not eligible:
            stop_reason = "no_available_turn"
            _record(journal, "stop", reason=stop_reason, at=list(target),
                    required_mm=minimum_leg)
            break
        if turns >= max_turns:
            stop_reason = "turn_limit"
            _record(journal, "stop", reason=stop_reason, at=list(target),
                    turns_used=turns, max_turns=max_turns,
                    available_turns=[entry["direction"] for entry in eligible])
            break
        selected = eligible[0]
        direction = tuple(selected["direction"])
        turns += 1
        _record(journal, "turn", at=list(target), direction=list(direction),
                preference="preferred" if selected is candidates[0] else "alternate",
                side="right" if direction == right else "left",
                reason="first_direction_with_minimum_leg",
                turns_used=turns, next_available_mm=selected["available_mm"],
                next_blocker=selected["blocker"])
    else:
        _record(journal, "stop", reason=stop_reason, at=list(vertices[-1]),
                max_legs=max_legs)
    return {"vertices": vertices, "contour": contour, "journal": journal,
            "turns_used": turns, "stop_reason": stop_reason, "validated": False}


def _paired_probe(point, direction, contour, own, other, spacing, sample):
    limit = _ray_boundary_distance(point, direction, contour)
    if limit <= TOLERANCE:
        return {"direction": list(direction), "available_mm": 0.0,
                "blocker": "boundary"}
    distance = min(sample, limit)
    last_valid = 0.0
    blocker_index = None
    while distance <= limit + TOLERANCE:
        current = min(distance, limit)
        candidate = _advance(point, direction, current)
        if not _inside(candidate, contour):
            blocker = "boundary"
            break
        blocked_by = next((index for index, segment in enumerate(other)
                           if _segment_distance(candidate, segment) <
                           spacing - TOLERANCE), None)
        if blocked_by is not None:
            blocker = "other_branch"
            blocker_index = blocked_by
            break
        if any(_segment_distance(candidate, segment) < spacing - TOLERANCE
               for segment in own[:-2]):
            blocker = "same_branch"
            break
        last_valid = current
        if current >= limit - TOLERANCE:
            blocker = "boundary"
            break
        distance = min(distance + sample, limit)
    return {"direction": list(direction), "available_mm": last_valid,
            "blocker": blocker, "blocker_segment_index": blocker_index}


def _turn_option(origin, direction, contour, own, other,
                 spacing, sample, minimum_leg):
    option = _paired_probe(origin, direction, contour, own, other,
                           spacing, sample)
    option["eligible"] = option["available_mm"] >= minimum_leg
    if not option["eligible"]:
        option["rejection_reason"] = "shorter_than_minimum_leg"
    elif option["available_mm"] <= spacing + TOLERANCE:
        next_end = _advance(origin, direction, option["available_mm"])
        prospective = list(own)
        prospective.append((origin, next_end))
        follow_left = (-direction[1], direction[0])
        follow_right = (direction[1], -direction[0])
        follow_lengths = [_paired_probe(next_end, follow_direction,
                                        contour, prospective, other,
                                        spacing, sample)["available_mm"]
                          for follow_direction in (follow_left, follow_right)]
        option["follow_up_available_mm"] = follow_lengths
        if max(follow_lengths) < minimum_leg:
            option["eligible"] = False
            option["rejection_reason"] = "terminal_stub_without_exit"
    return option


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


def _preserves_first_turn(first, first_reservation, second, second_length,
                          contour, spacing, sample, minimum_leg, clockwise,
                          second_may_turn):
    second_start = second["vertices"][-1]
    second_end = _advance(second_start, second["direction"], second_length)
    second_segments = list(second["segments"])
    second_segments.append((second_start, second_end))
    first_obstacles = list(first["segments"])
    first_obstacles.extend(first_reservation["segments"])
    second_future = None
    second_future_direction = None
    if second_may_turn:
        second_left = (-second["direction"][1], second["direction"][0])
        second_right = (second["direction"][1], -second["direction"][0])
        second_options = ((second_right, second_left) if clockwise else
                          (second_left, second_right))
        for direction in second_options:
            option = _turn_option(second_end, direction, contour,
                                  second_segments, first_obstacles,
                                  spacing, sample, minimum_leg)
            if option["eligible"]:
                second_future_direction = direction
                second_future = (second_end, _advance(
                    second_end, direction, option["available_mm"]))
                break
        if second_future is None:
            return False, "second_has_no_next_turn"
    first_segments = list(first["segments"])
    first_segments.append(first_reservation["leg"])
    first_end = first_reservation["leg"][1]
    first_vector = (first_end[0] - first_reservation["leg"][0][0],
                    first_end[1] - first_reservation["leg"][0][1])
    first_length = _distance(first_reservation["leg"][0], first_end)
    first_direction = (first_vector[0] / first_length,
                       first_vector[1] / first_length)
    first_left = (-first_direction[1], first_direction[0])
    first_right = (first_direction[1], -first_direction[0])
    first_options = ((first_right, first_left) if clockwise else
                     (first_left, first_right))
    second_obstacles = second_segments
    if second_future is not None:
        second_obstacles = second_segments + [second_future]
        second_next_left = (-second_future_direction[1],
                            second_future_direction[0])
        second_next_right = (second_future_direction[1],
                             -second_future_direction[0])
        second_next_turn = (second_next_right if clockwise else second_next_left)
        second_turn_probe = _paired_probe(second_future[1], second_next_turn,
                                          contour, second_obstacles,
                                          first_obstacles, spacing, sample)
        second_turn_space = min(spacing, second_turn_probe["available_mm"])
        if second_turn_space >= minimum_leg:
            second_obstacles.append((second_future[1], _advance(
                second_future[1], second_next_turn, second_turn_space)))
    for direction in first_options:
        option = _turn_option(first_end, direction, contour,
                              first_segments, second_obstacles,
                              spacing, sample, minimum_leg)
        if option["eligible"]:
            return True, "both_next_turns_available"
    return False, "first_next_turn_blocked"


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


def build_paired_route(polygon, right_start, left_start, entry_direction,
                       max_turns, offset_mm=200.0, spacing_mm=150.0,
                       clockwise=False, max_legs=500):
    """Grow two diagnostic branches from the open pipe ends.

    ``entry_direction`` points from the pipe body toward its free end.
    Right and left are viewed along this direction. Each branch has its own
    turn budget.
    """
    if isinstance(max_turns, bool) or int(max_turns) != max_turns or max_turns < 0:
        raise ValueError("max_turns_must_be_nonnegative_integer")
    if offset_mm < 0 or spacing_mm <= 0 or max_legs < 1:
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
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        dx, dy = abs(first[0] - second[0]), abs(first[1] - second[1])
        if (dx <= TOLERANCE and dy <= TOLERANCE) or (dx > TOLERANCE and dy > TOLERANCE):
            raise ValueError("polygon_must_have_nonzero_orthogonal_edges")
    right = (normal[1], -normal[0])
    left = (-normal[1], normal[0])
    first_side = "left" if clockwise else "right"
    second_side = "right" if clockwise else "left"
    starts = {"right": right_start, "left": left_start}
    directions = {"right": right, "left": left}
    order = (first_side, second_side)
    contour = _offset_polygon(polygon, float(offset_mm))
    journal = []
    _record(journal, "input", polygon=[list(point) for point in polygon],
            right_start=list(right_start), left_start=list(left_start),
            entry_direction=list(normal), right_vector=list(right),
            left_vector=list(left), clockwise=bool(clockwise),
            max_turns=max_turns, offset_mm=offset_mm, spacing_mm=spacing_mm,
            max_legs=max_legs, turn_budget_scope="per_branch")
    _record(journal, "offset_contour", vertices=[list(point) for point in contour])
    branches = {}
    for index, side in enumerate(order):
        depth = offset_mm + (spacing_mm if index == 1 else 0.0)
        start = starts[side]
        entry = _entry_at_depth(polygon, start, normal, depth)
        _record(journal, "branch_entry", branch=side, order=index + 1,
                start=list(start), target=list(entry), depth_mm=depth,
                inside_offset_contour=_inside(entry, contour))
        corrected = _correct_entry(entry, normal, contour, spacing_mm)
        _record(journal, "entry_correction", branch=side,
                before=list(entry), after=list(corrected),
                moved_mm=_distance(entry, corrected),
                inside_offset_contour=_inside(corrected, contour))
        if not _inside(corrected, contour):
            raise ValueError("{0}_entry_outside_offset_contour".format(side))
        vertices = [start]
        if _distance(start, entry) > TOLERANCE:
            vertices.append(entry)
        if _distance(vertices[-1], corrected) > TOLERANCE:
            vertices.append(corrected)
        direction = directions[side]
        correction = (corrected[0] - entry[0], corrected[1] - entry[1])
        if (correction[0] * direction[0] +
                correction[1] * direction[1] < -TOLERANCE):
            correction_length = _distance(entry, corrected)
            direction = (correction[0] / correction_length,
                         correction[1] / correction_length)
            _record(journal, "entry_direction_adjustment", branch=side,
                    at=list(corrected), original=list(directions[side]),
                    chosen=list(direction),
                    reason="continue_lateral_entry_without_retracing")
        branches[side] = {"vertices": vertices,
                          "segments": [(vertices[part - 1], vertices[part])
                                       for part in range(1, len(vertices))],
                          "direction": direction,
                          "stop_reason": None}
    sample = max(10.0, min(30.0, spacing_mm / 5.0))
    minimum_leg = max(30.0, spacing_mm / 2.0)
    _record(journal, "walk_thresholds", sample_mm=sample,
            minimum_leg_mm=minimum_leg)
    turns_by_branch = {"right": 0, "left": 0}
    reservations = {"right": None, "left": None}
    for round_index in range(max_legs):
        progressed = False
        for side in order:
            branch = branches[side]
            if branch["stop_reason"] is not None:
                continue
            other_side = "left" if side == "right" else "right"
            other_segments = list(branches[other_side]["segments"])
            if reservations[other_side] is not None:
                other_segments.extend(reservations[other_side]["segments"])
            current = branch["vertices"][-1]
            forward = _paired_probe(current, branch["direction"], contour,
                                    branch["segments"], other_segments,
                                    spacing_mm, sample)
            _record(journal, "forward_probe", branch=side,
                    round=round_index + 1, at=list(current), result=forward,
                    other_reservation=([[list(point) for point in segment]
                                        for segment in reservations[other_side]["segments"]]
                                       if reservations[other_side] else None))
            own_reservation = reservations[side]
            if own_reservation is not None:
                reserved_length = _distance(own_reservation["leg"][0],
                                            own_reservation["leg"][1])
                if forward["available_mm"] + TOLERANCE < reserved_length:
                    branch["stop_reason"] = "reserved_leg_became_blocked"
                    _record(journal, "branch_stop", branch=side,
                            reason=branch["stop_reason"], at=list(current),
                            reserved_mm=reserved_length,
                            available_mm=forward["available_mm"])
                    reservations[side] = None
                    continue
                forward["available_mm"] = reserved_length
            if (side == second_side and reservations[first_side] is not None and
                    turns_by_branch[first_side] < max_turns):
                candidate_length = forward["available_mm"]
                while candidate_length >= minimum_leg:
                    feasible, why = _preserves_first_turn(
                        branches[first_side], reservations[first_side],
                        branch, candidate_length, contour, spacing_mm,
                        sample, minimum_leg, clockwise,
                        turns_by_branch[side] < max_turns)
                    _record(journal, "paired_lookahead", branch=side,
                            proposed_length_mm=candidate_length,
                            accepted=feasible, reason=why)
                    if feasible:
                        break
                    candidate_length -= spacing_mm
                if candidate_length < minimum_leg:
                    branch["stop_reason"] = "no_paired_continuation"
                    reservations[side] = None
                    _record(journal, "branch_stop", branch=side,
                            reason=branch["stop_reason"], at=list(current))
                    continue
                if candidate_length < forward["available_mm"] - TOLERANCE:
                    _record(journal, "yield_for_first_corner", branch=side,
                            original_mm=forward["available_mm"],
                            resulting_mm=candidate_length,
                            pitch_mm=spacing_mm)
                    forward["available_mm"] = candidate_length
            if forward["available_mm"] < minimum_leg:
                branch["stop_reason"] = "forward_leg_too_short"
                _record(journal, "branch_stop", branch=side,
                        reason=branch["stop_reason"], at=list(current),
                        required_mm=minimum_leg)
                reservations[side] = None
                continue
            target = _advance(current, branch["direction"],
                              forward["available_mm"])
            merged = _append_branch_segment(branch, target)
            reservations[side] = None
            progressed = True
            _record(journal, "straight", branch=side, start=list(current),
                    end=list(target), direction=list(branch["direction"]),
                    length_mm=forward["available_mm"], blocker=forward["blocker"],
                    merged_with_previous=merged, round=round_index + 1)
            current = branch["vertices"][-1]
            direction = branch["direction"]
            left_turn = (-direction[1], direction[0])
            right_turn = (direction[1], -direction[0])
            preferred = right_turn if clockwise else left_turn
            alternate = left_turn if clockwise else right_turn
            candidates = []
            for candidate_direction in (preferred, alternate):
                candidates.append(_turn_option(
                    current, candidate_direction, contour,
                    branch["segments"], other_segments,
                    spacing_mm, sample, minimum_leg))
            _record(journal, "turn_candidates", branch=side,
                    at=list(current), preferred=list(preferred),
                    candidates=candidates)
            eligible = [entry for entry in candidates if entry["eligible"]]
            if not eligible:
                branch["stop_reason"] = "no_available_turn"
                _record(journal, "branch_stop", branch=side,
                        reason=branch["stop_reason"], at=list(current),
                        required_mm=minimum_leg)
                continue
            if turns_by_branch[side] >= max_turns:
                branch["stop_reason"] = "turn_limit"
                _record(journal, "branch_stop", branch=side,
                        reason=branch["stop_reason"], at=list(current),
                        turns_used=turns_by_branch[side], max_turns=max_turns)
                continue
            selected = eligible[0]
            branch["direction"] = tuple(selected["direction"])
            turns_by_branch[side] += 1
            next_end = _advance(current, branch["direction"],
                                selected["available_mm"])
            reserved_leg = (current, next_end)
            next_left = (-branch["direction"][1], branch["direction"][0])
            next_right = (branch["direction"][1], -branch["direction"][0])
            next_turn = next_right if clockwise else next_left
            turn_probe = _paired_probe(next_end, next_turn, contour,
                                       branch["segments"], other_segments,
                                       spacing_mm, sample)
            turn_space = min(spacing_mm, turn_probe["available_mm"])
            reserved_segments = [reserved_leg]
            if turn_space >= minimum_leg:
                reserved_segments.append(
                    (next_end, _advance(next_end, next_turn, turn_space)))
            reservations[side] = {"leg": reserved_leg,
                                  "segments": reserved_segments}
            _record(journal, "turn", branch=side, at=list(current),
                    direction=selected["direction"],
                    side="left" if branch["direction"] == left_turn else "right",
                    preference="preferred" if selected is candidates[0] else "alternate",
                    turns_used=turns_by_branch[side],
                    max_turns=max_turns,
                    next_available_mm=selected["available_mm"],
                    next_blocker=selected["blocker"])
            _record(journal, "reserve_next_leg", branch=side,
                    start=list(reserved_leg[0]), end=list(reserved_leg[1]),
                    turn_clearance=([[list(point) for point in segment]
                                     for segment in reserved_segments[1:]]),
                    spacing_mm=spacing_mm,
                    turn_clearance_probe=turn_probe)
        if all(branches[side]["stop_reason"] is not None for side in order):
            break
        if not progressed:
            break
    else:
        for side in order:
            if branches[side]["stop_reason"] is None:
                branches[side]["stop_reason"] = "leg_limit"
                _record(journal, "branch_stop", branch=side,
                        reason="leg_limit", max_legs=max_legs)
    if all(branches[side]["stop_reason"] == "turn_limit" for side in order):
        stop_reason = "turn_limit"
    elif all(branches[side]["stop_reason"] != "turn_limit" for side in order):
        stop_reason = "both_branches_blocked"
    else:
        stop_reason = "one_branch_blocked"
    total_turns = sum(turns_by_branch.values())
    _record(journal, "stop", reason=stop_reason,
            turns_by_branch=dict(turns_by_branch), turns_used=total_turns)
    return {"branches": {side: {"vertices": branches[side]["vertices"],
                               "stop_reason": branches[side]["stop_reason"],
                               "turns_used": turns_by_branch[side]}
                         for side in ("right", "left")},
            "contour": contour, "journal": journal,
            "turns_by_branch": turns_by_branch, "turns_used": total_turns,
            "stop_reason": stop_reason, "validated": False}
