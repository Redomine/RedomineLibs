#! /usr/bin/env python
# -*- coding: utf-8 -*-

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import FilteredWorksetCollector
from Autodesk.Revit.DB import FilteredElementCollector
from Autodesk.Revit.DB import Transaction
from Autodesk.Revit.DB import Workset
from Autodesk.Revit.DB import WorksetKind
from Autodesk.Revit.DB import WorksetTable
from Autodesk.Revit.DB import DeleteWorksetSettings
from Autodesk.Revit.DB import ElementWorksetFilter
from Autodesk.Revit.DB import BuiltInParameter
from Autodesk.Revit.DB import BuiltInCategory
from Autodesk.Revit.DB import RevitLinkInstance
from Autodesk.Revit.DB import GroupType
from pyrevit import forms


WORKSET_NAMES = [
    u"00_Связи_DWG",
    u"00_Связи_RVT_00_КООРД",
    u"00_Связи_RVT_01_AP_(Номер корпуса)",
    u"00_Связи_RVT_01_AP_(Паркинг)",
    u"00_Связи_RVT_02_КР_(Номер связи)",
    u"00_Связи_RVT_03_ОВ_ИТП_(Номер связи)",
    u"00_Связи_RVT_03_ОВ_(Номер связи)",
    u"00_Связи_RVT_04_ВК_(Номер связи)",
    u"00_Связи_RVT_05_ЭОМ_(Номер связи)",
    u"00_Связи_RVT_06_CC_(Номер связи)",
    u"03_ОВ_00_Оси",
    u"03_ОВ_00_Уровни",
    u"03_ОВ_01_Общеобменная вентиляция",
    u"03_ОВ_02_Отопление",
    u"03_ОВ_03_Противодымная вентиляция",
    u"03_ОВ_20_Задания на отверстия ОВ2 В ОБЩ",
    u"03_ОВ_21_Задания на отверстия ОВ1 О",
    u"03_ОВ_22_Задания на отверстия ОВ2 В ДУ",
    u"99_Немоделируемые элементы",
    u"99_Оси секций",
    u"99_Уровни секций"
]

HEATING_WORKSET_NAME = u"03_ОВ_02_Отопление"
VENTILATION_WORKSET_NAME = u"03_ОВ_01_Общеобменная вентиляция"
LINKS_BUILDING_WORKSET_NAME = u"00_Связи_RVT_01_AP_(Номер корпуса)"
LINKS_PARKING_WORKSET_NAME = u"00_Связи_RVT_01_AP_(Паркинг)"
GRIDS_WORKSET_NAME = u"03_ОВ_00_Оси"
LEVELS_WORKSET_NAME = u"03_ОВ_00_Уровни"


doc = __revit__.ActiveUIDocument.Document


def get_user_worksets(document):
    collector = FilteredWorksetCollector(document).OfKind(WorksetKind.UserWorkset)
    return list(collector)


def get_existing_workset_names(document):
    return set(ws.Name for ws in get_user_worksets(document))


def find_workset_by_name(document, workset_name):
    for ws in get_user_worksets(document):
        if ws.Name == workset_name:
            return ws
    return None


def get_workset_parameter_candidates(element):
    params = []

    builtin_param = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    if builtin_param is not None:
        params.append(builtin_param)

    for param_name in (u"Рабочий набор", u"Workset"):
        try:
            lookup_param = element.LookupParameter(param_name)
        except Exception:
            lookup_param = None

        if lookup_param is not None and lookup_param not in params:
            params.append(lookup_param)

    return params


def get_element_workset_id_value(element):
    try:
        ws_id = element.WorksetId
        if ws_id is not None and ws_id.IntegerValue > 0:
            return ws_id.IntegerValue
    except Exception:
        pass

    for param in get_workset_parameter_candidates(element):
        try:
            value = param.AsInteger()
        except Exception:
            continue

        if value > 0:
            return value

    return None


def set_element_workset(element, workset):
    if get_element_workset_id_value(element) == workset.Id.IntegerValue:
        return False, "already_target"

    readonly_found = False
    for param in get_workset_parameter_candidates(element):
        try:
            if param.IsReadOnly:
                readonly_found = True
                continue

            param.Set(workset.Id.IntegerValue)
            return True, "moved"
        except Exception:
            readonly_found = True

    if readonly_found:
        return False, "readonly_or_missing"

    return False, "readonly_or_missing"


def resolve_target_workset_name(model_name):
    if "OT" in model_name:
        return HEATING_WORKSET_NAME
    if "VN" in model_name:
        return VENTILATION_WORKSET_NAME
    return None


def resolve_element_target_workset(element, default_workset, grids_workset, levels_workset):
    try:
        category = element.Category
    except Exception:
        category = None

    if category is None:
        return default_workset

    try:
        category_id = category.Id.IntegerValue
    except Exception:
        return default_workset

    if grids_workset is not None and category_id == int(BuiltInCategory.OST_Grids):
        return grids_workset
    if levels_workset is not None and category_id == int(BuiltInCategory.OST_Levels):
        return levels_workset

    return default_workset


def move_model_elements_to_target_workset(document, target_workset, grids_workset, levels_workset):
    changed_count = 0
    skipped_readonly = 0
    failed_items = []

    collectors = [
        FilteredElementCollector(document).WhereElementIsNotElementType(),
        FilteredElementCollector(document).WhereElementIsElementType()
    ]

    for collector in collectors:
        for element in collector.ToElements():
            try:
                resolved_workset = resolve_element_target_workset(
                    element,
                    target_workset,
                    grids_workset,
                    levels_workset
                )
                moved, status = set_element_workset(element, resolved_workset)
                if moved:
                    changed_count += 1
                elif status == "readonly_or_missing":
                    skipped_readonly += 1
            except Exception as ex:
                failed_items.append((element.Id.IntegerValue, str(ex)))

    return changed_count, skipped_readonly, failed_items


def move_group_types_to_target_workset(document, target_workset):
    changed_count = 0
    skipped_readonly = 0
    failed_items = []

    group_types = FilteredElementCollector(document).OfClass(GroupType).ToElements()
    for group_type in group_types:
        try:
            moved, status = set_element_workset(group_type, target_workset)
            if moved:
                changed_count += 1
            elif status == "readonly_or_missing":
                skipped_readonly += 1
        except Exception as ex:
            failed_items.append((group_type.Id.IntegerValue, str(ex)))

    return changed_count, skipped_readonly, failed_items


def resolve_link_target_workset(link_name, ws_building, ws_parking):
    if "PRK" in link_name:
        return ws_parking
    if "K1" in link_name or "K2" in link_name or "K3" in link_name:
        return ws_building
    return None


def move_links_to_target_worksets(document, ws_building, ws_parking):
    moved_instances = 0
    moved_types = 0
    skipped_readonly = 0
    failed = []

    links = FilteredElementCollector(document).OfClass(RevitLinkInstance).ToElements()
    for link in links:
        try:
            target_ws = resolve_link_target_workset(link.Name, ws_building, ws_parking)
            if target_ws is None:
                continue

            moved_instance, status_instance = set_element_workset(link, target_ws)
            if moved_instance:
                moved_instances += 1
            elif status_instance == "readonly_or_missing":
                skipped_readonly += 1

            link_type = document.GetElement(link.GetTypeId())
            if link_type is not None:
                moved_type, status_type = set_element_workset(link_type, target_ws)
                if moved_type:
                    moved_types += 1
                elif status_type == "readonly_or_missing":
                    skipped_readonly += 1
        except Exception as ex:
            failed.append((link.Id.IntegerValue, link.Name, str(ex)))

    return moved_instances, moved_types, skipped_readonly, failed


def get_workset_content_counts(document, workset):
    ws_filter = ElementWorksetFilter(workset.Id)

    element_count = (
        FilteredElementCollector(document)
        .WherePasses(ws_filter)
        .WhereElementIsNotElementType()
        .GetElementCount()
    )

    element_type_count = (
        FilteredElementCollector(document)
        .WherePasses(ws_filter)
        .WhereElementIsElementType()
        .GetElementCount()
    )

    return element_count, element_type_count


def is_workset_empty(document, workset):
    element_count, element_type_count = get_workset_content_counts(document, workset)
    return (element_count + element_type_count) == 0


def get_workset_element_count(document, workset):
    element_count, element_type_count = get_workset_content_counts(document, workset)
    return element_count + element_type_count


def delete_empty_non_template_worksets(document):
    deleted = []
    not_empty = []
    closed = []
    cannot_delete = []
    failed = []

    all_worksets = get_user_worksets(document)
    for ws in all_worksets:
        if ws.Name in WORKSET_NAMES:
            continue

        try:
            if hasattr(ws, "IsOpen") and not ws.IsOpen:
                closed.append(ws.Name)
                continue

            if not is_workset_empty(document, ws):
                not_empty.append((ws.Name, get_workset_element_count(document, ws)))
                continue

            delete_settings = DeleteWorksetSettings()
            if not WorksetTable.CanDeleteWorkset(document, ws.Id, delete_settings):
                cannot_delete.append(ws.Name)
                continue

            WorksetTable.DeleteWorkset(document, ws.Id, delete_settings)
            deleted.append(ws.Name)
        except Exception as ex:
            failed.append((ws.Name, str(ex)))

    return deleted, not_empty, closed, cannot_delete, failed


def main():
    if not doc.IsWorkshared:
        forms.alert(
            u"Документ не является рабочим файлом (workshared).",
            title=u"Шаблонные РН",
            exitscript=True
        )
        return

    existing_names = get_existing_workset_names(doc)
    names_to_create = [name for name in WORKSET_NAMES if name not in existing_names]

    created = []
    create_failed = []

    if names_to_create:
        transaction = Transaction(doc, u"BIM: Создание шаблонных РН")
        transaction.Start()
        try:
            for workset_name in names_to_create:
                try:
                    Workset.Create(doc, workset_name)
                    created.append(workset_name)
                except Exception as ex:
                    create_failed.append((workset_name, str(ex)))

            transaction.Commit()
        except Exception:
            transaction.RollBack()
            raise

    model_name = doc.Title or ""
    target_workset_name = resolve_target_workset_name(model_name)

    moved_elements_count = 0
    skipped_readonly_elements = 0
    move_failed = []
    moved_group_types_count = 0
    skipped_readonly_group_types = 0
    group_type_failed = []
    moved_link_instances = 0
    moved_link_types = 0
    skipped_readonly_links = 0
    link_failed = []
    deleted_empty_worksets = []
    not_empty_non_template_worksets = []
    closed_non_template_worksets = []
    cannot_delete_non_template_worksets = []
    cleanup_failed = []

    if target_workset_name is not None:
        target_workset = find_workset_by_name(doc, target_workset_name)
        if target_workset is None:
            forms.alert(
                u"Не найден целевой рабочий набор для модели: {0}".format(target_workset_name),
                title=u"Шаблонные РН",
                exitscript=True
            )
            return

        grids_workset = find_workset_by_name(doc, GRIDS_WORKSET_NAME)
        levels_workset = find_workset_by_name(doc, LEVELS_WORKSET_NAME)
        if grids_workset is None or levels_workset is None:
            forms.alert(
                u"Не найдены рабочие наборы для осей/уровней:\n{0}\n{1}".format(
                    GRIDS_WORKSET_NAME,
                    LEVELS_WORKSET_NAME
                ),
                title=u"Шаблонные РН",
                exitscript=True
            )
            return

        # Вынесено отдельной транзакцией, чтобы типы групп были переложены сразу после создания шаблона.
        group_types_transaction = Transaction(doc, u"BIM: Раскладка типов групп по РН")
        group_types_transaction.Start()
        try:
            moved_group_types_count, skipped_readonly_group_types, group_type_failed = move_group_types_to_target_workset(
                doc, target_workset
            )
            group_types_transaction.Commit()
        except Exception:
            group_types_transaction.RollBack()
            raise

        ws_building = find_workset_by_name(doc, LINKS_BUILDING_WORKSET_NAME)
        ws_parking = find_workset_by_name(doc, LINKS_PARKING_WORKSET_NAME)
        if ws_building is None or ws_parking is None:
            forms.alert(
                u"Не найдены рабочие наборы для раскладки связей:\n{0}\n{1}".format(
                    LINKS_BUILDING_WORKSET_NAME,
                    LINKS_PARKING_WORKSET_NAME
                ),
                title=u"Шаблонные РН",
                exitscript=True
            )
            return

        move_transaction = Transaction(doc, u"BIM: Раскладка элементов и связей по РН")
        move_transaction.Start()
        try:
            moved_elements_count, skipped_readonly_elements, move_failed = move_model_elements_to_target_workset(
                doc, target_workset, grids_workset, levels_workset
            )

            moved_link_instances, moved_link_types, skipped_readonly_links, link_failed = move_links_to_target_worksets(
                doc, ws_building, ws_parking
            )
            move_transaction.Commit()
        except Exception:
            move_transaction.RollBack()
            raise

    cleanup_transaction = Transaction(doc, u"BIM: Удаление пустых РН вне шаблона")
    cleanup_transaction.Start()
    try:
        deleted_empty_worksets, not_empty_non_template_worksets, closed_non_template_worksets, cannot_delete_non_template_worksets, cleanup_failed = delete_empty_non_template_worksets(doc)
        cleanup_transaction.Commit()
    except Exception:
        cleanup_transaction.RollBack()
        raise

    summary = u"Создано рабочих наборов: {0}".format(len(created))
    if created:
        summary += u"\n\n" + u"\n".join(created)

    if create_failed:
        summary += u"\n\nНе удалось создать:\n"
        summary += u"\n".join(u"{0}: {1}".format(name, err) for name, err in create_failed)

    if target_workset_name is None:
        summary += u"\n\nИмя модели не содержит OT/VN. Перенос элементов и связей не выполнялся."
    else:
        summary += u"\n\nРежим модели: {0}".format(target_workset_name)
        summary += u"\nПеренесено элементов в целевой РН: {0}".format(moved_elements_count)
        summary += u"\nПропущено элементов (readonly/без параметра РН): {0}".format(skipped_readonly_elements)
        summary += u"\nПеренесено типов групп в целевой РН: {0}".format(moved_group_types_count)
        summary += u"\nПропущено типов групп (readonly/без параметра РН): {0}".format(skipped_readonly_group_types)
        summary += u"\nПеренесено экземпляров связей: {0}".format(moved_link_instances)
        summary += u"\nПеренесено типов связей: {0}".format(moved_link_types)
        summary += u"\nПропущено связей/типов (readonly/без параметра РН): {0}".format(skipped_readonly_links)

        if move_failed:
            summary += u"\n\nОшибки переноса элементов: {0}".format(len(move_failed))
        if group_type_failed:
            summary += u"\nОшибки переноса типов групп: {0}".format(len(group_type_failed))
        if link_failed:
            summary += u"\nОшибки переноса связей: {0}".format(len(link_failed))

    summary += u"\n\nУдалено пустых РН вне шаблона: {0}".format(len(deleted_empty_worksets))
    if deleted_empty_worksets:
        summary += u"\n" + u"\n".join(deleted_empty_worksets)

    summary += u"\n\nНепустые РН вне шаблона (не удалены): {0}".format(len(not_empty_non_template_worksets))
    if not_empty_non_template_worksets:
        summary += u"\n" + u"\n".join(u"{0} ({1})".format(name, count) for name, count in not_empty_non_template_worksets)

    summary += u"\n\nЗакрытые РН вне шаблона (пропущены): {0}".format(len(closed_non_template_worksets))
    if closed_non_template_worksets:
        summary += u"\n" + u"\n".join(closed_non_template_worksets)

    summary += u"\n\nПустые РН вне шаблона (не удалось удалить): {0}".format(len(cannot_delete_non_template_worksets))
    if cannot_delete_non_template_worksets:
        summary += u"\n" + u"\n".join(cannot_delete_non_template_worksets)

    if cleanup_failed:
        summary += u"\n\nОшибки удаления РН: {0}".format(len(cleanup_failed))
        summary += u"\n" + u"\n".join(u"{0}: {1}".format(name, err) for name, err in cleanup_failed)

    forms.alert(summary, title=u"Шаблонные РН")


main()
