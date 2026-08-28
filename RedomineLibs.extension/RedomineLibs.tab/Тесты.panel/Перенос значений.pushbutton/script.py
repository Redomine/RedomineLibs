# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys
import traceback
from collections import OrderedDict

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (  # noqa: E402
    ElementId,
    FilteredElementCollector,
    LabelUtils,
    StorageType,
    UnitUtils,
    ViewSchedule,
)
from pyrevit import forms, revit, script  # noqa: E402


COMMAND_DIR = os.path.dirname(__file__)
LIB_DIR = os.path.join(COMMAND_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from transfer_utils import (  # noqa: E402
    NumericValueError,
    format_number,
    parse_number,
    round_half_away_from_zero,
)


__title__ = "Перенос значений"
__doc__ = "Переносит значения между параметрами элементов активной спецификации."

TITLE = "Перенос значений"
XAML_FILE = os.path.join(COMMAND_DIR, "TransferWindow.xaml")
DIRECT_FIELD_TYPES = ("Instance", "ElementType", "ViewBased")

try:
    text_type = unicode
except NameError:
    text_type = str


class TransferError(Exception):
    pass


def as_text(value):
    if value is None:
        return u""
    try:
        return text_type(value)
    except Exception:
        return text_type(str(value))


def element_id_value(element_id):
    try:
        return int(element_id.IntegerValue)
    except Exception:
        return int(element_id.Value)


def same_element_id(first, second):
    if first is None or second is None:
        return False
    try:
        return first == second
    except Exception:
        return element_id_value(first) == element_id_value(second)


class FieldOption(object):
    def __init__(self, field, column_index):
        self.field = field
        self.column_index = column_index
        self.parameter_id = field.ParameterId
        self.field_type_name = as_text(field.FieldType)
        self.parameter_name = as_text(field.GetName())
        try:
            heading = as_text(field.ColumnHeading).strip()
        except Exception:
            heading = u""

        if heading and heading != self.parameter_name:
            self.base_label = u"{0} ({1})".format(heading, self.parameter_name)
        else:
            self.base_label = heading or self.parameter_name

        self.label = self.base_label
        self.details = u""
        self.sample_parameter = None


def assign_unique_labels(options):
    totals = {}
    for option in options:
        totals[option.base_label] = totals.get(option.base_label, 0) + 1

    for option in options:
        if totals[option.base_label] > 1:
            option.label = u"{0} [столбец {1}]".format(
                option.base_label,
                option.column_index + 1,
            )


def get_field_options(definition):
    options = []
    for column_index, field_id in enumerate(definition.GetFieldOrder()):
        field = definition.GetField(field_id)
        if as_text(field.FieldType) not in DIRECT_FIELD_TYPES:
            continue

        try:
            parameter_id = field.ParameterId
        except Exception:
            continue

        if same_element_id(parameter_id, ElementId.InvalidElementId):
            continue

        options.append(FieldOption(field, column_index))

    assign_unique_labels(options)
    return options


class ParameterResolver(object):
    def __init__(self, document):
        self.document = document
        self._parameter_maps = {}
        self._type_elements = {}

    def _get_parameter_map(self, owner):
        owner_id = element_id_value(owner.Id)
        parameter_map = self._parameter_maps.get(owner_id)
        if parameter_map is None:
            parameter_map = {}
            try:
                for parameter in owner.Parameters:
                    parameter_map[element_id_value(parameter.Id)] = parameter
            except Exception:
                pass
            self._parameter_maps[owner_id] = parameter_map
        return parameter_map

    def _get_type_element(self, element):
        try:
            type_id = element.GetTypeId()
        except Exception:
            return None

        if same_element_id(type_id, ElementId.InvalidElementId):
            return None

        type_id_value = element_id_value(type_id)
        if type_id_value not in self._type_elements:
            self._type_elements[type_id_value] = self.document.GetElement(type_id)
        return self._type_elements[type_id_value]

    def resolve(self, element, option):
        parameter_id = element_id_value(option.parameter_id)
        owners = []

        if option.field_type_name != "ElementType":
            owners.append(element)

        type_element = self._get_type_element(element)
        if type_element is not None:
            owners.append(type_element)

        for owner in owners:
            parameter = self._get_parameter_map(owner).get(parameter_id)
            if parameter is not None:
                return parameter, owner

        return None, None


class UnitBridge(object):
    def __init__(self, document):
        self.document = document

    @staticmethod
    def _spec(option, parameter):
        try:
            return option.field.GetSpecTypeId()
        except Exception:
            pass

        try:
            return option.field.UnitType
        except Exception:
            pass

        try:
            return parameter.Definition.GetDataType()
        except Exception:
            pass

        try:
            return parameter.Definition.UnitType
        except Exception:
            return None

    @staticmethod
    def _spec_key(spec):
        if spec is None:
            return u""
        try:
            return as_text(spec.TypeId)
        except Exception:
            return as_text(spec)

    def same_spec(self, source_option, source_parameter, target_option, target_parameter):
        source_spec = self._spec(source_option, source_parameter)
        target_spec = self._spec(target_option, target_parameter)
        return self._spec_key(source_spec) == self._spec_key(target_spec)

    def _is_measurable(self, option, parameter):
        spec = self._spec(option, parameter)
        if spec is None:
            return False

        try:
            return bool(UnitUtils.IsMeasurableSpec(spec))
        except Exception:
            spec_name = self._spec_key(spec)
            return spec_name not in (
                u"",
                u"UT_Undefined",
                u"UT_Number",
                u"UT_Custom",
            )

    @staticmethod
    def _unit_from_format_options(format_options):
        try:
            return format_options.GetUnitTypeId()
        except Exception:
            pass

        try:
            return format_options.DisplayUnits
        except Exception:
            return None

    def display_unit(self, option, parameter):
        try:
            format_options = option.field.GetFormatOptions()
            if not format_options.UseDefault:
                unit_id = self._unit_from_format_options(format_options)
                if unit_id is not None:
                    return unit_id
        except Exception:
            pass

        try:
            spec = self._spec(option, parameter)
            default_options = self.document.GetUnits().GetFormatOptions(spec)
            unit_id = self._unit_from_format_options(default_options)
            if unit_id is not None:
                return unit_id
        except Exception:
            pass

        try:
            return parameter.GetUnitTypeId()
        except Exception:
            pass

        try:
            return parameter.DisplayUnitType
        except Exception:
            return None

    def to_display(self, internal_value, option, parameter):
        if not self._is_measurable(option, parameter):
            return float(internal_value)

        unit_id = self.display_unit(option, parameter)
        if unit_id is None:
            raise TransferError(
                u"не удалось определить единицы исходного параметра"
            )

        try:
            return UnitUtils.ConvertFromInternalUnits(float(internal_value), unit_id)
        except Exception as error:
            raise TransferError(
                u"не удалось преобразовать исходные единицы: {0}".format(
                    as_text(error)
                )
            )

    def from_display(self, display_value, option, parameter):
        if not self._is_measurable(option, parameter):
            return float(display_value)

        unit_id = self.display_unit(option, parameter)
        if unit_id is None:
            raise TransferError(
                u"не удалось определить единицы целевого параметра"
            )

        try:
            return UnitUtils.ConvertToInternalUnits(float(display_value), unit_id)
        except Exception as error:
            raise TransferError(
                u"не удалось преобразовать целевые единицы: {0}".format(
                    as_text(error)
                )
            )

    def unit_label(self, option, parameter):
        if parameter.StorageType != StorageType.Double:
            return u""
        if not self._is_measurable(option, parameter):
            return u""

        unit_id = self.display_unit(option, parameter)
        if unit_id is None:
            return u""

        try:
            return as_text(LabelUtils.GetLabelForUnit(unit_id))
        except Exception:
            pass

        try:
            return as_text(LabelUtils.GetLabelFor(unit_id))
        except Exception:
            return u""


STORAGE_LABELS = {
    "Double": u"Число",
    "Integer": u"Целое число",
    "String": u"Текст",
    "ElementId": u"Элемент",
}


def describe_option(option, parameter, units):
    storage_name = as_text(parameter.StorageType)
    description = STORAGE_LABELS.get(storage_name, storage_name)
    unit_label = units.unit_label(option, parameter)
    if unit_label:
        description = u"{0}, {1}".format(description, unit_label)
    return description


def inspect_options(options, elements, resolver, units):
    source_options = []
    target_options = []

    for option in options:
        sample_parameter = None
        writable_parameter = None

        for element in elements:
            parameter, _owner = resolver.resolve(element, option)
            if parameter is None:
                continue

            if sample_parameter is None:
                sample_parameter = parameter
            if not parameter.IsReadOnly:
                writable_parameter = parameter
            if sample_parameter is not None and writable_parameter is not None:
                break

        if sample_parameter is None:
            continue

        option.sample_parameter = sample_parameter
        option.details = describe_option(option, sample_parameter, units)
        source_options.append(option)
        if writable_parameter is not None:
            target_options.append(option)

    return source_options, target_options


def same_parameter_field(source_option, target_option):
    return same_element_id(
        source_option.parameter_id,
        target_option.parameter_id,
    )


class TransferWindow(forms.WPFWindow):
    def __init__(self, source_options, target_options):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.result = None
        self._source_by_label = dict(
            (option.label, option) for option in source_options
        )
        self._target_by_label = dict(
            (option.label, option) for option in target_options
        )

        self.source_combo.ItemsSource = [
            option.label for option in source_options
        ]
        self.target_combo.ItemsSource = [
            option.label for option in target_options
        ]

        if source_options:
            self.source_combo.SelectedIndex = 0
        if target_options:
            self.target_combo.SelectedIndex = 0
            if (
                source_options
                and same_parameter_field(source_options[0], target_options[0])
                and len(target_options) > 1
            ):
                self.target_combo.SelectedIndex = 1

        self._refresh_state()

    def _selected_source(self):
        return self._source_by_label.get(as_text(self.source_combo.SelectedItem))

    def _selected_target(self):
        return self._target_by_label.get(as_text(self.target_combo.SelectedItem))

    def _refresh_state(self):
        source_option = self._selected_source()
        target_option = self._selected_target()

        self.source_details.Text = (
            source_option.details if source_option is not None else u""
        )
        self.target_details.Text = (
            target_option.details if target_option is not None else u""
        )

        is_valid = source_option is not None and target_option is not None
        if is_valid and same_parameter_field(source_option, target_option):
            self.validation_text.Text = (
                u"Исходный и целевой параметр должны отличаться."
            )
            is_valid = False
        else:
            self.validation_text.Text = u""

        self.transfer_button.IsEnabled = is_valid

    def selection_changed(self, sender, args):
        if hasattr(self, "transfer_button"):
            self._refresh_state()

    def accept(self, sender, args):
        source_option = self._selected_source()
        target_option = self._selected_target()
        if source_option is None or target_option is None:
            return
        if same_parameter_field(source_option, target_option):
            return

        self.result = source_option, target_option
        self.Close()

    def cancel(self, sender, args):
        self.result = None
        self.Close()


class Assignment(object):
    def __init__(self, method, value, was_rounded=False):
        self.method = method
        self.value = value
        self.was_rounded = was_rounded

    def equivalent_to(self, other):
        if self.method != other.method:
            return False

        if self.method == "double":
            first = float(self.value)
            second = float(other.value)
            tolerance = 1.0e-9 * max(1.0, abs(first), abs(second))
            return abs(first - second) <= tolerance

        if self.method == "element_id":
            return same_element_id(self.value, other.value)

        return self.value == other.value


def source_as_string(parameter, source_option, units):
    storage_type = parameter.StorageType
    if storage_type == StorageType.String:
        return as_text(parameter.AsString())
    if storage_type == StorageType.Integer:
        return as_text(parameter.AsInteger())
    if storage_type == StorageType.Double:
        display_value = units.to_display(
            parameter.AsDouble(),
            source_option,
            parameter,
        )
        return format_number(display_value)
    if storage_type == StorageType.ElementId:
        try:
            value_string = parameter.AsValueString()
        except Exception:
            value_string = None
        if value_string:
            return as_text(value_string)
        return as_text(element_id_value(parameter.AsElementId()))
    raise TransferError(u"неподдерживаемый тип исходного параметра")


def source_as_display_number(parameter, source_option, units):
    storage_type = parameter.StorageType
    if storage_type == StorageType.Double:
        return units.to_display(
            parameter.AsDouble(),
            source_option,
            parameter,
        )
    if storage_type == StorageType.Integer:
        return float(parameter.AsInteger())
    if storage_type == StorageType.String:
        value = as_text(parameter.AsString()).strip()
        if not value:
            return 0.0
        try:
            return parse_number(value)
        except NumericValueError:
            raise TransferError(
                u"текст «{0}» не является числом".format(value)
            )
    raise TransferError(u"исходное значение нельзя преобразовать в число")


def build_assignment(
    source_parameter,
    source_option,
    target_parameter,
    target_option,
    units,
):
    source_storage = source_parameter.StorageType
    target_storage = target_parameter.StorageType

    if target_storage == StorageType.String:
        return Assignment(
            "string",
            source_as_string(source_parameter, source_option, units),
        )

    if target_storage == StorageType.Integer:
        display_value = source_as_display_number(
            source_parameter,
            source_option,
            units,
        )
        integer_value = round_half_away_from_zero(display_value)
        was_rounded = abs(display_value - integer_value) > 1.0e-9
        return Assignment("integer", int(integer_value), was_rounded)

    if target_storage == StorageType.Double:
        if source_storage == StorageType.Double and units.same_spec(
            source_option,
            source_parameter,
            target_option,
            target_parameter,
        ):
            # Revit stores equal physical quantities in the same internal units.
            return Assignment("double", float(source_parameter.AsDouble()))

        if source_storage == StorageType.String:
            source_text = as_text(source_parameter.AsString()).strip()
            if not source_text:
                display_value = 0.0
            else:
                try:
                    display_value = parse_number(source_text)
                except NumericValueError:
                    # Let Revit parse explicit unit symbols using project settings.
                    return Assignment("value_string", source_text)
        else:
            display_value = source_as_display_number(
                source_parameter,
                source_option,
                units,
            )

        internal_value = units.from_display(
            display_value,
            target_option,
            target_parameter,
        )
        return Assignment("double", float(internal_value))

    if target_storage == StorageType.ElementId:
        if source_storage != StorageType.ElementId:
            raise TransferError(
                u"в параметр-ссылку можно перенести только другую ссылку"
            )
        return Assignment("element_id", source_parameter.AsElementId())

    raise TransferError(u"неподдерживаемый тип целевого параметра")


class IssueReport(object):
    def __init__(self):
        self._items = OrderedDict()

    def add(self, reason, element_id=None):
        reason = as_text(reason)
        if reason not in self._items:
            self._items[reason] = [0, []]
        item = self._items[reason]
        item[0] += 1
        if element_id is not None and len(item[1]) < 8:
            value = as_text(element_id)
            if value not in item[1]:
                item[1].append(value)

    @property
    def count(self):
        return sum(item[0] for item in self._items.values())

    @property
    def has_items(self):
        return bool(self._items)

    def rows(self):
        rows = []
        for reason, item in self._items.items():
            rows.append([reason, item[0], u", ".join(item[1])])
        return rows


class AssignmentGroup(object):
    def __init__(self, target_parameter, assignment, element_id):
        self.target_parameter = target_parameter
        self.assignment = assignment
        self.element_ids = [element_id]
        self.conflict = False

    def add(self, assignment, element_id):
        self.element_ids.append(element_id)
        if not self.assignment.equivalent_to(assignment):
            self.conflict = True


def target_key(owner, parameter):
    return (
        element_id_value(owner.Id),
        element_id_value(parameter.Id),
    )


def plan_assignments(
    elements,
    source_option,
    target_option,
    resolver,
    units,
    issues,
):
    groups = OrderedDict()

    for element in elements:
        current_element_id = element_id_value(element.Id)
        source_parameter, _source_owner = resolver.resolve(
            element,
            source_option,
        )
        if source_parameter is None:
            issues.add(u"Исходный параметр отсутствует", current_element_id)
            continue

        target_parameter, target_owner = resolver.resolve(
            element,
            target_option,
        )
        if target_parameter is None:
            issues.add(u"Целевой параметр отсутствует", current_element_id)
            continue
        if target_parameter.IsReadOnly:
            issues.add(
                u"Целевой параметр доступен только для чтения",
                current_element_id,
            )
            continue

        try:
            assignment = build_assignment(
                source_parameter,
                source_option,
                target_parameter,
                target_option,
                units,
            )
        except Exception as error:
            issues.add(
                u"Ошибка преобразования: {0}".format(as_text(error)),
                current_element_id,
            )
            continue

        key = target_key(target_owner, target_parameter)
        group = groups.get(key)
        if group is None:
            groups[key] = AssignmentGroup(
                target_parameter,
                assignment,
                current_element_id,
            )
        else:
            group.add(assignment, current_element_id)

    return groups


def apply_assignment(parameter, assignment):
    if assignment.method == "value_string":
        result = parameter.SetValueString(assignment.value)
    else:
        result = parameter.Set(assignment.value)

    if result is False:
        raise TransferError(u"Revit отклонил новое значение")


def write_assignments(groups, issues):
    successful_groups = 0
    affected_elements = set()
    rounded_groups = 0
    conflict_groups = 0

    writable_groups = []
    for key, group in groups.items():
        if group.conflict:
            conflict_groups += 1
            issues.add(
                u"Разные значения претендуют на один параметр типа",
                key[0],
            )
            continue
        writable_groups.append(group)

    if not writable_groups:
        return successful_groups, affected_elements, rounded_groups, conflict_groups

    with revit.Transaction(u"Перенос значений параметров"):
        for group in writable_groups:
            try:
                apply_assignment(group.target_parameter, group.assignment)
                successful_groups += 1
                affected_elements.update(group.element_ids)
                if group.assignment.was_rounded:
                    rounded_groups += 1
            except Exception as error:
                issues.add(
                    u"Ошибка записи: {0}".format(as_text(error)),
                    group.element_ids[0],
                )

    return successful_groups, affected_elements, rounded_groups, conflict_groups


def print_report(issues):
    if not issues.has_items:
        return

    output = script.get_output()
    output.print_md(u"### Перенос значений: пропуски и ошибки")
    try:
        output.print_table(
            table_data=issues.rows(),
            columns=[u"Причина", u"Количество", u"Примеры ID"],
        )
    except Exception:
        for reason, count, sample_ids in issues.rows():
            print(u"{0}: {1}. ID: {2}".format(reason, count, sample_ids))


def show_summary(
    element_count,
    successful_groups,
    affected_elements,
    rounded_groups,
    conflict_groups,
    issues,
):
    lines = [
        u"Элементов в спецификации: {0}".format(element_count),
        u"Записано значений: {0}".format(successful_groups),
        u"Обработано элементов: {0}".format(len(affected_elements)),
    ]
    if rounded_groups:
        lines.append(u"Округлено до целого: {0}".format(rounded_groups))
    if conflict_groups:
        lines.append(u"Конфликтов параметров типа: {0}".format(conflict_groups))
    if issues.count:
        lines.append(u"Пропусков и ошибок: {0}".format(issues.count))

    forms.alert(
        u"\n".join(lines),
        title=TITLE,
        warn_icon=issues.has_items,
    )


def main():
    document = revit.doc
    schedule = revit.active_view

    if not isinstance(schedule, ViewSchedule):
        forms.alert(
            u"Откройте спецификацию и повторите команду.",
            title=TITLE,
            warn_icon=True,
        )
        return

    try:
        elements = list(
            FilteredElementCollector(document, schedule.Id)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception as error:
        forms.alert(
            u"Элементы этой спецификации нельзя получить: {0}".format(
                as_text(error)
            ),
            title=TITLE,
            warn_icon=True,
        )
        return

    if not elements:
        forms.alert(
            u"В активной спецификации нет элементов.",
            title=TITLE,
            warn_icon=True,
        )
        return

    definition = schedule.Definition
    field_options = get_field_options(definition)
    resolver = ParameterResolver(document)
    units = UnitBridge(document)
    source_options, target_options = inspect_options(
        field_options,
        elements,
        resolver,
        units,
    )

    if not source_options:
        forms.alert(
            u"В спецификации нет доступных параметров для чтения. "
            u"Расчётные и объединённые поля не поддерживаются.",
            title=TITLE,
            warn_icon=True,
        )
        return
    if not target_options:
        forms.alert(
            u"В спецификации нет параметров, доступных для записи.",
            title=TITLE,
            warn_icon=True,
        )
        return

    window = TransferWindow(source_options, target_options)
    window.ShowDialog()
    if window.result is None:
        return

    source_option, target_option = window.result
    issues = IssueReport()
    groups = plan_assignments(
        elements,
        source_option,
        target_option,
        resolver,
        units,
        issues,
    )
    result = write_assignments(groups, issues)
    successful_groups, affected_elements, rounded_groups, conflict_groups = result

    print_report(issues)
    show_summary(
        len(elements),
        successful_groups,
        affected_elements,
        rounded_groups,
        conflict_groups,
        issues,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
        forms.alert(
            u"Команда завершилась с непредвиденной ошибкой. "
            u"Подробности выведены в окно pyRevit.",
            title=TITLE,
            warn_icon=True,
        )
