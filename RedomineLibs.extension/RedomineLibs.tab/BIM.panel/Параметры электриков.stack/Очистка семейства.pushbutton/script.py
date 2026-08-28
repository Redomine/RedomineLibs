#! /usr/bin/env python
# -*- coding: utf-8 -*-

__title__ = 'Очистка семейства'
__doc__ = "Удаление электрических параметров в семействе"

print(2)
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference('Microsoft.Office.Interop.Excel, Version=11.0.0.0, Culture=neutral, PublicKeyToken=71e9bce111e9429c')

import sys
import System
import math
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List
from rpw.ui.forms import SelectFromList
from System import Guid
from pyrevit import revit
from Autodesk.Revit.DB.Electrical import *

doc = __revit__.ActiveUIDocument.Document

if not doc.IsFamilyDocument:
    print 'Надстройка предназначена для работы с семействами'
    sys.exit()

def make_col(category):
    col = FilteredElementCollector(doc)\
                            .OfCategory(category)\
                            .WhereElementIsNotElementType()\
                            .ToElements()
    return col 

connectorCol = make_col(BuiltInCategory.OST_ConnectorElem)
loadsCol = make_col(BuiltInCategory.OST_ElectricalLoadClassifications)

t = Transaction(doc, 'Добавление формул')

try:
    manager = doc.FamilyManager
except Exception:
    print "Надстройка предназначена для работы с семействами"
    sys.exit()

def associate(param, famparam):
    manager.AssociateElementParameterToFamilyParameter(param, famparam)

spFile = doc.Application.OpenSharedParameterFile()

set = doc.FamilyManager.Parameters

paraNames = ['ADSK_Полная мощность', 'ADSK_Коэффициент мощности', 'ADSK_Количество фаз', 'ADSK_Напряжение',
             'ADSK_Номинальная мощность', 'ФОП_ВИС_Нагреватель или шкаф', 'ФОП_ВИС_Частотный регулятор',
                'mS_Имя нагрузки', 'mS_Координация оборудования', 'mS_Имя нагрузки Владельца', 'mS_Имя нагрузки Суффикс']


with revit.Transaction("Добавление параметров"):
    for param in set:
        if str(param.Definition.Name) in paraNames:
            manager.RemoveParameter(param)
