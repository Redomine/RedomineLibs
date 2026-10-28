# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import json
import math
import os
import sys
import uuid

import clr
clr.AddReference("dosymep.Revit.dll")
import dosymep
clr.ImportExtensions(dosymep.Revit)

from Autodesk.Revit.UI.Selection import ObjectType
from pyrevit import DB, forms, script


MM_PER_FOOT = 304.8
ROOT = os.path.join(os.environ["LOCALAPPDATA"], "RvtMcp",
                    "floor-heating-turn-sandbox")
SETTINGS_SECTION = "FloorHeatingTurnSandbox"
ROUTE_MODES = ("spiral_clockwise", "spiral_counterclockwise",
               "serpentine_left_to_right", "serpentine_right_to_left")
SETTINGS_XAML = os.path.join(os.path.dirname(__file__), "SettingsWindow.xaml")
COMMAND_DIR = os.path.dirname(os.path.abspath(__file__))
if COMMAND_DIR not in sys.path:
    sys.path.insert(0, COMMAND_DIR)
from floor_heating_pair import classify_open_ends
from vector_walk import (arrowhead_segments, build_vector_route,
                         limit_route_length)
from serpentine_walk import build_serpentine_route
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
                saved_mode = ("spiral_clockwise" if _saved_bool(
                    getattr(self.config, "clockwise", False)) else
                    "spiral_counterclockwise")
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
                    "algorithm": ("serpentine" if mode.startswith("serpentine")
                                  else "spiral"),
                    "offset_mm": offset,
                    "spacing_mm": spacing,
                    "clockwise": mode == "spiral_clockwise",
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


def _choose_region(uidoc):
    document = uidoc.Document
    selected = [document.GetElement(identifier)
                for identifier in uidoc.Selection.GetElementIds()]
    regions = [element for element in selected
               if isinstance(element, DB.FilledRegion)]
    if len(regions) == 1:
        return regions[0], "preselection"
    reference = uidoc.Selection.PickObject(ObjectType.Element,
                                             "Выберите одну залитую область")
    region = document.GetElement(reference.ElementId)
    if not isinstance(region, DB.FilledRegion):
        raise ValueError("Выбранный элемент не является залитой областью")
    return region, "pick"


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


def _free_ends(document, view, polygon):
    result = []
    collector = (DB.FilteredElementCollector(document)
                 .OfCategory(DB.BuiltInCategory.OST_PipeCurves)
                 .WhereElementIsNotElementType())
    for pipe in collector:
        manager = getattr(pipe, "ConnectorManager", None)
        if manager is None:
            continue
        connectors = [connector for connector in manager.Connectors
                      if connector.ConnectorType == DB.ConnectorType.End]
        for connector in connectors:
            if connector.ConnectorType != DB.ConnectorType.End or connector.IsConnected:
                continue
            point = _point(connector.Origin, view)
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


def _draw(document, view, branches, join, abandoned_segments,
          spacing_mm, min_segment_mm, show_abandoned):
    ids = {"right": [], "left": [], "bridge": [],
           "abandoned": [], "abandoned_arrows": []}
    red_override = DB.OverrideGraphicSettings()
    red_override.SetProjectionLineColor(DB.Color(255, 0, 0))
    blue_override = DB.OverrideGraphicSettings()
    blue_override.SetProjectionLineColor(DB.Color(0, 0, 255))
    transaction = DB.Transaction(document, "Теплый пол: диагностический фрагмент")
    transaction.Start()
    try:
        if show_abandoned:
            for first, second in abandoned_segments:
                line = DB.Line.CreateBound(_world(first, view), _world(second, view))
                detail = document.Create.NewDetailCurve(view, line)
                view.SetElementOverrides(detail.Id, blue_override)
                ids["abandoned"].append(detail.Id.GetIdValue())
                arrow_ids = []
                for wing_start, tip in arrowhead_segments(
                        first, second, spacing_mm, min_segment_mm):
                    wing = DB.Line.CreateBound(_world(wing_start, view),
                                               _world(tip, view))
                    arrow = document.Create.NewDetailCurve(view, wing)
                    view.SetElementOverrides(arrow.Id, blue_override)
                    arrow_ids.append(arrow.Id.GetIdValue())
                ids["abandoned_arrows"].append(arrow_ids)
        for side in ("right", "left"):
            vertices = branches[side]["vertices"]
            for first, second in zip(vertices, vertices[1:]):
                if abs(first[0] - second[0]) <= 0.001 and abs(first[1] - second[1]) <= 0.001:
                    continue
                line = DB.Line.CreateBound(_world(first, view), _world(second, view))
                detail = document.Create.NewDetailCurve(view, line)
                view.SetElementOverrides(detail.Id, red_override)
                ids[side].append(detail.Id.GetIdValue())
        if join["connected"] and join["bridge"] is not None:
            first, second = join["bridge"]
            line = DB.Line.CreateBound(_world(first, view), _world(second, view))
            detail = document.Create.NewDetailCurve(view, line)
            view.SetElementOverrides(detail.Id, red_override)
            ids["bridge"].append(detail.Id.GetIdValue())
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError("Транзакция построения не завершилась")
    except Exception:
        if transaction.HasStarted():
            transaction.RollBack()
        raise
    return ids


def main():
    uidoc = __revit__.ActiveUIDocument
    document = uidoc.Document
    view = uidoc.ActiveView
    window = SettingsWindow()
    window.ShowDialog()
    if window.result is None:
        return
    settings = window.result
    region, selection_method = _choose_region(uidoc)
    clockwise = settings["clockwise"]
    limit = settings["max_turns"]
    polygon = _polygon(region, view)
    ends = _free_ends(document, view, polygon)
    if len(ends) != 2:
        raise ValueError("В области требуется ровно два свободных конца труб; найдено: {0}".format(len(ends)))
    right_end, left_end, direction, separation = classify_open_ends(ends)
    minimum_curve_mm = document.Application.ShortCurveTolerance * MM_PER_FOOT
    if settings["algorithm"] == "serpentine":
        route = build_serpentine_route(
            polygon, right_end["point"], left_end["point"], direction, limit,
            left_to_right=settings["route_mode"] == "serpentine_left_to_right",
            offset_mm=settings["offset_mm"],
            spacing_mm=settings["spacing_mm"],
            min_segment_mm=minimum_curve_mm)
    else:
        route = build_vector_route(polygon, right_end["point"],
                                   left_end["point"], direction, limit,
                                   offset_mm=settings["offset_mm"],
                                   spacing_mm=settings["spacing_mm"],
                                   min_segment_mm=minimum_curve_mm,
                                   clockwise=clockwise)
    route = limit_route_length(route, settings["max_length_m"] * 1000.0)
    if (settings["algorithm"] == "serpentine" and
            route["stop_reason"]["right"] == "sweep_blocked" and
            route["turns_by_branch"]["right"] == 0):
        forms.alert("От стартовой позиции в выбранном направлении нет места "
                    "для первого прохода змейки.",
                    title="Теплый пол: змейка")
        return
    run_id = str(uuid.uuid4())
    data = {"run_id": run_id, "document_title": document.Title,
            "document_path": document.PathName, "view_id": view.Id.GetIdValue(),
            "view_name": view.Name, "region_id": region.Id.GetIdValue(),
            "region_selection": selection_method,
            "free_ends": [{"pipe_id": item["pipe_id"], "point": list(item["point"]),
                           "direction_vector": list(item["direction_vector"])}
                          for item in ends],
            "entry_direction": list(direction), "pipe_separation_mm": separation,
            "clockwise": (clockwise if settings["algorithm"] == "spiral" else None),
            "settings": settings,
            "right_pipe_id": right_end["pipe_id"],
            "left_pipe_id": left_end["pipe_id"],
            "route": route, "line_ids": {"right": [], "left": [],
                                           "bridge": [], "abandoned": [],
                                           "abandoned_arrows": []}}
    if settings["save_journal"]:
        _write_log(data)
    data["line_ids"] = _draw(document, view, route["branches"],
                             route["join"], route["abandoned_segments"],
                             settings["spacing_mm"], minimum_curve_mm,
                             settings["show_abandoned"])
    blue_events = [event for event in route["journal"] if
                   event["action"] == "abandoned_segment"]
    for event, element_id, arrow_ids in zip(
            blue_events, data["line_ids"]["abandoned"],
            data["line_ids"]["abandoned_arrows"]):
        event["element_id"] = element_id
        event["arrow_element_ids"] = arrow_ids
    if settings["save_journal"]:
        _write_log(data)
    if route["length_limit_hit"]:
        forms.alert(("Следующий парный шаг превысит предел общей длины двух труб: {0:g} м. "
                     "Построено {1:.2f} м.")
                    .format(settings["max_length_m"], route["length_mm"] / 1000.0),
                    title="Тёплый пол: предел длины")


try:
    main()
except Exception as error:
    forms.alert("Песочница не завершена: {0}".format(error),
                title="Теплый пол: песочница")
