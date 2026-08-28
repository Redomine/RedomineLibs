# -*- coding: utf-8 -*-

# Настройки. Значения маркеров должны совпадать в обоих скриптах.
MAINLINE_VALUE = u"Магистраль"
HORIZONTAL_VALUE = u"Горизонтальный"
VERTICAL_VALUE = u"Вертикальный"
SLOPED_VALUE = u"Наклонный"
TARGET_PARAMETER_NAME = u"Комментарии"

import os
import sys
import traceback

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Line,
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
    classify_line_orientation,
    remove_comment_tags,
    safe_text,
)


TITLE = u"Ориентация"
ORIENTATION_VALUES = (
    HORIZONTAL_VALUE,
    VERTICAL_VALUE,
    SLOPED_VALUE,
)


def new_stats():
    return {
        "pipes_total": 0,
        "ducts_total": 0,
        "horizontal": 0,
        "vertical": 0,
        "sloped": 0,
        "non_linear": 0,
        "analysis_errors": 0,
        "comments_cleared": 0,
        "clear_errors": 0,
        "comments_updated": 0,
        "comments_unavailable": 0,
        "write_errors": 0,
    }


def comments_parameter(element):
    try:
        return element.LookupParameter(TARGET_PARAMETER_NAME)
    except Exception:
        return None


def element_line(element):
    try:
        location = element.Location
        curve = location.Curve if location else None
        if curve is not None and isinstance(curve, Line):
            return curve
    except Exception:
        pass
    return None


def collect_orientation_assignments(document, stats):
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
    elements = pipes + ducts
    assignments = []

    stats["pipes_total"] = len(pipes)
    stats["ducts_total"] = len(ducts)

    for element in elements:
        line = element_line(element)
        if line is None:
            stats["non_linear"] += 1
            continue

        try:
            start = line.GetEndPoint(0)
            end = line.GetEndPoint(1)
            orientation = classify_line_orientation(
                start.Z,
                end.Z,
                line.Direction.Z,
                HORIZONTAL_VALUE,
                VERTICAL_VALUE,
                SLOPED_VALUE,
            )
        except Exception:
            stats["analysis_errors"] += 1
            continue

        if orientation == HORIZONTAL_VALUE:
            stats["horizontal"] += 1
            assignments.append((element, orientation))
        elif orientation == VERTICAL_VALUE:
            stats["vertical"] += 1
            assignments.append((element, orientation))
        else:
            stats["sloped"] += 1
            assignments.append((element, orientation))

    return elements, assignments


def refresh_orientation_comments(document, elements, assignments, stats):
    if not elements:
        return

    transaction = Transaction(document, u"Обновить ориентацию ВИС")
    transaction_started = False
    try:
        transaction.Start()
        transaction_started = True

        for element in elements:
            parameter = comments_parameter(element)
            if parameter is None:
                continue

            try:
                current_value = safe_text(parameter.AsString())
                cleared_value, removed_count = remove_comment_tags(
                    current_value,
                    ORIENTATION_VALUES,
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

        for element, orientation in assignments:
            parameter = comments_parameter(element)
            if parameter is None or parameter.IsReadOnly:
                stats["comments_unavailable"] += 1
                continue

            try:
                current_value, mainline_count = remove_comment_tags(
                    safe_text(parameter.AsString()),
                    (MAINLINE_VALUE,),
                )
                new_value = add_comment_tag(
                    current_value,
                    orientation,
                )
                if mainline_count > 0:
                    new_value = add_comment_tag(
                        new_value,
                        MAINLINE_VALUE,
                        prepend=True,
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
    lines = [
        u"Проанализировано:",
        u"- труб: {}".format(stats["pipes_total"]),
        u"- воздуховодов: {}".format(stats["ducts_total"]),
        u"",
        u"Определена ориентация:",
        u"- горизонтальных: {}".format(stats["horizontal"]),
        u"- вертикальных: {}".format(stats["vertical"]),
        u"- наклонных: {}".format(stats["sloped"]),
        u"- нелинейных: {}".format(stats["non_linear"]),
        u"- ошибок анализа: {}".format(stats["analysis_errors"]),
        u"",
        u"Запись в параметр «{}»:".format(TARGET_PARAMETER_NAME),
        u"- очищено прежних значений ориентации: {}".format(
            stats["comments_cleared"]
        ),
        u"- записано заново: {}".format(stats["comments_updated"]),
        u"- ошибок очистки: {}".format(stats["clear_errors"]),
        u"- параметр недоступен для записи: {}".format(
            stats["comments_unavailable"]
        ),
        u"- ошибок записи: {}".format(stats["write_errors"]),
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
        elements, assignments = collect_orientation_assignments(
            document,
            stats,
        )
        refresh_orientation_comments(
            document,
            elements,
            assignments,
            stats,
        )
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
