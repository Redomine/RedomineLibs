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

from collections import namedtuple
import re
import sys
import System
from Autodesk.Revit.DB import *
from pyrevit import revit, forms
from Autodesk.Revit.DB.Mechanical import DuctSystemType
from Autodesk.Revit.DB import ConnectorElement, MEPSystemClassification
import os
from pyrevit import revit, forms
from System import Guid
from System.Windows import TextWrapping, Thickness
from System.Windows.Controls import ComboBox, TextBlock
import shutil


uiapp = __revit__  # UIApplication
app = uiapp.Application  # Application
doc = __revit__.ActiveUIDocument.Document
view = doc.ActiveView

LINK_WORKSET_MARKER = u"Связи"
CHOICE_WINDOW_XAML = os.path.join(os.path.dirname(__file__), "WorksetChoiceWindow.xaml")
WorksetChoice = namedtuple("WorksetChoice", "file_name sections candidates links")
SECTION_ALIASES = {
    u"КООРД": (u"KOORD", u"КООРД"),
    u"АР": (u"AR", u"АР"),
    u"КР": (u"KR", u"КР"),
    u"ОВ": (u"OV", u"ОВ", u"KV", u"КВ"),
    u"ИТП": (u"ITP", u"ИТП"),
    u"ВК": (u"VK", u"ВК"),
    u"СС": (u"SS", u"СС"),
    u"ЭОМ": (u"EOM", u"ЭОМ"),
}
PARKING_ALIASES = (u"PRK", u"Паркинг")


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
        if name is not None:
            if name not in self.edited_reports:
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


def normalize_code(value):
    value = value.upper()
    for source, replacement in CONFUSABLE_CHARS.items():
        value = value.replace(source, replacement)
    return value


SECTION_BY_CODE = {
    normalize_code(alias): section
    for section, aliases in SECTION_ALIASES.items()
    for alias in aliases
}
PARKING_CODES = {normalize_code(alias) for alias in PARKING_ALIASES}
NAME_PART = re.compile(ur"[^\W_]+", re.UNICODE)


def get_sections(name):
    codes = set(NAME_PART.findall(normalize_code(name)))
    sections = {SECTION_BY_CODE[code] for code in codes if code in SECTION_BY_CODE}
    if u"ИТП" in sections:
        sections.discard(u"ОВ")
    if u"АР" in sections and codes & PARKING_CODES:
        sections.remove(u"АР")
        sections.add(u"АР паркинг")
    return tuple(sorted(sections))


def set_link_ws(link, ws):
    param = link.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    if not param.IsReadOnly:
        param.Set(ws.Id.IntegerValue)

    link_type = link.GetElementType()
    type_param = link_type.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    if not type_param.IsReadOnly:
        type_param.Set(ws.Id.IntegerValue)


class WorksetChoiceWindow(forms.WPFWindow):
    def __init__(self, choices):
        forms.WPFWindow.__init__(self, CHOICE_WINDOW_XAML)
        self.rows = []
        for choice in sorted(choices, key=lambda item: item.file_name.lower()):
            label = TextBlock()
            label.Text = choice.file_name
            if len(choice.links) > 1:
                label.Text += u" (экземпляров: {})".format(len(choice.links))
            label.TextWrapping = TextWrapping.Wrap
            label.Margin = Thickness(0, 0, 0, 4)
            self.choice_rows.Children.Add(label)

            combo = ComboBox()
            combo.ItemsSource = [u"Пропустить"] + [ws.Name for ws in choice.candidates]
            combo.SelectedIndex = 0
            combo.Margin = Thickness(0, 0, 0, 12)
            self.choice_rows.Children.Add(combo)
            self.rows.append((choice, combo))

        self.apply_button.Click += self.confirm

    def confirm(self, sender, args):
        self.DialogResult = True

    def selections(self):
        for choice, combo in self.rows:
            index = combo.SelectedIndex
            yield choice, choice.candidates[index - 1] if index > 0 else None


def record_unmatched(unmatched, sections, link, workset_table):
    link_workset = workset_table.GetWorkset(link.WorksetId)
    workset_name = link_workset.Name if link_workset else u"не определён"
    unmatched.setdefault(sections, []).append((link.Name, workset_name))


def show_workset_report(link_count, matched_count, unmatched, link_worksets, worksets_by_section):
    if not link_count:
        print u"В текущей модели нет экземпляров связей Revit."
        return

    missing_count = 0
    ambiguous_count = 0
    for sections, links in unmatched.items():
        if len(sections) == 1:
            if worksets_by_section.get(sections[0]):
                ambiguous_count += len(links)
            else:
                missing_count += len(links)

    skipped_count = link_count - matched_count - missing_count - ambiguous_count
    print (u"Экземпляров связей: {} | Рабочий набор подобран: {} | "
           u"Рабочий набор не найден: {} | Несколько подходящих наборов: {} | Пропущено: {}").format(
               link_count, matched_count, missing_count, ambiguous_count, skipped_count)

    for sections, links in sorted(unmatched.items()):
        if not sections:
            reason = u"В имени файла не распознан раздел"
        elif len(sections) > 1:
            reason = u"В имени файла несколько разделов: {}".format(u", ".join(sections))
        else:
            section = sections[0]
            candidates = worksets_by_section.get(section, [])
            if candidates:
                reason = u"{} — несколько подходящих наборов: {}".format(
                    section, u"; ".join(sorted(ws.Name for ws in candidates)))
            else:
                reason = u"{} — нет подходящего рабочего набора с маркером «{}»".format(
                    section, LINK_WORKSET_MARKER)

        print u"{} (связей: {})".format(reason, len(links))
        for link_name, workset_name in sorted(links):
            print u"  • {} | Текущий РН: {}".format(link_name, workset_name)

    if missing_count:
        if link_worksets:
            print u"Рабочие наборы с маркером «{}»: {}".format(
                LINK_WORKSET_MARKER, u"; ".join(sorted(ws.Name for ws in link_worksets)))
        else:
            print u"В модели нет рабочих наборов с маркером «{}».".format(LINK_WORKSET_MARKER)


def main():
    report = EditorReport(doc)
    workset_table = doc.GetWorksetTable()
    collector = FilteredWorksetCollector(doc)
    marker = LINK_WORKSET_MARKER.upper()
    link_worksets = [
        ws for ws in collector.OfKind(WorksetKind.UserWorkset)
        if marker in ws.Name.upper()
    ]
    worksets_by_section = {}
    for ws in link_worksets:
        sections = get_sections(ws.Name)
        if len(sections) == 1:
            worksets_by_section.setdefault(sections[0], []).append(ws)

    unmatched = {}
    assignments = []
    choices_by_type = {}
    link_instances = FilteredElementCollector(doc) \
        .OfClass(RevitLinkInstance) \
        .ToElements()

    for link in link_instances:
        if report.is_element_edited(link):
            continue

        file_name = link.Name.split(u" : ", 1)[0]
        sections = get_sections(file_name)
        candidates = worksets_by_section.get(sections[0], []) if len(sections) == 1 else []
        if len(candidates) == 1:
            assignments.append((link, candidates[0]))
        elif candidates:
            type_id = link.GetTypeId().GetIdValue()
            if type_id not in choices_by_type:
                choices_by_type[type_id] = WorksetChoice(
                    file_name, sections, sorted(candidates, key=lambda ws: ws.Name), [])
            choices_by_type[type_id].links.append(link)
        else:
            record_unmatched(unmatched, sections, link, workset_table)

    if choices_by_type:
        dialog = WorksetChoiceWindow(choices_by_type.values())
        if not dialog.ShowDialog():
            print u"Сопоставление отменено. Изменения в модель не внесены."
            return

        for choice, workset in dialog.selections():
            for link in choice.links:
                if workset is None:
                    record_unmatched(unmatched, choice.sections, link, workset_table)
                else:
                    assignments.append((link, workset))

    if assignments:
        with revit.Transaction("BIM: Сопоставление РН"):
            for link, workset in assignments:
                set_link_ws(link, workset)

    show_workset_report(link_instances.Count, len(assignments), unmatched, link_worksets, worksets_by_section)
    report.show_report()


main()
