# -*- coding: utf-8 -*-
from __future__ import division, print_function

from datetime import datetime
import logging
import math
import os
import sys

import clr
from pyrevit import DB, forms, revit, script
from Autodesk.Revit.Exceptions import ArgumentException as RevitArgumentException
from System import ArgumentException
from System.Collections.Generic import List
from System.IO import Directory

clr.AddReference("dosymep.Revit.dll")
import dosymep
clr.ImportExtensions(dosymep.Revit)

command_dir = os.path.dirname(os.path.abspath(__file__))
if command_dir not in sys.path:
    sys.path.insert(0, command_dir)
from plan_geometry import (
    BOOLEAN_TOLERANCE, build_zones, filter_narrow_zones, inset_zone, intersection,
    merge_zones_across_gaps, minimum_width, polygon, signed_area, union,
)
from System.Windows import Point, Rect


TITLE = u"Отметки плит парковки"
# True возвращает журнал и отчёт pyRevit.
DIAGNOSTICS_ENABLED = False
REGION_NAME = u"B4E_Область перепада высот"
PATTERN_NAME = u"B4E_Перекрёстная штриховка перепада высот"
MM_PER_FOOT = 304.8
SQUARE_METERS_PER_SQUARE_FOOT = 0.09290304
CUT_HEIGHT_MM = 1200
OPEN_HEIGHT_MM = 5000
MIN_ZONE_WIDTH_MM = 500
MAX_ZONE_GAP_MM = 500
HORIZONTAL_TOLERANCE = 1e-7  # Максимальный наклон нормали относительно оси Z.


def select_link(doc):
    selected = [doc.GetElement(element_id) for element_id in revit.uidoc.Selection.GetElementIds()]
    preselected = [element for element in selected
                   if isinstance(element, DB.RevitLinkInstance)
                   and element.GetLinkDocument() is not None]
    if len(preselected) == 1:
        return preselected[0]

    links = DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance)
    by_label = {u"{} [ID {}]".format(link.Name, link.Id.GetIdValue()): link
                for link in links if link.GetLinkDocument() is not None}
    if not by_label:
        forms.alert(u"В проекте нет загруженных связей Revit.", title=TITLE)
        return None
    label = forms.SelectFromList.show(sorted(by_label), title=TITLE, button_name=u"Выбрать связь")
    return by_label.get(label)


def to_outline(loops, origin, transform=None):
    rings = []
    for loop in loops:
        points = []
        for curve in loop:
            vertices = list(curve.Tessellate())
            for vertex in vertices[:-1]:
                point = transform.OfPoint(vertex) if transform is not None else vertex
                points.append(Point(point.X - origin.X, point.Y - origin.Y))
        if len(points) < 3:
            raise ValueError(u"Контур содержит меньше трёх вершин.")
        rings.append(points)
    if not rings:
        raise ValueError(u"У грани нет замкнутых контуров.")
    return polygon(rings)


def crop_outline(view, origin):
    outline = None
    for projection in view.GetModelToProjectionTransforms():
        boundary = projection.GetBoundary()
        if boundary is not None:
            outline = union(outline, to_outline([boundary], origin))
    if outline is None:
        raise ValueError(u"У активного вида не удалось получить контур обрезки.")
    return outline


def overlaps_crop(floor, transform, origin, crop_bounds):
    box = floor.get_BoundingBox(None)
    if box is None:
        return True
    box_transform = transform.Multiply(box.Transform)
    points = [box_transform.OfPoint(DB.XYZ(x, y, z))
              for x in (box.Min.X, box.Max.X)
              for y in (box.Min.Y, box.Max.Y)
              for z in (box.Min.Z, box.Max.Z)]
    bounds = Rect(
        Point(min(p.X for p in points) - origin.X, min(p.Y for p in points) - origin.Y),
        Point(max(p.X for p in points) - origin.X, max(p.Y for p in points) - origin.Y))
    return bounds.IntersectsWith(crop_bounds)


def horizontal_faces(floor, transform):
    tops = [floor.GetGeometryObjectFromReference(reference)
            for reference in DB.HostObjectUtils.GetTopFaces(floor)]
    bottoms = [floor.GetGeometryObjectFromReference(reference)
               for reference in DB.HostObjectUtils.GetBottomFaces(floor)]
    if not tops or not bottoms or any(face is None for face in tops + bottoms):
        raise ValueError(u"Не удалось получить верхние и нижние грани плиты.")
    for face in tops + bottoms:
        if not isinstance(face, DB.PlanarFace):
            return None
        normal = transform.OfVector(face.FaceNormal).Normalize()
        if math.hypot(normal.X, normal.Y) > HORIZONTAL_TOLERANCE:
            return None
    return tops, bottoms


def collect_faces(doc, view, link, origin, crop, journal):
    visible = DB.FilteredElementCollector(doc, view.Id, link.Id).OfClass(DB.Floor)
    visible_ids = set(element_id.GetIdValue() for element_id in visible.ToElementIds())
    journal.info(u"Плиты по фильтру видимости: %d.", len(visible_ids))
    transform = link.GetTotalTransform()
    floors = DB.FilteredElementCollector(link.GetLinkDocument()).OfClass(DB.Floor)
    lower, upper, inclined = [], [], []
    if not visible_ids:
        return lower, upper, inclined
    crop_bounds = crop.Bounds
    for floor in floors.WhereElementIsNotElementType():
        if not overlaps_crop(floor, transform, origin, crop_bounds):
            continue
        floor_id = floor.Id.GetIdValue()
        try:
            faces = horizontal_faces(floor, transform)
            if faces is None:
                inclined.append(floor_id)
                journal.debug(u"Плита %s пропущена: наклонная или криволинейная грань.", floor_id)
                continue
            tops, bottoms = faces
            for is_lower, surfaces, result in ((True, tops, lower), (False, bottoms, upper)):
                if is_lower and floor_id not in visible_ids:
                    continue
                for face in surfaces:
                    outline = intersection(to_outline(face.GetEdgesAsCurveLoops(), origin, transform), crop)
                    if outline is not None:
                        height = transform.OfPoint(face.Origin).Z
                        result.append((height, outline))
                        journal.debug(u"Плита %s, грань %s, отметка модели %.3f мм.", floor_id,
                                      u"верх" if is_lower else u"низ", height * MM_PER_FOOT)
        except Exception as error:
            raise ValueError(u"Плита ID {} в связи. {}".format(floor_id, error))
    return lower, upper, inclined


def make_boundaries(zone, origin, level_z, short_curve):
    loops = List[DB.CurveLoop]()
    for ring in zone.rings:
        points = [DB.XYZ(point.X + origin.X, point.Y + origin.Y, level_z) for point in ring]
        loop = DB.CurveLoop()
        for first, second in zip(points, points[1:] + points[:1]):
            if first.DistanceTo(second) <= short_curve:
                return None
            loop.Append(DB.Line.CreateBound(first, second))
        loops.Add(loop)
    return loops


def configure_region_type(doc, region_type, types):
    target = DB.FillPatternTarget.Drafting
    spacing = 3 / MM_PER_FOOT
    pattern = DB.FillPattern(PATTERN_NAME, target, DB.FillPatternHostOrientation.ToView,
                             math.pi / 4, spacing, spacing)
    element = DB.FillPatternElement.GetFillPatternElementByName(doc, target, PATTERN_NAME)
    if element is None:
        element = DB.FillPatternElement.Create(doc, pattern)
    elif not pattern.IsEqual(element.GetFillPattern()):
        element.SetFillPattern(pattern)

    if region_type is None:
        if not types:
            raise ValueError(u"В проекте нет типа залитой области для создания нового типа.")
        region_type = types[0].Duplicate(REGION_NAME)
    region_type.IsMasking = False
    region_type.ForegroundPatternId = element.Id
    region_type.ForegroundPatternColor = DB.Color(255, 38, 161)
    region_type.BackgroundPatternId = DB.ElementId.InvalidElementId
    return region_type


def region_comment(zone, level_z):
    bottom = (zone.bottom - level_z) * MM_PER_FOOT
    ceiling = (u"нет плиты сверху" if zone.ceiling is None
               else u"Верхняя плита отм. {:.1f} мм".format((zone.ceiling - level_z) * MM_PER_FOOT))
    return u"Нижняя плита отм. {:.1f} мм | {}".format(bottom, ceiling)


def try_create_region(doc, view, region_type, zone, origin, level_z):
    loops = make_boundaries(zone, origin, level_z, doc.Application.ShortCurveTolerance)
    if loops is None:
        return None, u"Длина ребра после перевода в координаты модели меньше допуска Revit."
    if not DB.BoundaryValidation.IsValidBoundaryOnView(doc, view.Id, loops):
        return None, u"Проверка границ Revit отклонила контур."
    with DB.SubTransaction(doc) as region_transaction:
        if region_transaction.Start() != DB.TransactionStatus.Started:
            raise ValueError(u"Не удалось начать подчинённую транзакцию области.")
        try:
            region = DB.FilledRegion.Create(doc, region_type.Id, view.Id, loops)
        except (RevitArgumentException, ArgumentException) as error:
            if (getattr(error, "ParamName", None) != "boundaries"
                    and u"Parameter name: boundaries" not in unicode(error)):
                raise
            if region_transaction.RollBack() != DB.TransactionStatus.RolledBack:
                raise
            return None, unicode(error)
        comments = region.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if comments is None or comments.IsReadOnly or not comments.Set(region_comment(zone, level_z)):
            raise ValueError(u"Не удалось записать параметр «Комментарии» области ID {}.".format(
                region.Id.GetIdValue()))
        if region_transaction.Commit() != DB.TransactionStatus.Committed:
            raise ValueError(u"Revit не подтвердил создание области.")
    return region.Id, None


def log_rejected_zone(journal, number, zone, origin, level_z, reason):
    if not journal.isEnabledFor(logging.WARNING):
        return
    ceiling = (u"нет" if zone.ceiling is None
               else u"{:.3f}".format((zone.ceiling - level_z) * MM_PER_FOOT))
    journal.warning(u"Зона № %s. Низ %.3f мм, верх %s мм относительно уровня. "
                    u"Площадь %.6f м², число контуров %d. %s",
                    number, (zone.bottom - level_z) * MM_PER_FOOT, ceiling,
                    zone.area * SQUARE_METERS_PER_SQUARE_FOOT, len(zone.rings), reason)
    for index, ring in enumerate(zone.rings, 1):
        journal.debug(u"Зона № %s, контур %d. Вершины XY в мм относительно начала модели: %s",
                      number, index, u"; ".join(u"({:.5f}, {:.5f})".format(
                          (point.X + origin.X) * MM_PER_FOOT,
                          (point.Y + origin.Y) * MM_PER_FOOT) for point in ring))


def replace_regions(doc, view, zones, origin, level_z, min_length, crop, journal):
    types = list(DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
    region_type = next((item for item in types if DB.Element.Name.GetValue(item) == REGION_NAME), None)
    old = List[DB.ElementId]()
    if region_type is not None:
        regions = DB.FilteredElementCollector(doc).OfClass(DB.FilledRegion).OwnedByView(view.Id)
        for region in regions:
            if region.GetTypeId().Equals(region_type.Id):
                old.Add(region.Id)
    if not zones and not old.Count:
        return [], 0, 0, 0

    created = []
    repaired = rejected = 0
    with DB.Transaction(doc, TITLE) as transaction:
        if transaction.Start() != DB.TransactionStatus.Started:
            raise ValueError(u"Не удалось начать транзакцию построения областей.")
        try:
            if old.Count:
                doc.Delete(old)
            if zones:
                region_type = configure_region_type(doc, region_type, types)
                for index, zone in enumerate(zones, 1):
                    region_id, reason = try_create_region(doc, view, region_type, zone, origin, level_z)
                    if region_id is not None:
                        created.append((zone, region_id))
                        continue
                    log_rejected_zone(journal, index, zone, origin, level_z, reason)
                    repair_issues = []
                    parts = inset_zone(zone, min_length, repair_issues)
                    log_contour_issues(journal, repair_issues, origin, level_z)
                    recovered = []
                    if len(parts) > 1:
                        protected = [polygon(other.rings) for other in zones if other is not zone]
                        rejoined, gaps, _ = merge_zones_across_gaps(
                            parts, MAX_ZONE_GAP_MM / MM_PER_FOOT, MIN_ZONE_WIDTH_MM / MM_PER_FOOT,
                            min_length, crop, protected)
                        if len(rejoined) == 1 and gaps:
                            combined = rejoined[0]
                            region_id, reason = try_create_region(doc, view, region_type, combined, origin, level_z)
                            if region_id is None:
                                log_rejected_zone(journal, index, combined, origin, level_z, reason)
                            else:
                                journal.info(u"Зона № %d. После отступа объединено частей: %d. "
                                             u"Максимальный зазор %.3f мм.",
                                             index, len(parts), max(gap[0] for gap in gaps) * MM_PER_FOOT)
                                parts = [combined]
                                recovered.append((combined, region_id))
                    for part_index, part in enumerate([] if recovered else parts, 1):
                        width_mm = minimum_width(part.rings[0]) * MM_PER_FOOT
                        if width_mm + BOOLEAN_TOLERANCE * MM_PER_FOOT < MIN_ZONE_WIDTH_MM:
                            journal.info(u"Часть зоны № %d.%d пропущена после отступа. Ширина %.3f мм.",
                                         index, part_index, width_mm)
                            continue
                        region_id, reason = try_create_region(doc, view, region_type, part, origin, level_z)
                        if region_id is None:
                            log_rejected_zone(journal, "{}.{}".format(index, part_index),
                                              part, origin, level_z, reason)
                        else:
                            recovered.append((part, region_id))
                    if recovered:
                        repaired += 1
                        created.extend(recovered)
                        journal.info(u"Зона № %d после отступа %.3f мм. Создано частей %d из %d.",
                                     index, min_length * MM_PER_FOOT, len(recovered), len(parts))
                    if not recovered or len(recovered) != len(parts) or repair_issues:
                        rejected += 1
                        journal.warning(u"Зона № %d пропущена полностью или частично.", index)
                if not created:
                    if transaction.RollBack() != DB.TransactionStatus.RolledBack:
                        raise ValueError(u"Revit не подтвердил откат перестроения.")
                    return [], 0, repaired, rejected
            if transaction.Commit() != DB.TransactionStatus.Committed:
                raise ValueError(u"Revit не подтвердил перестроение областей.")
        except Exception:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
            raise
    return created, old.Count, repaired, rejected


def print_report(output, view, link, zones, ids, removed, inclined):
    level_z = view.GenLevel.ProjectElevation
    print(u"План «{}», связь «{}»".format(view.Name, link.Name))
    print(u"Создано областей {}. Удалено прежних областей {}.".format(len(ids), removed))
    if inclined:
        print(u"Пропущены наклонные или криволинейные плиты. ID в связи: {}.".format(
            u", ".join(unicode(floor_id) for floor_id in sorted(inclined))))
    if not ids:
        print(u"Горизонтальные участки видимых нижних плит в пределах обрезки не найдены.")
        return

    print(u"Отметки в мм относительно уровня «{}». Площадь по контуру области.".format(view.GenLevel.Name))
    headings = (u"ID области", u"Площадь, м²", u"Верх нижней плиты", u"Низ верхней плиты",
                u"Низ", u"Верх", u"Сечение", u"Примечание")
    rows = [u"<tr>{}</tr>".format(u"".join(u"<th>{}</th>".format(value) for value in headings))]
    for zone, element_id in zip(zones, ids):
        bottom = (zone.bottom - level_z) * MM_PER_FOOT
        cut = bottom + CUT_HEIGHT_MM
        if zone.ceiling is None:
            ceiling_text, top, note = u"—", bottom + OPEN_HEIGHT_MM, u"Нет плиты сверху"
        else:
            top = (zone.ceiling - level_z) * MM_PER_FOOT
            ceiling_text = u"{:.1f}".format(top)
            note = u"Просвет меньше 1200 мм" if top < cut else u""
        values = (output.linkify(element_id, title=str(element_id.GetIdValue())),
                  u"{:.2f}".format(zone.area * SQUARE_METERS_PER_SQUARE_FOOT),
                  u"{:.1f}".format(bottom), ceiling_text, u"{:.1f}".format(bottom),
                  u"{:.1f}".format(top), u"{:.1f}".format(cut), note)
        rows.append(u"<tr>{}</tr>".format(u"".join(u"<td>{}</td>".format(value) for value in values)))
    output.print_html(u"<table>{}</table>".format(u"".join(rows)))


def log_contour_issues(journal, issues, origin, level_z):
    if not journal.isEnabledFor(logging.WARNING):
        return
    messages = {
        "degenerate": u"Вырожденный контур отброшен",
        "skip_zone": u"Область пропущена из-за короткого контура",
        "omit_hole": u"Микроотверстие опущено",
    }
    for issue in issues:
        points = issue.points
        shortest = min((a - b).Length for a, b in zip(points, points[1:] + points[:1]))
        model_x = [(point.X + origin.X) * MM_PER_FOOT for point in points]
        model_y = [(point.Y + origin.Y) * MM_PER_FOOT for point in points]
        ceiling = (u"нет" if issue.ceiling is None
                   else u"{:.3f}".format((issue.ceiling - level_z) * MM_PER_FOOT))
        journal.warning(
            u"%s. Контур %s, верх нижней плиты %.3f мм, низ верхней %s мм относительно уровня. "
            u"Площадь контура %.8f м², вершин %d, кратчайшее ребро %.6f мм, "
            u"границы XY модели X=[%.3f, %.3f], Y=[%.3f, %.3f] мм.",
            messages[issue.action], issue.ring, (issue.bottom - level_z) * MM_PER_FOOT, ceiling,
            abs(signed_area(points)) * SQUARE_METERS_PER_SQUARE_FOOT, len(points),
            shortest * MM_PER_FOOT, min(model_x), max(model_x), min(model_y), max(model_y))


def print_status(message):
    if DIAGNOSTICS_ENABLED:
        print(message)


def run(journal, journal_path):
    doc = revit.doc
    journal.info(u"Revit %s, проект «%s».", doc.Application.VersionNumber, doc.Title)
    if doc.Application.VersionNumber != "2024":
        journal.warning(u"Версия Revit вне диапазона команды.")
        forms.alert(u"Команда рассчитана на Revit 2024.", title=TITLE)
        return
    view = doc.ActiveView
    journal.info(u"Активный вид «%s», ID %s, тип %s.", view.Name, view.Id.GetIdValue(), view.ViewType)
    if not isinstance(view, DB.ViewPlan) or view.GenLevel is None or view.ViewType not in (
            DB.ViewType.FloorPlan, DB.ViewType.EngineeringPlan):
        journal.warning(u"Активный вид не поддерживается.")
        forms.alert(u"Откройте план этажа или конструктивный план.", title=TITLE)
        return
    if not view.CropBoxActive:
        journal.warning(u"Обрезка активного вида отключена.")
        forms.alert(u"Включите обрезку вида и ограничьте нужный участок парковки.", title=TITLE)
        return
    link = select_link(doc)
    if link is None:
        journal.info(u"Связь не выбрана.")
        return

    if DIAGNOSTICS_ENABLED:
        output = script.get_output()
        output.set_title(TITLE)
        print_status(u"Журнал: {}".format(journal_path))
    journal.info(u"Связь «%s», ID %s.", link.Name, link.Id.GetIdValue())
    try:
        origin = view.CropBox.Transform.Origin
        crop = crop_outline(view, origin)
        level_z = view.GenLevel.ProjectElevation
        short_curve = doc.Application.ShortCurveTolerance
        journal.info(u"Отметка уровня модели %.3f мм. Допуск короткой кривой %.6f мм.",
                     level_z * MM_PER_FOOT, short_curve * MM_PER_FOOT)
        lower, upper, inclined = collect_faces(doc, view, link, origin, crop, journal)
        journal.info(u"Граней нижних плит: %d. Граней верхних плит: %d. Пропущено плит: %d.",
                     len(lower), len(upper), len(inclined))
        issues = []
        zones = build_zones(lower, upper, short_curve * (1 + 1e-6), issues)
        log_contour_issues(journal, issues, origin, level_z)
        journal.info(u"Зон после обработки контуров: %d. Замечаний к контурам: %d.",
                     len(zones), len(issues))
        if issues:
            print_status(u"Замечаний к коротким или вырожденным контурам: {}. Подробности в журнале.".format(
                len(issues)))
            if not zones:
                journal.warning(u"После обработки не осталось пригодных зон. Замена отменена.")
                print_status(u"Из-за дефектов контуров пригодных зон не осталось. Прежние области сохранены.")
                return
        zones, narrow = filter_narrow_zones(zones, MIN_ZONE_WIDTH_MM / MM_PER_FOOT,
                                            short_curve * (1 + 1e-6))
        if DIAGNOSTICS_ENABLED and narrow:
            absorbed_count = 0
            for zone, width, absorbed in narrow:
                absorbed_count += int(absorbed)
                measurement = (u"Не найдено участка шириной {} мм.".format(MIN_ZONE_WIDTH_MM)
                               if width is None else u"Ширина проекции {:.3f} мм.".format(width * MM_PER_FOOT))
                log_rejected_zone(journal, u"фильтр ширины", zone, origin, level_z,
                                  u"{} {}.".format(measurement,
                                      u"Объединены соседние зоны" if absorbed else u"Зона исключена"))
            print_status(u"Зон уже {} мм: {}. Присоединено при объединении соседей: {}. Исключено: {}.".format(
                MIN_ZONE_WIDTH_MM, len(narrow), absorbed_count, len(narrow) - absorbed_count))
        zones, merged_gaps, dividers = merge_zones_across_gaps(
            zones, MAX_ZONE_GAP_MM / MM_PER_FOOT, MIN_ZONE_WIDTH_MM / MM_PER_FOOT,
            short_curve * (1 + 1e-6), crop)
        for divider in dividers:
            log_rejected_zone(journal, u"узкая разделяющая зона", divider, origin, level_z,
                              u"Зона исключена. В месте соединения ширина меньше {} мм.".format(
                                  MIN_ZONE_WIDTH_MM))
        if dividers:
            print_status(u"Исключено зон с локальным сужением в месте соединения: {}.".format(len(dividers)))
        for distance, first, second in merged_gaps:
            journal.info(u"Объединены зоны с одинаковыми отметками через зазор %.3f мм. "
                         u"Площади исходных зон %.3f и %.3f м². %s.",
                         distance * MM_PER_FOOT, first.area * SQUARE_METERS_PER_SQUARE_FOOT,
                         second.area * SQUARE_METERS_PER_SQUARE_FOOT, region_comment(first, level_z))
        if merged_gaps:
            print_status(u"Объединений через зазоры до {} мм: {}.".format(MAX_ZONE_GAP_MM, len(merged_gaps)))
        created, removed, repaired, rejected = replace_regions(
            doc, view, zones, origin, level_z, short_curve * (1 + 1e-6), crop, journal)
    except Exception as error:
        journal.exception(u"Перестроение областей прервано.")
        print_status(u"Перестроение областей прервано. {}".format(error))
        return
    if repaired or rejected:
        journal.info(u"Восстановлено зон: %d. Пропущено полностью или частично: %d.", repaired, rejected)
        print_status(u"Восстановлено зон с отступом {:.3f} мм: {}. Пропущено полностью или частично: {}.".format(
            short_curve * MM_PER_FOOT, repaired, rejected))
    if zones and not created:
        journal.warning(u"Ни одной области не создано. Прежний комплект сохранён.")
        print_status(u"Revit не принял ни одного контура. Прежние области сохранены.")
        return
    if DIAGNOSTICS_ENABLED:
        created_zones = [zone for zone, element_id in created]
        ids = [element_id for zone, element_id in created]
        journal.info(u"Перестроение завершено. Удалено областей: %d. Создано: %d.", removed, len(ids))
        journal.info(u"ID созданных областей: %s.", u", ".join(unicode(item.GetIdValue()) for item in ids))
        print_report(output, view, link, created_zones, ids, removed, inclined)


def main():
    if not DIAGNOSTICS_ENABLED:
        run(logging.Logger("parking_view_range", logging.CRITICAL + 1), None)
        return

    folder = os.path.join(os.environ["LOCALAPPDATA"], "RvtMcp", "parking-view-range", "logs")
    Directory.CreateDirectory(folder)
    path = os.path.join(folder, datetime.now().strftime("parking-%Y%m%d-%H%M%S-%f.log"))
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    journal = logging.Logger("parking_view_range", logging.DEBUG)
    journal.addHandler(handler)
    try:
        run(journal, path)
    except Exception:
        journal.exception(u"Ошибка команды.")
        raise
    finally:
        journal.removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    main()
