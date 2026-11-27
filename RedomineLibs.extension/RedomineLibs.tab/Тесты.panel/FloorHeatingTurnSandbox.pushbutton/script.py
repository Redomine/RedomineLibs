# -*- coding: utf-8 -*-
from __future__ import print_function

import math
import time
import os
import sys
import uuid
from contextlib import contextmanager

import clr
clr.AddReference("dosymep.Revit.dll")
import dosymep
clr.ImportExtensions(dosymep.Revit)

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException, RegenerationFailedException
from pyrevit import DB, forms, script


MM_PER_FOOT = 304.8
SETTINGS_SECTION = "FloorHeatingTurnSandbox"
ROUTE_MODES = ("spiral_contour_clockwise", "spiral_contour_counterclockwise",
               "serpentine_left_to_right", "serpentine_right_to_left",
               "single_serpentine_left_to_right", "single_serpentine_right_to_left")
SETTINGS_XAML = os.path.join(os.path.dirname(__file__), "SettingsWindow.xaml")
COMMAND_DIR = os.path.dirname(os.path.abspath(__file__))
if COMMAND_DIR not in sys.path:
    sys.path.insert(0, COMMAND_DIR)
from route_timing import RouteProfile, timing_scope
from route_coverage import measure_coverage
from build_progress import BuildProgress, BuildCancelled, MODEL_START, checkpoint, report_stage
from run_journal import RunJournal, batch_journals
from floor_heating_pair import classify_open_ends, spiral_inlets
from vector_walk import (arrowhead_segments,
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


def _world(point, view, elevation=None):
    on_view = (view.Origin + view.RightDirection.Multiply(point[0] / MM_PER_FOOT) +
               view.UpDirection.Multiply(point[1] / MM_PER_FOOT))
    if elevation is None:
        return on_view
    normal = view.ViewDirection
    if abs(normal.Z) <= 1e-9:
        raise ValueError(u"На вертикальном виде нельзя перенести плоскую улитку на отметку подвода.")
    return on_view + normal.Multiply((elevation - on_view.Z) / normal.Z)


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


def _free_end_candidates(document, view, view_only=False):
    from spiral_pipes import route_schema
    result = []
    schema = route_schema()
    collector = (DB.FilteredElementCollector(document, view.Id) if view_only
                 else DB.FilteredElementCollector(document))
    collector = collector.OfCategory(DB.BuiltInCategory.OST_PipeCurves).WhereElementIsNotElementType()
    for pipe in collector:
        checkpoint()
        manager = getattr(pipe, "ConnectorManager", None)
        if manager is None:
            continue
        connectors = [connector for connector in manager.Connectors
                      if connector.ConnectorType == DB.ConnectorType.End]
        free = [(i, c) for i, c in enumerate(connectors) if not c.IsConnected]
        if not free or (schema is not None and pipe.GetEntity(schema).IsValid()):
            continue
        for i, connector in free:
            origin = connector.Origin
            point = _point(origin, view)
            vector = None
            if len(connectors) == 2:
                other = _point(connectors[1 - i].Origin, view)
                vector = (point[0] - other[0], point[1] - other[1])
            result.append({"pipe_id": pipe.Id.GetIdValue(), "element_id": pipe.Id, "point": point,
                           "direction_vector": vector, "elevation_ft": origin.Z})
    return result


def _free_ends(document, view, polygon, cache=None, spiral=False):
    # Snapshot the original inputs once. A later region must not see newly placed pipes.
    if cache is None:
        candidates = _free_end_candidates(document, view, spiral)
    else:
        key = "view_free_ends" if spiral else "free_ends"
        if key not in cache:
            cache[key] = _free_end_candidates(document, view, spiral)
        candidates = cache[key]
    result = []
    for end in candidates:
        if _inside(end["point"], polygon):
            if end["direction_vector"] is None:
                if spiral:
                    continue
                raise ValueError("У трубы {0} ожидается два концевых соединителя".format(
                    end["pipe_id"]))
            result.append(end)
    if spiral:
        return spiral_inlets(result, polygon, cache.get("selected_ids", ()) if cache else ())
    return result


class _RollbackFailed(RuntimeError):
    pass


@contextmanager
def _model_edit(transaction):
    with transaction:
        if transaction.Start() != DB.TransactionStatus.Started:
            raise RuntimeError(u"Revit не начал транзакцию построения.")
        try:
            if isinstance(transaction, DB.Transaction):
                options = transaction.GetFailureHandlingOptions()
                transaction.SetFailureHandlingOptions(options.SetForcedModalHandling(True))
            yield
            status = transaction.Commit()
            if status != DB.TransactionStatus.Committed:
                raise RuntimeError(u"Revit не сохранил изменения. Состояние транзакции — {0}.".format(status))
        finally:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                try:
                    status = transaction.RollBack()
                except Exception as error:
                    raise _RollbackFailed(u"Не удалось откатить изменения. {0}".format(error))
                if status != DB.TransactionStatus.RolledBack:
                    raise _RollbackFailed(u"Revit не подтвердил откат изменений ({0}).".format(status))


def _draw(document, view, branches, join, abandoned_segments,
          abandoned_events, first_side, spacing_mm, min_segment_mm,
          show_abandoned, pipe_ends=None, region_id=None, max_length_m=None):
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
    pipe_ids, fitting_ids = None, None
    title = u"Тёплый пол — трубы и фитинги" if pipe_ends is not None else u"Теплый пол: диагностический фрагмент"
    edit = DB.SubTransaction(document) if document.IsModifiable else DB.Transaction(document, title)
    with _model_edit(edit):
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
        if pipe_ends is not None:
            from spiral_pipes import build_pipes
            pipe_ids, fitting_ids = build_pipes(document, region_id, branches, join, pipe_ends, first_side,
                                                lambda point, z: _world(point, view, z), max_length_m)
        else:
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
    return ids, pipe_ids, fitting_ids


def _build_region(document, view, region, selection_method, settings, run_id,
                  input_cache=None, journal=None):
    started = time.time()
    if journal is None:
        journal = RunJournal(document, run_id, region.Id.GetIdValue(), settings)
    clockwise = settings["clockwise"]
    limit = settings["max_turns"]
    polygon = _polygon(region, view)
    ends = _free_ends(document, view, polygon, input_cache, settings["algorithm"] == "spiral")
    if len(ends) != 2:
        raise ValueError("В области требуется ровно два свободных конца труб; найдено: {0}".format(len(ends)))
    right_end, left_end, direction, separation = classify_open_ends(ends)
    if settings["algorithm"] != "spiral":
        right_start, left_start = calculate_start_points(
            polygon, right_end["point"], left_end["point"], direction,
            settings["offset_mm"], settings["spacing_mm"])
    minimum_curve_mm = document.Application.ShortCurveTolerance * MM_PER_FOOT
    pipe_ends = None
    if settings["algorithm"] == "spiral":
        from spiral_pipes import MIN_PIPE_MM
        minimum_curve_mm = max(minimum_curve_mm, MIN_PIPE_MM)
        pipe_ends = {"right": right_end, "left": left_end}
    prepared = time.time()
    report_stage(u"Расчёт маршрута", 0.0)
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
            from spiral_native import calculate_spiral
            def spiral_progress(policy, index):
                report_stage(u"Расчёт улитки, вариант {0}/3".format(index), MODEL_START * (index - 1) / 3.0)
            with timing_scope("spiral_csharp"):
                route, starts = calculate_spiral(polygon, right_end["point"], left_end["point"],
                                                 direction, settings, minimum_curve_mm, spiral_progress)
            right_start, left_start = starts["right"], starts["left"]
        if settings["algorithm"] != "spiral":
            route = start_at_calculated_entries(route)
            route = limit_route_length(route, settings["max_length_m"] * 1000.0, include_bridge=False)
            route["area_coverage"] = measure_coverage(route, settings["spacing_mm"])
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
    if pipe_ends is not None:
        data["pipe_ids"] = {"right": [], "left": [], "bridge": []}
        data["fitting_ids"] = []
        data["inlet_elevations_mm"] = {side: end["elevation_ft"] * MM_PER_FOOT for side, end in pipe_ends.items()}
    data["timings_seconds"] = {"prepare": prepared - started,
                               "route": calculated - prepared}
    checkpoint()
    journal.save(data)
    test_events = [event for event in route["journal"] if
                   event["action"] == "abandoned_segment"]
    report_stage(u"Построение труб" if pipe_ends is not None else u"Отрисовка", MODEL_START)
    draw_started = time.time()
    lines, pipes, fittings = _draw(document, view, route["branches"], route["join"], route["abandoned_segments"],
                                   test_events, first_side, settings["spacing_mm"], minimum_curve_mm,
                                   settings["show_abandoned"], pipe_ends, region.Id, settings["max_length_m"])
    data["line_ids"] = lines
    if pipe_ends is not None:
        data["pipe_ids"], data["fitting_ids"] = pipes, fittings
    data["timings_seconds"]["draw"] = time.time() - draw_started
    for event, element_id, arrow_ids in zip(
            test_events, data["line_ids"]["abandoned"],
            data["line_ids"]["abandoned_arrows"]):
        event["element_id"] = element_id
        event["arrow_element_ids"] = arrow_ids
    journal.save(data, final=True)
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
    input_cache = {}
    if settings["algorithm"] == "spiral":
        input_cache["selected_ids"] = set(identifier.GetIdValue() for identifier in uidoc.Selection.GetElementIds())
    regions, selection_method = _choose_regions(uidoc)
    if not regions:
        return
    successes = []
    failures = []
    journal_errors = []
    limited = []
    timed_out = []
    cancelled = False
    with forms.ProgressBar(title=u"Тёплый пол [{value}%]", cancellable=True) as bar, \
            BuildProgress(len(regions), bar, __revit__.MainWindowHandle) as progress:
        batch = DB.Transaction(document, u"Тёплый пол — построение контуров")
        with batch_journals(journal_errors) as pending, _model_edit(batch):
            for index, region in enumerate(regions):
                identifier = region.Id.GetIdValue()
                run_id = str(uuid.uuid4())
                journal = RunJournal(document, run_id, identifier, settings, pending)
                try:
                    progress.region(u"Область {0}/{1}: {2}".format(index+1, len(regions), identifier))
                    data = _build_region(document, view, region, selection_method,
                                         settings, run_id, input_cache, journal)
                    route = data["route"]
                    if settings["algorithm"] != "spiral" or route["join"]["connected"]:
                        successes.append(identifier)
                    if route["length_limit_hit"]:
                        limited.append(identifier)
                    if route.get("time_limit_hit"):
                        timed_out.append(identifier)
                except BuildCancelled:
                    cancelled = True
                    journal.record_failure(u"Построение отменено пользователем.")
                except (RegenerationFailedException, _RollbackFailed):
                    raise  # Further edits are unsafe until the parent transaction is rolled back.
                except Exception as error:
                    failures.append((identifier, unicode(error)))
                    journal.record_failure(error)
                if journal.write_error:
                    journal_errors.append((identifier, journal.write_error))
                if cancelled:
                    break
                if index + 1 < len(regions):
                    progress.finish_region()
        if not cancelled:
            progress.finish_region()
    if len(regions) > 1 or failures or journal_errors or limited or timed_out or cancelled:
        lines = [u"Областей: {0}; построено: {1}; ошибок: {2}.".format(
            len(regions), len(successes), len(failures))]
        if cancelled:
            lines.append(u"Построение отменено. Текущая область не построена; готовые области сохранены.")
        if timed_out:
            lines.append(u"Поиск остановлен по времени: {0}.".format(
                u", ".join(str(value) for value in timed_out)))
        if limited:
            message = (u"Достигнут предел длины. Построение труб пропущено в областях {0}."
                       if settings["algorithm"] == "spiral" else u"Предел длины достигнут: {0}.")
            lines.append(message.format(u", ".join(str(value) for value in limited)))
        for identifier, error in failures:
            lines.append(u"Область {0}: {1}".format(identifier, error))
        for identifier, error in journal_errors:
            lines.append(u"Не удалось сохранить журнал области {0}. {1}".format(identifier, error))
        forms.alert(u"\n".join(lines), title=u"Тёплый пол: массовый тест")


try:
    main()
except Exception as error:
    forms.alert("Песочница не завершена: {0}".format(error),
                title="Теплый пол: песочница")
