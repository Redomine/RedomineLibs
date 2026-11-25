# -*- coding: utf-8 -*-
"""pyRevit boundary for the standalone Redomine.FloorHeating assembly."""
from __future__ import division

import hashlib
import os
import tempfile

import clr
from System import Action, Array, Int32, String
from System.Reflection import Assembly

_types = {}


def _core_types():
    # Revit keeps assemblies loaded for the process lifetime. Load a disk copy
    # keyed by its content so Release builds can replace the bundle DLL.
    source = os.path.join(os.path.dirname(__file__), "bin", "Redomine.FloorHeating.dll")
    with open(source, "rb") as stream:
        payload = stream.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest in _types:
        return _types[digest]
    directory = os.path.join(os.environ["LOCALAPPDATA"], "RvtMcp",
                             "floor-heating-turn-sandbox", "assemblies", digest)
    if not os.path.isdir(directory):
        try:
            os.makedirs(directory)
        except OSError:
            if not os.path.isdir(directory):
                raise
    shadow = os.path.join(directory, os.path.basename(source))
    if not os.path.isfile(shadow):
        descriptor, temp = tempfile.mkstemp(dir=directory, suffix=".dll")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            try:
                os.rename(temp, shadow)
            except OSError:
                if not os.path.isfile(shadow):
                    raise
        finally:
            if os.path.isfile(temp):
                os.remove(temp)
    assembly = Assembly.LoadFile(shadow)
    types = tuple(clr.GetPythonType(assembly.GetType("Redomine.FloorHeating." + name, True))
                  for name in ("Point2", "SpiralRequest", "SpiralSolver"))
    _types[digest] = types
    return types


def _point(p):
    return [p.X, p.Y]


def _segment(s):
    return [_point(s.Start), _point(s.End)]


def _coverage(area, spacing):
    if area is None:
        return None
    return {"measurable": True, "ratio": area.Ratio,
            "working_area_mm2": area.WorkingAreaMm2, "covered_area_mm2": area.CoveredAreaMm2,
            "uncovered_area_mm2": area.WorkingAreaMm2 - area.CoveredAreaMm2,
            "strip_width_mm": spacing, "method": "clipped_strip_union_no_end_caps"}


def _recovery(detail):
    if detail is None:
        return None
    info = {"reason": detail.Reason, "from_round": detail.FromRound, "to_round": detail.ToRound}
    if detail.Turn is not None:
        info["turn"] = detail.Turn
    if detail.Reason == "paired_vertex_recovery":
        info["partner_turn"] = detail.PartnerTurn
    if detail.CheckpointSource is not None:
        info.update(first_checkpoint_mm=detail.FirstCheckpointMm, second_checkpoint_mm=detail.SecondCheckpointMm,
                    first_limit_kind=detail.FirstLimitKind, second_limit_kind=detail.SecondLimitKind,
                    checkpoint_source=detail.CheckpointSource, anchor_branch=detail.AnchorBranch,
                    anchor_segment=_segment(detail.AnchorSegment) if detail.AnchorSegment else None)
    return info


def calculate_spiral(polygon, right_inlet, left_inlet, normal, settings, minimum, progress=None):
    point_type, request_type, solver = _core_types()
    request = request_type()
    request.Polygon = Array[point_type]([point_type(*p) for p in polygon])
    request.RightStart, request.LeftStart = point_type(*right_inlet), point_type(*left_inlet)
    request.EntryDirection = point_type(*normal)
    request.OffsetMm, request.SpacingMm = settings["offset_mm"], settings["spacing_mm"]
    request.MaximumTurns, request.MinimumSegmentMm = settings["max_turns"], minimum
    request.MaximumLengthMm = settings["max_length_m"] * 1000.0
    request.Clockwise = settings["clockwise"]
    callback = Action[String, Int32](progress) if progress is not None else None
    result = solver.Calculate(request, callback)
    starts = {"right": _point(result.RightStart), "left": _point(result.LeftStart)}
    journal = [{"step": 1, "action": "input", "polygon": [list(p) for p in polygon],
                "right_start": starts["right"], "left_start": starts["left"], "entry_direction": list(normal),
                "offset_mm": request.OffsetMm, "spacing_mm": request.SpacingMm, "min_segment_mm": minimum,
                "max_turns": request.MaximumTurns, "clockwise": request.Clockwise, "direction_policy": result.Policy}]
    abandoned = []
    for event in result.Journal:
        item = {"action": event.Action, "step": len(journal) + 1}
        if event.Branch:
            item["branch"] = event.Branch
        if event.Round:
            item["round"] = event.Round
        if event.Recovery is not None:
            item.update(_recovery(event.Recovery))
        elif event.Action.endswith("direction_probe"):
            item.update(at=_point(event.Origin), candidate=event.Choice, result={
                "direction": _point(event.Direction), "available_mm": event.AvailableMm,
                "boundary_limit_mm": event.BoundaryMm, "boundary_hit": _point(event.BoundaryHit),
                "blocker": event.BlockerKind, "blocker_index": event.BlockerIndex,
                "blocker_segment": _segment(event.BlockerSegment) if event.BlockerSegment else None})
        elif event.Action == "entry":
            item.update(start=_point(event.Segment.Start), corrected=_point(event.Segment.End),
                        target=_point(event.Segment.End))
            journal[0][event.Branch + "_start"] = item["start"]
        elif event.Action == "length_limit_reached":
            item.update(limit_mm=request.MaximumLengthMm, built_length_mm=result.LengthMm,
                        full_route_length_mm=result.FullLengthMm)
        elif event.Action.endswith("segment"):
            a, b = _segment(event.Segment)
            item.update(start=a, end=b)
            if event.Choice:
                item["choice"] = event.Choice
            if event.Action == "abandoned_segment":
                item["source_step"] = event.SourceStep + 1
                abandoned.append([a, b])
        journal.append(item)
    attempts = [{"variant": i+1, "policy": v.Policy, "selected": v.Selected, "valid": v.Connected,
                 "reason": v.Reason, "length_mm": v.LengthMm, "full_length_mm": v.FullLengthMm,
                 "coverage": _coverage(v.Coverage, request.SpacingMm), "seconds": v.Seconds,
                 "recoveries": v.Recoveries, "duplicate_continuations": v.DuplicateContinuations}
                for i, v in enumerate(result.Attempts)]
    journal.append({"step": len(journal)+1, "action": "route_variant_selection",
                    "selected_policy": result.Policy,
                    "selected_variant": next((v["variant"] for v in attempts if v["selected"]), None),
                    "reason": ("time_limit" if result.TimeLimitHit else "length_limit" if result.LengthLimitHit
                               else "connected_area_coverage"), "attempts": attempts})
    turns = {"right": result.RightTurns, "left": result.LeftTurns}
    stops = {"right": result.RightStop, "left": result.LeftStop}
    paths = {"right": result.Right, "left": result.Left}
    route = {"engine": "Redomine.FloorHeating", "contour": [_point(p) for p in result.Contour],
             "branches": {side: {"vertices": [_point(p) for p in paths[side]], "turns_used": turns[side],
                                  "stop_reason": stops[side]} for side in ("right", "left")},
             "join": {"connected": result.Connected, "bridge": _segment(result.Bridge) if result.Bridge else None},
             "validated": result.Connected, "turns_by_branch": turns, "turns_used": sum(turns.values()),
             "length_limit_hit": result.LengthLimitHit, "time_limit_hit": result.TimeLimitHit,
             "length_limit_mm": request.MaximumLengthMm, "length_mm": result.LengthMm,
             "full_length_mm": result.FullLengthMm, "area_coverage": _coverage(result.Coverage, request.SpacingMm),
             "stop_reason": stops, "abandoned_segments": abandoned, "journal": journal, "rollback": _recovery(result.Rollback)}
    route["join"]["method" if result.Connected else "reason"] = result.JoinMethod
    return route, starts
