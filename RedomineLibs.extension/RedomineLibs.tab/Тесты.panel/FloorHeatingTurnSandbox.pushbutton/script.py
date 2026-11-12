# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import json
import math
import time
import os
import sys
import uuid

import clr
clr.AddReference("dosymep.Revit.dll")
import dosymep
clr.ImportExtensions(dosymep.Revit)

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import DB, forms, script


MM_PER_FOOT = 304.8
ROOT = os.path.join(os.environ["LOCALAPPDATA"], "RvtMcp",
                    "floor-heating-turn-sandbox")
SETTINGS_SECTION = "FloorHeatingTurnSandbox"
ROUTE_MODES = ("spiral_contour_clockwise", "spiral_contour_counterclockwise",
               "serpentine_left_to_right", "serpentine_right_to_left",
               "single_serpentine_left_to_right", "single_serpentine_right_to_left")
SETTINGS_XAML = os.path.join(os.path.dirname(__file__), "SettingsWindow.xaml")
COMMAND_DIR = os.path.dirname(os.path.abspath(__file__))
if COMMAND_DIR not in sys.path:
    sys.path.insert(0, COMMAND_DIR)
from route_timing import RouteProfile, timing_scope
from build_progress import BuildProgress, BuildCancelled, checkpoint, report_stage
from floor_heating_pair import classify_open_ends
from vector_walk import (arrowhead_segments, build_vector_route,
                         calculate_start_points, limit_route_length,
                         start_at_calculated_entries)
from serpentine_walk import build_serpentine_route
from single_serpentine import build_single_serpentine_route
from floor_heating_geometry import _inside


def _saved_bool(value):
    if isinstance(value, basestring):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


class SettingsWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, SETTINGS_XAML)
        self.result = None
        self.config = script.get_config(SETTINGS_SECTION)
        saved_mode = getattr(self.config, "route_mode", None)
        if saved_mode not in ROUTE_MODES:
            if getattr(self.config, "algorithm", "spiral") == "serpentine":
                saved_mode = "serpentine_left_to_right"
            else:
                saved_mode = ("spiral_contour_clockwise" if _saved_bool(
                    getattr(self.config, "clockwise", False)) else
                    "spiral_contour_counterclockwise")
        self.route_mode_combo.SelectedIndex = ROUTE_MODES.index(saved_mode)
        self.turns_text.Text = str(getattr(self.config, "max_turns", 100))
        self.max_length_text.Text = str(getattr(self.config, "max_length_m", 100))
        self.offset_text.Text = str(getattr(self.config, "offset_mm", 200))
        self.spacing_text.Text = str(getattr(self.config, "spacing_mm", 150))
        self.blue_lines_check.IsChecked = _saved_bool(
            getattr(self.config, "show_abandoned", False))
        self.save_journal_check.IsChecked = _saved_bool(
            getattr(self.config, "save_journal", True))

    def run_click(self, sender, args):
        try:
            max_turns = int(self.turns_text.Text.strip())
            if max_turns < 0:
                raise ValueError("Число поворотов не может быть отрицательным")
            offset = float(self.offset_text.Text.strip().replace(",", "."))
            spacing = float(self.spacing_text.Text.strip().replace(",", "."))
            max_length_m = float(self.max_length_text.Text.strip().replace(",", "."))
            if (math.isnan(offset) or math.isinf(offset) or
                    math.isnan(spacing) or math.isinf(spacing) or
                    math.isnan(max_length_m) or math.isinf(max_length_m) or
                    offset < 0 or spacing <= 0 or max_length_m <= 0):
                raise ValueError("Отступ должен быть неотрицательным, шаг и длина — больше нуля")
        except (TypeError, ValueError) as error:
            forms.alert("Проверьте повороты, длину, отступ и шаг: {0}".format(error),
                        title="Тёплый пол: настройки")
            return
        mode = ROUTE_MODES[self.route_mode_combo.SelectedIndex]
        settings = {"max_turns": max_turns,
                    "max_length_m": max_length_m,
                    "route_mode": mode,
                    "algorithm": ("single_serpentine" if mode.startswith("single_serpentine")
                                  else "serpentine" if mode.startswith("serpentine")
                                  else "spiral"),
                    "offset_mm": offset,
                    "spacing_mm": spacing,
                    "clockwise": mode == "spiral_contour_clockwise",
                    "show_abandoned": bool(self.blue_lines_check.IsChecked),
                    "save_journal": bool(self.save_journal_check.IsChecked)}
        for key, value in settings.items():
            setattr(self.config, key, value)
        script.save_config()
        self.result = settings
        self.Close()

    def cancel_click(self, sender, args):
        self.Close()


def _point(world, view):
    relative = world - view.Origin
    return (relative.DotProduct(view.RightDirection) * MM_PER_FOOT,
            relative.DotProduct(view.UpDirection) * MM_PER_FOOT)


def _world(point, view):
    return (view.Origin + view.RightDirection.Multiply(point[0] / MM_PER_FOOT) +
            view.UpDirection.Multiply(point[1] / MM_PER_FOOT))


def _choose_regions(uidoc):
    document = uidoc.Document
    selected = [document.GetElement(identifier)
                for identifier in uidoc.Selection.GetElementIds()]
    regions = [element for element in selected
               if isinstance(element, DB.FilledRegion)]
    if regions:
        return regions, "preselection"
    try:
        references = uidoc.Selection.PickObjects(
            ObjectType.Element, "Выберите залитые области и нажмите Готово")
    except OperationCanceledException:
        return [], "cancel"
    regions = []
    seen = set()
    for reference in references:
        region = document.GetElement(reference.ElementId)
        if not isinstance(region, DB.FilledRegion):
            continue
        identifier = region.Id.GetIdValue()
        if identifier not in seen:
            regions.append(region)
            seen.add(identifier)
    return regions, "pick_multiple"


def _polygon(region, view):
    if region.OwnerViewId.GetIdValue() != view.Id.GetIdValue():
        raise ValueError("Область должна принадлежать активному виду")
    loops = list(region.GetBoundaries())
    if len(loops) != 1:
        raise ValueError("Пока поддерживается область с одним контуром без отверстий")
    edges = list(loops[0])
    if len(edges) < 4:
        raise ValueError("Граница области содержит менее четырех ребер")
    vertices = []
    for edge in edges:
        if not isinstance(edge, DB.Line):
            raise ValueError("Граница должна состоять из прямых отрезков")
        vertices.append(_point(edge.GetEndPoint(0), view))
    return vertices


def _free_end_candidates(document, view):
    result = []
    collector = (DB.FilteredElementCollector(document)
                 .OfCategory(DB.BuiltInCategory.OST_PipeCurves)
                 .WhereElementIsNotElementType())
    for pipe in collector:
        checkpoint()
        manager = getattr(pipe, "ConnectorManager", None)
        if manager is None:
            continue
        connectors = [connector for connector in manager.Connectors
                      if connector.ConnectorType == DB.ConnectorType.End]
        for connector in connectors:
            if connector.ConnectorType != DB.ConnectorType.End or connector.IsConnected:
                continue
            point = _point(connector.Origin, view)
            result.append((pipe, connectors, connector, point))
    return result


def _free_ends(document, view, polygon, cache=None):
    # Scoped to one command: detail curves do not change pipe connections.
    if cache is None:
        candidates = _free_end_candidates(document, view)
    else:
        if "free_ends" not in cache:
            cache["free_ends"] = _free_end_candidates(document, view)
        candidates = cache["free_ends"]
    result = []
    for pipe, connectors, connector, point in candidates:
        if _inside(point, polygon):
            if len(connectors) != 2:
                raise ValueError("У трубы {0} ожидается два концевых соединителя".format(
                    pipe.Id.GetIdValue()))
            other = connectors[0] if connectors[1] == connector else connectors[1]
            other_point = _point(other.Origin, view)
            vector = (point[0] - other_point[0], point[1] - other_point[1])
            result.append({"pipe_id": pipe.Id.GetIdValue(), "point": point,
                           "direction_vector": vector})
    return result


def _write_log(data):
    if not os.path.isdir(ROOT):
        os.makedirs(ROOT)
    path = os.path.join(ROOT, "run-{0}.json".format(data["run_id"]))
    with io.open(path, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def _write_failure_log(run_id, region_id, settings, error):
    path = os.path.join(ROOT, "run-{0}.json".format(run_id))
    data = {"run_id": run_id, "region_id": region_id,
            "settings": settings}
    if os.path.isfile(path):
        with io.open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
    data["error"] = unicode(error)
    _write_log(data)


def _draw(document, view, branches, join, abandoned_segments,
          abandoned_events, first_side, spacing_mm, min_segment_mm,
          show_abandoned):
    checkpoint()
    ids = {"right": [], "left": [], "bridge": [],
           "abandoned": [], "abandoned_arrows": []}
    second_side = "left" if first_side == "right" else "right"
    colors = {first_side: ((255, 0, 0), (0, 160, 0)),
              second_side: ((0, 0, 255), (255, 128, 0))}
    overrides = {}
    for side in ("right", "left"):
        overrides[side] = []
        for rgb in colors[side]:
            setting = DB.OverrideGraphicSettings()
            setting.SetProjectionLineColor(DB.Color(*rgb))
            overrides[side].append(setting)
    if len(abandoned_segments) != len(abandoned_events):
        raise ValueError("abandoned_segment_journal_mismatch")
    transaction = DB.Transaction(document, "Теплый пол: диагностический фрагмент")
    transaction.Start()
    try:
        if show_abandoned:
            for (first, second), event in zip(abandoned_segments,
                                              abandoned_events):
                checkpoint()
                test_override = overrides[event["branch"]][1]
                line = DB.Line.CreateBound(_world(first, view), _world(second, view))
                detail = document.Create.NewDetailCurve(view, line)
                view.SetElementOverrides(detail.Id, test_override)
                ids["abandoned"].append(detail.Id.GetIdValue())
                arrow_ids = []
                for wing_start, tip in arrowhead_segments(
                        first, second, spacing_mm, min_segment_mm):
                    wing = DB.Line.CreateBound(_world(wing_start, view),
                                               _world(tip, view))
                    arrow = document.Create.NewDetailCurve(view, wing)
                    view.SetElementOverrides(arrow.Id, test_override)
                    arrow_ids.append(arrow.Id.GetIdValue())
                ids["abandoned_arrows"].append(arrow_ids)
        for side in ("right", "left"):
            vertices = branches[side]["vertices"]
            for first, second in zip(vertices, vertices[1:]):
                checkpoint()
                if abs(first[0] - second[0]) <= 0.001 and abs(first[1] - second[1]) <= 0.001:
                    continue
                line = DB.Line.CreateBound(_world(first, view), _world(second, view))
                detail = document.Create.NewDetailCurve(view, line)
                view.SetElementOverrides(detail.Id, overrides[side][0])
                ids[side].append(detail.Id.GetIdValue())
        if join["connected"] and join["bridge"] is not None:
            first, second = join["bridge"]
            line = DB.Line.CreateBound(_world(first, view), _world(second, view))
            detail = document.Create.NewDetailCurve(view, line)
            view.SetElementOverrides(detail.Id, overrides[first_side][0])
            ids["bridge"].append(detail.Id.GetIdValue())
        checkpoint()
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError("Транзакция построения не завершилась")
    except Exception:
        if transaction.HasStarted():
            transaction.RollBack()
        raise
    return ids


def _build_region(document, view, region, selection_method, settings, run_id,
                  input_cache=None):
    started = time.time()
    clockwise = settings["clockwise"]
    limit = settings["max_turns"]
    polygon = _polygon(region, view)
    ends = _free_ends(document, view, polygon, input_cache)
    if len(ends) != 2:
        raise ValueError("В области требуется ровно два свободных конца труб; найдено: {0}".format(len(ends)))
    right_end, left_end, direction, separation = classify_open_ends(ends)
    right_start, left_start = calculate_start_points(
        polygon, right_end["point"], left_end["point"], direction,
        settings["offset_mm"], settings["spacing_mm"])
    minimum_curve_mm = document.Application.ShortCurveTolerance * MM_PER_FOOT
    prepared = time.time()
    with RouteProfile() as route_profile:
        if settings["algorithm"] == "single_serpentine":
            with timing_scope("build_single_serpentine_route"):
                route = build_single_serpentine_route(
                    polygon, right_start, left_start, direction, limit,
                    left_to_right=settings["route_mode"] == "single_serpentine_left_to_right",
                    offset_mm=settings["offset_mm"], spacing_mm=settings["spacing_mm"],
                    min_segment_mm=minimum_curve_mm)
        elif settings["algorithm"] == "serpentine":
            with timing_scope("build_serpentine_route"):
                route = build_serpentine_route(
                    polygon, right_start, left_start, direction, limit,
                    left_to_right=settings["route_mode"] == "serpentine_left_to_right",
                    offset_mm=settings["offset_mm"],
                    spacing_mm=settings["spacing_mm"],
                    min_segment_mm=minimum_curve_mm)
        else:
            route = build_vector_route(polygon, right_start,
                                       left_start, direction, limit,
                                       offset_mm=settings["offset_mm"],
                                       spacing_mm=settings["spacing_mm"],
                                       min_segment_mm=minimum_curve_mm,
                                       clockwise=clockwise)
        route = start_at_calculated_entries(route)
        route = limit_route_length(route, settings["max_length_m"] * 1000.0)
        first_side = ("left" if clockwise else "right") if settings[
            "algorithm"] == "spiral" else ("right" if settings["route_mode"].endswith(
                "left_to_right") else "left")
        second_side = "left" if first_side == "right" else "right"
        if (settings["algorithm"] == "serpentine" and
                route["stop_reason"]["right"] == "sweep_blocked" and
                route["turns_by_branch"]["right"] == 0):
            raise ValueError(u"От стартовой позиции нет места для первого прохода змейки")
    calculated = time.time()
    data = {"run_id": run_id, "document_title": document.Title,
            "document_path": document.PathName, "view_id": view.Id.GetIdValue(),
            "view_name": view.Name, "region_id": region.Id.GetIdValue(),
            "region_selection": selection_method,
            "free_ends": [{"pipe_id": item["pipe_id"], "point": list(item["point"]),
                           "direction_vector": list(item["direction_vector"])}
                          for item in ends],
            "entry_direction": list(direction), "pipe_separation_mm": separation,
            "calculated_starts": {"right": list(right_start),
                                  "left": list(left_start)},
            "pipe_roles": {"pipe_1": first_side, "pipe_2": second_side},
            "clockwise": (clockwise if settings["algorithm"] == "spiral" else None),
            "settings": settings,
            "right_pipe_id": right_end["pipe_id"],
            "left_pipe_id": left_end["pipe_id"],
            "route": route, "line_ids": {"right": [], "left": [],
                                           "bridge": [], "abandoned": [],
                                           "abandoned_arrows": []}}
    data["route_profile"] = route_profile.report()
    data["timings_seconds"] = {"prepare": prepared - started,
                               "route": calculated - prepared}
    checkpoint()
    if settings["save_journal"]:
        _write_log(data)
    test_events = [event for event in route["journal"] if
                   event["action"] == "abandoned_segment"]
    report_stage(u"Отрисовка")
    draw_started = time.time()
    data["line_ids"] = _draw(document, view, route["branches"],
                             route["join"], route["abandoned_segments"],
                             test_events, first_side, settings["spacing_mm"],
                             minimum_curve_mm, settings["show_abandoned"])
    data["timings_seconds"]["draw"] = time.time() - draw_started
    for event, element_id, arrow_ids in zip(
            test_events, data["line_ids"]["abandoned"],
            data["line_ids"]["abandoned_arrows"]):
        event["element_id"] = element_id
        event["arrow_element_ids"] = arrow_ids
    if settings["save_journal"]:
        _write_log(data)
    return data


def main():
    uidoc = __revit__.ActiveUIDocument
    document = uidoc.Document
    view = uidoc.ActiveView
    window = SettingsWindow()
    window.ShowDialog()
    if window.result is None:
        return
    settings = window.result
    regions, selection_method = _choose_regions(uidoc)
    if not regions:
        return
    successes = []
    failures = []
    limited = []
    cancelled = False
    input_cache = {}
    with BuildProgress(len(regions)) as progress:
        for index, region in enumerate(regions):
            identifier = region.Id.GetIdValue()
            run_id = str(uuid.uuid4())
            try:
                progress.region(u"Область {0}/{1}: {2}".format(index+1, len(regions), identifier))
                data = _build_region(document, view, region, selection_method,
                                     settings, run_id, input_cache)
                successes.append(identifier)
                if data["route"]["length_limit_hit"]:
                    limited.append(identifier)
            except BuildCancelled:
                cancelled = True
                break
            except Exception as error:
                failures.append((identifier, unicode(error)))
                if settings["save_journal"]:
                    _write_failure_log(run_id, identifier, settings, error)
            progress.finish_region()
    if len(regions) > 1 or failures or limited or cancelled:
        lines = [u"Областей: {0}; построено: {1}; ошибок: {2}.".format(
            len(regions), len(successes), len(failures))]
        if cancelled:
            lines.append(u"Построение отменено. Текущая область не построена; готовые области сохранены.")
        if limited:
            lines.append(u"Предел длины достигнут: {0}.".format(
                u", ".join(str(value) for value in limited)))
        for identifier, error in failures:
            lines.append(u"Область {0}: {1}".format(identifier, error))
        forms.alert(u"\n".join(lines), title=u"Тёплый пол: массовый тест")


try:
    main()
except Exception as error:
    forms.alert("Песочница не завершена: {0}".format(error),
                title="Теплый пол: песочница")
