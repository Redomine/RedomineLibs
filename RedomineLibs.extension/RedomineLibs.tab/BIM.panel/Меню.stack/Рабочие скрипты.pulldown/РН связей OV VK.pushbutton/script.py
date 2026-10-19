# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import re

import clr

clr.AddReference("dosymep.Revit.dll")
import dosymep
clr.ImportExtensions(dosymep.Revit)

from Autodesk.Revit.DB import (
    BuiltInParameter,
    Element,
    FilteredElementCollector,
    FilteredWorksetCollector,
    RevitLinkInstance,
    RevitLinkType,
    Transaction,
    TransactionStatus,
    Workset,
    WorksetKind,
)
from pyrevit import forms, revit


__title__ = u"РН связей OV/VK"
__doc__ = u"Создаёт РН для выбранных RVT-связей и переносит их типы и экземпляры."

DEFAULT_PREFIX = u"00_Связи_RVT_01_"
SUFFIX_PATTERN = re.compile(r"(?:^|_)(OV|VK)(?:_|$)", re.IGNORECASE)


def link_file_name(link_type):
    name = Element.Name.GetValue(link_type)
    return os.path.basename(name.split(u" : ", 1)[0])


def suffix_from_name(name):
    stem = os.path.splitext(name)[0]
    match = SUFFIX_PATTERN.search(stem)
    return stem[match.start(1):] if match else None


def set_workset(element, workset_id):
    parameter = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    if parameter is None or parameter.IsReadOnly:
        raise Exception(u"РН элемента недоступен для изменения")
    parameter.Set(workset_id.IntegerValue)


document = revit.doc
if not document.IsWorkshared:
    forms.alert(u"В текущем документе не включена совместная работа.", exitscript=True)

link_types = list(FilteredElementCollector(document).OfClass(RevitLinkType))
instances_by_type = {}
for instance in FilteredElementCollector(document).OfClass(RevitLinkInstance):
    instances_by_type.setdefault(instance.GetTypeId().GetIdValue(), []).append(instance)

if not link_types:
    forms.alert(u"В текущем документе нет RVT-связей.", exitscript=True)

rows = []
for link_type in link_types:
    file_name = link_file_name(link_type)
    suffix = suffix_from_name(file_name)
    instances = instances_by_type.get(link_type.Id.GetIdValue(), [])
    rows.append((file_name, link_type, instances, suffix))
rows.sort(key=lambda row: (row[0].lower(), row[1].Id.GetIdValue()))

choices = []
choice_rows = {}
for file_name, link_type, instances, suffix in rows:
    label = u"{0} | экземпляров: {1} | {2} | ID: {3}".format(
        file_name,
        len(instances),
        suffix if suffix else u"нет OV/VK",
        link_type.Id.GetIdValue(),
    )
    choices.append(label)
    choice_rows[label] = (link_type, instances, suffix)

selected = forms.SelectFromList.show(
    choices,
    title=u"Связанные файлы",
    multiselect=True,
    button_name=u"Создать рабочие наборы",
)
if not selected:
    raise SystemExit

prefix = forms.ask_for_string(
    default=DEFAULT_PREFIX,
    prompt=u"Начало имени рабочего набора",
    title=u"РН связей OV/VK",
)
if prefix is None:
    raise SystemExit
prefix = prefix.strip()
if not prefix:
    forms.alert(u"Начало имени рабочего набора не может быть пустым.", exitscript=True)

worksets = dict(
    (workset.Name.lower(), workset)
    for workset in FilteredWorksetCollector(document).OfKind(WorksetKind.UserWorkset)
)
results = []
for label in selected:
    link_type, instances, suffix = choice_rows[label]
    if not suffix:
        results.append(u"Пропущено: {0} (нет OV/VK в имени)".format(label))
        continue
    name = prefix + suffix
    transaction = Transaction(document, u"РН связи: " + name)
    try:
        transaction.Start()
        workset = worksets.get(name.lower())
        if workset is None:
            workset = Workset.Create(document, name)
        set_workset(link_type, workset.Id)
        for instance in instances:
            set_workset(instance, workset.Id)
        if transaction.Commit() != TransactionStatus.Committed:
            raise Exception(u"Revit откатил транзакцию")
        worksets[name.lower()] = workset
        results.append(u"Готово: {0} -> {1} (экземпляров: {2})".format(
            link_file_name(link_type), name, len(instances)))
    except Exception as error:
        if transaction.HasStarted():
            transaction.RollBack()
        results.append(u"Ошибка: {0}: {1}".format(link_file_name(link_type), error))

forms.alert(u"\n".join(results), title=u"РН связей OV/VK")
