# -*- coding: utf-8 -*-
SHOW_REPORT = True
SAVE_LOG = False

import io
import math
import os
import sys
import time
import traceback
from datetime import datetime

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    AttachmentType,
    BuiltInParameter,
    ConnectorProfileType,
    Element,
    ElementId,
    FilteredElementCollector,
    InsulationLiningBase,
    Level,
    Line,
    MEPSystemClassification,
    RevitLinkInstance,
    StorageType,
    SubTransaction,
    Transaction,
    TransactionStatus,
    XYZ,
)
from Autodesk.Revit.DB.Mechanical import Duct, DuctType, MechanicalSystemType
from Autodesk.Revit.DB.Plumbing import Pipe
from System.Collections.Generic import List as CList
from pyrevit import forms


COMMAND_DIR = os.path.dirname(__file__)
LIB_DIR = os.path.join(COMMAND_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from bundle_geometry import (
    BundleAnalysisLimitError,
    PipeSegment,
    apply_transform_chain,
    bundle_meets_minimum_dimension,
    effective_pipe_radius,
    find_pipe_bundles,
    make_pipe_source_key,
)


TITLE = u"Пучки труб"
DUCT_TYPE_NAME = u"B4E_A101_СПИС_Пучок труб"
ADSK_NAME_PARAMETER = u"ADSK_Наименование"
ADSK_NAME_VALUE = u"!Не учитывать"

MM_PER_FOOT = 304.8
MAX_OUTSIDE_GAP = 150.0 / MM_PER_FOOT
MAX_AXIS_HEIGHT_DIFFERENCE = 150.0 / MM_PER_FOOT
PARALLEL_ANGLE_TOLERANCE = math.radians(2.0)
MINIMUM_OVERLAP = 3.0 / MM_PER_FOOT
MINIMUM_BUNDLE_DIMENSION = 400.0 / MM_PER_FOOT

REVIT_ELEMENT_ID_64BIT_VERSION = 2024
MAX_ANALYSIS_PAIR_CHECKS = 250000
MAX_ANALYSIS_SECONDS = 20.0
ANALYSIS_PROGRESS_INTERVAL = 10000
PIPE_COLLECTION_PROGRESS_INTERVAL = 100
MAX_LOGGED_COLLECTION_ERRORS = 50
MAX_LINK_DEPTH = 8
LOG_PATH = None


try:
    text_type = unicode
    binary_type = str
except NameError:
    text_type = str
    binary_type = bytes


class CommandError(Exception):
    pass


def safe_text(value):
    if value is None:
        return u""
    if isinstance(value, binary_type) and not isinstance(value, text_type):
        try:
            return value.decode(sys.getfilesystemencoding() or "utf-8", "replace")
        except Exception:
            pass
    try:
        return text_type(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return u"<не удалось получить текст>"


def get_revit_major_version():
    try:
        return int(__revit__.Application.VersionNumber)
    except Exception:
        return 0


REVIT_MAJOR_VERSION = get_revit_major_version()


def create_log_path():
    if not SAVE_LOG:
        return None
    base_directory = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("TEMP")
        or COMMAND_DIR
    )
    base_directory = safe_text(base_directory)
    log_directory = os.path.join(base_directory, "RedomineLibs", "Logs")
    try:
        if not os.path.isdir(log_directory):
            os.makedirs(log_directory)
        filename = "pipe_bundles_{}_{}.log".format(
            datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            os.getpid(),
        )
        return os.path.join(log_directory, filename)
    except Exception:
        return None


def write_diagnostic(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = u"{} [{}] {}".format(timestamp, level, safe_text(message))
    if SHOW_REPORT:
        try:
            print(line)
        except Exception:
            pass

    if not LOG_PATH:
        return
    try:
        stream = io.open(LOG_PATH, "a", encoding="utf-8")
        try:
            stream.write(line + u"\n")
            stream.flush()
        finally:
            stream.close()
    except Exception:
        pass


def write_exception(context, error):
    write_diagnostic(
        u"{}: {}".format(context, safe_text(error)),
        level="ERROR",
    )
    try:
        trace = safe_text(traceback.format_exc()).strip()
    except Exception:
        trace = u""
    if trace and trace != u"NoneType: None":
        for line in trace.splitlines():
            write_diagnostic(u"  {}".format(line), level="TRACE")


def log_hint():
    if not LOG_PATH:
        return u""
    return u"\n\nОтладочный журнал:\n{}".format(LOG_PATH)


def id_value(element_id):
    if element_id is None:
        return -1

    if REVIT_MAJOR_VERSION >= REVIT_ELEMENT_ID_64BIT_VERSION:
        try:
            return int(element_id.Value)
        except Exception:
            return -1

    if REVIT_MAJOR_VERSION > 0:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return -1

    # VersionNumber is normally available. Probe both APIs only as a fallback
    # for non-standard pyRevit hosts.
    try:
        return int(element_id.Value)
    except Exception:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return -1


def is_valid_id(element_id):
    return element_id is not None and id_value(element_id) >= 0


def element_name(element):
    try:
        parameter = element.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if parameter:
            value = parameter.AsString()
            if value:
                return safe_text(value)
    except Exception:
        pass
    try:
        return safe_text(element.Name)
    except Exception:
        try:
            return safe_text(Element.Name.GetValue(element))
        except Exception:
            return u""


def get_pipe_outer_diameter(pipe):
    built_in_parameters = []
    try:
        built_in_parameters.append(BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)
    except Exception:
        pass
    try:
        built_in_parameters.append(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    except Exception:
        pass
    try:
        built_in_parameters.append(BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
    except Exception:
        pass

    for built_in_parameter in built_in_parameters:
        try:
            parameter = pipe.get_Parameter(built_in_parameter)
            if parameter:
                value = parameter.AsDouble()
                if value > 0.0:
                    return value
        except Exception:
            continue
    return None


def get_pipe_insulation(document, pipe):
    try:
        insulation_ids = InsulationLiningBase.GetInsulationIds(
            document,
            pipe.Id,
        )
    except Exception as error:
        raise CommandError(
            u"Не удалось получить изоляцию трубы: {}".format(safe_text(error))
        )

    if insulation_ids is None:
        return 0.0, 0

    maximum_thickness = 0.0
    insulation_count = 0
    for insulation_id in insulation_ids:
        insulation_count += 1
        try:
            insulation = document.GetElement(insulation_id)
            if insulation is None:
                raise CommandError(
                    u"Элемент изоляции {} не найден.".format(
                        id_value(insulation_id)
                    )
                )
            thickness = float(insulation.Thickness)
            if math.isnan(thickness) or math.isinf(thickness) or thickness < 0.0:
                raise CommandError(
                    u"Некорректная толщина изоляции {} у элемента {}.".format(
                        safe_text(thickness),
                        id_value(insulation_id),
                    )
                )
            maximum_thickness = max(maximum_thickness, thickness)
        except CommandError:
            raise
        except Exception as error:
            raise CommandError(
                u"Не удалось прочитать толщину изоляции {}: {}".format(
                    id_value(insulation_id),
                    safe_text(error),
                )
            )

    return maximum_thickness, insulation_count


def get_pipe_level_id(pipe):
    try:
        reference_level = pipe.ReferenceLevel
        if reference_level and is_valid_id(reference_level.Id):
            return reference_level.Id
    except Exception:
        pass
    try:
        if is_valid_id(pipe.LevelId):
            return pipe.LevelId
    except Exception:
        pass
    return ElementId.InvalidElementId


def document_label(document):
    try:
        title = safe_text(document.Title).strip()
        if title:
            return title
    except Exception:
        pass
    return u"<документ без названия>"


def document_identity(document):
    try:
        path = safe_text(document.PathName).strip()
        if path:
            return path.lower()
    except Exception:
        pass
    try:
        return u"{}#{}".format(document_label(document), document.GetHashCode())
    except Exception:
        return document_label(document)


def get_link_transform(link_instance):
    try:
        return link_instance.GetTotalTransform()
    except Exception:
        try:
            return link_instance.GetTransform()
        except Exception as error:
            raise CommandError(
                u"Не удалось получить преобразование экземпляра связи: {}".format(
                    safe_text(error)
                )
            )


def new_source_stats(label, is_link, instance_ids):
    return {
        "label": label,
        "is_link": is_link,
        "instance_ids": tuple(instance_ids),
        "total": 0,
        "accepted": 0,
        "non_linear": 0,
        "no_diameter": 0,
        "no_plan_length": 0,
        "insulated": 0,
        "multiple_insulations": 0,
        "failed": 0,
        "source_failed": False,
    }


def increment_collection_stat(stats, source_stats, name):
    stats[name] += 1
    source_stats[name] += 1


def collect_source_pipe_segments(
    source_document,
    transform_chain,
    link_instance_ids,
    source_label,
    is_host_source,
    required,
    stats,
    segments,
    errors,
):
    source_stats = new_source_stats(
        source_label,
        not is_host_source,
        link_instance_ids,
    )
    stats["sources"].append(source_stats)
    write_diagnostic(u"Чтение источника труб: {}.".format(source_label))
    try:
        pipes = list(
            FilteredElementCollector(source_document)
            .OfClass(Pipe)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception as error:
        source_stats["source_failed"] = True
        stats["source_failures"] += 1
        write_exception(
            u"Не удалось получить список труб из источника '{}'".format(
                source_label
            ),
            error,
        )
        if len(errors) < MAX_LOGGED_COLLECTION_ERRORS:
            errors.append(
                u"Источник '{}': не удалось получить список труб: {}".format(
                    source_label,
                    safe_text(error),
                )
            )
        if required:
            raise CommandError(u"Не удалось получить список труб из проекта.")
        return False

    source_stats["total"] = len(pipes)
    stats["total"] += len(pipes)
    if is_host_source:
        stats["host_total"] += len(pipes)
    else:
        stats["linked_total"] += len(pipes)
    write_diagnostic(
        u"Источник '{}': получено труб {}.".format(source_label, len(pipes))
    )

    for index, pipe in enumerate(pipes, 1):
        pipe_id = -1
        try:
            pipe_id = id_value(pipe.Id)
            pipe_key = make_pipe_source_key(pipe_id, link_instance_ids)
            if (
                index == 1
                or index == len(pipes)
                or index % PIPE_COLLECTION_PROGRESS_INTERVAL == 0
            ):
                write_diagnostic(
                    u"Источник '{}': трубы {}/{}; текущий ключ {}.".format(
                        source_label,
                        index,
                        len(pipes),
                        pipe_key,
                    )
                )
            location = pipe.Location
            curve = location.Curve if location else None
            if curve is None or not isinstance(curve, Line):
                increment_collection_stat(stats, source_stats, "non_linear")
                continue

            diameter = get_pipe_outer_diameter(pipe)
            if diameter is None:
                increment_collection_stat(stats, source_stats, "no_diameter")
                continue
            insulation_thickness, insulation_count = get_pipe_insulation(
                source_document,
                pipe,
            )

            start = apply_transform_chain(curve.GetEndPoint(0), transform_chain)
            end = apply_transform_chain(curve.GetEndPoint(1), transform_chain)
            level_id = (
                get_pipe_level_id(pipe)
                if is_host_source
                else ElementId.InvalidElementId
            )
            try:
                segment = PipeSegment(
                    pipe_key,
                    (start.X, start.Y, start.Z),
                    (end.X, end.Y, end.Z),
                    effective_pipe_radius(diameter, insulation_thickness),
                    {
                        "level_id": level_id,
                        "outer_diameter": diameter,
                        "insulation_thickness": insulation_thickness,
                        "source_document": source_label,
                        "source_pipe_id": pipe_id,
                        "link_instance_ids": tuple(link_instance_ids),
                    },
                )
            except ValueError:
                increment_collection_stat(stats, source_stats, "no_plan_length")
                continue

            segments.append(segment)
            increment_collection_stat(stats, source_stats, "accepted")
            if is_host_source:
                stats["host_accepted"] += 1
            else:
                stats["linked_accepted"] += 1
            if insulation_count > 0:
                increment_collection_stat(stats, source_stats, "insulated")
                stats["maximum_insulation_thickness"] = max(
                    stats["maximum_insulation_thickness"],
                    insulation_thickness,
                )
            if insulation_count > 1:
                increment_collection_stat(
                    stats,
                    source_stats,
                    "multiple_insulations",
                )
        except Exception as error:
            increment_collection_stat(stats, source_stats, "failed")
            if len(errors) < MAX_LOGGED_COLLECTION_ERRORS:
                errors.append(
                    u"Источник '{}', труба {}: {}".format(
                        source_label,
                        pipe_id,
                        safe_text(error),
                    )
                )
                write_exception(
                    u"Ошибка чтения трубы {} из источника '{}'".format(
                        pipe_id,
                        source_label,
                    ),
                    error,
                )

    write_diagnostic(
        u"Источник '{}': принято труб {}, ошибок {}.".format(
            source_label,
            source_stats["accepted"],
            source_stats["failed"],
        )
    )
    return True


def collect_linked_pipe_segments(
    parent_document,
    parent_transform_chain,
    parent_instance_ids,
    ancestry,
    depth,
    stats,
    segments,
    errors,
):
    try:
        link_instances = list(
            FilteredElementCollector(parent_document)
            .OfClass(RevitLinkInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception as error:
        stats["link_scan_failures"] += 1
        write_exception(
            u"Не удалось получить список связей из '{}'".format(
                document_label(parent_document)
            ),
            error,
        )
        if len(errors) < MAX_LOGGED_COLLECTION_ERRORS:
            errors.append(
                u"Документ '{}': не удалось получить список связей: {}".format(
                    document_label(parent_document),
                    safe_text(error),
                )
            )
        return

    if depth >= MAX_LINK_DEPTH:
        if link_instances:
            stats["maximum_link_depth_skipped"] += 1
            write_diagnostic(
                u"Достигнут предел вложенности связей {} для '{}'.".format(
                    MAX_LINK_DEPTH,
                    document_label(parent_document),
                ),
                level="WARNING",
            )
        return

    for link_instance in link_instances:
        link_instance_id = id_value(link_instance.Id)
        child_instance_ids = tuple(parent_instance_ids) + (link_instance_id,)
        stats["link_instances_found"] += 1
        try:
            link_type = parent_document.GetElement(link_instance.GetTypeId())
            if link_type is None:
                raise CommandError(u"Не найден тип экземпляра связи.")
            if depth == 0:
                try:
                    if link_type.IsNestedLink:
                        stats["nested_instances_skipped"] += 1
                        continue
                except Exception:
                    pass
            elif link_type.AttachmentType != AttachmentType.Attachment:
                stats["overlay_links_skipped"] += 1
                write_diagnostic(
                    u"Вложенная Overlay-связь {} пропущена.".format(
                        u"/".join(safe_text(value) for value in child_instance_ids)
                    )
                )
                continue

            link_document = link_instance.GetLinkDocument()
            if link_document is None:
                stats["unloaded_link_instances"] += 1
                write_diagnostic(
                    u"Незагруженная связь '{}' (ElementId={}) пропущена.".format(
                        element_name(link_type),
                        link_instance_id,
                    ),
                    level="WARNING",
                )
                continue

            child_identity = document_identity(link_document)
            if child_identity in ancestry:
                stats["cyclic_links_skipped"] += 1
                write_diagnostic(
                    u"Циклическая связь '{}' пропущена.".format(
                        document_label(link_document)
                    ),
                    level="WARNING",
                )
                continue

            link_transform = get_link_transform(link_instance)
            child_transform_chain = (
                (link_transform,) + tuple(parent_transform_chain)
            )
            source_label = u"{} [связь {}]".format(
                document_label(link_document),
                u"/".join(safe_text(value) for value in child_instance_ids),
            )
            stats["loaded_link_instances"] += 1
            collect_source_pipe_segments(
                link_document,
                child_transform_chain,
                child_instance_ids,
                source_label,
                False,
                False,
                stats,
                segments,
                errors,
            )

            child_ancestry = set(ancestry)
            child_ancestry.add(child_identity)
            collect_linked_pipe_segments(
                link_document,
                child_transform_chain,
                child_instance_ids,
                child_ancestry,
                depth + 1,
                stats,
                segments,
                errors,
            )
        except Exception as error:
            stats["link_source_failures"] += 1
            write_exception(
                u"Ошибка чтения связи ElementId={}".format(link_instance_id),
                error,
            )
            if len(errors) < MAX_LOGGED_COLLECTION_ERRORS:
                errors.append(
                    u"Связь {}: {}".format(
                        link_instance_id,
                        safe_text(error),
                    )
                )


def collect_pipe_segments(document):
    started_at = time.time()
    stats = {
        "total": 0,
        "accepted": 0,
        "host_total": 0,
        "host_accepted": 0,
        "linked_total": 0,
        "linked_accepted": 0,
        "non_linear": 0,
        "no_diameter": 0,
        "no_plan_length": 0,
        "insulated": 0,
        "multiple_insulations": 0,
        "maximum_insulation_thickness": 0.0,
        "failed": 0,
        "source_failures": 0,
        "link_instances_found": 0,
        "loaded_link_instances": 0,
        "unloaded_link_instances": 0,
        "nested_instances_skipped": 0,
        "overlay_links_skipped": 0,
        "cyclic_links_skipped": 0,
        "maximum_link_depth_skipped": 0,
        "link_scan_failures": 0,
        "link_source_failures": 0,
        "sources": [],
    }
    errors = []
    segments = []
    write_diagnostic(u"Этап чтения труб активной модели и связей: начало.")
    collect_source_pipe_segments(
        document,
        (),
        (),
        u"{} [активная модель]".format(document_label(document)),
        True,
        True,
        stats,
        segments,
        errors,
    )
    collect_linked_pipe_segments(
        document,
        (),
        (),
        set([document_identity(document)]),
        0,
        stats,
        segments,
        errors,
    )

    total_error_count = (
        stats["failed"]
        + stats["source_failures"]
        + stats["link_scan_failures"]
        + stats["link_source_failures"]
    )
    omitted_errors = total_error_count - len(errors)
    if omitted_errors > 0:
        errors.append(
            u"Ещё {} ошибок чтения не выведено.".format(omitted_errors)
        )
    stats["elapsed_seconds"] = max(0.0, time.time() - started_at)
    write_diagnostic(
        u"Этап чтения труб завершён за {:.2f} с: активная модель {}/{}, "
        u"связи {}/{}, загруженных экземпляров связей {}, незагруженных {}, "
        u"ошибок труб {}, ошибок связей {}, с изоляцией {}.".format(
            stats["elapsed_seconds"],
            stats["host_accepted"],
            stats["host_total"],
            stats["linked_accepted"],
            stats["linked_total"],
            stats["loaded_link_instances"],
            stats["unloaded_link_instances"],
            stats["failed"],
            stats["link_source_failures"],
            stats["insulated"],
        )
    )

    return segments, stats, errors


def is_rectangular_duct_type(duct_type):
    try:
        return duct_type.Shape == ConnectorProfileType.Rectangular
    except Exception:
        return False


def find_duct_types(document):
    return list(
        FilteredElementCollector(document)
        .OfClass(DuctType)
        .ToElements()
    )


def find_target_duct_type(duct_types):
    expected_name = DUCT_TYPE_NAME.lower()
    matching_types = [
        duct_type for duct_type in duct_types
        if (
            is_rectangular_duct_type(duct_type)
            and element_name(duct_type).strip().lower() == expected_name
        )
    ]
    matching_types.sort(key=lambda item: id_value(item.Id))
    return matching_types[0] if matching_types else None


def resolve_duct_type_source(document):
    duct_types = find_duct_types(document)
    rectangular_types = [
        duct_type for duct_type in duct_types
        if is_rectangular_duct_type(duct_type)
    ]
    rectangular_types.sort(key=lambda item: (
        element_name(item).lower(),
        id_value(item.Id),
    ))
    target = find_target_duct_type(duct_types)
    if target is None and not rectangular_types:
        raise CommandError(
            u"В проекте нет прямоугольного типа воздуховода, "
            u"который можно использовать как основу для «{}».".format(
                DUCT_TYPE_NAME
            )
        )

    source = target if target is not None else rectangular_types[0]
    return target, source


def resolve_system_type(document):
    system_types = list(
        FilteredElementCollector(document)
        .OfClass(MechanicalSystemType)
        .ToElements()
    )
    if not system_types:
        raise CommandError(
            u"В проекте нет типа системы воздуховодов. "
            u"Без него Revit не может создать воздуховод."
        )

    def system_sort_key(system_type):
        try:
            is_other_air = (
                system_type.SystemClassification
                == MEPSystemClassification.OtherAir
            )
        except Exception:
            is_other_air = False
        return (0 if is_other_air else 1, element_name(system_type).lower())

    system_types.sort(key=system_sort_key)
    return system_types[0]


def collect_levels(document):
    levels = list(
        FilteredElementCollector(document)
        .OfClass(Level)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    if not levels:
        raise CommandError(
            u"В проекте нет уровней. Без уровня Revit не может создать воздуховод."
        )
    return levels


def choose_bundle_level(levels, bundle):
    level_by_id = dict((id_value(level.Id), level) for level in levels)
    counts = {}
    for segment in bundle.segments:
        try:
            level_id = segment.payload["level_id"]
            level_key = id_value(level_id)
        except Exception:
            continue
        if level_key in level_by_id:
            counts[level_key] = counts.get(level_key, 0) + 1

    middle_height = (bundle.start[2] + bundle.end[2]) * 0.5
    if counts:
        selected_key = sorted(
            counts.keys(),
            key=lambda key: (
                -counts[key],
                abs(level_by_id[key].Elevation - middle_height),
                key,
            ),
        )[0]
        return level_by_id[selected_key].Id

    nearest = min(
        levels,
        key=lambda level: abs(level.Elevation - middle_height),
    )
    return nearest.Id


def ensure_duct_type(target, source):
    if target is not None:
        return target, False
    duplicated = source.Duplicate(DUCT_TYPE_NAME)
    if not is_rectangular_duct_type(duplicated):
        raise CommandError(
            u"Созданный тип «{}» не является прямоугольным.".format(
                DUCT_TYPE_NAME
            )
        )
    return duplicated, True


def set_adsk_name(duct_type):
    parameter = duct_type.LookupParameter(ADSK_NAME_PARAMETER)
    if parameter is None:
        return "missing"
    if parameter.StorageType != StorageType.String:
        return "wrong_type"
    if safe_text(parameter.AsString()) == ADSK_NAME_VALUE:
        return "already_set"
    if parameter.IsReadOnly:
        return "read_only"
    result = parameter.Set(ADSK_NAME_VALUE)
    if result is False:
        return "failed"
    return "set"


def collect_existing_ducts(document, duct_type):
    type_key = id_value(duct_type.Id)
    return [
        duct for duct in (
            FilteredElementCollector(document)
            .OfClass(Duct)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        if id_value(duct.GetTypeId()) == type_key
    ]


def delete_existing_ducts(document, duct_type):
    existing_ducts = collect_existing_ducts(document, duct_type)
    if not existing_ducts:
        return 0

    write_diagnostic(
        u"Подготовлено к удалению старых воздуховодов: {}; тип ElementId={}.".format(
            len(existing_ducts),
            id_value(duct_type.Id),
        )
    )
    element_ids = CList[ElementId]()
    for duct in existing_ducts:
        element_ids.Add(duct.Id)
    document.Delete(element_ids)
    return len(existing_ducts)


def rollback_if_started(transaction, context):
    try:
        status = transaction.GetStatus()
    except Exception as error:
        write_exception(
            u"Не удалось получить статус транзакции ({})".format(context),
            error,
        )
        return False

    if status != TransactionStatus.Started:
        return False
    try:
        rollback_status = transaction.RollBack()
        write_diagnostic(
            u"Транзакция откачена ({}), статус={}.".format(
                context,
                safe_text(rollback_status),
            ),
            level="WARNING",
        )
        return True
    except Exception as error:
        write_exception(u"Ошибка отката транзакции ({})".format(context), error)
        return False


def delete_old_bundle_ducts(document, duct_type):
    if duct_type is None:
        return 0

    transaction = Transaction(document, u"СПИС: Удалить старые пучки труб")
    try:
        write_diagnostic(u"Транзакция удаления старых воздуховодов: начало.")
        start_status = transaction.Start()
        if start_status != TransactionStatus.Started:
            raise CommandError(u"Не удалось начать транзакцию удаления воздуховодов.")
        deleted = delete_existing_ducts(document, duct_type)
        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            raise CommandError(u"Транзакция удаления воздуховодов не зафиксирована.")
        write_diagnostic(
            u"Транзакция удаления завершена; удалено: {}.".format(deleted)
        )
        return deleted
    except Exception as error:
        write_exception(u"Ошибка удаления старых воздуховодов", error)
        rollback_if_started(transaction, u"удаление старых воздуховодов")
        raise


def set_duct_size(duct, width, height):
    width_parameter = duct.get_Parameter(BuiltInParameter.RBS_CURVE_WIDTH_PARAM)
    height_parameter = duct.get_Parameter(BuiltInParameter.RBS_CURVE_HEIGHT_PARAM)
    if not width_parameter or not height_parameter:
        raise CommandError(
            u"У созданного воздуховода нет параметров ширины и высоты."
        )
    if width_parameter.IsReadOnly or height_parameter.IsReadOnly:
        raise CommandError(
            u"Параметры ширины или высоты созданного воздуховода недоступны для записи."
        )
    if width_parameter.Set(width) is False:
        raise CommandError(u"Revit не принял рассчитанную ширину воздуховода.")
    if height_parameter.Set(height) is False:
        raise CommandError(u"Revit не принял рассчитанную высоту воздуховода.")


def create_bundle_ducts(
    document,
    bundles,
    target_type,
    source_type,
    system_type,
    levels,
):
    result = {
        "created": 0,
        "deleted": 0,
        "failed": 0,
        "type_created": False,
        "adsk_status": "missing",
        "errors": [],
    }
    transaction = Transaction(document, u"СПИС: Пучки труб")
    try:
        write_diagnostic(
            u"Транзакция создания воздуховодов: начало; пучков {}.".format(
                len(bundles)
            )
        )
        start_status = transaction.Start()
        if start_status != TransactionStatus.Started:
            raise CommandError(u"Не удалось начать транзакцию создания воздуховодов.")
        duct_type, result["type_created"] = ensure_duct_type(
            target_type,
            source_type,
        )
        write_diagnostic(
            u"Тип воздуховода ElementId={}; создан новый тип: {}.".format(
                id_value(duct_type.Id),
                result["type_created"],
            )
        )
        result["adsk_status"] = set_adsk_name(duct_type)
        result["deleted"] = delete_existing_ducts(document, duct_type)

        for bundle_index, bundle in enumerate(bundles, 1):
            subtransaction = SubTransaction(document)
            duct = None
            try:
                write_diagnostic(
                    u"Создание пучка {}/{}: трубы [{}], начало {}, конец {}, "
                    u"габарит {:.0f}x{:.0f} мм.".format(
                        bundle_index,
                        len(bundles),
                        u", ".join(safe_text(key) for key in bundle.pipe_keys),
                        safe_text(bundle.start),
                        safe_text(bundle.end),
                        millimetres(bundle.width),
                        millimetres(bundle.height),
                    )
                )
                subtransaction_status = subtransaction.Start()
                if subtransaction_status != TransactionStatus.Started:
                    raise CommandError(
                        u"Не удалось начать подтранзакцию создания воздуховода."
                    )
                level_id = choose_bundle_level(levels, bundle)
                write_diagnostic(
                    u"Пучок {}/{}: уровень ElementId={}, вызов Duct.Create.".format(
                        bundle_index,
                        len(bundles),
                        id_value(level_id),
                    )
                )
                duct = Duct.Create(
                    document,
                    system_type.Id,
                    duct_type.Id,
                    level_id,
                    XYZ(*bundle.start),
                    XYZ(*bundle.end),
                )
                set_duct_size(duct, bundle.width, bundle.height)
                status = subtransaction.Commit()
                if status != TransactionStatus.Committed:
                    raise CommandError(
                        u"Подтранзакция создания воздуховода не зафиксирована."
                    )
                result["created"] += 1
                write_diagnostic(
                    u"Пучок {}/{} создан; воздуховод ElementId={}.".format(
                        bundle_index,
                        len(bundles),
                        id_value(duct.Id),
                    )
                )
            except Exception as error:
                write_exception(
                    u"Ошибка создания пучка {}/{}; трубы [{}]".format(
                        bundle_index,
                        len(bundles),
                        u", ".join(safe_text(key) for key in bundle.pipe_keys),
                    ),
                    error,
                )
                rollback_if_started(
                    subtransaction,
                    u"пучок {}/{}".format(bundle_index, len(bundles)),
                )
                result["failed"] += 1
                result["errors"].append(
                    u"Трубы {}: {}".format(
                        u", ".join(
                            safe_text(key) for key in bundle.pipe_keys
                        ),
                        safe_text(error),
                    )
                )

        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            raise CommandError(u"Транзакция Revit не была зафиксирована.")
        write_diagnostic(
            u"Транзакция создания завершена: создано {}, ошибок {}, удалено старых {}.".format(
                result["created"],
                result["failed"],
                result["deleted"],
            )
        )
        return result
    except Exception as error:
        write_exception(u"Ошибка общей транзакции создания воздуховодов", error)
        rollback_if_started(transaction, u"общая транзакция создания")
        raise


def millimetres(value_feet):
    return value_feet * MM_PER_FOOT


def adsk_status_text(status):
    return {
        "set": u"заполнен",
        "already_set": u"уже был заполнен",
        "missing": u"параметр отсутствует",
        "read_only": u"параметр только для чтения",
        "wrong_type": u"параметр имеет неподходящий тип данных",
        "failed": u"Revit не принял значение",
    }.get(status, safe_text(status))


def collection_has_warnings(collection_stats):
    return (
        collection_stats["failed"] > 0
        or collection_stats["source_failures"] > 0
        or collection_stats["link_scan_failures"] > 0
        or collection_stats["link_source_failures"] > 0
        or collection_stats["cyclic_links_skipped"] > 0
        or collection_stats["maximum_link_depth_skipped"] > 0
    )


def print_diagnostics(
    collection_stats,
    collection_errors,
    bundles,
    result,
    analysis_diagnostics=None,
):
    print(u"=== СПИС: Пучки труб ===")
    print(u"Труб в активной модели: {}".format(collection_stats["host_total"]))
    print(
        u"Труб в загруженных связях с учётом размещений: {}".format(
            collection_stats["linked_total"]
        )
    )
    print(u"Прямых участков в расчёте: {}".format(collection_stats["accepted"]))
    if collection_stats["loaded_link_instances"]:
        print(
            u"Обработано загруженных экземпляров связей: {}".format(
                collection_stats["loaded_link_instances"]
            )
        )
    if collection_stats["unloaded_link_instances"]:
        print(
            u"Пропущено незагруженных экземпляров связей: {}".format(
                collection_stats["unloaded_link_instances"]
            )
        )
    if collection_stats["overlay_links_skipped"]:
        print(
            u"Пропущено вложенных Overlay-связей: {}".format(
                collection_stats["overlay_links_skipped"]
            )
        )
    if collection_stats["cyclic_links_skipped"]:
        print(
            u"Пропущено циклических связей: {}".format(
                collection_stats["cyclic_links_skipped"]
            )
        )
    link_errors = (
        collection_stats["source_failures"]
        + collection_stats["link_scan_failures"]
        + collection_stats["link_source_failures"]
    )
    if link_errors:
        print(u"Ошибок чтения источников/связей: {}".format(link_errors))
    print(u"Найдено участков пучков: {}".format(len(bundles)))
    for index, bundle in enumerate(bundles, 1):
        print(
            u"  {}. трубы [{}], {:.0f} x {:.0f} мм, длина {:.0f} мм".format(
                index,
                u", ".join(safe_text(key) for key in bundle.pipe_keys),
                millimetres(bundle.width),
                millimetres(bundle.height),
                millimetres(bundle.length),
            )
        )

    if collection_stats["non_linear"]:
        print(u"Пропущено непрямых труб: {}".format(collection_stats["non_linear"]))
    if collection_stats["no_plan_length"]:
        print(u"Пропущено вертикальных труб: {}".format(collection_stats["no_plan_length"]))
    if collection_stats["no_diameter"]:
        print(u"Пропущено труб без наружного диаметра: {}".format(collection_stats["no_diameter"]))
    if collection_stats["insulated"]:
        print(
            u"Труб с учтённой изоляцией: {}; максимальная толщина: {:.0f} мм".format(
                collection_stats["insulated"],
                millimetres(collection_stats["maximum_insulation_thickness"]),
            )
        )
    if collection_stats["multiple_insulations"]:
        print(
            u"Труб с несколькими элементами изоляции: {} "
            u"(использована максимальная толщина)".format(
                collection_stats["multiple_insulations"]
            )
        )
    if collection_stats["failed"]:
        print(u"Ошибок чтения труб: {}".format(collection_stats["failed"]))
    for error in collection_errors:
        print(u"  [Чтение] {}".format(error))
    if analysis_diagnostics:
        print(
            u"Проверено пар-кандидатов: {}; точных проверок: {}; "
            u"смежных пар: {}; время анализа: {:.2f} с".format(
                analysis_diagnostics.get("pair_checks", 0),
                analysis_diagnostics.get("geometry_checks", 0),
                analysis_diagnostics.get("adjacent_pairs", 0),
                analysis_diagnostics.get("elapsed_seconds", 0.0),
            )
        )
        if analysis_diagnostics.get("filtered_by_minimum_dimension"):
            print(
                u"Исключено пучков с шириной и высотой менее {:.0f} мм: {}".format(
                    millimetres(analysis_diagnostics["minimum_bundle_dimension"]),
                    analysis_diagnostics["filtered_by_minimum_dimension"],
                )
            )
        if analysis_diagnostics.get("analysis_errors"):
            print(
                u"Ошибок геометрического анализа: {}".format(
                    analysis_diagnostics["analysis_errors"]
                )
            )
        for error in analysis_diagnostics.get("errors", []):
            print(u"  [Анализ] {}".format(safe_text(error)))
    if result.get("deleted"):
        print(u"Удалено старых воздуховодов: {}".format(result["deleted"]))
    for error in result.get("errors", []):
        print(u"  [Создание] {}".format(error))
    if LOG_PATH:
        print(u"Отладочный журнал: {}".format(LOG_PATH))


def build_summary(collection_stats, bundles, result, analysis_diagnostics=None):
    type_status = u"создан" if result["type_created"] else u"использован существующий"
    skipped_total = (
        collection_stats["non_linear"]
        + collection_stats["no_plan_length"]
        + collection_stats["no_diameter"]
        + collection_stats["failed"]
    )
    lines = [
        u"Труб в активной модели: {}".format(collection_stats["host_total"]),
        u"Труб в загруженных связях: {}".format(collection_stats["linked_total"]),
        u"Принято прямых участков из связей: {}".format(
            collection_stats["linked_accepted"]
        ),
        u"Труб с учтённой изоляцией: {}".format(collection_stats["insulated"]),
        u"Найдено участков пучков: {}".format(len(bundles)),
        u"Удалено старых воздуховодов: {}".format(result["deleted"]),
        u"Создано воздуховодов: {}".format(result["created"]),
        u"Ошибок создания: {}".format(result["failed"]),
        u"Тип {}: {}".format(DUCT_TYPE_NAME, type_status),
        u"{}: {}".format(
            ADSK_NAME_PARAMETER,
            adsk_status_text(result["adsk_status"]),
        ),
    ]
    if skipped_total:
        lines.append(u"Пропущено неподходящих труб: {}".format(skipped_total))
    if collection_stats["unloaded_link_instances"]:
        lines.append(
            u"Незагруженных связей пропущено: {}".format(
                collection_stats["unloaded_link_instances"]
            )
        )
    link_errors = (
        collection_stats["source_failures"]
        + collection_stats["link_scan_failures"]
        + collection_stats["link_source_failures"]
    )
    if link_errors:
        lines.append(u"Ошибок чтения связей: {}".format(link_errors))
    if analysis_diagnostics and analysis_diagnostics.get(
        "filtered_by_minimum_dimension"
    ):
        lines.append(
            u"Исключено пучков меньше {:.0f} мм по обоим габаритам: {}".format(
                millimetres(analysis_diagnostics["minimum_bundle_dimension"]),
                analysis_diagnostics["filtered_by_minimum_dimension"],
            )
        )
    if analysis_diagnostics and analysis_diagnostics.get("analysis_errors"):
        lines.append(
            u"Ошибок геометрического анализа: {}".format(
                analysis_diagnostics["analysis_errors"]
            )
        )
    return u"\n".join(lines)


def log_analysis_progress(stage, diagnostics):
    write_diagnostic(
        u"Анализ пучков: этап={}, труб={}, пар-кандидатов={}, "
        u"точных проверок={}, смежных пар={}, компонентов={}, "
        u"событий диапазонов={}, пучков={}, время={:.2f} с.".format(
            safe_text(stage),
            diagnostics.get("segment_count", 0),
            diagnostics.get("pair_checks", 0),
            diagnostics.get("geometry_checks", 0),
            diagnostics.get("adjacent_pairs", 0),
            diagnostics.get("components", 0),
            diagnostics.get("range_events", 0),
            diagnostics.get("bundle_count", 0),
            diagnostics.get("elapsed_seconds", 0.0),
        )
    )


def run():
    command_started_at = time.time()
    element_id_api = u"Value/IntegerValue fallback"
    if REVIT_MAJOR_VERSION >= REVIT_ELEMENT_ID_64BIT_VERSION:
        element_id_api = u"Value"
    elif REVIT_MAJOR_VERSION > 0:
        element_id_api = u"IntegerValue"
    write_diagnostic(
        u"Запуск команды. Revit {}; ElementId API: {}; Python: {}.".format(
            REVIT_MAJOR_VERSION or u"не определён",
            element_id_api,
            safe_text(sys.version).replace(u"\r", u" ").replace(u"\n", u" "),
        )
    )
    uidocument = __revit__.ActiveUIDocument
    if uidocument is None:
        raise CommandError(u"Откройте проект Revit и повторите запуск.")
    document = uidocument.Document
    if document.IsFamilyDocument:
        raise CommandError(u"Команда работает только в проекте Revit.")
    if document.IsReadOnly:
        raise CommandError(u"Текущий проект открыт только для чтения.")
    try:
        document_title = safe_text(document.Title)
        document_path = safe_text(document.PathName) or u"<не сохранён>"
    except Exception as error:
        document_title = u"<не удалось прочитать>"
        document_path = u"<не удалось прочитать>"
        write_exception(u"Ошибка чтения сведений о документе", error)
    write_diagnostic(
        u"Документ: название='{}', путь='{}'.".format(
            document_title,
            document_path,
        )
    )

    segments, collection_stats, collection_errors = collect_pipe_segments(document)
    analysis_diagnostics = {}
    write_diagnostic(
        u"Этап анализа пучков: начало; лимит {} пар и {:.0f} с.".format(
            MAX_ANALYSIS_PAIR_CHECKS,
            MAX_ANALYSIS_SECONDS,
        )
    )
    try:
        bundles = find_pipe_bundles(
            segments,
            max_gap=MAX_OUTSIDE_GAP,
            height_tolerance=MAX_AXIS_HEIGHT_DIFFERENCE,
            angle_tolerance_radians=PARALLEL_ANGLE_TOLERANCE,
            minimum_overlap=MINIMUM_OVERLAP,
            diagnostics=analysis_diagnostics,
            max_pair_checks=MAX_ANALYSIS_PAIR_CHECKS,
            timeout_seconds=MAX_ANALYSIS_SECONDS,
            progress_callback=log_analysis_progress,
            progress_interval=ANALYSIS_PROGRESS_INTERVAL,
        )
    except BundleAnalysisLimitError as error:
        write_exception(u"Анализ остановлен защитным лимитом", error)
        raise CommandError(
            u"Анализ труб остановлен защитой до начала транзакции: {}\n"
            u"Обработано пар-кандидатов: {}. Уменьшите число длинных "
            u"пересекающихся в плане труб или разбейте трассы на участки.".format(
                safe_text(error),
                analysis_diagnostics.get("pair_checks", 0),
            )
        )
    except Exception as error:
        write_exception(u"Непредвиденная ошибка анализа пучков", error)
        raise CommandError(
            u"Не удалось завершить геометрический анализ труб: {}".format(
                safe_text(error)
            )
        )

    for analysis_error in analysis_diagnostics.get("errors", []):
        write_diagnostic(
            u"Локальная ошибка анализа: {}".format(safe_text(analysis_error)),
            level="WARNING",
        )

    detected_bundle_count = len(bundles)
    bundles = [
        bundle for bundle in bundles
        if bundle_meets_minimum_dimension(bundle, MINIMUM_BUNDLE_DIMENSION)
    ]
    filtered_bundle_count = detected_bundle_count - len(bundles)
    analysis_diagnostics["detected_bundle_count"] = detected_bundle_count
    analysis_diagnostics["filtered_by_minimum_dimension"] = filtered_bundle_count
    analysis_diagnostics["minimum_bundle_dimension"] = MINIMUM_BUNDLE_DIMENSION
    analysis_diagnostics["bundle_count"] = len(bundles)
    write_diagnostic(
        u"Фильтр габарита пучков: найдено {}, допущено {}, исключено {}; "
        u"минимум по ширине или высоте {:.0f} мм.".format(
            detected_bundle_count,
            len(bundles),
            filtered_bundle_count,
            millimetres(MINIMUM_BUNDLE_DIMENSION),
        )
    )

    if not bundles:
        write_diagnostic(u"Подходящие пучки не найдены.")
        target_type = find_target_duct_type(find_duct_types(document))
        deleted = delete_old_bundle_ducts(document, target_type)
        empty_result = {"errors": [], "deleted": deleted}
        has_warnings = (
            collection_has_warnings(collection_stats)
            or analysis_diagnostics.get("analysis_errors", 0) > 0
        )
        if SHOW_REPORT:
            print_diagnostics(
                collection_stats,
                collection_errors,
                bundles,
                empty_result,
                analysis_diagnostics,
            )
        if SHOW_REPORT or has_warnings:
            forms.alert(
                u"Подходящие пучки не найдены.\n"
                u"Удалено старых воздуховодов: {}\n\n".format(deleted)
                + u"Учитываются трубы активной модели и загруженных Revit-связей: "
                u"прямые параллельные участки с продольным перекрытием, "
                + u"зазором между наружными поверхностями не более {:.0f} мм и ".format(
                    millimetres(MAX_OUTSIDE_GAP)
                )
                + u"разницей отметок осей не более 50 мм. Наружный габарит труб "
                u"рассчитывается с учётом толщины изоляции. Создаются только "
                u"пучки с шириной или высотой не менее {:.0f} мм.".format(
                    millimetres(MINIMUM_BUNDLE_DIMENSION)
                )
                + log_hint(),
                title=TITLE,
                warn_icon=has_warnings,
            )
        write_diagnostic(
            u"Команда завершена без найденных пучков за {:.2f} с.".format(
                max(0.0, time.time() - command_started_at)
            )
        )
        return

    write_diagnostic(
        u"Подготовка типов, системы и уровней для {} пучков.".format(len(bundles))
    )
    target_type, source_type = resolve_duct_type_source(document)
    system_type = resolve_system_type(document)
    levels = collect_levels(document)
    result = create_bundle_ducts(
        document,
        bundles,
        target_type,
        source_type,
        system_type,
        levels,
    )
    has_warnings = (
        collection_has_warnings(collection_stats)
        or result["failed"] > 0
        or analysis_diagnostics.get("analysis_errors", 0) > 0
        or result["adsk_status"] not in (
            "set",
            "already_set",
            "missing",
        )
    )
    if SHOW_REPORT:
        print_diagnostics(
            collection_stats,
            collection_errors,
            bundles,
            result,
            analysis_diagnostics,
        )
    if SHOW_REPORT or has_warnings:
        forms.alert(
            build_summary(collection_stats, bundles, result, analysis_diagnostics)
            + log_hint(),
            title=TITLE,
            warn_icon=has_warnings,
        )
    write_diagnostic(
        u"Команда завершена за {:.2f} с.".format(
            max(0.0, time.time() - command_started_at)
        )
    )


LOG_PATH = create_log_path()
try:
    run()
except CommandError as known_error:
    write_exception(u"Команда остановлена", known_error)
    forms.alert(
        safe_text(known_error) + log_hint(),
        title=TITLE,
        warn_icon=True,
    )
except Exception as unexpected_error:
    write_exception(u"Непредвиденная ошибка команды", unexpected_error)
    forms.alert(
        u"Непредвиденная ошибка:\n{}".format(safe_text(unexpected_error))
        + log_hint(),
        title=TITLE,
        warn_icon=True,
    )
