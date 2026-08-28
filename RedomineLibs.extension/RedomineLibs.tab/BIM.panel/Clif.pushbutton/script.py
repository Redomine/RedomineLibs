# -*- coding: utf-8 -*-

import os
import re

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    ModelPathUtils,
    OpenOptions,
    Transaction,
    TransactionStatus,
)
from pyrevit import forms


TITLE = u"Clif"
ROOT_FOLDER = u"C:\\Users\\Mankaev_r\\Downloads\\Clif"
SOURCES_FOLDER_NAME = u"Исходники"
MARK_PARAMETER = u"ADSK_Марка"
SOURCE_NAME_PARAMETER = u"ADSK_Наименование"
REVIT_BACKUP_RE = re.compile(r"\.\d{4}\.rfa$", re.IGNORECASE)
FAMILY_KEY_RE = re.compile(
    u"(?P<number>[КK]\\s*\\d+\\s*[A-Za-zА-Яа-яЁё]?)\\s+"
    u"(?P<model>[СC]LIF\\s*-\\s*[A-Za-zА-Яа-яЁё0-9]+)",
    re.IGNORECASE | re.UNICODE,
)

try:
    text_type = unicode
except NameError:
    text_type = str


def safe_text(value):
    try:
        return text_type(value)
    except Exception:
        return u"<не удалось получить текст ошибки>"


def collect_family_files(folder_path):
    family_files = []
    for filename in os.listdir(folder_path):
        full_path = os.path.join(folder_path, filename)
        if not os.path.isfile(full_path):
            continue
        if not filename.lower().endswith(u".rfa"):
            continue
        if REVIT_BACKUP_RE.search(filename):
            continue
        family_files.append(full_path)

    return sorted(
        family_files,
        key=lambda path: os.path.basename(path).lower(),
    )


def family_name_from_path(family_path):
    return os.path.splitext(os.path.basename(family_path))[0]


def extract_family_key(family_name):
    match = FAMILY_KEY_RE.search(family_name)
    if match is None:
        return None

    key = u"{}{}".format(
        match.group(u"number"),
        match.group(u"model"),
    ).upper()

    # В именах встречаются как латинские, так и кириллические двойники.
    key = key.replace(u"К", u"K")
    key = key.replace(u"А", u"A")
    key = key.replace(u"С", u"C")
    return re.sub(u"[\\s_-]+", u"", key)


def build_source_index(source_files):
    source_index = {}
    unrecognized = []

    for source_path in source_files:
        source_name = family_name_from_path(source_path)
        source_key = extract_family_key(source_name)
        if source_key is None:
            unrecognized.append(source_path)
            continue
        source_index.setdefault(source_key, []).append(source_path)

    return source_index, unrecognized


def find_source_family(ready_name, source_index):
    ready_key = extract_family_key(ready_name)
    if ready_key is None:
        return None, u"В имени не найден идентификатор вида «К### CLIF-модель»."

    candidates = source_index.get(ready_key, [])
    if not candidates:
        return None, u"В папке «Исходники» нет семейства с ключом {}.".format(
            ready_key
        )
    if len(candidates) > 1:
        candidate_names = u", ".join(
            family_name_from_path(path) for path in candidates
        )
        return None, u"Ключу {} соответствуют несколько исходников: {}.".format(
            ready_key,
            candidate_names,
        )

    return candidates[0], None


def open_family_document(application, family_path):
    model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(family_path)
    return application.OpenDocumentFile(model_path, OpenOptions())


def get_family_parameter(family_manager, parameter_name):
    for parameter in family_manager.Parameters:
        try:
            if safe_text(parameter.Definition.Name) == parameter_name:
                return parameter
        except Exception:
            continue
    return None


def get_formula(parameter):
    formula = parameter.Formula
    if formula is None:
        return None

    formula_text = safe_text(formula)
    if not formula_text.strip():
        return None
    return formula_text


def rollback(transaction):
    if transaction is None:
        return
    try:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
    except Exception:
        pass


def read_source_formula(application, source_path):
    source_document = None
    status = u"failed"
    formula = None
    message = u"Неизвестная ошибка чтения исходника."

    try:
        source_document = open_family_document(application, source_path)
        if not source_document.IsFamilyDocument:
            status = u"skipped"
            message = u"Исходный файл не является документом семейства."
        else:
            parameter = get_family_parameter(
                source_document.FamilyManager,
                SOURCE_NAME_PARAMETER,
            )
            if parameter is None:
                status = u"skipped"
                message = u"В исходнике нет параметра {}.".format(
                    SOURCE_NAME_PARAMETER
                )
            else:
                formula = get_formula(parameter)
                if formula is None:
                    status = u"skipped"
                    message = u"В исходнике параметр {} не содержит формулы.".format(
                        SOURCE_NAME_PARAMETER
                    )
                else:
                    status = u"ok"
                    message = u"Формула прочитана."
    except Exception as error:
        status = u"failed"
        message = u"Не удалось прочитать исходник: {}".format(
            safe_text(error)
        )
    finally:
        if source_document is not None:
            try:
                source_document.Close(False)
            except Exception as close_error:
                status = u"failed"
                formula = None
                message = u"Не удалось закрыть исходник: {}".format(
                    safe_text(close_error)
                )

    return status, formula, message


def process_ready_family(application, ready_path, source_index):
    ready_name = family_name_from_path(ready_path)
    ready_document = None
    transaction = None
    status = u"failed"
    message = u"Неизвестная ошибка."

    try:
        ready_document = open_family_document(application, ready_path)
        if not ready_document.IsFamilyDocument:
            status = u"skipped"
            message = u"Файл не является документом семейства."
        else:
            manager = ready_document.FamilyManager
            mark_parameter = get_family_parameter(manager, MARK_PARAMETER)

            if mark_parameter is None:
                status = u"skipped"
                message = u"Нет параметра {}.".format(MARK_PARAMETER)
            elif get_formula(mark_parameter) is not None:
                status = u"unchanged"
                message = u"Параметр {} уже содержит формулу.".format(
                    MARK_PARAMETER
                )
            else:
                source_path, match_error = find_source_family(
                    ready_name,
                    source_index,
                )
                if source_path is None:
                    status = u"skipped"
                    message = match_error
                else:
                    source_status, source_formula, source_message = (
                        read_source_formula(application, source_path)
                    )
                    if source_status != u"ok":
                        status = source_status
                        message = u"{}: {}".format(
                            family_name_from_path(source_path),
                            source_message,
                        )
                    else:
                        transaction = Transaction(
                            ready_document,
                            u"Скопировать формулу ADSK_Марка",
                        )
                        start_status = transaction.Start()
                        if start_status != TransactionStatus.Started:
                            raise Exception(u"Revit не начал транзакцию.")

                        manager.SetFormula(mark_parameter, source_formula)

                        commit_status = transaction.Commit()
                        if commit_status != TransactionStatus.Committed:
                            raise Exception(u"Revit откатил транзакцию.")
                        transaction = None

                        ready_document.Save()
                        status = u"updated"
                        message = u"Формула скопирована из исходника «{}».".format(
                            family_name_from_path(source_path)
                        )
    except Exception as error:
        rollback(transaction)
        status = u"failed"
        message = safe_text(error)
    finally:
        if ready_document is not None:
            try:
                ready_document.Close(False)
            except Exception as close_error:
                status = u"failed"
                message = u"Не удалось закрыть документ: {}".format(
                    safe_text(close_error)
                )

    return status, ready_name, message


def print_result(status, family_name, message):
    labels = {
        u"updated": u"ГОТОВО",
        u"unchanged": u"БЕЗ ИЗМЕНЕНИЙ",
        u"skipped": u"ПРОПУЩЕНО",
        u"failed": u"ОШИБКА",
    }
    print(
        u"[{}] {}: {}".format(
            labels.get(status, status),
            family_name,
            message,
        )
    )


def show_summary(results, cancelled):
    summary = [
        u"Обновлено: {}".format(results[u"updated"]),
        u"Уже содержали формулу: {}".format(results[u"unchanged"]),
        u"Пропущено: {}".format(results[u"skipped"]),
        u"Ошибок: {}".format(results[u"failed"]),
    ]
    if cancelled:
        summary.insert(0, u"Обработка отменена пользователем.")
    if results[u"skipped"] or results[u"failed"]:
        summary.append(u"")
        summary.append(u"Подробности показаны в выводе pyRevit.")

    forms.alert(u"\n".join(summary), title=TITLE)


def main():
    sources_folder = os.path.join(ROOT_FOLDER, SOURCES_FOLDER_NAME)
    if not os.path.isdir(ROOT_FOLDER):
        forms.alert(
            u"Не найдена папка:\n{}".format(ROOT_FOLDER),
            title=TITLE,
        )
        return
    if not os.path.isdir(sources_folder):
        forms.alert(
            u"Не найдена папка исходников:\n{}".format(sources_folder),
            title=TITLE,
        )
        return

    try:
        ready_files = collect_family_files(ROOT_FOLDER)
        source_files = collect_family_files(sources_folder)
    except Exception as error:
        forms.alert(
            u"Не удалось прочитать папки.\n\n{}".format(safe_text(error)),
            title=TITLE,
        )
        return

    if not ready_files:
        forms.alert(
            u"В основной папке нет файлов семейств .rfa.",
            title=TITLE,
        )
        return
    if not source_files:
        forms.alert(
            u"В папке «Исходники» нет файлов семейств .rfa.",
            title=TITLE,
        )
        return

    source_index, unrecognized_sources = build_source_index(source_files)
    for source_path in unrecognized_sources:
        print(
            u"[ПРОПУЩЕН ИСХОДНИК] Не найден идентификатор: {}".format(
                family_name_from_path(source_path)
            )
        )

    confirmed = forms.alert(
        u"Готовых семейств: {}.\n"
        u"Исходников: {}.\n\n"
        u"Заполнить пустые формулы {}?".format(
            len(ready_files),
            len(source_files),
            MARK_PARAMETER,
        ),
        title=TITLE,
        yes=True,
        no=True,
    )
    if not confirmed:
        return

    application = __revit__.Application
    results = {
        u"updated": 0,
        u"unchanged": 0,
        u"skipped": 0,
        u"failed": 0,
    }
    cancelled = False
    total = len(ready_files)

    with forms.ProgressBar(
        title=u"Clif: {value} из {max_value}",
        cancellable=True,
    ) as progress:
        for index, ready_path in enumerate(ready_files, 1):
            if progress.cancelled:
                cancelled = True
                break

            progress.update_progress(index, total)
            status, family_name, message = process_ready_family(
                application,
                ready_path,
                source_index,
            )
            results[status] += 1
            print_result(status, family_name, message)

    show_summary(results, cancelled)


main()
