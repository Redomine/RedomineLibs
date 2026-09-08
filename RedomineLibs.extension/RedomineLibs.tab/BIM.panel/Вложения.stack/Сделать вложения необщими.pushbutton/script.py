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

doc = __revit__.ActiveUIDocument.Document


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
        self.family = family
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


def make_family_not_shared(parent_document, family):
    family_document = None
    transaction = None
    temp_folder = None
    temp_path = None
    new_name = family.Name + NAME_SUFFIX

    try:
        family_document = parent_document.EditFamily(family)
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

        temp_folder, temp_path = temporary_family_path(family.Name)
        save_options = SaveAsOptions()
        save_options.OverwriteExistingFile = True
        family_document.SaveAs(temp_path, save_options)
        family_document.LoadFamily(
            parent_document,
            OverwriteFamilyLoadOptions(),
        )
        loaded_family = find_family_by_name(parent_document, new_name)
        if loaded_family is None:
            raise RuntimeError(
                u"загруженное семейство «{}» не найдено".format(new_name)
            )
        return loaded_family
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


def family_symbols_by_name(document, family):
    result = {}
    for symbol_id in family.GetFamilySymbolIds():
        symbol = document.GetElement(symbol_id)
        result[symbol.Name] = symbol
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
        symbols_by_family_id = {}
        for old_family_id, new_family in replacements.items():
            symbols_by_family_id[old_family_id] = family_symbols_by_name(
                document, new_family
            )

        for instance in instances:
            if instance.GroupId != ElementId.InvalidElementId:
                continue
            old_symbol = instance.Symbol
            old_family_id = old_symbol.Family.Id.IntegerValue
            if old_family_id not in replacements:
                continue

            new_symbol = symbols_by_family_id[old_family_id].get(
                old_symbol.Name
            )
            if new_symbol is None:
                errors.append(
                    u"{} (ID {}): в новом семействе нет типа «{}».".format(
                        old_symbol.Family.Name,
                        instance.Id.IntegerValue,
                        old_symbol.Name,
                    )
                )
                continue

            subtransaction = SubTransaction(document)
            try:
                subtransaction.Start()
                if not new_symbol.IsActive:
                    new_symbol.Activate()
                instance.Symbol = new_symbol
                subtransaction.Commit()
                replaced += 1
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
        transaction.Commit()
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
    for option in selected:
        try:
            new_family = make_family_not_shared(doc, option.family)
            replacements[option.family.Id.IntegerValue] = new_family
            completed.append(new_family.Name)
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
        u"Создано и загружено семейств: {0} из {1}.\n"
        u"Заменено экземпляров вне групп: {2}."
    ).format(
        len(completed),
        len(selected),
        replaced_count,
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
