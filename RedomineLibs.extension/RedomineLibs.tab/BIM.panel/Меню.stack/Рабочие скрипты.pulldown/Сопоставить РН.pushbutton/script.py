#! /usr/bin/env python
# -*- coding: utf-8 -*-

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

clr.AddReference("dosymep.Revit.dll")
clr.AddReference("dosymep.Bim4Everyone.dll")

import dosymep
clr.ImportExtensions(dosymep.Revit)
clr.ImportExtensions(dosymep.Bim4Everyone)

from dosymep_libs.bim4everyone import *
from dosymep.Bim4Everyone.SharedParams import SharedParamsConfig
from dosymep.Bim4Everyone import *

import sys
import System
from Autodesk.Revit.DB import *
from pyrevit import revit, forms
from Autodesk.Revit.DB.Mechanical import DuctSystemType
from Autodesk.Revit.DB import ConnectorElement, MEPSystemClassification
import os
from pyrevit import revit, forms
from System import Guid
import shutil


uiapp = __revit__  # UIApplication
app = uiapp.Application  # Application
doc = __revit__.ActiveUIDocument.Document
view = doc.ActiveView

WORKSET_TABLE = \
    {
        "_KOORD": ["00_Связи_RVT_00_KOORD", "КООРД", "KOORD"],
        "_AR": ["00_Связи_RVT_01_AP_(Номер корпуса)", "00_Связи_RVT_01_АР_(Номер корпуса)", "AP_(Номер корпуса)", "АР_(К", "АР_(Номер корпуса)", "АР_(Номер связи", "АР (Связи)", "RVT_01_АР_K", "RVT_01_АР_К", "00_Связи_АР_Жилая"],
        "_AR_PRK": ["00_Связи_RVT_01_AP_(Паркинг)", "00_Связи_RVT_01_АР_(Паркинг)", "AP_(Паркинг)", "АР_(Паркинг)", "АР Паркинг (Связи)", "RVT_01_АР_PRK", "00_Связи_АР_Паркинг"],
        "_EOM": ["00_Связи_RVT_05_ЭОМ_(Номер связи)", "00_Связи_RVT_05_3OM_(Номер связи)", "00_Связи_RVT_05_EOM_(Номер связи)", "3OM_(Номер связи)", "EOM_(Номер связи)", "ЭОМ_(Номер связи)", "ЭОМ (Связи)", "00_Связи_ЭОМ", "ЭОМ_Номер связи"],
        "_SS": ["00_Связи_RVT_06_CC_(Номер связи)", "00_Связи_RVT_06_СС_(Номер связи)", "CC_(Номер связи)", "СС_(Номер связи)", "СС (Связи)", "00_Связи_СС", "СС_Номер связи"],
        "_VK": ["00_Связи_RVT_04_ВК_(Номер связи)", "00_Связи_RVT_04_BK_(Номер связи)", "BK_(Номер связи)", "ВК_(Номер связи)", "ВК (Связи)", "ВК_Номер связи", "00_Связи_ВК"],
        "_ITP": ["00_Связи_RVT_03_ОВ_ИТП_(Номер связи)", "ОВ_ИТП_(Номер связи)", "ИТП_(Номер связи)", "ИТП (Связи)"],
        "_OV": ["00_Связи_RVT_03_OB_(Номер связи)", "00_Связи_RVT_03_ОВ_(Номер связи)", "OB_(Номер связи)", "ОВ_(Номер связи)", "ОВ (Связи)", "Связи_RVT_03_ОВ", "00_Связи_ОВ"],
        "_KV": ["00_Связи_RVT_03_OB_(Номер связи)", "00_Связи_RVT_03_ОВ_(Номер связи)", "OB_(Номер связи)", "ОВ_(Номер связи)", "ОВ (Связи)", "Связи_RVT_03_ОВ", "00_Связи_ОВ"],
        "_KR": ["00_Связи_RVT_02_KP_(Номер связи)", "00_Связи_RVT_02_КР_(Номер связи)", "KP_(Номер связи)", "КР_(Номер связи)", "КР (Связи)", "КР_(Номер"]
    }


"""
Бэкап исходной версии

WORKSET_TABLE = \
    {
        "_KOORD":"_КООРД",
        "_AR": "АР (Связи)",
        "_AR_PRK": "АР Паркинг (Связи)",
        "_EOM": "ЭОМ (Связи)",
        "_SS": "СС (Связи)",
        "_VK": "ВК (Связи)",
        "_ITP": "ИТП (Связи)",
        "_OV": "ОВ (Связи)"
    }

"""

class EditorReport:
    def __init__(self, doc):
        self.doc = doc
        self.edited_reports = []
        self.status_report = ''
        self.edited_report = ''

    def __get_element_editor_name(self, element):
        user_name = __revit__.Application.Username
        edited_by = element.GetParamValueOrDefault(BuiltInParameter.EDITED_BY)
        if edited_by is None:
            return None
        if edited_by.lower() in user_name.lower():
            return None
        return edited_by

    def is_element_edited(self, element):
        self.update_status = WorksharingUtils.GetModelUpdatesStatus(self.doc, element.Id)
        if self.update_status == ModelUpdatesStatus.UpdatedInCentral:
            self.status_report = "Вы владеете элементами, но ваш файл устарел. Выполните синхронизацию."

        name = self.__get_element_editor_name(element)
        if name is not None and name not in self.edited_reports:
            self.edited_reports.append(name)
            return True
        return False

    def show_report(self):
        if len(self.edited_reports) > 0:
            self.edited_report = (
                "Часть элементов занята пользователями: {}".format(", ".join(self.edited_reports))
            )
        if self.edited_report or self.status_report:
            message = self.status_report
            if self.edited_report and self.status_report:
                message += "\n"
            message += self.edited_report
            forms.alert(message, "Ошибка", exitscript=True)

CONFUSABLE_CHARS = {
    u"А": u"A",
    u"В": u"B",
    u"Е": u"E",
    u"К": u"K",
    u"М": u"M",
    u"Н": u"H",
    u"О": u"O",
    u"Р": u"P",
    u"С": u"C",
    u"Т": u"T",
    u"Х": u"X",
    u"У": u"Y",
    u"Э": u"3",
}


def normalize_workset_name(value):
    if not isinstance(value, unicode):
        value = unicode(value, "utf-8")
    else:
        value = unicode(value)
    for source, replacement in CONFUSABLE_CHARS.items():
        value = value.replace(source, replacement)
    return value.lower()


def get_workset(worksets, name_variants):
    normalized_worksets = [(ws, normalize_workset_name(ws.Name)) for ws in worksets]
    for name in name_variants:
        normalized_name = normalize_workset_name(name)
        for ws, normalized_ws_name in normalized_worksets:
            if normalized_name in normalized_ws_name:
                return ws
    return None

def get_link_inst(link_insts, name):
    for link in link_insts:
        if name in link.Name:
            return link
    return None

def set_link_ws(link, ws):
    param = link.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    if not param.IsReadOnly:
        param.Set(ws.Id.IntegerValue)

    link_type = link.GetElementType()
    type_param = link_type.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    if not type_param.IsReadOnly:
        type_param.Set(ws.Id.IntegerValue)

def main():
    report = EditorReport(doc)
    collector = FilteredWorksetCollector(doc)
    worksets = list(collector.OfKind(WorksetKind.UserWorkset))

    link_instances = FilteredElementCollector(doc) \
        .OfClass(RevitLinkInstance) \
        .ToElements()

    with revit.Transaction("BIM: Сопоставление РН"):
        for link in link_instances:
            for link_name_key in sorted(WORKSET_TABLE.keys(), key=len, reverse=True):
                if report.is_element_edited(link):
                    continue
                if link_name_key in link.Name:
                    if link.Name == "PRKS-10.3_KOORD.rvt : 37 : позиция Встроенный":
                        print link.Name

                    current_link = link
                    current_workset = get_workset(worksets, WORKSET_TABLE[link_name_key])
                    if current_workset is None:
                        values = WORKSET_TABLE[link_name_key]
                        print link.Name
                        print("Не найден рабочий набор содержащий в себе: {}".format(", ".join(values)))
                    else:
                        set_link_ws(current_link, current_workset)
                    break

    report.show_report()

main()
