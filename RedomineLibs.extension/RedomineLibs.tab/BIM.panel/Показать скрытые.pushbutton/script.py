# -*- coding: utf-8 -*-
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("dosymep.Revit.dll")
clr.AddReference("dosymep.Bim4Everyone.dll")

import dosymep

clr.ImportExtensions(dosymep.Revit)
clr.ImportExtensions(dosymep.Bim4Everyone)

from System.Collections.Generic import List as CList
from Autodesk.Revit.DB import *
from pyrevit import forms
from pyrevit import revit
from pyrevit import EXEC_PARAMS
from dosymep_libs.bim4everyone import *


doc = __revit__.ActiveUIDocument.Document  # type: Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

CATEGORIES = [
    BuiltInCategory.OST_DuctFitting,
    BuiltInCategory.OST_PipeFitting,
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_FlexDuctCurves,
    BuiltInCategory.OST_FlexPipeCurves,
    BuiltInCategory.OST_DuctTerminal,
    BuiltInCategory.OST_DuctAccessory,
    BuiltInCategory.OST_PipeAccessory,
    BuiltInCategory.OST_MechanicalEquipment,
    BuiltInCategory.OST_DuctInsulations,
    BuiltInCategory.OST_PipeInsulations,
    BuiltInCategory.OST_PlumbingFixtures,
    BuiltInCategory.OST_Sprinklers,
    BuiltInCategory.OST_CableTray,
    BuiltInCategory.OST_Grids
]


def get_category_filter():
    categories = CList[BuiltInCategory]()

    for category in CATEGORIES:
        categories.Add(category)

    return ElementMulticategoryFilter(categories)


def get_hidden_elements():
    category_filter = get_category_filter()
    elements = FilteredElementCollector(doc) \
        .WherePasses(category_filter) \
        .WhereElementIsNotElementType() \
        .ToElements()

    return [element for element in elements if element.IsHidden(view)]


def unhide_elements(elements):
    element_ids = CList[ElementId]()

    for element in elements:
        element_ids.Add(element.Id)

    view.UnhideElements(element_ids)


@notification()
@log_plugin(EXEC_PARAMS.command_name)
def script_execute(plugin_logger):
    hidden_elements = get_hidden_elements()

    if not hidden_elements:
        forms.alert("На активном виде нет скрытых элементов выбранных категорий.", "Показать скрытые")
        return

    with revit.Transaction("BIM: Показать скрытые элементы"):
        unhide_elements(hidden_elements)

    forms.alert("Показано скрытых элементов: {}".format(len(hidden_elements)), "Показать скрытые")


script_execute()
