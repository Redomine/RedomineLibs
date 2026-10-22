# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import json
import os
import traceback
import uuid

import clr
clr.AddReference("dosymep.Revit.dll")
import dosymep
clr.ImportExtensions(dosymep.Revit)

from pyrevit import DB


MM_PER_FOOT = 304.8
ROOT = os.path.join(os.environ["LOCALAPPDATA"], "RvtMcp", "floor-heating-lab")
PROCESS_ID = os.getpid()
REQUEST_PATH = os.path.join(ROOT, "request-{0}.json".format(PROCESS_ID))
RESULT_PATH = os.path.join(ROOT, "result-{0}.json".format(PROCESS_ID))
STATE_PATH = os.path.join(ROOT, "state-{0}.json".format(PROCESS_ID))
SCRIPT_DIR = os.path.dirname(__file__)

ENDPOINT_TOLERANCE_MM = 0.5


def _read_json(path):
    with io.open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(path, value):
    if not os.path.isdir(ROOT):
        os.makedirs(ROOT)
    temporary = path + ".tmp"
    with io.open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
    if os.path.exists(path):
        os.remove(path)
    os.rename(temporary, path)


def _point(world, view):
    relative = world - view.Origin
    return (relative.DotProduct(view.RightDirection) * MM_PER_FOOT,
            relative.DotProduct(view.UpDirection) * MM_PER_FOOT)


def _world(point, view):
    return (view.Origin + view.RightDirection.Multiply(point[0] / MM_PER_FOOT) +
            view.UpDirection.Multiply(point[1] / MM_PER_FOOT))


def _bounds(points):
    return (min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points))


def _region_data(region, view):
    loops = []
    for loop in region.GetBoundaries():
        edges = []
        for curve in loop:
            edges.append({"kind": curve.GetType().Name,
                          "start": _point(curve.GetEndPoint(0), view),
                          "end": _point(curve.GetEndPoint(1), view)})
        loops.append(edges)
    points = [edge["start"] for loop in loops for edge in loop]
    return {"id": region.Id.GetIdValue(), "view_id": view.Id.GetIdValue(),
            "loops": loops, "bounds": _bounds(points) if points else None}


def _inside(point, bounds):
    return (bounds[0] - 0.001 <= point[0] <= bounds[2] + 0.001 and
            bounds[1] - 0.001 <= point[1] <= bounds[3] + 0.001)


def _pipe_ends(pipe, view):
    manager = getattr(pipe, "ConnectorManager", None)
    if manager is None:
        raise ValueError("pipe_has_no_connector_manager")
    ends = []
    for connector in manager.Connectors:
        if connector.ConnectorType != DB.ConnectorType.End:
            continue
        ends.append({"point": _point(connector.Origin, view),
                     "connected": bool(connector.IsConnected)})
    return ends


def _single_inside_end(pipe, view, bounds):
    candidates = [entry["point"] for entry in _pipe_ends(pipe, view)
                  if not entry["connected"] and _inside(entry["point"], bounds)]
    if len(candidates) != 1:
        raise ValueError("expected_one_unconnected_pipe_end_inside_region; found={0}".format(len(candidates)))
    return candidates[0]


def _get_element(document, identifier, expected):
    element = document.GetElement(DB.ElementId(int(identifier)))
    if element is None or not isinstance(element, expected):
        raise ValueError("element_{0}_is_not_{1}".format(identifier, expected.__name__))
    return element


def _inspect(document, view):
    regions = []
    for region in DB.FilteredElementCollector(document, view.Id).OfClass(DB.FilledRegion):
        regions.append(_region_data(region, view))
    regions.sort(key=lambda row: row["id"])
    pipes = []
    for pipe in (DB.FilteredElementCollector(document)
                 .OfCategory(DB.BuiltInCategory.OST_PipeCurves)
                 .WhereElementIsNotElementType()):
        ends = _pipe_ends(pipe, view)
        if any(region["bounds"] and any(
                _inside(entry["point"], region["bounds"]) for entry in ends)
               for region in regions):
            pipes.append({"id": pipe.Id.GetIdValue(), "ends": ends})
    return {"action": "inspect", "view_id": view.Id.GetIdValue(),
            "view_name": view.Name, "regions": regions, "pipes": pipes}


def _state_matches(state, document, view):
    return (state.get("document_title") == document.Title and
            state.get("document_path") == document.PathName and
            state.get("view_id") == view.Id.GetIdValue())


def _close(point_a, point_b):
    return (abs(point_a[0] - point_b[0]) <= ENDPOINT_TOLERANCE_MM and
            abs(point_a[1] - point_b[1]) <= ENDPOINT_TOLERANCE_MM)


def _preview_line_matches(actual_start, actual_end, expected_start, expected_end):
    return ((_close(actual_start, expected_start) and
             _close(actual_end, expected_end)) or
            (_close(actual_start, expected_end) and
             _close(actual_end, expected_start)))


def _orient_actual_segment(actual_start, actual_end, stored_start, stored_end):
    """Orient actual endpoints to the stored order (Revit may reverse them).

    Returns ``(oriented_start, oriented_end)`` matching the stored direction, or
    ``None`` when the actual endpoints fail to match either direction within
    ``ENDPOINT_TOLERANCE_MM``. Callers use the ``None`` branch to leave a joint
    unverified rather than silently treat it as continuous.
    """
    if (_close(actual_start, stored_start) and
            _close(actual_end, stored_end)):
        return actual_start, actual_end
    if (_close(actual_start, stored_end) and
            _close(actual_end, stored_start)):
        return actual_end, actual_start
    return None


def _region_joint_gaps(oriented_segments, tolerance_mm):
    """Joint metrics over a per_region contiguous slice of oriented segments.

    ``oriented_segments`` is a list whose i-th entry is either ``None`` (the
    line is missing on the view or its endpoints fail to match either stored
    direction) or a ``(start, end)`` tuple oriented to the stored order.

    Returns ``(max_joint_gap_mm, disconnected_joint_count)``.

    * ``max_joint_gap_mm`` is the largest Euclidean distance between adjacent
      oriented endpoints among joints whose *both* sides are verified. Unverified
      joints do not contribute (their actual geometry is unknown).
    * ``disconnected_joint_count`` counts every joint that cannot be certified
      continuous: either an adjacent line is unverified, or both sides verify
      but their actual gap exceeds ``tolerance_mm``.
    """
    max_gap = 0.0
    disconnected = 0
    count = len(oriented_segments)
    for index in range(1, count):
        previous = oriented_segments[index - 1]
        current = oriented_segments[index]
        if previous is None or current is None:
            disconnected += 1
            continue
        dx = current[0][0] - previous[1][0]
        dy = current[0][1] - previous[1][1]
        gap = (dx * dx + dy * dy) ** 0.5
        if gap > max_gap:
            max_gap = gap
        if gap > tolerance_mm:
            disconnected += 1
    return max_gap, disconnected


CENTER_ENTRY_UNAVAILABLE = "full_coverage_unavailable_for_center_entry"


def _entry_is_centered(source, start, end, step):
    """True when the entry pair sits in the middle band of a rectangle edge."""
    left, bottom, right, top = source
    same_y = abs(start[1] - end[1]) <= ENDPOINT_TOLERANCE_MM
    same_x = abs(start[0] - end[0]) <= ENDPOINT_TOLERANCE_MM
    if same_y:
        return abs((start[0] + end[0]) / 2.0 - (left + right) / 2.0) <= step
    if same_x:
        return abs((start[1] + end[1]) / 2.0 - (bottom + top) / 2.0) <= step
    return False


def _with_leads_or_fallback(geometry, source, start, end, offset, step, extend,
                           allow_partial=False):
    """Build with leads; fall back to a plain spiral for corner entries only.

    A centered entry cannot be covered by any of the available generators, and
    the fallback would silently produce a partial route, so it is rejected
    outright instead of being routed around. When ``allow_partial`` is true the
    lead builder is permitted to emit a diagnostic partial route (still routed,
    still continuous, but not full coverage) for center entries instead of
    raising; non-center entries keep their normal behaviour.
    """
    try:
        segments = geometry["build_spiral_with_leads"](source, start, end,
                                                       offset, step,
                                                       allow_partial)
    except ValueError:
        if _entry_is_centered(source, start, end, step):
            raise ValueError(CENTER_ENTRY_UNAVAILABLE)
        segments = geometry["build_spiral"](
            source,
            (source[0] + offset, source[1] + offset,
             source[2] - offset, source[3] - offset),
            start, end, step)
    return extend(segments)


def _compute_segments(item, document, view, offset, step, geometry,
                      allow_partial=False, clockwise=True):
    region = _get_element(document, item["filled_region_id"], DB.FilledRegion)
    if region.OwnerViewId.GetIdValue() != view.Id.GetIdValue():
        raise ValueError("filled_region_is_not_on_active_view")
    region_info = _region_data(region, view)
    if len(region_info["loops"]) != 1:
        raise ValueError("unsupported_shape")
    edges = region_info["loops"][0]
    if len(edges) not in (4, 6, 8, 12):
        raise ValueError("unsupported_shape")
    if any(edge["kind"] != "Line" for edge in edges):
        raise ValueError("unsupported_shape")
    source = region_info["bounds"]
    for edge in edges:
        start, end = edge["start"], edge["end"]
        if abs(start[0] - end[0]) > 0.001 and abs(start[1] - end[1]) > 0.001:
            raise ValueError("unsupported_shape")
    pipe_in = _get_element(document, item["pipe_in_id"], DB.Plumbing.Pipe)
    pipe_out = _get_element(document, item["pipe_out_id"], DB.Plumbing.Pipe)
    start = _single_inside_end(pipe_in, view, source)
    end = _single_inside_end(pipe_out, view, source)
    vertices = [(edge["end"][0], edge["end"][1]) for edge in edges]
    if len(edges) == 12:
        main_box, nub_box, side_box = geometry["top_nub_and_right_nub_boxes"](vertices)
        inset = (main_box[0] + offset, main_box[1] + offset,
                 main_box[2] - offset, main_box[3] - offset)

        def extend(segments):
            segments = geometry["insert_top_nub"](segments, main_box, nub_box,
                                                  offset, step)
            segments = geometry["insert_right_nub"](segments, main_box, side_box,
                                                    offset, step)
            return geometry["insert_central_u"](segments, main_box, offset, step)

        segments = _with_leads_or_fallback(geometry, main_box, start, end,
                                           offset, step, extend, allow_partial)
    elif len(edges) == 8:
        main_box, nub_box = geometry["top_nub_boxes"](vertices)
        inset = (main_box[0] + offset, main_box[1] + offset,
                 main_box[2] - offset, main_box[3] - offset)

        def extend(segments):
            segments = geometry["insert_top_nub"](segments, main_box, nub_box,
                                                  offset, step)
            return geometry["insert_central_u"](segments, main_box, offset, step)

        segments = _with_leads_or_fallback(geometry, main_box, start, end,
                                           offset, step, extend, allow_partial)
    elif len(edges) == 6:
        main_box, wing_box = geometry["left_upper_wing_boxes"](vertices)
        inset = (main_box[0] + offset, main_box[1] + offset,
                 main_box[2] - offset, main_box[3] - offset)
        if geometry["top_right_start_fits_bottom_left_flip"](main_box, start, end,
                                                            offset, step):
            try:
                segments = geometry["build_left_upper_l_top_right_flip"](
                    main_box, wing_box, start, end, offset, step)
                return {"region": region, "region_info": region_info,
                        "source": source, "inset": inset,
                        "start": start, "end": end, "segments": segments}
            except ValueError:
                pass

        def extend(segments):
            segments = geometry["insert_left_upper_wing"](segments, main_box,
                                                          wing_box, offset, step)
            return geometry["insert_central_u"](segments, main_box, offset, step)

        segments = _with_leads_or_fallback(geometry, main_box, start, end,
                                           offset, step, extend, allow_partial)
    else:
        inset = (source[0] + offset, source[1] + offset,
                 source[2] - offset, source[3] - offset)

        if _entry_is_centered(source, start, end, step):
            segments = geometry["build_rectangular_spiral"](source, start, end,
                                                            offset, step, clockwise)
            return {"region": region, "region_info": region_info,
                    "source": source, "inset": inset,
                    "start": start, "end": end, "segments": segments}

        def extend(segments):
            return geometry["insert_central_u"](segments, source, offset, step)

        segments = _with_leads_or_fallback(geometry, source, start, end,
                                           offset, step, extend, allow_partial)
    return {"region": region, "region_info": region_info,
            "source": source, "inset": inset,
            "start": start, "end": end, "segments": segments}


def _preview(request, document, view):
    if os.path.exists(STATE_PATH):
        state = _read_json(STATE_PATH)
        if _state_matches(state, document, view) and state.get("line_ids"):
            raise ValueError("preview_exists; clear_it_before_another_run")
    offset = float(request.get("offset_mm", 200))
    step = float(request.get("spacing_mm", 150))
    if offset < 0 or offset > 1000 or step <= 0 or step > 2000:
        raise ValueError("invalid_offset_or_spacing")
    allow_partial = bool(request.get("diagnostic_partial", False))
    spiral_direction = request.get("spiral_direction", "clockwise")
    if spiral_direction not in ("clockwise", "counterclockwise"):
        raise ValueError("invalid_spiral_direction")
    clockwise = spiral_direction == "clockwise"
    raw_items = request.get("items")
    if raw_items:
        if not isinstance(raw_items, list) or len(raw_items) < 1 or len(raw_items) > 10:
            raise ValueError("invalid_items_count")
        items = []
        seen_regions = set()
        seen_pipes = set()
        for entry in raw_items:
            if not isinstance(entry, dict):
                raise ValueError("invalid_item")
            for key in ("filled_region_id", "pipe_in_id", "pipe_out_id"):
                if key not in entry:
                    raise ValueError("invalid_item")
            region_id = int(entry["filled_region_id"])
            pipe_in_id = int(entry["pipe_in_id"])
            pipe_out_id = int(entry["pipe_out_id"])
            if region_id in seen_regions:
                raise ValueError("duplicate_filled_region_id")
            if pipe_in_id in seen_pipes or pipe_out_id in seen_pipes:
                raise ValueError("duplicate_pipe_id")
            if pipe_in_id == pipe_out_id:
                raise ValueError("duplicate_pipe_id")
            seen_regions.add(region_id)
            seen_pipes.add(pipe_in_id)
            seen_pipes.add(pipe_out_id)
            items.append({"filled_region_id": region_id,
                          "pipe_in_id": pipe_in_id,
                          "pipe_out_id": pipe_out_id})
    else:
        items = [{"filled_region_id": int(request["filled_region_id"]),
                  "pipe_in_id": int(request["pipe_in_id"]),
                  "pipe_out_id": int(request["pipe_out_id"])}]
    geometry = {"__file__": os.path.join(SCRIPT_DIR, "geometry.py")}
    execfile(os.path.join(SCRIPT_DIR, "geometry.py"), geometry)
    prepared = [_compute_segments(item, document, view, offset, step, geometry,
                              allow_partial, clockwise)
                for item in items]
    run_id = str(uuid.uuid4())
    line_ids = []
    lines = []
    per_region = []
    transaction = DB.Transaction(document, "Floor Heating Lab preview")
    transaction.Start()
    try:
        for entry in prepared:
            for first, second in entry["segments"]:
                line = DB.Line.CreateBound(_world(first, view), _world(second, view))
                detail = document.Create.NewDetailCurve(view, line)
                identifier = detail.Id.GetIdValue()
                line_ids.append(identifier)
                lines.append({"id": identifier, "start": list(first), "end": list(second)})
            per_region.append({"region_id": entry["region"].Id.GetIdValue(),
                               "segment_count": len(entry["segments"])})
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError("transaction_did_not_commit")
    except Exception:
        if transaction.HasStarted():
            transaction.RollBack()
        raise
    state = {"run_id": run_id, "line_ids": line_ids, "lines": lines,
             "document_title": document.Title, "document_path": document.PathName,
             "view_id": view.Id.GetIdValue(), "per_region": per_region}
    response = {"action": "preview", "run_id": run_id, "line_ids": list(line_ids),
                "segment_count": sum(item["segment_count"] for item in per_region),
                "per_region": per_region}
    if not raw_items:
        only = prepared[0]
        response.update({
            "region_id": only["region"].Id.GetIdValue(),
            "start": only["start"], "end": only["end"],
            "source": only["source"], "inset": only["inset"],
            "total_length_mm": sum(((second[0] - first[0]) ** 2 +
                                     (second[1] - first[1]) ** 2) ** 0.5
                                    for first, second in only["segments"])})
        state["filled_region_id"] = only["region"].Id.GetIdValue()
    _write_json(STATE_PATH, state)
    return response


def _audit(request, document, view):
    result = {"action": "audit", "stored_count": 0,
              "existing_count": 0, "existing_ids": [], "missing_ids": [],
              "mismatched_ids": [], "continuous_per_region": [],
              "state_matches": False}
    if not os.path.exists(STATE_PATH):
        return result
    state = _read_json(STATE_PATH)
    result["state_matches"] = bool(_state_matches(state, document, view))
    stored_lines = state.get("lines") or []
    result["stored_count"] = len(stored_lines)
    if not result["state_matches"]:
        return result
    view_id = view.Id.GetIdValue()
    oriented_by_index = [None] * len(stored_lines)
    for index, entry in enumerate(stored_lines):
        element = document.GetElement(DB.ElementId(int(entry["id"])))
        if element is None:
            result["missing_ids"].append(int(entry["id"]))
            continue
        if not isinstance(element, DB.DetailCurve):
            raise ValueError("preview_element_type_changed")
        identifier = element.Id.GetIdValue()
        on_view = element.OwnerViewId.GetIdValue() == view_id
        if not on_view:
            result["missing_ids"].append(identifier)
            continue
        actual_start = _point(element.GeometryCurve.GetEndPoint(0), view)
        actual_end = _point(element.GeometryCurve.GetEndPoint(1), view)
        result["existing_ids"].append(identifier)
        oriented = _orient_actual_segment(actual_start, actual_end,
                                          tuple(entry["start"]),
                                          tuple(entry["end"]))
        if oriented is None:
            result["mismatched_ids"].append(identifier)
        oriented_by_index[index] = oriented
    result["existing_count"] = len(result["existing_ids"])
    stored_per_region = state.get("per_region") or []
    slice_start = 0
    for region in stored_per_region:
        try:
            count = int(region.get("segment_count") or 0)
        except (TypeError, ValueError):
            count = 0
        region_id = region.get("region_id")
        slice_segments = oriented_by_index[slice_start:slice_start + count]
        slice_start += count
        verified = sum(1 for seg in slice_segments if seg is not None)
        max_gap, disconnected = _region_joint_gaps(
            slice_segments, ENDPOINT_TOLERANCE_MM)
        result["continuous_per_region"].append({
            "region_id": region_id,
            "segment_count": count,
            "verified_count": verified,
            "missing_count": count - verified,
            "max_joint_gap_mm": max_gap,
            "disconnected_joint_count": disconnected,
        })
    return result


def _clear(request, document, view):
    if not os.path.exists(STATE_PATH):
        return {"action": "clear", "deleted_count": 0}
    state = _read_json(STATE_PATH)
    if not _state_matches(state, document, view):
        raise ValueError("preview_belongs_to_another_document_or_view")
    run_id = request.get("run_id") or state.get("run_id")
    if run_id != state.get("run_id"):
        raise ValueError("run_id_does_not_match_last_preview")
    stored_lines = state.get("lines") or []
    candidates = []
    for entry in stored_lines:
        identifier = entry["id"]
        element = document.GetElement(DB.ElementId(int(identifier)))
        if element is None:
            continue
        if not isinstance(element, DB.DetailCurve):
            raise ValueError("preview_element_type_changed")
        if element.OwnerViewId.GetIdValue() != view.Id.GetIdValue():
            raise ValueError("preview_element_view_changed")
        actual_start = _point(element.GeometryCurve.GetEndPoint(0), view)
        actual_end = _point(element.GeometryCurve.GetEndPoint(1), view)
        if not _preview_line_matches(actual_start, actual_end,
                                     entry["start"], entry["end"]):
            raise ValueError("preview_element_endpoints_changed")
        candidates.append(element.Id)
    transaction = DB.Transaction(document, "Floor Heating Lab clear")
    transaction.Start()
    try:
        for identifier in candidates:
            document.Delete(identifier)
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError("clear_transaction_did_not_commit")
    except Exception:
        if transaction.HasStarted():
            transaction.RollBack()
        raise
    state["line_ids"] = []
    state["lines"] = []
    _write_json(STATE_PATH, state)
    return {"action": "clear", "run_id": run_id, "deleted_count": len(candidates)}


def main():
    result = {"ok": False, "process_id": PROCESS_ID}
    try:
        request = _read_json(REQUEST_PATH)
        ui_document = __revit__.ActiveUIDocument
        if ui_document is None:
            raise ValueError("no_active_document")
        document = ui_document.Document
        view = document.ActiveView
        action = request.get("action", "inspect")
        if action == "inspect":
            data = _inspect(document, view)
        elif action == "preview":
            data = _preview(request, document, view)
        elif action == "audit":
            data = _audit(request, document, view)
        elif action == "clear":
            data = _clear(request, document, view)
        else:
            raise ValueError("unsupported_action")
        result.update({"ok": True, "data": data})
    except Exception as error:
        result.update({"error": str(error), "traceback": traceback.format_exc()})
    _write_json(RESULT_PATH, result)


main()
