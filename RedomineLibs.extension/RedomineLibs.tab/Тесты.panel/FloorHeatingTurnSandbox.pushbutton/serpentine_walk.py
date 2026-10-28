# -*- coding: utf-8 -*-
"""Paired serpentine walk with a geometric check before each U-turn."""
from __future__ import division

import copy

from floor_heating_geometry import (TOLERANCE, _advance, _inside,
                                  _offset_polygon, _ray_boundary_distance)
from floor_heating_pair import _append_branch_segment, _entry_at_depth
from vector_walk import (_finish_route, _initial_branch,
                         _intermediate_checkpoints, _obstacles,
                         _paired_entry_alignment, _paired_turn_room,
                         _record, probe_ray)


def _negate(direction):
    return (-direction[0], -direction[1])


def _entry_branches(polygon, contour, starts, normal, sweep, offset,
                    spacing, journal):
    first_side = "right" if sweep[0] > 0 else "left"
    second_side = "left" if first_side == "right" else "right"
    first_raw = _entry_at_depth(polygon, starts[first_side], normal, offset)
    if _inside(first_raw, contour):
        first_target = first_raw
    else:
        aligned = _paired_entry_alignment(
            polygon, contour, starts, normal, first_side, second_side,
            offset, spacing, sweep[0] < 0, journal)
        first_target = aligned[first_side]
    backward_room = _ray_boundary_distance(first_target, _negate(sweep),
                                            contour)
    shift = max(0.0, spacing - backward_room)
    first_target = _advance(first_target, sweep, shift)
    second_target = _advance(first_target, _negate(sweep), spacing)
    second_target = _advance(second_target, normal, spacing)
    if not _inside(first_target, contour) or not _inside(second_target, contour):
        raise ValueError("serpentine_entry_has_no_room_for_pair")
    targets = {first_side: first_target, second_side: second_target}
    _record(journal, "serpentine_entry_alignment",
            first_branch=first_side, second_branch=second_side,
            first_target=list(first_target), second_target=list(second_target),
            transverse_shift_mm=shift)
    branches = {}
    for side, depth in ((first_side, offset),
                        (second_side, offset + spacing)):
        branches[side] = _initial_branch(
            polygon, contour, starts[side], normal, depth, spacing,
            side, journal, targets[side])
    return branches


def _append_move(branch, side, direction, length, turn, round_index,
                 journal):
    start = branch["vertices"][-1]
    end = _advance(start, direction, length)
    merged = _append_branch_segment(branch, end)
    branch["heading"] = direction
    if turn:
        branch["turns_used"] += 1
    _record(journal, "serpentine_segment", branch=side, round=round_index,
            start=list(start), end=list(end), length_mm=length,
            direction=list(direction), merged_with_previous=merged,
            turns_used=branch["turns_used"])


def _probe(point, direction, branch, other, contour, spacing):
    return probe_ray(point, direction, contour, branch["segments"],
                     _obstacles(other, "other_branch"), spacing)


def _turn_candidate(branches, outer_side, inner_side, sweep, normal,
                    travel, contour, spacing, offset, min_segment,
                    journal, round_index):
    trial = copy.deepcopy(branches)
    move_log = []
    for side in (outer_side, inner_side):
        _append_move(trial[side], side, sweep, travel, True,
                     round_index, move_log)
    room_log = []
    tight = _paired_turn_room(trial, contour, spacing, offset,
                              room_log, round_index)
    room_probes = [event for event in room_log
                   if event["action"] == "paired_turn_room_probe"]
    if tight:
        _record(journal, "serpentine_turn_probe", round=round_index,
                travel_mm=travel, accepted=False, reason="narrow_pair_room",
                tight_branches=tight, room_probes=room_probes)
        return None
    outer = trial[outer_side]
    inner = trial[inner_side]
    outer_probe = _probe(outer["vertices"][-1], normal, outer, inner,
                         contour, spacing)
    if outer_probe["available_mm"] + TOLERANCE < 3 * spacing:
        _record(journal, "serpentine_turn_probe", round=round_index,
                travel_mm=travel, accepted=False,
                reason="outer_turn_room", result=outer_probe,
                room_probes=room_probes)
        return None
    _append_move(outer, outer_side, normal, 3 * spacing, True,
                 round_index, move_log)
    inner_probe = _probe(inner["vertices"][-1], normal, inner, outer,
                         contour, spacing)
    if inner_probe["available_mm"] + TOLERANCE < spacing:
        _record(journal, "serpentine_turn_probe", round=round_index,
                travel_mm=travel, accepted=False,
                reason="inner_turn_room", result=inner_probe,
                room_probes=room_probes)
        return None
    _append_move(inner, inner_side, normal, spacing, True,
                 round_index, move_log)
    reverse = _negate(sweep)
    exits = {}
    for side, other_side in ((outer_side, inner_side),
                             (inner_side, outer_side)):
        exits[side] = _probe(trial[side]["vertices"][-1], reverse,
                             trial[side], trial[other_side],
                             contour, spacing)
    accepted = all(exits[side]["available_mm"] + TOLERANCE >= spacing
                   for side in (outer_side, inner_side))
    _record(journal, "serpentine_turn_probe", round=round_index,
            travel_mm=travel, accepted=accepted,
            reason=("return_clear" if accepted else "return_blocked"),
            room_probes=room_probes, outer_turn_probe=outer_probe,
            inner_turn_probe=inner_probe, reverse_probes=exits)
    return (trial, move_log) if accepted else None


def build_serpentine_route(polygon, right_start, left_start,
                           entry_direction, max_turns, left_to_right=True,
                           offset_mm=200.0, spacing_mm=150.0,
                           min_segment_mm=1.0, max_rounds=500):
    """Sweep paired lanes; back off to the last viable paired U-turn."""
    if isinstance(max_turns, bool) or int(max_turns) != max_turns or max_turns < 0:
        raise ValueError("max_turns_must_be_nonnegative_integer")
    if (offset_mm < 0 or spacing_mm <= 0 or min_segment_mm < 0 or
            max_rounds < 1):
        raise ValueError("invalid_geometry_parameters")
    normal = tuple(map(float, entry_direction))
    if normal not in ((0.0, 1.0), (0.0, -1.0)):
        raise ValueError(
            u"Для змейки нужны вертикальные входящие трубы")
    polygon = [tuple(map(float, point)) for point in polygon]
    starts = {"right": tuple(map(float, right_start)),
              "left": tuple(map(float, left_start))}
    contour = _offset_polygon(polygon, float(offset_mm))
    sweep = (1.0, 0.0) if left_to_right else (-1.0, 0.0)
    journal = []
    _record(journal, "serpentine_input", polygon=[list(p) for p in polygon],
            right_start=list(starts["right"]),
            left_start=list(starts["left"]),
            entry_direction=list(normal), left_to_right=bool(left_to_right),
            offset_mm=offset_mm, spacing_mm=spacing_mm,
            max_turns=max_turns, min_segment_mm=min_segment_mm)
    _record(journal, "offset_contour", vertices=[list(p) for p in contour])
    branches = _entry_branches(polygon, contour, starts, normal, sweep,
                               offset_mm, spacing_mm, journal)
    stop_reason = "round_limit"
    outer_side = "right" if sweep[0] > 0 else "left"
    for round_index in range(1, max_rounds + 1):
        if any(branches[side]["turns_used"] >= max_turns
               for side in ("right", "left")):
            stop_reason = "turn_limit"
            break
        outer_side = min(("right", "left"),
                         key=lambda side: (branches[side]["vertices"][-1][0] *
                                           normal[0] +
                                           branches[side]["vertices"][-1][1] *
                                           normal[1]))
        inner_side = "left" if outer_side == "right" else "right"
        outer = branches[outer_side]
        inner = branches[inner_side]
        limits = {}
        for side, other_side in ((outer_side, inner_side),
                                 (inner_side, outer_side)):
            limits[side] = _probe(branches[side]["vertices"][-1], sweep,
                                   branches[side], branches[other_side],
                                   contour, spacing_mm)
            _record(journal, "serpentine_sweep_probe", round=round_index,
                    branch=side, direction=list(sweep), result=limits[side])
        travel = min(limits[outer_side]["available_mm"],
                     limits[inner_side]["available_mm"])
        if travel <= min_segment_mm + TOLERANCE:
            stop_reason = "sweep_blocked"
            break
        selected = None
        if branches[outer_side]["turns_used"] + 2 <= max_turns:
            candidates = [(travel, "far_limit")]
            candidates.extend((distance, source)
                              for distance, source, kind in
                              _intermediate_checkpoints(
                                  outer["vertices"][-1], sweep, travel,
                                  spacing_mm, min_segment_mm,
                                  _obstacles(inner, "other_branch")))
            for corner in contour:
                distance = ((corner[0] - outer["vertices"][-1][0]) * sweep[0] -
                            max(min_segment_mm, TOLERANCE))
                if min_segment_mm + TOLERANCE < distance < travel - TOLERANCE:
                    candidates.append((distance, "contour_corner"))
            for distance, source in sorted(set(candidates), reverse=True):
                if distance <= min_segment_mm + TOLERANCE:
                    continue
                trial = _turn_candidate(
                    branches, outer_side, inner_side, sweep, normal,
                    distance, contour, spacing_mm, offset_mm,
                    min_segment_mm, journal, round_index)
                if trial is not None:
                    selected = (trial[0], trial[1], distance, source)
                    break
        if selected is None:
            for side in (outer_side, inner_side):
                _append_move(branches[side], side, sweep, travel, True,
                             round_index, journal)
            stop_reason = "no_paired_return"
            _record(journal, "serpentine_stop", round=round_index,
                    reason=stop_reason, travel_mm=travel)
            break
        branches, move_log, distance, source = selected
        for event in move_log:
            details = dict(event)
            details.pop("step", None)
            _record(journal, details.pop("action"), **details)
        for side in (outer_side, inner_side):
            _record(journal, "serpentine_pair_turn", round=round_index,
                    branch=side, turn_distance_mm=distance,
                    checkpoint_source=source,
                    end=list(branches[side]["vertices"][-1]))
        sweep = _negate(sweep)
    else:
        round_index = max_rounds
    join = _finish_route(branches, contour, spacing_mm,
                         min_segment_mm, journal, outer_side)
    for side in ("right", "left"):
        branches[side]["stop_reason"] = stop_reason
    turns = {side: branches[side]["turns_used"]
             for side in ("right", "left")}
    _record(journal, "serpentine_complete", round=round_index,
            stop_reason=stop_reason, turns_by_branch=turns,
            connected=join["connected"])
    return {"branches": {side: {"vertices": branches[side]["vertices"],
                               "turns_used": turns[side],
                               "stop_reason": stop_reason}
                         for side in ("right", "left")},
            "contour": contour, "journal": journal,
            "turns_by_branch": turns,
            "turns_used": turns["right"] + turns["left"],
            "stop_reason": {"right": stop_reason, "left": stop_reason},
            "join": join, "rollback": None,
            "abandoned_segments": [], "validated": False}
