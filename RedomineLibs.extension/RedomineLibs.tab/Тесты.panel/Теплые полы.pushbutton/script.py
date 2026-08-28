# -*- coding: utf-8 -*-
import os
import sys
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import Transaction, TransactionStatus
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import forms
from pyrevit import script


COMMAND_DIR = os.path.dirname(__file__)
LIB_DIR = os.path.join(COMMAND_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from geometry import (
    UnitConverter,
    PlaneCoordinateSystem,
    ensure_ccw,
    merge_collinear_segments,
    polygon_area,
    remove_duplicate_points
)
from polygon import (
    OffsetLevel,
    PolygonNormalizer,
    PolygonException,
    min_distance_to_polygon_boundary,
    segment_intersects_polygon
)
from clipper_adapter import ClipperAdapter, ClipperUnavailableException, ClipperOperationException
from revit_selection import RevitSelectionService, RevitSelectionException
from debug_visualizer import DebugVisualizer
from settings import SettingsStorage, DEFAULT_SETTINGS
from contour_tree import ContourTreeBuilder, ContourTreeException
from portal_builder import PortalBuilder
from feature_analyzer import FeatureAnalyzer
from spiral_route_builder import SpiralRouteBuilder, SpiralRouteException


TITLE = "Теплый пол - улитка"
MODE_MODEL_LABEL = "Модельные линии"
MODE_DETAIL_LABEL = "Детальные линии"


class UserCancelledException(Exception):
    pass


def show_error(message):
    forms.alert(message, title=TITLE, warn_icon=True)


def parse_float(value, caption):
    if value is None:
        raise UserCancelledException()
    text = str(value).replace(",", ".").strip()
    try:
        return float(text)
    except Exception:
        raise ValueError("Некорректное числовое значение для параметра '{0}': {1}".format(caption, value))


def ask_float(default_value, prompt, caption):
    value = forms.ask_for_string(default=str(default_value), prompt=prompt, title=TITLE)
    return parse_float(value, caption)


def ask_bool(current_value, prompt):
    if current_value:
        options = ["Да", "Нет"]
    else:
        options = ["Нет", "Да"]
    result = forms.CommandSwitchWindow.show(options, message=prompt)
    if result is None:
        raise UserCancelledException()
    return result == "Да"


def ask_create_mode(current_mode):
    if current_mode == "detail":
        options = [MODE_DETAIL_LABEL, MODE_MODEL_LABEL]
    else:
        options = [MODE_MODEL_LABEL, MODE_DETAIL_LABEL]

    result = forms.CommandSwitchWindow.show(options, message="Способ создания диагностической геометрии")
    if result is None:
        raise UserCancelledException()
    if result == MODE_DETAIL_LABEL:
        return "detail"
    return "model"


def ask_user_settings(storage):
    settings = storage.load()
    settings["step_mm"] = ask_float(settings["step_mm"], "Шаг между осями труб, мм", "Шаг")
    settings["boundary_offset_mm"] = ask_float(
        settings["boundary_offset_mm"],
        "Отступ оси крайней трубы от границы, мм",
        "Отступ"
    )
    settings["min_bend_radius_mm"] = ask_float(
        settings["min_bend_radius_mm"],
        "Минимальный радиус изгиба трубы, мм",
        "Минимальный радиус"
    )
    settings["max_loop_length_m"] = ask_float(
        settings["max_loop_length_m"],
        "Максимальная длина контура, м",
        "Максимальная длина"
    )
    settings["tolerance_mm"] = ask_float(settings["tolerance_mm"], "Допуск расчета, мм", "Допуск")
    settings["create_mode"] = ask_create_mode(settings["create_mode"])
    settings["allow_diagonal_transitions"] = ask_bool(
        settings["allow_diagonal_transitions"],
        "Разрешить диагональные переходы?"
    )
    settings["auto_exclude_unavailable_features"] = ask_bool(
        settings["auto_exclude_unavailable_features"],
        "Разрешить автоматическое исключение недоступных выступов?"
    )
    settings["show_diagnostics"] = ask_bool(
        settings["show_diagnostics"],
        "Показывать диагностическую геометрию исходного контура и входных линий?"
    )

    validate_settings(settings)
    storage.save(settings)
    return settings


def validate_settings(settings):
    if settings["step_mm"] <= 0.0:
        raise ValueError("Шаг трубы должен быть больше 0 мм.")
    if settings["boundary_offset_mm"] <= 0.0:
        raise ValueError("Отступ от границы должен быть больше 0 мм.")
    if settings["min_bend_radius_mm"] < 0.0:
        raise ValueError("Минимальный радиус изгиба не может быть отрицательным.")
    if settings["max_loop_length_m"] <= 0.0:
        raise ValueError("Максимальная длина контура должна быть больше 0 м.")
    if settings["tolerance_mm"] <= 0.0:
        raise ValueError("Допуск расчета должен быть больше 0 мм.")


def require_clipper(settings):
    dll_path = os.path.join(LIB_DIR, "Clipper2Lib.dll")
    scale = settings.get("clipper_scale", DEFAULT_SETTINGS["clipper_scale"])
    return ClipperAdapter(dll_path, scale)


def clean_offset_contours(contours, tolerance_feet):
    result = []
    min_area = tolerance_feet * tolerance_feet * 10.0
    for contour in contours:
        points = remove_duplicate_points(contour, tolerance_feet, True)
        points = merge_collinear_segments(points, tolerance_feet, True)
        if len(points) < 3:
            continue
        points = ensure_ccw(points)
        if polygon_area(points) <= min_area:
            continue
        result.append(points)
    return result


def build_offset_levels(adapter, polygon, start_offset_feet, step_feet, tolerance_feet, max_levels):
    levels = []
    for level_index in range(int(max_levels)):
        offset_value = start_offset_feet + step_feet * level_index
        contours = adapter.offset([polygon], -offset_value)
        contours = clean_offset_contours(contours, tolerance_feet)
        if not contours:
            break
        levels.append(OffsetLevel(level_index, offset_value, contours))
    return levels


def validate_input_curve(name, curve, coordinate_system, polygon, tolerance_feet):
    start_xyz = curve.GetEndPoint(0)
    end_xyz = curve.GetEndPoint(1)
    start_plane_distance = abs(coordinate_system.distance_to_plane(start_xyz))
    end_plane_distance = abs(coordinate_system.distance_to_plane(end_xyz))
    if start_plane_distance > tolerance_feet or end_plane_distance > tolerance_feet:
        raise ValueError(
            "Линия {0} не лежит в плоскости границы. Отклонения: {1:.6f} фт, {2:.6f} фт.".format(
                name,
                start_plane_distance,
                end_plane_distance
            )
        )

    start = coordinate_system.to_2d(start_xyz)
    end = coordinate_system.to_2d(end_xyz)
    if not segment_intersects_polygon(start, end, polygon, tolerance_feet):
        raise ValueError("Линия {0} не пересекает границу и не имеет концов внутри контура.".format(name))

    start_distance = min_distance_to_polygon_boundary(start, polygon)
    end_distance = min_distance_to_polygon_boundary(end, polygon)
    if start_distance <= end_distance:
        boundary_end = start
        free_end = end
    else:
        boundary_end = end
        free_end = start

    return {
        "name": name,
        "start": start,
        "end": end,
        "boundary_end": boundary_end,
        "free_end": free_end,
        "boundary_distance": min(start_distance, end_distance)
    }


def validate_supply_return_distance(supply_info, return_info, step_feet, tolerance_feet):
    distance = supply_info["boundary_end"].distance_to(return_info["boundary_end"])
    if distance + tolerance_feet < step_feet:
        raise ValueError(
            "Между точками входа подачи и обратки меньше одного шага трубы: {0:.1f} мм при шаге {1:.1f} мм.".format(
                UnitConverter.feet_to_mm(distance),
                UnitConverter.feet_to_mm(step_feet)
            )
        )


def draw_result(doc, view, coordinate_system, polygon, supply_info, return_info, levels,
                tree_result, portals, feature_result, route_candidate, settings, tolerance_feet):
    transaction = Transaction(doc, "ТП улитка: маршрут этап 3")
    created = []
    try:
        transaction.Start()
        visualizer = DebugVisualizer(doc, view, coordinate_system, tolerance_feet)

        if settings["show_diagnostics"]:
            created.extend(visualizer.draw_closed_polygons([polygon], settings["create_mode"]))
            created.extend(visualizer.draw_closed_polygons(feature_result.pair_area_contours, settings["create_mode"]))
            created.extend(visualizer.draw_open_polylines(
                [
                    [supply_info["start"], supply_info["end"]],
                    [return_info["start"], return_info["end"]]
                ],
                settings["create_mode"]
            ))

            created.extend(visualizer.draw_offset_levels(levels, settings["create_mode"]))
            created.extend(visualizer.draw_open_polylines(portal_polylines(portals), settings["create_mode"]))

        created.extend(visualizer.draw_open_polylines([route_candidate.route_points], settings["create_mode"]))
        transaction.Commit()
        return created
    except Exception:
        if transaction.GetStatus() == TransactionStatus.Started:
            transaction.RollBack()
        raise


def portal_polylines(portals):
    result = []
    for portal in portals:
        result.append(portal.as_polyline())
    return result


def print_diagnostics(boundary_curves, polygon, levels, tree_result, portals,
                      feature_result, route_candidate, route_candidates,
                      supply_info, return_info, settings):
    output = script.get_output()
    output.print_md("### Теплый пол - этап 3: простая прямоугольная улитка")
    print("Исходных сегментов: {0}".format(len(boundary_curves)))
    print("Вершин после нормализации: {0}".format(len(polygon)))
    print("Площадь полигона: {0:.2f} м2".format(polygon_area(polygon) * 0.3048 * 0.3048))
    print("Шаг S: {0:.1f} мм".format(settings["step_mm"]))
    print("Отступ A: {0:.1f} мм".format(settings["boundary_offset_mm"]))
    print("Минимальный радиус изгиба: {0:.1f} мм (будет использован на этапе 5)".format(settings["min_bend_radius_mm"]))
    print("Количество уровней Offset: {0}".format(len(levels)))
    for level in levels:
        print(
            "  C{0}: отступ {1:.1f} мм, компонентов {2}".format(
                level.level,
                UnitConverter.feet_to_mm(level.offset_feet),
                level.component_count()
            )
        )
    print("Дерево контуров: узлов {0}, переходов {1}, ветвлений {2}".format(
        tree_result.node_count(),
        tree_result.transition_count(),
        tree_result.branch_count()
    ))
    print("Сиротских узлов дерева: {0}".format(len(tree_result.orphan_nodes)))
    print("PairArea: компонентов {0}, площадь {1:.2f} м2".format(
        feature_result.pair_area_component_count(),
        feature_result.pair_area_total_area() * 0.3048 * 0.3048
    ))
    print("Порталов между уровнями: {0}".format(len(portals)))
    for portal in portals:
        status = "OK"
        if not portal.is_valid:
            status = "проблема"
        print("  {0}: {1} -> {2}, ширина {3:.1f} мм, {4} {5}".format(
            portal.id,
            portal.parent_node.id,
            portal.child_node.id,
            UnitConverter.feet_to_mm(portal.width),
            status,
            portal.reason
        ))
    print("Событий анализа выступов/горловин: {0}".format(len(feature_result.features)))
    for feature in feature_result.features:
        status = "доступно"
        if not feature.is_accessible:
            status = "недоступно"
        width_text = ""
        if feature.width_feet > 0.0:
            width_text = ", ширина {0:.1f} мм".format(UnitConverter.feet_to_mm(feature.width_feet))
        print("  {0} C{1}.{2}: {3}{4}; {5}".format(
            feature.code,
            feature.level,
            feature.component_index,
            status,
            width_text,
            feature.reason
        ))
    print("Кандидатов маршрута: {0}".format(len(route_candidates)))
    for candidate in route_candidates:
        candidate_status = "OK"
        if candidate.validation is None or not candidate.validation.is_valid():
            candidate_status = "отклонен"
        length_m = 0.0
        error_count = 0
        warning_count = 0
        if candidate.validation is not None:
            length_m = candidate.validation.total_length * 0.3048
            error_count = len(candidate.validation.errors)
            warning_count = len(candidate.validation.warnings) + len(candidate.warnings)
        print("  {0}: {1}, score {2:.3f}, длина {3:.2f} м, ошибок {4}, предупреждений {5}".format(
            candidate.name,
            candidate_status,
            candidate.score,
            length_m,
            error_count,
            warning_count
        ))
    print("Выбранный маршрут: {0}".format(route_candidate.name))
    print("Четные уровни подачи: {0}".format(", ".join([str(item) for item in route_candidate.feed_levels])))
    print("Нечетные уровни обратки: {0}".format(", ".join([str(item) for item in route_candidate.return_levels])))
    print("Точек маршрута: {0}".format(len(route_candidate.route_points)))
    print("Общая длина трассы: {0:.2f} м".format(route_candidate.validation.total_length * 0.3048))
    if route_candidate.validation.min_parallel_distance is not None:
        print("Минимальное расстояние между параллельными участками: {0:.1f} мм".format(
            UnitConverter.feet_to_mm(route_candidate.validation.min_parallel_distance)
        ))
    for warning in route_candidate.validation.warnings:
        print("Предупреждение маршрута: {0}".format(warning))
    for warning in route_candidate.warnings:
        print("Предупреждение подключения: {0}".format(warning))
    print(
        "Ближайший к границе конец подачи: {0}; дистанция до границы {1:.1f} мм".format(
            supply_info["boundary_end"],
            UnitConverter.feet_to_mm(supply_info["boundary_distance"])
        )
    )
    print(
        "Ближайший к границе конец обратки: {0}; дистанция до границы {1:.1f} мм".format(
            return_info["boundary_end"],
            UnitConverter.feet_to_mm(return_info["boundary_distance"])
        )
    )
    print("Результат проверки маршрута: маршрут непрерывен и принят валидатором этапа 3.")


def run():
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    view = doc.ActiveView

    storage = SettingsStorage(COMMAND_DIR)
    settings = storage.load()

    try:
        adapter = require_clipper(settings)
    except ClipperUnavailableException as exc:
        show_error(str(exc))
        return

    settings = ask_user_settings(storage)

    selection_service = RevitSelectionService(uidoc, doc)
    boundary_elements, boundary_curves = selection_service.pick_boundary_curves()
    supply_element, supply_curve = selection_service.pick_single_curve("Выберите первую входную линию: подача")
    return_element, return_curve = selection_service.pick_single_curve("Выберите вторую входную линию: обратка")

    tolerance_feet = UnitConverter.mm_to_feet(settings["tolerance_mm"])
    step_feet = UnitConverter.mm_to_feet(settings["step_mm"])
    offset_feet = UnitConverter.mm_to_feet(settings["boundary_offset_mm"])

    coordinate_system = PlaneCoordinateSystem.from_curves(boundary_curves, tolerance_feet)
    normalizer = PolygonNormalizer(tolerance_feet)
    polygon = normalizer.normalize_curves(boundary_curves, coordinate_system)

    supply_info = validate_input_curve("подачи", supply_curve, coordinate_system, polygon, tolerance_feet)
    return_info = validate_input_curve("обратки", return_curve, coordinate_system, polygon, tolerance_feet)
    validate_supply_return_distance(supply_info, return_info, step_feet, tolerance_feet)

    levels = build_offset_levels(
        adapter,
        polygon,
        offset_feet,
        step_feet,
        tolerance_feet,
        settings.get("max_offset_levels", DEFAULT_SETTINGS["max_offset_levels"])
    )
    if not levels:
        raise ValueError(
            "Не удалось построить первый внутренний Offset C0. Проверьте отступ A={0:.1f} мм и размеры области.".format(
                settings["boundary_offset_mm"]
            )
        )

    tree_builder = ContourTreeBuilder(adapter, tolerance_feet)
    tree_result = tree_builder.build(levels)

    portal_builder = PortalBuilder(tolerance_feet, step_feet)
    portals = portal_builder.build(tree_result)

    feature_analyzer = FeatureAnalyzer(adapter, tolerance_feet)
    feature_result = feature_analyzer.analyze(
        polygon,
        levels,
        tree_result,
        portals,
        offset_feet,
        step_feet,
        UnitConverter.mm_to_feet(settings["min_bend_radius_mm"])
    )

    spiral_builder = SpiralRouteBuilder(
        tolerance_feet,
        step_feet,
        UnitConverter.mm_to_feet(settings["min_bend_radius_mm"])
    )
    route_candidate, route_candidates = spiral_builder.build(
        polygon,
        levels,
        tree_result,
        supply_info,
        return_info,
        UnitConverter.meters_to_feet(settings["max_loop_length_m"])
    )

    created = draw_result(
        doc,
        view,
        coordinate_system,
        polygon,
        supply_info,
        return_info,
        levels,
        tree_result,
        portals,
        feature_result,
        route_candidate,
        settings,
        tolerance_feet
    )
    print_diagnostics(
        boundary_curves,
        polygon,
        levels,
        tree_result,
        portals,
        feature_result,
        route_candidate,
        route_candidates,
        supply_info,
        return_info,
        settings
    )

    forms.alert(
        "Этап 3 выполнен.\nСоздано элементов: {0}\nДлина трассы: {1:.2f} м\nПостроено уровней Offset: {2}".format(
            len(created),
            route_candidate.validation.total_length * 0.3048,
            len(levels)
        ),
        title=TITLE
    )


try:
    run()
except (UserCancelledException, OperationCanceledException):
    pass
except (ValueError, PolygonException, RevitSelectionException, ClipperOperationException, ContourTreeException, SpiralRouteException) as known_error:
    show_error(str(known_error))
except Exception as unexpected_error:
    print(traceback.format_exc())
    show_error("Непредвиденная ошибка:\n{0}".format(unexpected_error))
