# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    BuiltInParameter,
    BuiltInParameterGroup,
    Family,
    FilteredElementCollector,
    StorageType,
    Transaction,
    XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType
from pyrevit import forms


TITLE = u"Загрузить комплектацию"
PARAMETER_NAME = u"ФОП_ВИС_Тип вложения"
FAMILY_PATH = (
    u"W:\\Проектный институт\\Отд.стандарт.BIM и RD\\BIM-Ресурсы\\"
    u"3-03-Семейства ОВ-ВК\\01_Оборудование\\Отопление\\!Комплектация\\"
    u"!Влж_Комплектация _Шаблон(Не редактировать).rfa"
)
SHARED_PARAMETERS_PATH = (
    u"W:\\Проектный институт\\Отд.стандарт.BIM и RD\\BIM-Ресурсы\\"
    u"2-Стандарты\\01 - ФОП\\ФОП_v2.txt"
)
UNC_PREFIX = u"\\\\gk.local\\Depart\\"

doc = __revit__.ActiveUIDocument.Document


def alert(message, details=None):
    forms.alert(
        message,
        title=TITLE,
        expanded=details,
        warn_icon=True,
    )


def available_path(path):
    if os.path.exists(path):
        return path
    if path.upper().startswith(u"W:\\"):
        unc_path = UNC_PREFIX + path[3:]
        if os.path.exists(unc_path):
            return unc_path
    raise IOError(u"Файл не найден: {}".format(path))


def family_parameters_by_name():
    result = {}
    for parameter in doc.FamilyManager.Parameters:
        result[parameter.Definition.Name] = parameter
    return result


def find_external_definition(shared_file, parameter_name):
    for group in shared_file.Groups:
        for definition in group.Definitions:
            if definition.Name == parameter_name:
                return definition
    return None


def find_loaded_family(family_path):
    family_name = os.path.splitext(os.path.basename(family_path))[0]
    for family in FilteredElementCollector(doc).OfClass(Family):
        if family.Name == family_name:
            return family
    return None


def load_family(family_path):
    family_reference = clr.Reference[Family]()
    transaction = Transaction(doc, u"Загрузка семейства комплектации")
    transaction.Start()
    try:
        loaded = doc.LoadFamily(family_path, family_reference)
        family = family_reference.Value
        if family is None and not loaded:
            family = find_loaded_family(family_path)
        if family is None:
            raise RuntimeError(
                u"Revit не вернул загруженное семейство (результат: {}).".format(
                    loaded
                )
            )
        transaction.Commit()
        return family
    except Exception:
        transaction.RollBack()
        raise


def ensure_family_type_parameter(definition):
    manager = doc.FamilyManager
    existing = family_parameters_by_name().get(PARAMETER_NAME)
    if existing is not None:
        try:
            if not existing.IsShared or existing.GUID != definition.GUID:
                raise RuntimeError(
                    u"Параметр «{}» уже существует, но не соответствует ФОП.".format(
                        PARAMETER_NAME
                    )
                )
        except AttributeError:
            raise RuntimeError(
                u"Параметр «{}» уже существует, но не является общим.".format(
                    PARAMETER_NAME
                )
            )
        if existing.IsInstance:
            manager.MakeType(existing)
        return existing

    return manager.AddParameter(
        definition,
        BuiltInParameterGroup.PG_CONSTRAINTS,
        False,
    )


def first_family_symbol(family):
    symbol_ids = list(family.GetFamilySymbolIds())
    if not symbol_ids:
        raise RuntimeError(u"В загруженном семействе нет типоразмеров.")
    return doc.GetElement(symbol_ids[0])


def add_parameter_place_and_associate(family, definition):
    transaction = Transaction(
        doc, u"Добавление и привязка параметра типа вложения"
    )
    transaction.Start()
    try:
        family_parameter = ensure_family_type_parameter(definition)
        symbol = first_family_symbol(family)
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()

        instance = doc.FamilyCreate.NewFamilyInstance(
            XYZ.Zero,
            symbol,
            StructuralType.NonStructural,
        )
        type_label = instance.get_Parameter(BuiltInParameter.ELEM_TYPE_LABEL)
        if type_label is None:
            raise RuntimeError(
                u"У размещённого экземпляра не найден параметр метки типоразмера."
            )
        if type_label.StorageType != StorageType.ElementId:
            raise RuntimeError(
                u"Параметр метки типоразмера имеет неподходящий тип данных."
            )
        if not type_label.Set(family_parameter.Id):
            raise RuntimeError(
                u"Revit не назначил параметр выбора типоразмера экземпляру."
            )
        transaction.Commit()
        return instance
    except Exception:
        transaction.RollBack()
        raise


def main():
    if not doc.IsFamilyDocument:
        alert(u"Команда работает только в редакторе семейств.")
        return

    try:
        family_path = available_path(FAMILY_PATH)
        shared_parameters_path = available_path(SHARED_PARAMETERS_PATH)

        family = load_family(family_path)

        application = doc.Application
        if application.SharedParametersFilename != shared_parameters_path:
            application.SharedParametersFilename = shared_parameters_path
        shared_file = application.OpenSharedParameterFile()
        if shared_file is None:
            raise RuntimeError(u"Не удалось открыть указанный ФОП.")
        definition = find_external_definition(shared_file, PARAMETER_NAME)
        if definition is None:
            raise RuntimeError(
                u"В ФОП отсутствует параметр «{}».".format(PARAMETER_NAME)
            )

        instance = add_parameter_place_and_associate(family, definition)
        forms.alert(
            u"Семейство загружено, экземпляр размещён в точке 0, 0, 0, "
            u"параметр «{}» добавлен и связан.".format(PARAMETER_NAME),
            title=TITLE,
        )
    except Exception as error:
        alert(
            u"Не удалось выполнить команду: {}".format(error),
            traceback.format_exc(),
        )


if __name__ == "__main__":
    main()
