# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import re
import sys
import traceback

import clr
clr.AddReference('ProtoGeometry')
from Autodesk.DesignScript.Geometry import *
clr.AddReference("RevitNodes")
clr.AddReference("dosymep.Revit.dll")
clr.AddReference("dosymep.Bim4Everyone.dll")

import Revit
clr.ImportExtensions(Revit.Elements)
clr.ImportExtensions(Revit.GeometryConversion)
clr.AddReference("RevitServices")
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

from pyrevit import forms, revit, script
from System import Guid
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
import dosymep
clr.ImportExtensions(dosymep.Revit)
clr.ImportExtensions(dosymep.Bim4Everyone)
from dosymep.Bim4Everyone.Templates import ProjectParameters
from dosymep.Bim4Everyone.SharedParams import SharedParamsConfig
from dosymep.Revit import ParamExtensions
import Autodesk
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *


__title__ = 'Перенос значений'
__doc__ = "Переносит между собой значения параметров в активной спецификации"

COMMAND_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(COMMAND_DIR, "TransferWindow.xaml")
POSITION_PARAM_NAME = u'ФОП_ВИС_Позиция'
POSITION_PARAM_GUID = Guid('3f809907-b64c-4a8d-be5e-06709ee28386')


class paramCell:
    def __init__(self, paraIndex, sortGroupInd, sortname):
        self.index = paraIndex
        self.sortGroupInd = sortGroupInd
        self.name = sortname
        self.unitType = None
        self.displayUnitType = ''


class projectParam:
    def __init__(self, name, unit):
        self.name = name
        self.unit = unit


report_rows = set()
created_position_param_id = None


def get_position_parameter_element():
    for param_element in FilteredElementCollector(doc).OfClass(SharedParameterElement):
        try:
            if param_element.GuidValue == POSITION_PARAM_GUID:
                return param_element
        except:
            pass
    return None


def ensure_position_parameter():
    global created_position_param_id

    param_element = get_position_parameter_element()
    if param_element:
        return param_element

    shared_params = SharedParamsConfig.Instance
    if shared_params is None:
        raise Exception(u'Конфигурация общих параметров dosymep не загружена')

    ProjectParameters.Create(doc.Application).SetupRevitParam(
        doc,
        shared_params.VISPosition
    )

    param_element = get_position_parameter_element()
    if not param_element:
        raise Exception(
            u'Не удалось создать параметр {0} из шаблона dosymep'.format(
                POSITION_PARAM_NAME
            )
        )

    created_position_param_id = param_element.Id
    return param_element


def ensure_position_schedule_field(definition, param_element):
    for field_id in definition.GetFieldOrder():
        schedule_field = definition.GetField(field_id)
        try:
            if schedule_field.ParameterId == param_element.Id:
                return field_id, False
        except:
            pass

    for schedulable_field in definition.GetSchedulableFields():
        if schedulable_field.ParameterId == param_element.Id:
            schedule_field = definition.AddField(schedulable_field)
            return schedule_field.FieldId, True

    raise Exception(
        u'Параметр {0} нельзя добавить в эту спецификацию'.format(
            POSITION_PARAM_NAME
        )
    )


def remove_created_position_parameter():
    global created_position_param_id
    if created_position_param_id is None:
        return

    param_id = created_position_param_id
    created_position_param_id = None
    with revit.Transaction(u'Удаление временного параметра'):
        if doc.GetElement(param_id):
            doc.Delete(param_id)


def make_col(category):
    col = FilteredElementCollector(doc) \
        .OfCategory(category) \
        .WhereElementIsNotElementType() \
        .ToElements()
    return col


def get_duct_area(element):
    length_reserve = 1 + (doc.ProjectInformation.LookupParameter(
        'ФОП_ВИС_Запас воздуховодов/труб').AsDouble() / 100)
    if element.Category.IsId(BuiltInCategory.OST_DuctCurves):
        fop_number = (element.GetParamValue(BuiltInParameter.RBS_CURVE_SURFACE_AREA) * 0.092903) * length_reserve
        fop_number = round(fop_number, 2)
    return fop_number


def getParaInd(paraName, definition):
    sortGroupInd = []
    paraIndex = None
    paraType = None
    index = 0

    for scheduleGroupField in definition.GetFieldOrder():
        scheduleField = definition.GetField(scheduleGroupField)
        if scheduleField.GetName() == paraName:
            paraIndex = index
            try:
                paraFormat = scheduleField.GetFormatOptions()
                if not paraFormat.UseDefault:
                    try:
                        paraType = paraFormat.GetUnitTypeId()
                    except:
                        paraType = paraFormat.DisplayUnits
            except:
                pass
        index += 1

    index = 0
    for field in definition.GetFieldOrder():
        for scheduleSortGroupField in definition.GetSortGroupFields():
            if scheduleSortGroupField.FieldId.ToString() == field.ToString():
                sortGroupInd = index
        index += 1

    if paraIndex is None:
        raise Exception(u'Параметр {0} не обнаружен в спецификации'.format(paraName))

    param = paramCell(paraIndex, sortGroupInd, paraName)
    param.unitType = paraType
    return param


def isCalculatedField(scheduleField):
    try:
        if getattr(scheduleField, 'IsCalculatedField', False):
            return True
    except:
        pass

    try:
        fieldType = str(scheduleField.FieldType)
        if fieldType in ('Formula', 'Percentage', 'Combined', 'Count'):
            return True
    except:
        pass

    try:
        paramId = scheduleField.ParameterId
        if paramId == ElementId.InvalidElementId:
            return True
        try:
            if paramId.IntegerValue == -1:
                return True
        except:
            if getattr(paramId, 'Value', None) == -1:
                return True
    except:
        pass

    return False


def getParamsInShed(definition):
    paramList = []
    for scheduleGroupField in definition.GetFieldOrder():
        scheduleField = definition.GetField(scheduleGroupField)
        name = scheduleField.GetName()
        if name and name not in paramList:
            paramList.append(name)
    return paramList


def getTargetParamsInShed(definition, elements=None):
    targetList = []
    for scheduleGroupField in definition.GetFieldOrder():
        scheduleField = definition.GetField(scheduleGroupField)
        name = scheduleField.GetName()
        if isCalculatedField(scheduleField):
            continue
        if not name or name in targetList:
            continue

        if elements:
            is_writable = False
            for el in elements:
                p = el.LookupParameter(name)
                if not p:
                    try:
                        type_id = el.GetTypeId()
                        if type_id and type_id != ElementId.InvalidElementId:
                            elem_type = doc.GetElement(type_id)
                            if elem_type:
                                p = elem_type.LookupParameter(name)
                    except:
                        pass
                if p and not p.IsReadOnly:
                    is_writable = True
                    break
            if not is_writable:
                continue

        targetList.append(name)
    return targetList


def isNoneUnitType(element, parameterObj):
    if parameterObj.unitType is None:
        targetParam = element.LookupParameter(parameterObj.name)
        if not targetParam:
            try:
                ElemTypeId = element.GetTypeId()
                ElemType = doc.GetElement(ElemTypeId)
                if ElemType:
                    targetParam = ElemType.LookupParameter(parameterObj.name)
            except:
                pass

        if targetParam:
            definition = targetParam.Definition
            if str(targetParam.StorageType) != 'String':
                try:
                    unit = definition.UnitType
                except:
                    try:
                        unit = targetParam.GetUnitTypeId()
                    except:
                        unit = None

                parameterObj.unitType = unit


class TransferWindow(forms.WPFWindow):
    def __init__(self, source_names, target_names):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.result = None
        self.source_combo.ItemsSource = source_names
        self.target_combo.ItemsSource = target_names

        if source_names:
            self.source_combo.SelectedIndex = 0
        if target_names:
            if len(target_names) > 1 and source_names and target_names[0] == source_names[0]:
                self.target_combo.SelectedIndex = 1
            else:
                self.target_combo.SelectedIndex = 0

        self._refresh_state()

    def _selected_source(self):
        if self.source_combo.SelectedItem:
            return str(self.source_combo.SelectedItem)
        return None

    def _selected_target(self):
        if self.target_combo.SelectedItem:
            return str(self.target_combo.SelectedItem)
        return None

    def _refresh_state(self):
        source = self._selected_source()
        target = self._selected_target()

        if hasattr(self, "source_details"):
            self.source_details.Text = ""
        if hasattr(self, "target_details"):
            self.target_details.Text = ""

        is_valid = bool(source and target)
        if is_valid and source == target:
            if hasattr(self, "validation_text"):
                self.validation_text.Text = u"Исходный и целевой параметр должны отличаться."
            is_valid = False
        else:
            if hasattr(self, "validation_text"):
                self.validation_text.Text = u""

        if hasattr(self, "transfer_button"):
            self.transfer_button.IsEnabled = is_valid

    def selection_changed(self, sender, args):
        self._refresh_state()

    def accept(self, sender, args):
        source = self._selected_source()
        target = self._selected_target()
        if not source or not target or source == target:
            return
        no_unfold = False
        if hasattr(self, "no_unfold_checkbox") and self.no_unfold_checkbox is not None:
            no_unfold = bool(self.no_unfold_checkbox.IsChecked)
        self.result = (source, target, no_unfold)
        self.Close()

    def cancel(self, sender, args):
        self.result = None
        self.Close()


def parse_number_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, bool):
        return 1.0 if value else 0.0

    s = str(value).strip()
    if not s:
        return None

    s = s.replace(u"\u2212", u"-").replace(u"\xa0", u"").replace(u"\u202f", u"").replace(u" ", u"")

    try:
        return float(s.replace(u",", u"."))
    except:
        pass

    match = re.search(r'[-+]?\d+(?:[.,]\d+)?', s)
    if match:
        try:
            return float(match.group(0).replace(u",", u"."))
        except:
            pass

    return None


def get_cell_value(view_schedule, row, col_index, first_row=0):
    # 1. TableView.GetCellText (SectionType.Body)
    try:
        v = view_schedule.GetCellText(SectionType.Body, row, col_index)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    except:
        pass

    # 2. TableSectionData.GetCellText
    try:
        table_data = view_schedule.GetTableData()
        body_section = table_data.GetSectionData(SectionType.Body)
        v = body_section.GetCellText(row, col_index)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    except:
        pass

    # 3. Относительный индекс строки (row - first_row), если first_row > 0
    if first_row > 0 and row >= first_row:
        rel_row = row - first_row
        try:
            v = view_schedule.GetCellText(SectionType.Body, rel_row, col_index)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        except:
            pass
        try:
            table_data = view_schedule.GetTableData()
            body_section = table_data.GetSectionData(SectionType.Body)
            v = body_section.GetCellText(rel_row, col_index)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        except:
            pass

    return ""


def get_sort_column_indexes(definition):
    field_order = list(definition.GetFieldOrder())
    indexes = []
    for sort_field in definition.GetSortGroupFields():
        try:
            indexes.append(field_order.index(sort_field.FieldId))
        except:
            pass
    return indexes


def normalize_group_cell(value):
    return str(value or '').strip().lower()


def get_group_row_key(view_schedule, row, sort_indexes):
    return tuple(
        normalize_group_cell(get_cell_value(view_schedule, row, col_index))
        for col_index in sort_indexes
    )


def capture_grouped_values(view_schedule, definition, source_col_index):
    sort_indexes = get_sort_column_indexes(definition)
    data_rows = get_schedule_data_rows(view_schedule, definition, 0)
    values = {}
    fallback_found = False
    fallback_value = None

    for row in data_rows:
        source_value = get_cell_value(view_schedule, row, source_col_index)
        if sort_indexes:
            key = get_group_row_key(view_schedule, row, sort_indexes)
            if any(key):
                values[key] = source_value
        else:
            fallback_found = True
            fallback_value = source_value

    return {
        'sort_indexes': sort_indexes,
        'values': values,
        'fallback_found': fallback_found,
        'fallback_value': fallback_value
    }


def get_grouped_value(snapshot, view_schedule, row):
    sort_indexes = snapshot['sort_indexes']
    if not sort_indexes:
        return snapshot['fallback_found'], snapshot['fallback_value']

    key = get_group_row_key(view_schedule, row, sort_indexes)
    if key in snapshot['values']:
        return True, snapshot['values'][key]
    return False, None


def get_schedule_data_rows(view_schedule, definition, elements_count=0):
    try:
        table_data = view_schedule.GetTableData()
        body_section = table_data.GetSectionData(SectionType.Body)
        first_row = body_section.FirstRowNumber
        num_rows = body_section.NumberOfRows
        last_row = body_section.LastRowNumber
        num_cols = body_section.NumberOfColumns
    except Exception as e:
        return list(range(elements_count)) if elements_count > 0 else [0]

    field_names = []
    field_headings = []
    for field_id in definition.GetFieldOrder():
        try:
            f = definition.GetField(field_id)
            name = str(f.GetName()).strip()
            field_names.append(name)
            try:
                heading = str(f.ColumnHeading).strip()
                field_headings.append(heading)
            except:
                field_headings.append(name)
        except:
            pass

    field_names_lower = set(n.lower() for n in field_names if n)
    field_headings_lower = set(h.lower() for h in field_headings if h)
    sched_name = getattr(view_schedule, 'Name', '') or ''
    sched_name_lower = str(sched_name).strip().lower()

    header_rows = []
    data_rows = []

    # Читаем все строки секции
    all_row_data = {}
    for r in range(first_row, first_row + num_rows):
        cells = []
        for c in range(num_cols):
            val = get_cell_value(view_schedule, r, c, first_row)
            cells.append(val)
        all_row_data[r] = cells

    # Если спецификация развернута по элементам (elements_count > 0):
    if elements_count > 0 and num_rows >= elements_count:
        data_start_row = first_row + num_rows - elements_count
        for r in range(first_row, data_start_row):
            header_rows.append(r)
        for r in range(data_start_row, first_row + num_rows):
            data_rows.append(r)
        return data_rows

    # Свернутый вид (no_unfold или elements_count == 0):
    for r in range(first_row, first_row + num_rows):
        cells = all_row_data.get(r, [])
        non_empty_cells = [c for c in cells if c and str(c).strip() != ""]

        # 1. Проверка на полностью пустую строку
        if not non_empty_cells:
            if not data_rows:
                header_rows.append(r)
                continue

        # 2. Проверка на совпадение с именем спецификации (Title)
        is_title = False
        if sched_name_lower and len(non_empty_cells) == 1:
            first_val = str(non_empty_cells[0]).strip().lower()
            if first_val == sched_name_lower or sched_name_lower in first_val or first_val in sched_name_lower:
                is_title = True

        # 3. Проверка на совпадение с названиями столбцов (Headers)
        matches = 0
        for cell_val in non_empty_cells:
            cv_lower = str(cell_val).strip().lower()
            if cv_lower in field_names_lower or cv_lower in field_headings_lower:
                matches += 1
            else:
                for fn in field_names_lower:
                    if fn and (fn in cv_lower or cv_lower in fn):
                        matches += 1
                        break

        is_header = False
        if is_title:
            is_header = True
        elif matches > 0 and r < first_row + 3:
            is_header = True
        elif definition.ShowHeaders and not header_rows and not data_rows and r == first_row:
            is_header = True

        if is_header:
            header_rows.append(r)
        else:
            data_rows.append(r)

    # Если все строки оказались помечены как заголовки (или data_rows пуст), берем последнюю строку как данные
    if not data_rows and num_rows > 0:
        last_r = first_row + num_rows - 1
        data_rows = [last_r]
        if last_r in header_rows:
            header_rows.remove(last_r)

    return data_rows


def find_schedule_row_for_element(view_schedule, definition, data_rows, element, default_idx=0, target_col_idx=None):
    if not data_rows:
        return None
    if len(data_rows) == 1:
        return data_rows[0]

    try:
        sort_fields = list(definition.GetSortGroupFields())
        field_order = list(definition.GetFieldOrder())

        matching_rows = list(data_rows)
        for sg in sort_fields:
            field = definition.GetField(sg.FieldId)
            field_name = field.GetName()
            if sg.FieldId in field_order:
                col_idx = field_order.index(sg.FieldId)
            else:
                continue

            elem_val = None
            try:
                elem_val = element.GetParamValue(field_name)
            except:
                pass
            if elem_val is None:
                p = element.LookupParameter(field_name)
                if not p:
                    try:
                        type_id = element.GetTypeId()
                        if type_id and type_id != ElementId.InvalidElementId:
                            elem_type = doc.GetElement(type_id)
                            if elem_type:
                                p = elem_type.LookupParameter(field_name)
                    except:
                        pass
                if p:
                    elem_val = p.AsValueString() or p.AsString()
                    if elem_val is None:
                        try:
                            elem_val = p.AsDouble()
                        except:
                            try:
                                elem_val = p.AsInteger()
                            except:
                                pass

            if elem_val is not None:
                elem_val_str = str(elem_val).strip()
                elem_num = parse_number_value(elem_val)
                filtered = []
                for r in matching_rows:
                    cell_text = get_cell_value(view_schedule, r, col_idx)
                    if cell_text is None or cell_text == "":
                        continue
                    cell_text_clean = str(cell_text).strip()
                    if (elem_val_str.lower() == cell_text_clean.lower()
                            or elem_val_str.lower() in cell_text_clean.lower()
                            or cell_text_clean.lower() in elem_val_str.lower()):
                        filtered.append(r)
                        continue
                    if elem_num is not None:
                        cell_num = parse_number_value(cell_text_clean)
                        if cell_num is not None and abs(elem_num - cell_num) < 1e-5:
                            filtered.append(r)
                            continue

                if filtered:
                    matching_rows = filtered

        if matching_rows:
            if target_col_idx is not None:
                for mr in matching_rows:
                    val = get_cell_value(view_schedule, mr, target_col_idx)
                    if val is not None and val != "":
                        return mr
            return matching_rows[0]
    except Exception as e:
        pass

    if target_col_idx is not None:
        if default_idx < len(data_rows):
            val = get_cell_value(view_schedule, data_rows[default_idx], target_col_idx)
            if val is not None and val != "":
                return data_rows[default_idx]
        for r in data_rows:
            val = get_cell_value(view_schedule, r, target_col_idx)
            if val is not None and val != "":
                return r

    if default_idx < len(data_rows):
        return data_rows[default_idx]
    return data_rows[0]


def execute():
    definition = vs.Definition

    try:
        elementsOnView = list(
            FilteredElementCollector(doc, vs.Id)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception as e:
        elementsOnView = []

    source_params = getParamsInShed(definition)
    target_params = getTargetParamsInShed(definition, elementsOnView)
    if not target_params:
        target_params = getTargetParamsInShed(definition)

    if not source_params:
        forms.alert(
            u"В спецификации нет доступных параметров для чтения.",
            title=__title__,
            warn_icon=True
        )
        return

    if not target_params:
        forms.alert(
            u"В спецификации нет доступных параметров для записи.",
            title=__title__,
            warn_icon=True
        )
        return

    window = TransferWindow(source_params, target_params)
    window.ShowDialog()
    if not window.result:
        return

    if isinstance(window.result, (tuple, list)):
        if len(window.result) == 3:
            startParamName, endParamName, no_unfold = window.result
        else:
            startParamName, endParamName = window.result[0], window.result[1]
            no_unfold = False
    else:
        return

    position_param_element = ensure_position_parameter()
    errorList = []
    grouped_values = None

    with revit.Transaction("Перенос параметров"):
        rollback_itemized = False
        rollback_header = False
        position_params = []
        position_field_id = None
        position_field_created = False

        # Уникальный ID можно прочитать по строкам только в развернутой спецификации.
        if definition.IsItemized == False:
            rollback_itemized = True

        if definition.ShowHeaders == False:
            rollback_header = True
        definition.ShowHeaders = True

        position_field_id, position_field_created = ensure_position_schedule_field(
            definition,
            position_param_element
        )

        hidden = []
        i = 0
        while i < definition.GetFieldCount():
            if definition.GetField(i).IsHidden == True:
                hidden.append(i)
            definition.GetField(i).IsHidden = False
            i += 1

        try:
            doc.Regenerate()
            paraObj = getParaInd(startParamName, definition)
            if no_unfold:
                grouped_values = capture_grouped_values(
                    vs,
                    definition,
                    paraObj.index
                )

            definition.IsItemized = True

            for element in elementsOnView:
                position_param = element.get_Parameter(POSITION_PARAM_GUID)
                if not position_param or position_param.IsReadOnly:
                    raise Exception(
                        u'Параметр {0} отсутствует или недоступен для записи у элемента ID {1}'.format(
                            POSITION_PARAM_NAME,
                            element.Id.IntegerValue if hasattr(element.Id, 'IntegerValue') else element.Id.Value
                        )
                    )
                position_param.Set(str(
                    element.Id.IntegerValue if hasattr(element.Id, 'IntegerValue') else element.Id.Value
                ))
                position_params.append(position_param)

            doc.Regenerate()

            paraObj = getParaInd(startParamName, definition)
            endParaObj = getParaInd(endParamName, definition)
            posParaObj = getParaInd(POSITION_PARAM_NAME, definition)

            body_section = vs.GetTableData().GetSectionData(SectionType.Body)
            data_rows = range(body_section.FirstRowNumber, body_section.LastRowNumber + 1)
            schedule_elements = []
            for sched_row in data_rows:
                element_id_text = get_cell_value(vs, sched_row, posParaObj.index)
                try:
                    element_id = int(str(element_id_text).strip())
                    schedule_element = doc.GetElement(ElementId(element_id))
                except:
                    continue
                if schedule_element:
                    schedule_elements.append((sched_row, schedule_element))

            if not schedule_elements:
                raise Exception(
                    u'Не удалось сопоставить строки спецификации по параметру {0}. '
                    u'Добавьте этот параметр в спецификацию.'.format(POSITION_PARAM_NAME)
                )

            for idx, row_element in enumerate(schedule_elements):
                sched_row, sheduleElement = row_element
                try:
                    elem_id_val = sheduleElement.Id.IntegerValue if hasattr(sheduleElement.Id, 'IntegerValue') else sheduleElement.Id.Value
                except:
                    elem_id_val = str(sheduleElement.Id)

                isNoneUnitType(sheduleElement, paraObj)
                isNoneUnitType(sheduleElement, endParaObj)

                startParamValue = None

                if no_unfold:
                    value_found, startParamValue = get_grouped_value(
                        grouped_values,
                        vs,
                        sched_row
                    )
                    if not value_found:
                        errorList.append(
                            u'Не найдена исходная строка свернутой спецификации для ID {0}'.format(
                                elem_id_val
                            )
                        )
                        continue
                else:
                    # Стандартный режим с раскрытием
                    # 1. Сначала пробуем получить значение напрямую из параметра модели
                    try:
                        startParamValue = sheduleElement.GetParamValue(startParamName)
                    except Exception as e:
                        startParamValue = None

                    if startParamValue is None:
                        p_src = sheduleElement.LookupParameter(startParamName)
                        if not p_src:
                            try:
                                type_id = sheduleElement.GetTypeId()
                                if type_id and type_id != ElementId.InvalidElementId:
                                    elem_type = doc.GetElement(type_id)
                                    if elem_type:
                                        p_src = elem_type.LookupParameter(startParamName)
                            except Exception as e:
                                pass
                        if p_src:
                            try:
                                src_storage = str(p_src.StorageType)
                                if src_storage == 'Double':
                                    startParamValue = p_src.AsDouble()
                                elif src_storage == 'Integer':
                                    startParamValue = p_src.AsInteger()
                                elif src_storage == 'String':
                                    startParamValue = p_src.AsString()
                                elif src_storage == 'ElementId':
                                    startParamValue = p_src.AsElementId()
                            except Exception as e:
                                pass

                    # Конвертация единиц для параметров, прочитанных из модели (во внутренних единицах)
                    if startParamValue is not None and paraObj.unitType is not None:
                        try:
                            converted = UnitUtils.ConvertFromInternalUnits(startParamValue, paraObj.unitType)
                            startParamValue = converted
                        except Exception as e:
                            pass

                    # 2. ЕСЛИ ЗНАЧЕНИЕ НЕ НАЙДЕНО В ПАРАМЕТРАХ МОДЕЛИ: Читаем из расчетной ячейки спецификации!
                    if startParamValue is None or startParamValue == "":
                        cell_val = get_cell_value(vs, sched_row, paraObj.index)
                        if cell_val is not None and cell_val != "":
                            startParamValue = cell_val

                targetParam = sheduleElement.LookupParameter(endParamName)
                if not targetParam:
                    ElemTypeId = sheduleElement.GetTypeId()
                    ElemType = doc.GetElement(ElemTypeId)
                    if ElemType:
                        targetParam = ElemType.LookupParameter(endParamName)

                if not targetParam:
                    err = 'У элементов спецификации не существует целевого параметра. Возможно вы выбрали расчетное значение.'
                    if err not in errorList:
                        errorList.append(err)
                    continue

                if targetParam.IsReadOnly:
                    error = 'Целевой параметр недоступен для редактирования'
                    if error not in errorList:
                        errorList.append(error)
                    continue

                try:
                    storage_str = str(targetParam.StorageType)
                    if storage_str == 'Double':
                        if startParamValue is None or startParamValue == '':
                            final_val = 0.0
                        else:
                            parsed_num = parse_number_value(startParamValue)
                            if parsed_num is None:
                                final_val = 0.0
                            else:
                                if endParaObj.unitType is not None:
                                    try:
                                        final_val = UnitUtils.ConvertToInternalUnits(float(parsed_num), endParaObj.unitType)
                                    except Exception as e:
                                        final_val = float(parsed_num)
                                else:
                                    final_val = float(parsed_num)

                        targetParam.Set(float(final_val))

                    elif storage_str == 'Integer':
                        if startParamValue is None or startParamValue == '':
                            final_val = 0
                        else:
                            parsed_num = parse_number_value(startParamValue)
                            if parsed_num is not None:
                                final_val = int(round(parsed_num))
                            else:
                                val_str = str(startParamValue).strip().lower()
                                if val_str in ('да', 'true', 'истина', 'yes'):
                                    final_val = 1
                                else:
                                    final_val = 0

                        targetParam.Set(int(final_val))

                    elif storage_str == 'String':
                        if startParamValue is None:
                            final_val = ''
                        else:
                            final_val = str(startParamValue)
                        targetParam.Set(str(final_val))

                except Exception as e:
                    errorList.append("Ошибка записи для ID {0}: {1}".format(elem_id_val, e))

        finally:
            for position_param in position_params:
                try:
                    position_param.Set('')
                except Exception as cleanup_error:
                    errorList.append(
                        u'Не удалось очистить параметр {0}: {1}'.format(
                            POSITION_PARAM_NAME, cleanup_error
                        )
                    )

            if rollback_itemized == True:
                definition.IsItemized = False

            if rollback_header == True:
                definition.ShowHeaders = False

            i = 0
            while i < definition.GetFieldCount():
                if i in hidden:
                    definition.GetField(i).IsHidden = True
                i += 1

            if position_field_created and position_field_id is not None:
                try:
                    definition.RemoveField(position_field_id)
                except Exception as cleanup_error:
                    errorList.append(
                        u'Не удалось удалить временное поле {0}: {1}'.format(
                            POSITION_PARAM_NAME, cleanup_error
                        )
                    )

    if errorList:
        for error in set(errorList):
            print(error)


doc = __revit__.ActiveUIDocument.Document  # type: Document
vs = doc.ActiveView

try:
    if isinstance(vs, ViewSchedule) or (vs.Category and vs.Category.IsId(BuiltInCategory.OST_Schedules)):
        vsShedule = True
    else:
        vsShedule = False
except Exception as e:
    vsShedule = False

if vsShedule:
    try:
        execute()
    except Exception as e:
        print(traceback.format_exc())
    finally:
        try:
            remove_created_position_parameter()
        except Exception as cleanup_error:
            print(
                u'Не удалось удалить созданный параметр {0}: {1}'.format(
                    POSITION_PARAM_NAME, cleanup_error
                )
            )
    if len(report_rows) > 0:
        for report in report_rows:
            print('Некоторые элементы не были отработаны так как заняты пользователем ' + report)
else:
    print("Применяйте скрипт на активном виде спецификации")
