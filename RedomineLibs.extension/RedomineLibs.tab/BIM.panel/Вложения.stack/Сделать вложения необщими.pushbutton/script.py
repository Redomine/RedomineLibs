# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import re
import tempfile
import traceback
import uuid

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    BuiltInParameter,
    ElementId,
    Family,
    FamilyInstance,
    FamilySource,
    FilteredElementCollector,
    IFamilyLoadOptions,
    SaveAsOptions,
    SubTransaction,
    Transaction,
)
from pyrevit import forms


TITLE = u"Сделать вложения необщими"
NAME_SUFFIX = u"(Не общее)"
DEBUG = False

doc = __revit__.ActiveUIDocument.Document


def debug(message):
    if not DEBUG:
        return
    print(u"[Сделать вложения необщими] {}".format(message))


def alert(message, details=None):
    forms.alert(
        message,
        title=TITLE,
        expanded=details,
        warn_icon=True,
    )


def is_shared_family(family):
    parameter = family.get_Parameter(BuiltInParameter.FAMILY_SHARED)
    return parameter is not None and parameter.AsInteger() == 1


def collect_shared_nested_families(document):
    families_by_id = {}
    instances = (
        FilteredElementCollector(document)
        .OfClass(FamilyInstance)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for instance in instances:
        try:
            family = instance.Symbol.Family
            if is_shared_family(family):
                families_by_id[family.Id.IntegerValue] = family
        except Exception:
            pass
    return sorted(families_by_id.values(), key=lambda item: item.Name.lower())


class FamilyOption(object):
    def __init__(self, family):
        self.family_id = family.Id.IntegerValue
        self.name = family.Name

    def __str__(self):
        return self.name


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


def safe_file_name(name):
    cleaned = re.sub(u'[<>:"/\\\\|?*]', u"_", name).rstrip(u" .")
    return cleaned or u"Семейство"


def temporary_family_path(family_name):
    unique_folder = os.path.join(
        tempfile.gettempdir(),
        "RedomineLibs_" + uuid.uuid4().hex,
    )
    os.makedirs(unique_folder)
    file_name = safe_file_name(family_name + NAME_SUFFIX) + u".rfa"
    return unique_folder, os.path.join(unique_folder, file_name)


def find_family_by_name(document, family_name):
    for family in FilteredElementCollector(document).OfClass(Family):
        if family.Name == family_name:
            return family
    return None


def make_family_not_shared(parent_document, family, depth=1):
    family_document = None
    transaction = None
    temp_folder = None
    temp_path = None
    family_name = family.Name
    new_name = family_name + NAME_SUFFIX
    result = {
        "created": 0,
        "replaced": 0,
        "errors": [],
        "loaded_family_id": None,
        "loaded_family_name": new_name,
    }

    try:
        debug(
            u"Уровень {0}: открываю «{1}», ID {2}; ожидаемая копия «{3}»".format(
                depth, family_name, family.Id.IntegerValue, new_name
            )
        )
        family_document = parent_document.EditFamily(family)

        nested_items = [
            (item.Id, item.Name)
            for item in collect_shared_nested_families(family_document)
        ]
        nested_replacements = {}
        for nested_id, nested_name in nested_items:
            try:
                nested_family = family_document.GetElement(nested_id)
                if nested_family is None or not nested_family.IsValidObject:
                    raise RuntimeError(
                        u"семейство с ID {} стало недоступно".format(
                            nested_id.IntegerValue
                        )
                    )
                nested_result = make_family_not_shared(
                    family_document, nested_family, depth + 1
                )
                result["created"] += nested_result["created"]
                result["replaced"] += nested_result["replaced"]
                result["errors"].extend(nested_result["errors"])
                nested_replacements[nested_name] = (
                    nested_result["loaded_family_id"]
                )
            except Exception as error:
                result["errors"].append(
                    u"Уровень {0}: {1} -> {2}: {3}\n{4}".format(
                        depth,
                        family_name,
                        nested_name,
                        error,
                        traceback.format_exc(),
                    )
                )

        if nested_replacements:
            nested_replaced, nested_errors = replace_ungrouped_instances(
                family_document, nested_replacements
            )
            result["replaced"] += nested_replaced
            for error in nested_errors:
                result["errors"].append(
                    u"Уровень {}: {}: {}".format(
                        depth, family_name, error
                    )
                )

        parameter = family_document.OwnerFamily.get_Parameter(
            BuiltInParameter.FAMILY_SHARED
        )
        if parameter is None or parameter.IsReadOnly:
            raise RuntimeError(u"параметр «Общее» недоступен для изменения")

        transaction = Transaction(family_document, u"Снять флаг «Общее»")
        transaction.Start()
        parameter.Set(0)
        transaction.Commit()
        transaction = None

        temp_folder, temp_path = temporary_family_path(family_name)
        save_options = SaveAsOptions()
        save_options.OverwriteExistingFile = True
        family_document.SaveAs(temp_path, save_options)
        family_document.LoadFamily(
            parent_document,
            OverwriteFamilyLoadOptions(),
        )
        debug(
            u"Уровень {}: LoadFamily завершён для «{}»".format(
                depth, new_name
            )
        )
        loaded_family = find_family_by_name(parent_document, new_name)
        if loaded_family is None:
            raise RuntimeError(
                u"загруженное семейство «{}» не найдено".format(new_name)
            )
        result["created"] += 1
        result["loaded_family_id"] = loaded_family.Id.IntegerValue
        debug(
            u"Уровень {0}: копия найдена, имя «{1}», ID {2}".format(
                depth, loaded_family.Name, loaded_family.Id.IntegerValue
            )
        )
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
        if temp_path and os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        if temp_folder and os.path.isdir(temp_folder):
            try:
                os.rmdir(temp_folder)
            except Exception:
                pass


def family_symbol_name(symbol):
    parameter = symbol.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
    if parameter is None:
        raise RuntimeError(
            u"у типа ID {} отсутствует параметр имени".format(
                symbol.Id.IntegerValue
            )
        )
    name = parameter.AsString()
    if not name:
        name = parameter.AsValueString()
    if not name:
        raise RuntimeError(
            u"не удалось прочитать имя типа ID {}".format(
                symbol.Id.IntegerValue
            )
        )
    return name


def family_symbols_by_name(document, family):
    result = {}
    for symbol_id in family.GetFamilySymbolIds():
        debug(
            u"Читаю тип целевого семейства «{}», SymbolId {}".format(
                family.Name, symbol_id.IntegerValue
            )
        )
        symbol = document.GetElement(symbol_id)
        if symbol is None or not symbol.IsValidObject:
            raise RuntimeError(
                u"тип с ID {} недоступен".format(symbol_id.IntegerValue)
            )
        result[family_symbol_name(symbol)] = symbol
    return result


def replace_ungrouped_instances(document, replacements):
    replaced = 0
    errors = []
    transaction = Transaction(
        document,
        u"Замена общих вложений на необщие",
    )
    transaction.Start()
    try:
        instances = (
            FilteredElementCollector(document)
            .OfClass(FamilyInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        symbols_by_source_name = {}
        debug(
            u"Начало замены. Карта исходных семейств: {}".format(
                u", ".join(sorted(replacements.keys()))
            )
        )
        for old_family_name, new_family_id_value in replacements.items():
            try:
                debug(
                    u"Восстанавливаю целевое семейство для «{0}» по ID {1}".format(
                        old_family_name, new_family_id_value
                    )
                )
                new_family = document.GetElement(
                    ElementId(new_family_id_value)
                )
                if new_family is None or not new_family.IsValidObject:
                    raise RuntimeError(u"загруженная копия недоступна")
                debug(
                    u"Целевое семейство получено: «{}», ID {}".format(
                        new_family.Name, new_family.Id.IntegerValue
                    )
                )
                target_symbols = family_symbols_by_name(
                    document, new_family
                )
                if (
                    old_family_name not in target_symbols
                    and new_family.Name in target_symbols
                ):
                    target_symbols[old_family_name] = target_symbols[
                        new_family.Name
                    ]
                    debug(
                        u"Добавлен алиас типа: «{}» -> «{}»".format(
                            old_family_name, new_family.Name
                        )
                    )
                symbols_by_source_name[old_family_name] = target_symbols
                debug(
                    u"Источник «{0}» -> цель «{1}» (ID {2}); типы цели: {3}".format(
                        old_family_name,
                        new_family.Name,
                        new_family.Id.IntegerValue,
                        u", ".join(
                            sorted(
                                symbols_by_source_name[
                                    old_family_name
                                ].keys()
                            )
                        ),
                    )
                )
            except Exception as error:
                errors.append(
                    u"Для исходного семейства «{}» не удалось подготовить "
                    u"загруженную копию: {}\n{}".format(
                        old_family_name, error, traceback.format_exc()
                    )
                )
                debug(
                    u"Ошибка подготовки цели для «{}»: {}\n{}".format(
                        old_family_name, error, traceback.format_exc()
                    )
                )

        matched_source_names = set()
        for instance in instances:
            old_symbol = instance.Symbol
            old_family_name = old_symbol.Family.Name
            old_symbol_name = family_symbol_name(old_symbol)
            debug(
                u"Экземпляр ID {0}: семейство «{1}», тип «{2}», GroupId {3}".format(
                    instance.Id.IntegerValue,
                    old_family_name,
                    old_symbol_name,
                    instance.GroupId.IntegerValue,
                )
            )
            if old_family_name not in symbols_by_source_name:
                debug(
                    u"Экземпляр ID {} пропущен: имя семейства отсутствует "
                    u"в карте замены".format(instance.Id.IntegerValue)
                )
                continue
            matched_source_names.add(old_family_name)
            if instance.GroupId != ElementId.InvalidElementId:
                debug(
                    u"Экземпляр ID {} пропущен: находится в группе".format(
                        instance.Id.IntegerValue
                    )
                )
                errors.append(
                    u"{} (ID {}): экземпляр в группе не заменён.".format(
                        old_symbol.Family.Name,
                        instance.Id.IntegerValue,
                    )
                )
                continue

            new_symbol = symbols_by_source_name[old_family_name].get(
                old_symbol_name
            )
            if new_symbol is None:
                debug(
                    u"Экземпляр ID {}: тип «{}» отсутствует в целевом "
                    u"семействе".format(
                        instance.Id.IntegerValue, old_symbol_name
                    )
                )
                errors.append(
                    u"{} (ID {}): в новом семействе нет типа «{}».".format(
                        old_symbol.Family.Name,
                        instance.Id.IntegerValue,
                        old_symbol_name,
                    )
                )
                continue

            subtransaction = SubTransaction(document)
            try:
                subtransaction.Start()
                if not new_symbol.IsActive:
                    new_symbol.Activate()
                debug(
                    u"Экземпляр ID {0}: назначаю «{1} : {2}», SymbolId {3}".format(
                        instance.Id.IntegerValue,
                        new_symbol.Family.Name,
                        family_symbol_name(new_symbol),
                        new_symbol.Id.IntegerValue,
                    )
                )
                instance.Symbol = new_symbol
                subtransaction.Commit()
                replaced += 1
                current_symbol = instance.Symbol
                debug(
                    u"Экземпляр ID {0}: замена завершена; теперь «{1} : {2}», "
                    u"SymbolId {3}".format(
                        instance.Id.IntegerValue,
                        current_symbol.Family.Name,
                        family_symbol_name(current_symbol),
                        current_symbol.Id.IntegerValue,
                    )
                )
            except Exception as error:
                try:
                    subtransaction.RollBack()
                except Exception:
                    pass
                errors.append(
                    u"{} (ID {}): {}".format(
                        old_symbol.Family.Name,
                        instance.Id.IntegerValue,
                        error,
                    )
                )
                debug(
                    u"Экземпляр ID {}: ошибка замены: {}\n{}".format(
                        instance.Id.IntegerValue,
                        error,
                        traceback.format_exc(),
                    )
                )
        for source_name in symbols_by_source_name:
            if source_name not in matched_source_names:
                errors.append(
                    u"Для исходного семейства «{}» не найдено размещённых "
                    u"экземпляров.".format(source_name)
                )
        transaction.Commit()
        debug(u"Транзакция замены завершена. Заменено: {}".format(replaced))
    except Exception:
        transaction.RollBack()
        raise
    return replaced, errors


def main():
    if not doc.IsFamilyDocument:
        alert(u"Команда работает только в редакторе семейств.")
        return

    families = collect_shared_nested_families(doc)
    if not families:
        forms.alert(
            u"В текущем документе нет общих вложенных семейств.",
            title=TITLE,
        )
        return

    selected = forms.SelectFromList.show(
        [FamilyOption(family) for family in families],
        title=TITLE,
        button_name=u"Сделать необщими",
        multiselect=True,
    )
    if not selected:
        return

    completed = []
    replacements = {}
    errors = []
    created_count = 0
    nested_replaced_count = 0
    for option in selected:
        try:
            family = doc.GetElement(ElementId(option.family_id))
            if family is None or not family.IsValidObject:
                raise RuntimeError(
                    u"семейство с ID {} стало недоступно".format(
                        option.family_id
                    )
                )
            family_result = make_family_not_shared(doc, family)
            replacements[option.name] = (
                family_result["loaded_family_id"]
            )
            completed.append(family_result["loaded_family_name"])
            created_count += family_result["created"]
            nested_replaced_count += family_result["replaced"]
            errors.extend(family_result["errors"])
        except Exception as error:
            errors.append(
                u"{}: {}\n{}".format(
                    option.name,
                    error,
                    traceback.format_exc(),
                )
            )

    replaced_count = 0
    if replacements:
        try:
            replaced_count, replacement_errors = replace_ungrouped_instances(
                doc, replacements
            )
            errors.extend(replacement_errors)
        except Exception as error:
            errors.append(
                u"Не удалось выполнить замену экземпляров: {}\n{}".format(
                    error,
                    traceback.format_exc(),
                )
            )

    summary = (
        u"Создано и загружено необщих семейств на всех уровнях: {0}.\n"
        u"Обработано выбранных семейств первого уровня: {1} из {2}.\n"
        u"Заменено экземпляров первого уровня вне групп: {3}.\n"
        u"Заменено экземпляров на вложенных уровнях вне групп: {4}."
    ).format(
        created_count,
        len(completed),
        len(selected),
        replaced_count,
        nested_replaced_count,
    )
    if completed:
        summary += u"\n\n" + u"\n".join(completed)
    forms.alert(
        summary,
        title=TITLE,
        expanded=u"\n\n".join(errors) if errors else None,
        warn_icon=bool(errors),
    )


if __name__ == "__main__":
    main()
