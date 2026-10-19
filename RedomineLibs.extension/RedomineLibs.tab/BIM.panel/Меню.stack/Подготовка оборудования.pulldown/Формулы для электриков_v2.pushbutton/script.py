#! /usr/bin/env python
# -*- coding: utf-8 -*-

__title__ = 'Добавление формул'
__doc__ = "Добавление формул"

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference('Microsoft.Office.Interop.Excel, Version=11.0.0.0, Culture=neutral, PublicKeyToken=71e9bce111e9429c')

clr.AddReference("dosymep.Revit.dll")
clr.AddReference("dosymep.Bim4Everyone.dll")

import dosymep
clr.ImportExtensions(dosymep.Revit)
clr.ImportExtensions(dosymep.Bim4Everyone)

import sys
import System
import math
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB import Electrical
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List
from rpw.ui.forms import SelectFromList
from System import Guid
from pyrevit import revit

doc = __revit__.ActiveUIDocument.Document
view = doc.ActiveView
if not doc.IsFamilyDocument:
    print 'Надстройка предназначена для работы с семействами'
    sys.exit()

def make_col(category):
    col = FilteredElementCollector(doc) \
        .OfCategory(category) \
        .WhereElementIsNotElementType() \
        .ToElements()
    return col

connectorCol = make_col(BuiltInCategory.OST_ConnectorElem)
loadsCol = make_col(BuiltInCategory.OST_ElectricalLoadClassifications)

try:
    manager = doc.FamilyManager
except Exception:
    print "Надстройка предназначена для работы с семействами"
    sys.exit()


set = doc.FamilyManager.Parameters


def setFormula(parameter, formula):

    try:
        manager.SetFormula(parameter, formula)
    except:

        print 'Не удалось присвоить формулу к параметру ' + str(parameter.Definition.Name)

with revit.Transaction("Добавление формул"):
    for param in set:


        if str(param.Definition.Name) == "ФОП_ВИС_Частотный регулятор": regulator = param

        if str(param.Definition.Name) == "ФОП_ВИС_Нагреватель или шкаф": heater = param

        if str(param.Definition.Name) == 'ADSK_Количество фаз': ADSK_phase = param

        if str(param.Definition.Name) == 'ADSK_Напряжение': ADSK_U = param

        if str(param.Definition.Name) == 'ADSK_Классификация нагрузок': ADSK_Class = param

        if str(param.Definition.Name) == 'ADSK_Коэффициент мощности': ADSK_K = param

        if str(param.Definition.Name) == 'ADSK_Полная мощность': ADSK_power = param

    #присвоение формул сделал через исключение, потому что вообще без понятия в каких случаях оно может крашнуть
    setFormula(ADSK_K, 'if(ФОП_ВИС_Частотный регулятор, 0.95, if(ФОП_ВИС_Нагреватель или шкаф, 1, if(ADSK_Номинальная мощность < 1000 Вт, 0.65, if(ADSK_Номинальная мощность < 4000 Вт, 0.75, 0.85))))')
    setFormula(ADSK_power, "ADSK_Номинальная мощность / ADSK_Коэффициент мощности")
    setFormula(ADSK_phase, "if(ADSK_Напряжение < 250 В, 1, 3)")

    #присваеваем стандартные значения
    typeset = manager.Types
    startType = manager.CurrentType
    for famType in typeset:
        manager.CurrentType = famType
        manager.Set(heater, 0)
        manager.Set(regulator, 0)
    manager.CurrentType = startType





