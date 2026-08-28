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
import codecs
import datetime
import re
import traceback


uiapp = __revit__  # UIApplication
app = uiapp.Application  # Application

FOLDER_DONE = u"Готово"
FOLDER_ARCHIVE = u"Архив"
LOG_FILE_NAME = u"Пересоздать_фитинги.log"
LOG_PATH = None
REVIT_BACKUP_RE = re.compile(r"\.\d{4}\.rfa$", re.IGNORECASE)

nulify_child_names = True # Нужно ли заменять ADSK_Наименование вложений на !Не учитывать
nulify_names = True # Нужно ли приводить ADSK_Наименование установок к виду заданному в NAME_KEYS
nulify_codes = True # Нужно ли обнулять значение ADSK_Код изделия
use_custom_formulas = True # Нужно ли назначать формулы

"""
В ключи словаря пишем ключ, встречаемый в имени семейства.  
Можно свободно добавлять новые ключи
"""
NAME_KEYS = {"-p": "Приточная установка",
             "-v": "Вытяжная установка",
             "-mo":"Вытяжная установка",
             "Вытяжная": "Вытяжная установка"}

"""
В кастом формулы пишем, если нужно задать формулу во всех семействах.
Например, {"ADSK_Марка": 'ADSK_Наименование краткое'} приравняет все марки кратким наименованиям
Формул можно писать любое число
"""

#CUSTOM_FORMULAS = {"": ""}
CUSTOM_FORMULAS = {"ADSK_Марка": 'ADSK_Наименование краткое'}

def safe_text(value):
    try:
        return unicode(value)
    except Exception:
        try:
            return str(value).decode("utf-8", "replace")
        except Exception:
            return u"<не удалось преобразовать значение в текст>"

def init_log(base_dir):
    global LOG_PATH
    LOG_PATH = os.path.join(base_dir, LOG_FILE_NAME)
    try:
        with codecs.open(LOG_PATH, "a", "utf-8") as log_file:
            log_file.write(u"\n\n=== Запуск {} ===\n".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            log_file.write(u"Папка семейств: {}\n".format(safe_text(base_dir)))
        print(u"[Лог] {}".format(LOG_PATH))
    except Exception as log_err:
        LOG_PATH = None
        print(u"[Внимание] Не удалось создать лог-файл: {}".format(safe_text(log_err)))

def log_message(message, exc=None):
    text = safe_text(message)
    if exc:
        text = u"{}\n{}".format(text, safe_text(traceback.format_exc()))

    print(text)

    if not LOG_PATH:
        return

    try:
        with codecs.open(LOG_PATH, "a", "utf-8") as log_file:
            log_file.write(u"[{}] {}\n".format(datetime.datetime.now().strftime("%H:%M:%S"), text))
    except Exception as log_err:
        print(u"[Внимание] Не удалось записать в лог-файл: {}".format(safe_text(log_err)))

def path_info(path):
    try:
        exists = os.path.exists(path)
    except Exception as exists_err:
        exists = u"ошибка проверки: {}".format(safe_text(exists_err))

    try:
        is_file = os.path.isfile(path)
    except Exception as file_err:
        is_file = u"ошибка проверки: {}".format(safe_text(file_err))

    try:
        path_len = len(path)
    except Exception:
        path_len = u"неизвестно"

    return u"Путь: {}\nExists: {}\nIsFile: {}\nДлина пути: {}".format(
        safe_text(path),
        safe_text(exists),
        safe_text(is_file),
        safe_text(path_len)
    )

def is_revit_backup_file(filename):
    return REVIT_BACKUP_RE.search(filename) is not None

def select_families_folder():
    filepath = forms.pick_folder()

    if filepath is None:
        sys.exit()

    return filepath

def get_fam_param(family_doc, para_name):
    params = family_doc.FamilyManager.Parameters
    for param in params:
        if str(param.Definition.Name) == para_name:
            return param
    return None

def set_fam_param_value(family_doc, para_name, value):
    manager = family_doc.FamilyManager
    param = get_fam_param(family_doc, para_name)
    if param:
        manager.Set(param, value)

def set_fam_param_formula(family_doc, para_name, formula):
    manager = family_doc.FamilyManager
    param = get_fam_param(family_doc, para_name)
    if param:
        manager.SetFormula(param, formula)

def get_type_or_inst_param(element, para_name):
    # Сначала ищем параметр экземпляра
    param = element.LookupParameter(para_name)
    if param and not param.IsReadOnly:
        return param

    # Если не нашли или read-only, ищем в типе
    type_param = element.GetElementType().LookupParameter(para_name)
    if type_param and not type_param.IsReadOnly:
        return type_param

    # Если ничего не подошло, вернем None
    return None

def is_file_locked(filepath):
    if not os.path.exists(filepath):
        return False
    try:
        # пробуем открыть с эксклюзивным доступом
        with open(filepath, "r+"):
            return False  # открылся → значит свободен
    except IOError as e:
        if e.errno == 13 or e.errno == 32:  # доступ запрещён / используется другим процессом
            return True
        raise  # если другая ошибка — пробрасываем

def get_duct_connectors(family_doc):
    connectors = FilteredElementCollector(family_doc)\
                            .OfCategory(BuiltInCategory.OST_ConnectorElem)\
                            .WhereElementIsNotElementType()\
                            .ToElements()
    safe_connectors = []
    for connector in connectors:
        try:
            if connector.Domain == Domain.DomainHvac:
                safe_connectors.append(connector)
        except Exception:
            continue
    return safe_connectors

def connect_duct_connectors(family_doc):
    connectors = get_duct_connectors(family_doc)
    if len(connectors) != 2:
        return False

    for connector in connectors:
        param = connector.LookupParameter("Классификация систем")
        if param:
            param.Set(28)
        connectors[0].SetLinkedConnectorElement(connectors[1])

    return True


def is_params_exist(family_doc, family_name):
    param_names = [
        u"ADSK_Наименование",
        u"ADSK_Марка",
        u"ADSK_Код изделия",
        u"ADSK_Завод-изготовитель"
    ]
    params_set = set(
        unicode(param.Definition.Name)
        for param in family_doc.FamilyManager.Parameters
        if param and param.Definition and param.Definition.Name
    )
    is_all_params_exist = True

    for param_name in param_names:
        if param_name not in params_set:
            print(u"[Внимание] В семействе \"{}\" нет параметра \"{}\"".format(family_name, param_name))
            is_all_params_exist = False

    return is_all_params_exist

def process_adsk_parameters(family_doc, family_name):
    is_params_exist(family_doc, family_name)

    if nulify_child_names:
        process_nested_families(family_doc)

    if nulify_names:
        set_installation_name(family_doc, family_name)

    if nulify_codes:
        set_fam_param_value(family_doc, "ADSK_Код изделия", "")
    if use_custom_formulas:
        for param_name in CUSTOM_FORMULAS:
            set_fam_param_formula(family_doc, param_name, CUSTOM_FORMULAS[param_name])

    return True

def process_nested_families(family_doc):
    nested_families = FilteredElementCollector(family_doc)\
        .OfClass(FamilyInstance)\
        .ToElements()

    for nested_family in nested_families:
        name_param = get_type_or_inst_param(nested_family, "ADSK_Наименование")
        if name_param:
            name_param.Set("!Не учитывать")

def set_installation_name(family_doc, family_name):
    for key in NAME_KEYS:
        if key in family_name:
            set_fam_param_value(family_doc, "ADSK_Наименование", NAME_KEYS[key])

def ensure_output_dirs(base_dir):
    done_dir = os.path.join(base_dir, FOLDER_DONE)
    archive_dir = os.path.join(base_dir, FOLDER_ARCHIVE)
    for d in (done_dir, archive_dir):
        if not os.path.exists(d):
            os.makedirs(d)
    return done_dir, archive_dir

def move_copy_family(file_path, dst_dir, copy = False):
    dst_path = os.path.join(dst_dir, os.path.basename(file_path))
    operation = u"копирование" if copy else u"перемещение"

    if os.path.exists(dst_path) and copy: # Когда копируем бэкапы не надо затирать уже лежащие бэкапы
        log_message(u"[Инфо] Бэкап уже существует, повторное копирование пропущено:\n{}".format(path_info(dst_path)))
        return True

    if not os.path.exists(file_path):
        log_message(u"[Ошибка] Не найден исходный файл для операции \"{}\".\n{}".format(operation, path_info(file_path)))
        return False

    if not os.path.isdir(dst_dir):
        log_message(u"[Ошибка] Не найдена папка назначения для операции \"{}\".\n{}".format(operation, path_info(dst_dir)))
        return False

    try:
        if not copy:
            shutil.move(file_path, dst_path)
        else:
            shutil.copy(file_path, dst_path)
        return True
    except Exception as move_err:
        log_message(
            u"[Внимание] Не удалось выполнить {} файла.\nИсходный файл:\n{}\nПапка назначения:\n{}\nПуть назначения:\n{}\nОшибка: {}".format(
                operation,
                path_info(file_path),
                path_info(dst_dir),
                path_info(dst_path),
                safe_text(move_err)
            ),
            move_err
        )
        return False

def remove_backups(base_dir):
    for filename in os.listdir(base_dir):
        if is_revit_backup_file(filename):
            src = os.path.join(base_dir, filename)
            try:
                if os.path.isfile(src):
                    os.remove(src)
                    log_message(u"[Инфо] Удален резервный файл Revit: {}".format(src))
            except Exception as remove_err:
                log_message(
                    u"[Внимание] Не удалось удалить резервный файл Revit.\n{}\nОшибка: {}".format(
                        path_info(src),
                        safe_text(remove_err)
                    ),
                    remove_err
                )

def main():
    families_folder = select_families_folder()
    init_log(families_folder)

    try:
        done_dir, archive_dir = ensure_output_dirs(families_folder)
    except Exception as dirs_err:
        log_message(u"[Ошибка] Не удалось создать папки вывода в выбранной папке.\n{}\nОшибка: {}".format(
            path_info(families_folder),
            safe_text(dirs_err)
        ), dirs_err)
        forms.alert(u"Не удалось создать папки вывода.\nПодробности в выводе pyRevit и лог-файле.", title=u"Пересоздать фитинги")
        return

    try:
        family_files = [
            os.path.join(families_folder, f)
            for f in os.listdir(families_folder)
            if f.lower().endswith('.rfa') and not is_revit_backup_file(f)
        ]
    except Exception as list_err:
        log_message(u"[Ошибка] Не удалось прочитать выбранную папку.\n{}\nОшибка: {}".format(
            path_info(families_folder),
            safe_text(list_err)
        ), list_err)
        forms.alert(u"Не удалось прочитать выбранную папку.\nПодробности в выводе pyRevit и лог-файле.", title=u"Пересоздать фитинги")
        return

    if not family_files:
        forms.alert(u"В папке нет файлов .rfa!\n" + families_folder)
        return

    for family_path in family_files:
        log_message(u"[Инфо] Обработка семейства:\n{}".format(path_info(family_path)))

        if not move_copy_family(family_path, archive_dir, copy=True): # Копируем исходники для бэкапа
            log_message(u"[Пропущено] Семейство не обработано, потому что не удалось создать бэкап:\n{}".format(path_info(family_path)))
            continue

        if not os.path.exists(family_path):
            log_message(u"[Пропущено] Файл отсутствует перед открытием в Revit:\n{}".format(path_info(family_path)))
            continue

        try:
            file_locked = is_file_locked(family_path)
        except Exception as lock_err:
            log_message(
                u"[Ошибка] Не удалось проверить доступность файла перед открытием.\n{}\nОшибка: {}".format(
                    path_info(family_path),
                    safe_text(lock_err)
                ),
                lock_err
            )
            continue

        if file_locked:
            log_message(u"[Пропущено] Документ доступен только для чтения:\n{}".format(path_info(family_path)))
            continue

        updated = False
        saved_successfully = False
        family_doc = None
        t = None

        try:
            model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(family_path)
            open_opts = OpenOptions()
            family_doc = app.OpenDocumentFile(model_path, open_opts)
        except Exception as open_err:
            log_message(
                u"[Ошибка открытия] Revit не смог открыть файл семейства.\n{}\nОшибка: {}".format(
                    path_info(family_path),
                    safe_text(open_err)
                ),
                open_err
            )
            continue

        try:
            t = Transaction(family_doc, "Подготовка семейств")
            t.Start()

            family_name = os.path.splitext(os.path.basename(family_path))[0]
            process_adsk_parameters(family_doc, family_name)

            if connect_duct_connectors(family_doc):
                updated = True

            t.Commit()

            family_doc.Save()
            saved_successfully = True

        except Exception as e:
            log_message(u"[Ошибка обработки] {}\nОшибка: {}".format(path_info(family_path), safe_text(e)), e)
            if t and t.HasStarted() and not t.HasEnded():
                try:
                    t.RollBack()
                except Exception as rollback_err:
                    log_message(u"[Внимание] Не удалось откатить транзакцию.\nОшибка: {}".format(safe_text(rollback_err)), rollback_err)
        finally:
            if family_doc:
                try:
                    family_doc.Close(False)
                except Exception as close_err:
                    log_message(u"[Внимание] Не удалось закрыть документ семейства.\n{}\nОшибка: {}".format(
                        path_info(family_path),
                        safe_text(close_err)
                    ), close_err)

        if updated and saved_successfully:
            move_copy_family(family_path, done_dir)

    try:
        remove_backups(families_folder)
    except Exception as cleanup_err:
        log_message(u"[Внимание] Ошибка при удалении резервных файлов Revit.\nОшибка: {}".format(safe_text(cleanup_err)), cleanup_err)

    log_message(u"[Готово] Обработка завершена. Лог: {}".format(safe_text(LOG_PATH)))

main()
