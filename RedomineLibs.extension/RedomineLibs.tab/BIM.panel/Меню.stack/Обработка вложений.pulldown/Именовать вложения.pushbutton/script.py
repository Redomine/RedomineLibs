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
from Autodesk.Revit.UI import TaskDialog
from pyrevit import forms


TITLE = u"Именовать вложения"
DEBUG = False
DESCRIPTION_PARAMETER = u"ФОП_Описание"
TYPE_DESCRIPTION_PARAMETER = u"ФОП_Описание типа"
TYPE_DESCRIPTION_SOURCES = (
    u"ADSK_Марка",
    u"ADSK_Код изделия",
    u"ADSK_Наименование",
)

doc = __revit__.ActiveUIDocument.Document


def debug(message):
    if not DEBUG:
        return
    print(u"[Именовать вложения] {}".format(message))


def trace_description_parameters(document, element_id, stage):
    if not DEBUG:
        return
    try:
        instance = document.GetElement(element_id)
        if not isinstance(instance, FamilyInstance):
            debug(u"{}: экземпляр ID {} не найден".format(stage, element_id.GetIdValue()))
            return
        parameters = list(instance.GetParameters(DESCRIPTION_PARAMETER))
        debug(
            u"{}: документ «{}», экземпляр ID {}, параметров «{}»: {}".format(
                stage, document.Title, element_id.GetIdValue(),
                DESCRIPTION_PARAMETER, len(parameters),
            )
        )
        for parameter in parameters:
            debug(
                u"{}: параметр ID {}, значение {!r}, тип {}, только чтение {}".format(
                    stage, parameter.Id.GetIdValue(), parameter.AsString(),
                    parameter.StorageType, parameter.IsReadOnly,
                )
            )
    except Exception as error:
        debug(u"{}: не удалось прочитать параметр: {}".format(stage, error))


def refresh_description_parameter(document, element_id, parameter_id):
    document.Regenerate()
    instance = document.GetElement(element_id)
    if not isinstance(instance, FamilyInstance):
        raise RuntimeError(u"Экземпляр ID {} стал недоступен.".format(element_id.GetIdValue()))
    for parameter in instance.GetParameters(DESCRIPTION_PARAMETER):
        if parameter.Id.GetIdValue() == parameter_id.GetIdValue():
            return parameter
    raise RuntimeError(
        u"Параметр «{}» экземпляра ID {} стал недоступен.".format(
            DESCRIPTION_PARAMETER, element_id.GetIdValue()
        )
    )


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


def choose_type_description_source(family_document, parameters):
    family_types = list(family_document.FamilyManager.Types)
    if len(family_types) < 2:
        return None
    for name in TYPE_DESCRIPTION_SOURCES:
        parameter = parameters.get(name)
        if parameter is None or parameter.StorageType != StorageType.String:
            continue
        values = [family_type.AsString(parameter) for family_type in family_types]
        if all(value and value.strip() for value in values) and len(set(values)) == len(family_types):
            return parameter
    return None


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


def update_nested_family(
    parent_document, family, definitions, root_family_name, depth=1
):
    family_document = None
    transaction = None
    family_name = family.Name
    family_id = family.Id.GetIdValue()
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
                            nested_id.GetIdValue()
                        )
                    )
                nested_result = update_nested_family(
                    family_document,
                    nested_family,
                    definitions,
                    root_family_name,
                    depth + 1,
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
        unused_updated, unused_grouped, nested_errors = (
            associate_nested_instance_parameters(
                family_document,
                own_parameters[TYPE_DESCRIPTION_PARAMETER],
                root_family_name,
                own_parameters[DESCRIPTION_PARAMETER],
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


def collect_nested_families(document):
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
            result_by_id[family.Id.GetIdValue()] = family
        except Exception:
            pass
    return sorted(result_by_id.values(), key=lambda item: item.Name.lower())


def associate_instance(
    document, instance, type_description_source, description_value,
    description_source=None,
):
    manager = document.FamilyManager
    family_name = instance.Symbol.Family.Name
    debug(
        u"Документ «{}»: экземпляр «{}» (ID {}), записываемое имя "
        u"«{}», длина {}".format(
            document.Title,
            family_name,
            instance.Id.GetIdValue(),
            description_value,
            len(description_value),
        )
    )
    description = instance.LookupParameter(DESCRIPTION_PARAMETER)
    trace_description_parameters(document, instance.Id, u"До записи")
    if description is None:
        raise RuntimeError(
            u"параметр «{}» не найден".format(DESCRIPTION_PARAMETER)
        )
    if description.StorageType != StorageType.String:
        raise RuntimeError(
            u"параметр «{}» не является текстовым".format(
                DESCRIPTION_PARAMETER
            )
        )

    associated_description = manager.GetAssociatedFamilyParameter(description)
    debug(
        u"До записи: значение «{}», только чтение: {}, связь: {}".format(
            description.AsString(),
            description.IsReadOnly,
            associated_description.Definition.Name
            if associated_description is not None
            else u"нет",
        )
    )
    if description_source is not None:
        if description.StorageType != description_source.StorageType:
            raise RuntimeError(
                u"типы параметров «{}» и «{}» не совпадают".format(
                    DESCRIPTION_PARAMETER, description_source.Definition.Name,
                )
            )
        if associated_description is None or associated_description.Id != description_source.Id:
            if associated_description is not None:
                manager.AssociateElementParameterToFamilyParameter(description, None)
            if not manager.CanElementParameterBeAssociated(description):
                raise RuntimeError(
                    u"параметр «{}» нельзя связать".format(DESCRIPTION_PARAMETER)
                )
            manager.AssociateElementParameterToFamilyParameter(
                description, description_source
            )
    else:
        if associated_description is not None:
            manager.AssociateElementParameterToFamilyParameter(description, None)
            debug(u"Связь параметра «{}» снята".format(DESCRIPTION_PARAMETER))
            description = refresh_description_parameter(
                document, instance.Id, description.Id
            )
        set_result = description.Set(description_value)
        debug(u"Сразу после Set(): {!r}".format(description.AsString()))
        description = refresh_description_parameter(document, instance.Id, description.Id)
        read_value = description.AsString()
        debug(
            u"После Regenerate: Set() вернул {}; ожидается {!r}; прочитано {!r}; "
            u"только чтение {}".format(
                set_result, description_value, read_value, description.IsReadOnly
            )
        )
        trace_description_parameters(document, instance.Id, u"После Set")
        if read_value != description_value:
            raise RuntimeError(
                u"не удалось записать «{}» в параметр «{}»: Set()={}, "
                u"прочитано {!r}, только чтение {}".format(
                    description_value, DESCRIPTION_PARAMETER, set_result,
                    read_value, description.IsReadOnly,
                )
            )

    type_description = instance.LookupParameter(TYPE_DESCRIPTION_PARAMETER)
    if type_description is None:
        raise RuntimeError(
            u"параметр «{}» не найден".format(TYPE_DESCRIPTION_PARAMETER)
        )
    if type_description.StorageType != StorageType.String:
        raise RuntimeError(
            u"параметр «{}» не является текстовым".format(TYPE_DESCRIPTION_PARAMETER)
        )
    associated = manager.GetAssociatedFamilyParameter(type_description)
    if type_description_source is None:
        if associated is not None:
            manager.AssociateElementParameterToFamilyParameter(type_description, None)
        type_description.Set(u"")
    else:
        if type_description.StorageType != type_description_source.StorageType:
            raise RuntimeError(
                u"типы параметров «{}» и «{}» не совпадают".format(
                    TYPE_DESCRIPTION_PARAMETER,
                    type_description_source.Definition.Name,
                )
            )
        if associated is None or associated.Id != type_description_source.Id:
            if associated is not None:
                manager.AssociateElementParameterToFamilyParameter(
                    type_description, None
                )
            if not manager.CanElementParameterBeAssociated(type_description):
                raise RuntimeError(
                    u"параметр «{}» нельзя связать".format(
                        TYPE_DESCRIPTION_PARAMETER
                    )
                )
            manager.AssociateElementParameterToFamilyParameter(
                type_description, type_description_source
            )
    trace_description_parameters(document, instance.Id, u"После обработки типа")
    return True


def associate_nested_instance_parameters(
    document, type_description_source, description_value, description_source=None
):
    updated = 0
    grouped = 0
    skipped = []
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
            debug(
                u"Обход: документ «{}», экземпляр ID {}".format(
                    document.Title, instance.Id.GetIdValue(),
                )
            )
            if instance.GroupId != ElementId.InvalidElementId:
                grouped += 1
                debug(
                    u"Документ «{}»: экземпляр ID {} пропущен, так как "
                    u"находится в группе".format(
                        document.Title, instance.Id.GetIdValue()
                    )
                )
                continue
            family = None
            subtransaction = SubTransaction(document)
            try:
                family = instance.Symbol.Family
                subtransaction.Start()
                if associate_instance(
                    document, instance, type_description_source, description_value,
                    description_source,
                ):
                    updated += 1
                subtransaction.Commit()
                trace_description_parameters(
                    document, instance.Id, u"После SubTransaction.Commit"
                )
            except Exception as error:
                try:
                    subtransaction.RollBack()
                except Exception:
                    pass
                skipped.append(
                    u"{} (ID {}): {}".format(
                        family.Name if family is not None else u"Экземпляр",
                        instance.Id.GetIdValue(),
                        error,
                    )
                )
                debug(
                    u"Документ «{}»: ошибка экземпляра ID {}: {}".format(
                        document.Title, instance.Id.GetIdValue(), error
                    )
                )
                debug(traceback.format_exc())
                trace_description_parameters(
                    document, instance.Id, u"После SubTransaction.RollBack"
                )
        transaction.Commit()
        debug(u"Транзакция документа «{}» завершена".format(document.Title))
        for instance in instances:
            trace_description_parameters(
                document, instance.Id, u"После Transaction.Commit"
            )
    except Exception:
        transaction.RollBack()
        raise

    return updated, grouped, skipped


def main():
    clr.AddReference("dosymep.Revit.dll")
    import dosymep
    clr.ImportExtensions(dosymep.Revit)

    if not doc.IsFamilyDocument:
        alert(u"Команда работает только в редакторе семейств.")
        return

    root_family = doc.OwnerFamily
    owner_family_name = root_family.Name if root_family is not None else None
    root_family_name = (owner_family_name or doc.Title or u"").strip()
    if not root_family_name:
        alert(u"Не удалось определить имя открытого семейства.")
        return
    debug(u"========== НАЧАЛО ОТЧЁТА ==========")
    debug(
        u"Верхнеуровневый документ: «{}»; имя для записи: «{}»".format(
            doc.Title, root_family_name
        )
    )

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

    families = collect_nested_families(doc)
    family_items = [(family.Id, family.Name) for family in families]
    debug(
        u"Найдено семейств первого уровня: {}".format(
            u", ".join(name for unused_id, name in family_items) or u"нет"
        )
    )
    debug(
        u"Режим: {}; семейств первого уровня: {}".format(
            u"все элементы",
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
                        family_id.GetIdValue()
                    )
                )
            family_result = update_nested_family(
                doc,
                family,
                definitions,
                root_family_name,
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
                    family_name, family_id.GetIdValue(), error
                )
            )

    try:
        parent_parameters = family_parameters_by_name(doc)
        debug(u"Родительские параметры повторно получены после загрузок")
        type_description_source = choose_type_description_source(
            doc, parent_parameters
        )
        debug(
            u"Источник описания типа: {}".format(
                type_description_source.Definition.Name
                if type_description_source is not None else u"нет"
            )
        )
        updated_count, grouped_count, instance_errors = associate_nested_instance_parameters(
            doc,
            type_description_source,
            root_family_name,
        )
    except Exception as error:
        alert(
            u"Не удалось связать параметры вложенных экземпляров.",
            traceback.format_exc(),
        )
        return
    summary = (
        u"Режим: все вложенные элементы.\n"
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
    debug(summary)
    if errors:
        debug(u"Ошибки:\n{}".format(u"\n".join(errors)))
    debug(u"========== КОНЕЦ ОТЧЁТА ==========")
    forms.alert(
        summary,
        title=TITLE,
        expanded=u"\n".join(errors) if errors else None,
        warn_icon=bool(errors),
    )


try:
    main()
except Exception:
    TaskDialog.Show(TITLE, traceback.format_exc())
