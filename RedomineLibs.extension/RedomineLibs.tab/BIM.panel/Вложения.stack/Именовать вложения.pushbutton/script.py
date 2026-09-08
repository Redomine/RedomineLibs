# -*- coding: utf-8 -*-
from __future__ import print_function

import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    BuiltInParameterGroup,
    ElementId,
    FamilyInstance,
    FamilySource,
    FilteredElementCollector,
    IFamilyLoadOptions,
    StorageType,
    SubTransaction,
    Transaction,
)
from pyrevit import forms


TITLE = u"Именовать вложения"
DEBUG = False
DESCRIPTION_PARAMETER = u"ФОП_Описание"
TYPE_DESCRIPTION_PARAMETER = u"ФОП_Описание типа"
PARENT_NAME_PARAMETER = u"ADSK_Наименование"
PARENT_MARK_PARAMETER = u"ADSK_Марка"

doc = __revit__.ActiveUIDocument.Document


def debug(message):
    if not DEBUG:
        return
    print(u"[Именовать вложения] {}".format(message))


def alert(message, details=None):
    forms.alert(
        message,
        title=TITLE,
        expanded=details,
        warn_icon=True,
    )


def find_shared_definitions(application, names):
    shared_file = application.OpenSharedParameterFile()
    if shared_file is None:
        return None, list(names)

    found = {}
    for group in shared_file.Groups:
        for definition in group.Definitions:
            name = definition.Name
            if name in names and name not in found:
                found[name] = definition

    missing = [name for name in names if name not in found]
    return found, missing


def family_parameters_by_name(family_document):
    result = {}
    for parameter in family_document.FamilyManager.Parameters:
        result[parameter.Definition.Name] = parameter
    return result


def same_shared_parameter(parameter, external_definition):
    try:
        return parameter.IsShared and parameter.GUID == external_definition.GUID
    except Exception:
        return False


def ensure_instance_parameters(family_document, definitions):
    manager = family_document.FamilyManager
    existing = family_parameters_by_name(family_document)
    added = []

    for name in (DESCRIPTION_PARAMETER, TYPE_DESCRIPTION_PARAMETER):
        definition = definitions[name]
        parameter = existing.get(name)
        if parameter is not None:
            if not same_shared_parameter(parameter, definition):
                raise RuntimeError(
                    u"Параметр «{}» уже существует, но не соответствует "
                    u"параметру из текущего ФОП.".format(name)
                )
            if not parameter.IsInstance:
                manager.MakeInstance(parameter)
            continue

        manager.AddParameter(
            definition,
            BuiltInParameterGroup.PG_IDENTITY_DATA,
            True,
        )
        added.append(name)

    return added


class OverwriteFamilyLoadOptions(IFamilyLoadOptions):
    def OnFamilyFound(self, family_in_use, overwrite_parameter_values):
        overwrite_parameter_values.Value = True
        return True

    def OnSharedFamilyFound(
        self,
        shared_family,
        family_in_use,
        source,
        overwrite_parameter_values,
    ):
        source.Value = FamilySource.Family
        overwrite_parameter_values.Value = True
        return True


def update_nested_family(parent_document, family, definitions, depth=1):
    family_document = None
    transaction = None
    family_name = family.Name
    family_id = family.Id.IntegerValue
    result = {
        "added": 0,
        "processed": 0,
        "errors": [],
    }
    try:
        debug(
            u"Уровень {}: открытие семейства «{}» (ID {})".format(
                depth, family_name, family_id
            )
        )
        family_document = parent_document.EditFamily(family)
        transaction = Transaction(
            family_document, u"Добавление параметров описания"
        )
        transaction.Start()
        added = ensure_instance_parameters(family_document, definitions)
        transaction.Commit()
        transaction = None
        result["added"] += len(added)
        result["processed"] += 1
        debug(
            u"Уровень {}: «{}», добавлено параметров: {}".format(
                depth, family_name, len(added)
            )
        )

        nested_items = [
            (item.Id, item.Name)
            for item in collect_nested_families(family_document)
        ]
        debug(
            u"Уровень {}: в «{}» найдено вложенных семейств: {}".format(
                depth, family_name, len(nested_items)
            )
        )
        for nested_id, nested_name in nested_items:
            try:
                nested_family = family_document.GetElement(nested_id)
                if nested_family is None or not nested_family.IsValidObject:
                    raise RuntimeError(
                        u"семейство с ID {} стало недоступно".format(
                            nested_id.IntegerValue
                        )
                    )
                nested_result = update_nested_family(
                    family_document, nested_family, definitions, depth + 1
                )
                result["added"] += nested_result["added"]
                result["processed"] += nested_result["processed"]
                result["errors"].extend(nested_result["errors"])
            except Exception as error:
                result["errors"].append(
                    u"{} -> {}: {}\n{}".format(
                        family_name, nested_name, error, traceback.format_exc()
                    )
                )
                debug(
                    u"Ошибка уровня {}: «{}» -> «{}»: {}".format(
                        depth, family_name, nested_name, error
                    )
                )

        own_parameters = family_parameters_by_name(family_document)
        nested_parent_parameters = {
            PARENT_NAME_PARAMETER: own_parameters[DESCRIPTION_PARAMETER],
            PARENT_MARK_PARAMETER: own_parameters[
                TYPE_DESCRIPTION_PARAMETER
            ],
        }
        unused_updated, unused_grouped, nested_errors = (
            associate_nested_instance_parameters(
                family_document, nested_parent_parameters
            )
        )
        for error in nested_errors:
            result["errors"].append(
                u"{}: {}".format(family_name, error)
            )

        debug(
            u"Уровень {}: загрузка «{}» в материнское семейство".format(
                depth, family_name
            )
        )
        family_document.LoadFamily(
            parent_document, OverwriteFamilyLoadOptions()
        )
        debug(u"Уровень {}: «{}» загружено".format(depth, family_name))
        return result
    except Exception:
        if transaction is not None:
            try:
                transaction.RollBack()
            except Exception:
                pass
        raise
    finally:
        if family_document is not None:
            try:
                family_document.Close(False)
            except Exception:
                pass


def collect_nested_families(document, instances=None):
    if instances is None:
        instances = (
            FilteredElementCollector(document)
            .OfClass(FamilyInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    result_by_id = {}
    for instance in instances:
        try:
            family = instance.Symbol.Family
            result_by_id[family.Id.IntegerValue] = family
        except Exception:
            pass
    return sorted(result_by_id.values(), key=lambda item: item.Name.lower())


def associate_instance(document, instance, associations):
    manager = document.FamilyManager
    for nested_name, parent_parameter in associations:
        element_parameter = instance.LookupParameter(nested_name)
        if element_parameter is None:
            raise RuntimeError(u"параметр «{}» не найден".format(nested_name))
        if element_parameter.StorageType != parent_parameter.StorageType:
            raise RuntimeError(
                u"типы параметров «{}» и «{}» не совпадают".format(
                    nested_name, parent_parameter.Definition.Name
                )
            )

        associated = manager.GetAssociatedFamilyParameter(element_parameter)
        if associated is not None and associated.Id == parent_parameter.Id:
            continue
        if not manager.CanElementParameterBeAssociated(element_parameter):
            raise RuntimeError(
                u"параметр «{}» нельзя связать".format(nested_name)
            )
        manager.AssociateElementParameterToFamilyParameter(
            element_parameter, parent_parameter
        )
    return True


def associate_nested_instance_parameters(document, parent_parameters):
    updated = 0
    grouped = 0
    skipped = []
    associations = (
        (DESCRIPTION_PARAMETER, parent_parameters[PARENT_NAME_PARAMETER]),
        (TYPE_DESCRIPTION_PARAMETER, parent_parameters[PARENT_MARK_PARAMETER]),
    )

    transaction = Transaction(document, u"Связь параметров вложений")
    transaction.Start()
    try:
        instances = (
            FilteredElementCollector(document)
            .OfClass(FamilyInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for instance in instances:
            if instance.GroupId != ElementId.InvalidElementId:
                grouped += 1
                continue
            family = None
            subtransaction = SubTransaction(document)
            try:
                family = instance.Symbol.Family
                subtransaction.Start()
                if associate_instance(document, instance, associations):
                    updated += 1
                subtransaction.Commit()
            except Exception as error:
                try:
                    subtransaction.RollBack()
                except Exception:
                    pass
                skipped.append(
                    u"{} (ID {}): {}".format(
                        family.Name if family is not None else u"Экземпляр",
                        instance.Id.IntegerValue,
                        error,
                    )
                )
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    return updated, grouped, skipped


def associate_selected_instance_parameters(
    document, parent_parameters, selected_ids
):
    updated = 0
    skipped = []
    associations = (
        (DESCRIPTION_PARAMETER, parent_parameters[PARENT_NAME_PARAMETER]),
        (TYPE_DESCRIPTION_PARAMETER, parent_parameters[PARENT_MARK_PARAMETER]),
    )

    transaction = Transaction(document, u"Связь параметров выделенных вложений")
    transaction.Start()
    try:
        for element_id in selected_ids:
            instance = document.GetElement(element_id)
            if not isinstance(instance, FamilyInstance):
                continue
            if instance.GroupId != ElementId.InvalidElementId:
                skipped.append(
                    u"Экземпляр в группе пропущен (ID {}).".format(
                        element_id.IntegerValue
                    )
                )
                continue
            family = None
            subtransaction = SubTransaction(document)
            try:
                family = instance.Symbol.Family
                subtransaction.Start()
                if associate_instance(document, instance, associations):
                    updated += 1
                subtransaction.Commit()
            except Exception as error:
                try:
                    subtransaction.RollBack()
                except Exception:
                    pass
                skipped.append(
                    u"{} (ID {}): {}".format(
                        family.Name if family is not None else u"Экземпляр",
                        element_id.IntegerValue,
                        error,
                    )
                )
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    return updated, skipped


def main():
    if not doc.IsFamilyDocument:
        alert(u"Команда работает только в редакторе семейств.")
        return

    selected_ids = list(
        __revit__.ActiveUIDocument.Selection.GetElementIds()
    )
    selected_instances = []
    if selected_ids:
        selected_instances = [
            doc.GetElement(element_id) for element_id in selected_ids
            if isinstance(doc.GetElement(element_id), FamilyInstance)
        ]
        if not selected_instances:
            alert(u"Среди выделенных элементов нет вложенных семейств.")
            return

    definitions, missing_definitions = find_shared_definitions(
        doc.Application,
        (DESCRIPTION_PARAMETER, TYPE_DESCRIPTION_PARAMETER),
    )
    if missing_definitions:
        alert(
            u"В текущем ФОП отсутствуют обязательные общие параметры:",
            u"\n".join(missing_definitions),
        )
        return

    parent_parameters = family_parameters_by_name(doc)
    missing_parent_parameters = [
        name
        for name in (PARENT_NAME_PARAMETER, PARENT_MARK_PARAMETER)
        if name not in parent_parameters
    ]
    if missing_parent_parameters:
        alert(
            u"В текущем семействе отсутствуют обязательные параметры:",
            u"\n".join(missing_parent_parameters),
        )
        return

    families = collect_nested_families(
        doc, selected_instances if selected_ids else None
    )
    family_items = [(family.Id, family.Name) for family in families]
    debug(
        u"Режим: {}; семейств первого уровня: {}".format(
            u"выделение" if selected_ids else u"все элементы",
            len(family_items),
        )
    )
    family_errors = []
    added_count = 0
    processed_count = 0
    for family_id, family_name in family_items:
        try:
            family = doc.GetElement(family_id)
            if family is None or not family.IsValidObject:
                raise RuntimeError(
                    u"семейство с ID {} стало недоступно".format(
                        family_id.IntegerValue
                    )
                )
            family_result = update_nested_family(
                doc,
                family,
                definitions,
            )
            added_count += family_result["added"]
            processed_count += family_result["processed"]
            family_errors.extend(family_result["errors"])
        except Exception as error:
            family_errors.append(
                u"{}: {}\n{}".format(
                    family_name, error, traceback.format_exc()
                )
            )
            debug(
                u"Ошибка семейства первого уровня «{}» (ID {}): {}".format(
                    family_name, family_id.IntegerValue, error
                )
            )

    try:
        parent_parameters = family_parameters_by_name(doc)
        debug(u"Родительские параметры повторно получены после загрузок")
        if selected_ids:
            updated_count, instance_errors = associate_selected_instance_parameters(
                doc, parent_parameters, selected_ids
            )
            grouped_count = sum(
                1 for element_id in selected_ids
                if isinstance(doc.GetElement(element_id), FamilyInstance)
                and doc.GetElement(element_id).GroupId
                != ElementId.InvalidElementId
            )
        else:
            updated_count, grouped_count, instance_errors = associate_nested_instance_parameters(
                doc, parent_parameters
            )
    except Exception as error:
        alert(
            u"Не удалось связать параметры вложенных экземпляров.",
            traceback.format_exc(),
        )
        return
    mode_text = (
        u"Режим: только выделенные элементы.\n"
        if selected_ids
        else u"Режим: все вложенные элементы.\n"
    )
    summary = mode_text + (
        u"Обработано вложенных семейств на всех уровнях: {0}.\n"
        u"Семейств первого уровня: {1}.\n"
        u"Добавлено параметров: {2}.\n"
        u"Связано вложенных экземпляров: {3}.\n"
        u"Пропущено экземпляров в группах: {4}."
    ).format(
        processed_count,
        len(family_items),
        added_count,
        updated_count,
        grouped_count,
    )
    errors = family_errors + instance_errors
    forms.alert(
        summary,
        title=TITLE,
        expanded=u"\n".join(errors) if errors else None,
        warn_icon=bool(errors),
    )


if __name__ == "__main__":
    main()
