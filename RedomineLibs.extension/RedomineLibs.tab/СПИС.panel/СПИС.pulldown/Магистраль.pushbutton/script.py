# -*- coding: utf-8 -*-

# Настройки. Значения маркеров должны совпадать в обоих скриптах.
MAINLINE_VALUE = u"Магистраль"
HORIZONTAL_VALUE = u"Горизонтальный"
VERTICAL_VALUE = u"Вертикальный"
SLOPED_VALUE = u"Наклонный"
TARGET_PARAMETER_NAME = u"Комментарии"
SYSTEM_NAME_PARAMETER_NAME = u"ФОП_ВИС_Имя системы"

# Условия редактируются здесь. Для имени системы используется вхождение
# подстроки без учета регистра; размеры указаны в миллиметрах.

"""
inclusive
True → размер больше или равен порогу (>=).
False → размер строго больше порога (>).

Имя системы во всех случаях проверяется по вхождению подстроки без учёта регистра.
"""
MAINLINE_CONDITIONS = {
    "pipes": {
        u"К": {"diameter_mm": 100.0, "inclusive": True},
        u"В1": {"diameter_mm": 40.0, "inclusive": True},
        u"Т3": {"diameter_mm": 40.0, "inclusive": True},
        u"Т4": {"diameter_mm": 32.0, "inclusive": True},

        u"Т1.1-Т2.1": {"diameter_mm": 50.0, "inclusive": True}, # Отопление жилье
        u"Т1.2-Т2.2": {"diameter_mm": 32.0, "inclusive": True}, # Коммерция
        u"Т1.3-Т2.3": {"diameter_mm": 40.0, "inclusive": True}, # Теплоснабжение
        u"Т14/Т24": {"diameter_mm": 50.0, "inclusive": True}, # Отопление паркинг
    },
    "ducts": {
        u"ДВ": {"width_mm": 0.0, "height_mm": 0.0},
        u"ДП": {"width_mm": 0.0, "height_mm": 0.0},
        u"П": {"width_mm": 0.0, "height_mm": 0.0},
        u"В": {"width_mm": 201.0, "height_mm": 201.0},
    },
}

import os
import sys
import traceback

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter,
    ConnectorProfileType,
    FilteredElementCollector,
    Transaction,
)
from Autodesk.Revit.DB.Mechanical import Duct
from Autodesk.Revit.DB.Plumbing import Pipe
from pyrevit import forms


COMMAND_DIR = os.path.dirname(__file__)
EXTENSION_LIB_DIR = os.path.abspath(
    os.path.join(COMMAND_DIR, "..", "..", "..", "..", "lib")
)
if EXTENSION_LIB_DIR not in sys.path:
    sys.path.insert(0, EXTENSION_LIB_DIR)

from spis_mep_tags import (
    add_comment_tag,
    remove_comment_tags,
)


TITLE = u"Магистраль"
MM_PER_FOOT = 304.8
DIMENSION_TOLERANCE_MM = 0.0001


try:
    text_type = unicode
except NameError:
    text_type = str


def safe_text(value):
    if value is None:
        return u""
    try:
        return text_type(value)
    except Exception:
        try:
            return text_type(value.ToString())
        except Exception:
            return u""


def parameter_text(element, parameter_name):
    try:
        parameter = element.LookupParameter(parameter_name)
    except Exception:
        parameter = None

    if parameter is None:
        return u""

    try:
        value = parameter.AsString()
        if value:
            return safe_text(value).strip()
    except Exception:
        pass

    try:
        value = parameter.AsValueString()
        if value:
            return safe_text(value).strip()
    except Exception:
        pass

    return u""


def double_parameter(element, built_in_parameters, fallback_name):
    for built_in_parameter in built_in_parameters:
        try:
            parameter = element.get_Parameter(built_in_parameter)
            if parameter is not None:
                return float(parameter.AsDouble())
        except Exception:
            continue

    try:
        parameter = element.LookupParameter(fallback_name)
        if parameter is not None:
            return float(parameter.AsDouble())
    except Exception:
        pass

    return None


def dimension_mm(element, built_in_parameters, fallback_name):
    value = double_parameter(element, built_in_parameters, fallback_name)
    if value is None:
        return None
    return value * MM_PER_FOOT


def pipe_diameter_mm(pipe):
    return dimension_mm(
        pipe,
        (
            BuiltInParameter.RBS_PIPE_DIAMETER_PARAM,
            BuiltInParameter.RBS_CURVE_DIAMETER_PARAM,
        ),
        u"Диаметр",
    )


def duct_width_mm(duct):
    return dimension_mm(
        duct,
        (BuiltInParameter.RBS_CURVE_WIDTH_PARAM,),
        u"Ширина",
    )


def duct_height_mm(duct):
    return dimension_mm(
        duct,
        (BuiltInParameter.RBS_CURVE_HEIGHT_PARAM,),
        u"Высота",
    )


def duct_is_round(duct):
    try:
        connectors = duct.ConnectorManager.Connectors
        for connector in connectors:
            if connector.Shape == ConnectorProfileType.Round:
                return True
            if connector.Shape in (
                ConnectorProfileType.Rectangular,
                ConnectorProfileType.Oval,
            ):
                return False
    except Exception:
        pass

    diameter = dimension_mm(
        duct,
        (BuiltInParameter.RBS_CURVE_DIAMETER_PARAM,),
        u"Диаметр",
    )
    if diameter is None or diameter <= 0.0:
        return False

    width = duct_width_mm(duct)
    height = duct_height_mm(duct)
    return (
        width is None
        or height is None
        or width <= 0.0
        or height <= 0.0
    )


def conditions_for_system(category_name, system_name):
    normalized_name = safe_text(system_name).strip().upper()
    if not normalized_name:
        return []

    matches = []
    for system_fragment in MAINLINE_CONDITIONS[category_name]:
        if safe_text(system_fragment).upper() in normalized_name:
            matches.append(MAINLINE_CONDITIONS[category_name][system_fragment])
    return matches


def minimum_matches(actual_mm, minimum_mm, inclusive=True):
    if actual_mm is None:
        return False
    if inclusive:
        return actual_mm + DIMENSION_TOLERANCE_MM >= minimum_mm
    return actual_mm > minimum_mm + DIMENSION_TOLERANCE_MM


def pipe_matches(diameter_mm, conditions):
    for condition in conditions:
        if minimum_matches(
            diameter_mm,
            condition["diameter_mm"],
            condition.get("inclusive", True),
        ):
            return True
    return False


def duct_matches(width_mm, height_mm, conditions):
    for condition in conditions:
        if (
            minimum_matches(width_mm, condition["width_mm"])
            and minimum_matches(height_mm, condition["height_mm"])
        ):
            return True
    return False


def new_stats():
    return {
        "pipes_total": 0,
        "ducts_total": 0,
        "round_ducts": 0,
        "pipe_matches": 0,
        "duct_matches": 0,
        "missing_system": 0,
        "unmatched_system": 0,
        "missing_size": 0,
        "comments_cleared": 0,
        "clear_errors": 0,
        "comments_updated": 0,
        "comments_unavailable": 0,
        "write_errors": 0,
    }


def collect_mainline_candidates(document, stats):
    candidates = []
    pipes = list(
        FilteredElementCollector(document)
        .OfClass(Pipe)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ducts = list(
        FilteredElementCollector(document)
        .OfClass(Duct)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    stats["pipes_total"] = len(pipes)
    stats["ducts_total"] = len(ducts)

    for pipe in pipes:
        system_name = parameter_text(pipe, SYSTEM_NAME_PARAMETER_NAME)
        if not system_name:
            stats["missing_system"] += 1
            continue

        conditions = conditions_for_system("pipes", system_name)
        if not conditions:
            stats["unmatched_system"] += 1
            continue

        diameter = pipe_diameter_mm(pipe)
        if diameter is None or diameter <= 0.0:
            stats["missing_size"] += 1
            continue

        if pipe_matches(diameter, conditions):
            candidates.append(pipe)
            stats["pipe_matches"] += 1

    for duct in ducts:
        if duct_is_round(duct):
            stats["round_ducts"] += 1
            continue

        system_name = parameter_text(duct, SYSTEM_NAME_PARAMETER_NAME)
        if not system_name:
            stats["missing_system"] += 1
            continue

        conditions = conditions_for_system("ducts", system_name)
        if not conditions:
            stats["unmatched_system"] += 1
            continue

        width = duct_width_mm(duct)
        height = duct_height_mm(duct)
        if (
            width is None
            or height is None
            or width < 0.0
            or height < 0.0
        ):
            stats["missing_size"] += 1
            continue

        if duct_matches(width, height, conditions):
            candidates.append(duct)
            stats["duct_matches"] += 1

    return pipes + ducts, candidates


def comments_parameter(element):
    try:
        return element.LookupParameter(TARGET_PARAMETER_NAME)
    except Exception:
        return None


def refresh_mainline_comments(document, elements, candidates, stats):
    if not elements:
        return

    transaction = Transaction(document, u"Пометить магистрали")
    transaction_started = False
    try:
        transaction.Start()
        transaction_started = True

        for element in elements:
            parameter = comments_parameter(element)
            if parameter is None:
                continue

            try:
                cleared_value, removed_count = remove_comment_tags(
                    safe_text(parameter.AsString()),
                    (MAINLINE_VALUE,),
                )
                if removed_count == 0:
                    continue
                if parameter.IsReadOnly:
                    stats["clear_errors"] += 1
                    continue

                result = parameter.Set(cleared_value)
                if result == False:
                    stats["clear_errors"] += 1
                else:
                    stats["comments_cleared"] += 1
            except Exception:
                stats["clear_errors"] += 1

        for element in candidates:
            parameter = comments_parameter(element)
            if parameter is None or parameter.IsReadOnly:
                stats["comments_unavailable"] += 1
                continue

            try:
                current_value = safe_text(parameter.AsString())
                orientation_values = []
                for orientation_value in (
                    HORIZONTAL_VALUE,
                    VERTICAL_VALUE,
                    SLOPED_VALUE,
                ):
                    current_value, removed_count = remove_comment_tags(
                        current_value,
                        (orientation_value,),
                    )
                    if removed_count > 0:
                        orientation_values.append(orientation_value)

                new_value = add_comment_tag(
                    current_value,
                    MAINLINE_VALUE,
                    prepend=True,
                )
                for orientation_value in orientation_values:
                    new_value = add_comment_tag(
                        new_value,
                        orientation_value,
                    )
                result = parameter.Set(new_value)
                if result == False:
                    stats["write_errors"] += 1
                else:
                    stats["comments_updated"] += 1
            except Exception:
                stats["write_errors"] += 1

        transaction.Commit()
        transaction_started = False
    except Exception:
        if transaction_started:
            try:
                transaction.RollBack()
            except Exception:
                pass
        raise


def show_report(stats):
    matched = stats["pipe_matches"] + stats["duct_matches"]
    lines = [
        u"Проанализировано:",
        u"- труб: {}".format(stats["pipes_total"]),
        u"- воздуховодов: {}".format(stats["ducts_total"]),
        u"- круглых воздуховодов пропущено: {}".format(
            stats["round_ducts"]
        ),
        u"",
        u"Подошло по условиям: {}".format(matched),
        u"- труб: {}".format(stats["pipe_matches"]),
        u"- воздуховодов: {}".format(stats["duct_matches"]),
        u"",
        u"Запись в параметр «{}»:".format(TARGET_PARAMETER_NAME),
        u"- очищено прежних значений «{}»: {}".format(
            MAINLINE_VALUE,
            stats["comments_cleared"]
        ),
        u"- записано заново: {}".format(stats["comments_updated"]),
        u"- ошибок очистки: {}".format(stats["clear_errors"]),
        u"- параметр недоступен для записи: {}".format(
            stats["comments_unavailable"]
        ),
        u"- ошибок записи: {}".format(stats["write_errors"]),
        u"",
        u"Пропуски исходных данных:",
        u"- нет имени системы: {}".format(stats["missing_system"]),
        u"- система не подходит: {}".format(stats["unmatched_system"]),
        u"- нет требуемого размера: {}".format(stats["missing_size"]),
    ]
    forms.alert(u"\n".join(lines), title=TITLE)


def main():
    ui_document = __revit__.ActiveUIDocument
    if ui_document is None:
        forms.alert(u"Нет активного документа Revit.", title=TITLE)
        return

    stats = new_stats()
    try:
        document = ui_document.Document
        elements, candidates = collect_mainline_candidates(document, stats)
        refresh_mainline_comments(document, elements, candidates, stats)
    except Exception as error:
        try:
            print(traceback.format_exc())
        except Exception:
            pass
        forms.alert(
            u"Не удалось обработать элементы.\n\n{}".format(
                safe_text(error)
            ),
            title=TITLE,
        )
        return

    show_report(stats)


main()
