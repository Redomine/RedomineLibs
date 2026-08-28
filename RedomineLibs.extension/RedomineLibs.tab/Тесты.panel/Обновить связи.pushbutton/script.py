# -*- coding: utf-8 -*-
__persistentengine__ = True

import os
import re
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System import EventHandler, GC

from Autodesk.Revit.DB import (
    BasicFileInfo,
    DetachFromCentralOption,
    ExternalFileReferenceType,
    ExternalFileUtils,
    FilteredElementCollector,
    LinkLoadResult,
    LinkedFileStatus,
    ModelPathUtils,
    OpenOptions,
    RelinquishOptions,
    RevitLinkType,
    SaveAsOptions,
    SynchronizeWithCentralOptions,
    TransactWithCentralOptions,
    TransmissionData,
    WorksetConfiguration,
    WorksetConfigurationOption,
    WorksharingSaveAsOptions,
)
from Autodesk.Revit.UI.Events import IdlingEventArgs
from pyrevit import forms


TITLE = u"Обновить связи"
GRID_LEVEL_WORKSET = u"Общие уровни и сетки"
DEFAULT_WORKSET = u"Рабочий набор 1"
SYNC_COMMENT = u"Автоматическое обновление связей"
REVIT_BACKUP_RE = re.compile(r"\.\d{4}\.rvt$", re.IGNORECASE)

app = __revit__.Application

if "_BATCH_STATE" not in globals():
    _BATCH_STATE = None
if "_BATCH_IDLING_HANDLER" not in globals():
    _BATCH_IDLING_HANDLER = None

try:
    text_type = unicode
except NameError:
    text_type = str


def safe_text(value):
    if value is None:
        return u""
    try:
        return text_type(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return u"<не удалось получить текст>"


def write(message):
    try:
        print(safe_text(message))
    except Exception:
        pass


def dispose_api_object(value):
    if value is None:
        return
    try:
        value.Dispose()
    except Exception:
        pass


def normalized_path(path):
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def is_revit_project_file(filename):
    lowered = filename.lower()
    return (
        lowered.endswith(u".rvt")
        and not REVIT_BACKUP_RE.search(filename)
        and not filename.startswith(u"~")
    )


def find_rvt_files(directory):
    result = []
    scan_errors = []
    skipped_backups = 0

    def handle_walk_error(error):
        scan_errors.append(safe_text(error))

    for root, directories, filenames in os.walk(
        directory, topdown=True, onerror=handle_walk_error
    ):
        directories.sort(key=lambda value: value.lower())
        filenames.sort(key=lambda value: value.lower())

        for filename in filenames:
            if not filename.lower().endswith(u".rvt"):
                continue
            if not is_revit_project_file(filename):
                skipped_backups += 1
                continue
            result.append(os.path.join(root, filename))

    return result, scan_errors, skipped_backups


def build_link_index(link_paths):
    grouped = {}
    for path in link_paths:
        key = os.path.basename(path).lower()
        grouped.setdefault(key, []).append(path)

    unique = {}
    ambiguous = {}
    for key, paths in grouped.items():
        paths.sort(key=lambda value: value.lower())
        if len(paths) == 1:
            unique[key] = paths[0]
        else:
            ambiguous[key] = paths

    return unique, ambiguous


def element_id_key(element_id):
    try:
        return int(element_id.IntegerValue)
    except Exception:
        return safe_text(element_id)


def read_file_flags(project_path):
    info = None
    result = {
        "is_workshared": False,
        "format": u"",
    }

    try:
        info = BasicFileInfo.Extract(project_path)
        result["is_workshared"] = bool(info.IsWorkshared)
        result["format"] = safe_text(info.Format)
    finally:
        dispose_api_object(info)

    return result


def create_open_options(detach_from_central, open_all_worksets):
    options = OpenOptions()
    workset_configuration = None
    try:
        configuration_option = (
            WorksetConfigurationOption.OpenAllWorksets
            if open_all_worksets
            else WorksetConfigurationOption.CloseAllWorksets
        )
        workset_configuration = WorksetConfiguration(
            configuration_option
        )
        options.SetOpenWorksetsConfiguration(workset_configuration)
        if detach_from_central:
            options.DetachFromCentralOption = (
                DetachFromCentralOption.DetachAndPreserveWorksets
            )
        return options, workset_configuration
    except Exception:
        dispose_api_object(workset_configuration)
        dispose_api_object(options)
        raise


def enable_worksharing_if_needed(document):
    if document.IsWorkshared:
        return False

    document.EnableWorksharing(GRID_LEVEL_WORKSET, DEFAULT_WORKSET)
    return True


def save_as_central(document, project_path, is_transmitted):
    save_options = SaveAsOptions()
    worksharing_options = WorksharingSaveAsOptions()

    try:
        save_options.OverwriteExistingFile = True
        worksharing_options.SaveAsCentral = True
        if is_transmitted:
            worksharing_options.ClearTransmitted = True
        save_options.SetWorksharingOptions(worksharing_options)
        document.SaveAs(project_path, save_options)
    finally:
        dispose_api_object(worksharing_options)
        dispose_api_object(save_options)


def get_link_filename(document, link_type):
    reference = None
    model_path = None
    try:
        reference = ExternalFileUtils.GetExternalFileReference(
            document, link_type.Id
        )
        model_path = reference.GetAbsolutePath()
        visible_path = ModelPathUtils.ConvertModelPathToUserVisiblePath(
            model_path
        )
        filename = os.path.basename(visible_path)
        if filename:
            return filename
    except Exception:
        pass
    finally:
        dispose_api_object(model_path)
        dispose_api_object(reference)

    name = safe_text(link_type.Name).strip()
    if name and not name.lower().endswith(u".rvt"):
        name += u".rvt"
    return os.path.basename(name)


def reference_should_load(status):
    return status not in (
        LinkedFileStatus.Unloaded,
        LinkedFileStatus.LocallyUnloaded,
        LinkedFileStatus.Imported,
        LinkedFileStatus.Invalid,
    )


def get_transmission_reference_data(transmission_data, reference_id):
    try:
        reference_data = transmission_data.GetDesiredReferenceData(
            reference_id
        )
    except Exception:
        reference_data = None
    if reference_data is not None:
        return reference_data
    return transmission_data.GetLastSavedReferenceData(reference_id)


def dispose_link_preparation(preparation):
    if preparation is None:
        return
    for reference in preparation["references"]:
        dispose_api_object(reference["path"])
    preparation["references"] = []


# Старые версии Revit запрещают LoadFrom и Unload для связи в закрытом наборе.
# Поэтому связи временно выгружаются в закрытом RVT до открытия всех наборов.
def prepare_revit_links_for_open(project_path):
    preparation = {
        "transmission_available": False,
        "original_is_transmitted": False,
        "written": False,
        "references": [],
        "link_load_statuses": {},
    }
    transmission_data = None
    reference_data = None
    model_path = None

    try:
        model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(
            project_path
        )
        transmission_data = TransmissionData.ReadTransmissionData(model_path)
        if transmission_data is None:
            return preparation

        preparation["transmission_available"] = True
        preparation["original_is_transmitted"] = bool(
            transmission_data.IsTransmitted
        )

        for reference_id in (
            transmission_data.GetAllExternalFileReferenceIds()
        ):
            try:
                reference_data = get_transmission_reference_data(
                    transmission_data, reference_id
                )
                if (
                    reference_data.ExternalFileReferenceType
                    != ExternalFileReferenceType.RevitLink
                ):
                    continue

                status = reference_data.GetLinkedFileStatus()
                reference_path = reference_data.GetPath()
                reference = {
                    "id": reference_id,
                    "path": reference_path,
                    "path_type": reference_data.PathType,
                    "should_load": reference_should_load(status),
                }
                preparation["references"].append(reference)
                preparation["link_load_statuses"][
                    element_id_key(reference_id)
                ] = status

                transmission_data.SetDesiredReferenceData(
                    reference_id,
                    reference_path,
                    reference_data.PathType,
                    False,
                )
            finally:
                dispose_api_object(reference_data)
                reference_data = None

        if preparation["references"]:
            transmission_data.IsTransmitted = True
            TransmissionData.WriteTransmissionData(
                model_path, transmission_data
            )
            preparation["written"] = True

        return preparation
    except Exception:
        dispose_link_preparation(preparation)
        raise
    finally:
        dispose_api_object(reference_data)
        dispose_api_object(transmission_data)
        dispose_api_object(model_path)


def restore_prepared_transmission_data(project_path, preparation):
    if preparation is None or not preparation["written"]:
        return

    transmission_data = None
    model_path = None
    try:
        model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(
            project_path
        )
        transmission_data = TransmissionData.ReadTransmissionData(model_path)
        if transmission_data is None:
            raise RuntimeError(
                u"Не удалось прочитать TransmissionData для восстановления."
            )

        for reference in preparation["references"]:
            transmission_data.SetDesiredReferenceData(
                reference["id"],
                reference["path"],
                reference["path_type"],
                reference["should_load"],
            )
        transmission_data.IsTransmitted = preparation[
            "original_is_transmitted"
        ]
        TransmissionData.WriteTransmissionData(
            model_path, transmission_data
        )
        preparation["written"] = False
    finally:
        dispose_api_object(transmission_data)
        dispose_api_object(model_path)


def collect_link_items(document):
    result = []
    link_types = (
        FilteredElementCollector(document)
        .OfClass(RevitLinkType)
        .ToElements()
    )
    for link_type in link_types:
        result.append(
            (link_type, get_link_filename(document, link_type))
        )
    result.sort(key=lambda item: item[1].lower())
    return result


def update_revit_links(
    document,
    project_path,
    links_index,
    ambiguous_links,
    link_load_statuses,
):
    stats = {
        "total": 0,
        "matched": 0,
        "updated": 0,
        "failed": 0,
        "restore_failed": 0,
        "restored_loaded": 0,
        "ambiguous": 0,
        "not_found": 0,
        "nested": 0,
    }

    link_items = collect_link_items(document)
    stats["total"] = len(link_items)

    for link_type, link_filename in link_items:
        display_name = link_filename or safe_text(link_type.Name)

        if link_type.IsNestedLink:
            stats["nested"] += 1
            write(
                u"    [Пропущено] Вложенная связь: {}".format(display_name)
            )
            continue

        key = link_filename.lower()
        if not key:
            stats["not_found"] += 1
            write(
                u"    [Нет имени] Не удалось определить имя связи: {}".format(
                    safe_text(link_type.Id)
                )
            )
            continue

        if key in ambiguous_links:
            stats["ambiguous"] += 1
            write(
                u"    [Неоднозначно] Для связи {} найдено файлов:".format(
                    display_name
                )
            )
            for duplicate_path in ambiguous_links[key]:
                write(u"      {}".format(duplicate_path))
            continue

        new_link_path = links_index.get(key)
        if not new_link_path:
            stats["not_found"] += 1
            write(
                u"    [Нет совпадения] {}".format(display_name)
            )
            continue

        stats["matched"] += 1
        if normalized_path(new_link_path) == normalized_path(project_path):
            stats["failed"] += 1
            write(
                u"    [Ошибка] Связь {} указывает на сам проект.".format(
                    display_name
                )
            )
            continue

        restore_unloaded = not reference_should_load(
            link_load_statuses.get(element_id_key(link_type.Id))
        )
        write(
            u"    Обновление: {} -> {}".format(
                display_name, new_link_path
            )
        )

        load_result = None
        new_model_path = None
        try:
            new_model_path = (
                ModelPathUtils.ConvertUserVisiblePathToModelPath(
                    new_link_path
                )
            )
            load_result = link_type.LoadFrom(new_model_path, None)
            if not LinkLoadResult.IsCodeSuccess(load_result.LoadResult):
                raise RuntimeError(
                    u"Revit вернул результат {}".format(
                        safe_text(load_result.LoadResult)
                    )
                )

            stats["updated"] += 1

            if restore_unloaded:
                try:
                    link_type.Unload(None)
                except Exception as unload_error:
                    stats["restore_failed"] += 1
                    write(
                        u"      [Предупреждение] Не удалось вернуть связи "
                        u"состояние «выгружена»: {}".format(
                            safe_text(unload_error)
                        )
                    )
        except Exception as link_error:
            stats["failed"] += 1
            write(
                u"      [Ошибка] {}".format(safe_text(link_error))
            )
        finally:
            dispose_api_object(load_result)
            dispose_api_object(new_model_path)

    for link_type, link_filename in link_items:
        if link_type.IsNestedLink:
            continue

        original_status = link_load_statuses.get(
            element_id_key(link_type.Id)
        )
        if not reference_should_load(original_status):
            continue
        if RevitLinkType.IsLoaded(document, link_type.Id):
            continue

        display_name = link_filename or safe_text(link_type.Name)
        load_result = None
        try:
            write(
                u"    Восстановление исходной загрузки: {}".format(
                    display_name
                )
            )
            load_result = link_type.Load()
            if not LinkLoadResult.IsCodeSuccess(load_result.LoadResult):
                raise RuntimeError(
                    u"Revit вернул результат {}".format(
                        safe_text(load_result.LoadResult)
                    )
                )
            stats["restored_loaded"] += 1
        except Exception as restore_error:
            stats["restore_failed"] += 1
            write(
                u"      [Ошибка восстановления] {}".format(
                    safe_text(restore_error)
                )
            )
        finally:
            dispose_api_object(load_result)

    return stats


def synchronize_and_relinquish(document):
    relinquish_options = RelinquishOptions(True)
    sync_options = SynchronizeWithCentralOptions()
    transact_options = TransactWithCentralOptions()

    try:
        relinquish_options.UserWorksets = True
        relinquish_options.StandardWorksets = True
        relinquish_options.CheckedOutElements = True
        relinquish_options.FamilyWorksets = True
        relinquish_options.ViewWorksets = True

        sync_options.SetRelinquishOptions(relinquish_options)
        sync_options.Comment = SYNC_COMMENT
        document.SynchronizeWithCentral(
            transact_options, sync_options
        )
    finally:
        dispose_api_object(transact_options)
        dispose_api_object(sync_options)
        dispose_api_object(relinquish_options)


def unload_revit_links_for_session_cleanup(document):
    stats = {
        "loaded": 0,
        "unloaded": 0,
        "failed": 0,
    }

    for link_type, link_filename in collect_link_items(document):
        display_name = link_filename or safe_text(link_type.Name)
        try:
            if link_type.IsNestedLink:
                continue
            if not RevitLinkType.IsLoaded(document, link_type.Id):
                continue

            stats["loaded"] += 1
            link_type.Unload(None)
            stats["unloaded"] += 1
            write(
                u"    Выгружена из памяти сеанса: {}".format(
                    display_name
                )
            )
        except Exception as unload_error:
            stats["failed"] += 1
            write(
                u"    [Ошибка очистки сеанса] {}: {}".format(
                    display_name, safe_text(unload_error)
                )
            )

    return stats


def close_document(document):
    if not document.Close(False):
        raise RuntimeError(
            u"Revit вернул False при закрытии документа."
        )


def process_project(project_path, links_index, ambiguous_links):
    result = {
        "success": False,
        "synchronized": False,
        "error": u"",
        "close_error": u"",
        "worksharing_enabled": False,
        "link_stats": None,
        "session_cleanup_failed": 0,
    }
    document = None
    open_options = None
    open_workset_configuration = None
    model_path = None
    preparation = None
    preparation_committed = False

    write(u"-" * 72)
    write(u"Проект: {}".format(project_path))

    try:
        file_flags = read_file_flags(project_path)
        if file_flags["format"]:
            write(
                u"  Формат исходного файла: Revit {}".format(
                    file_flags["format"]
                )
            )

        preparation = prepare_revit_links_for_open(project_path)
        prepared_link_count = len(preparation["references"])
        if preparation["written"]:
            write(
                u"  Через TransmissionData временно выгружено "
                u"RVT-связей: {}".format(prepared_link_count)
            )

        open_all_worksets = bool(
            preparation["transmission_available"]
        )
        (
            open_options,
            open_workset_configuration,
        ) = create_open_options(
            file_flags["is_workshared"], open_all_worksets
        )
        model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(
            project_path
        )

        if open_all_worksets:
            write(
                u"  Открытие проекта со всеми пользовательскими "
                u"рабочими наборами; RVT-связи выгружены..."
            )
        else:
            write(
                u"  TransmissionData отсутствует. Открытие с закрытыми "
                u"пользовательскими рабочими наборами..."
            )
        document = app.OpenDocumentFile(model_path, open_options)

        if document.IsReadOnly:
            raise RuntimeError(u"Документ открыт только для чтения.")
        if document.IsFamilyDocument:
            raise RuntimeError(u"Открытый документ является семейством.")

        if not preparation["transmission_available"]:
            top_level_links = [
                link_type
                for link_type, unused_name in collect_link_items(document)
                if not link_type.IsNestedLink
            ]
            if top_level_links:
                raise RuntimeError(
                    u"В проекте есть RVT-связи, но TransmissionData "
                    u"недоступна. Безопасное открытие всех рабочих "
                    u"наборов невозможно."
                )

        result["worksharing_enabled"] = enable_worksharing_if_needed(
            document
        )
        if result["worksharing_enabled"]:
            write(u"  Совместная работа включена.")

        write(u"  Сохранение по исходному пути как центральный файл...")
        save_as_central(
            document,
            project_path,
            (
                preparation["original_is_transmitted"]
                or preparation["written"]
            ),
        )
        preparation_committed = True

        write(u"  Обновление RVT-связей...")
        result["link_stats"] = update_revit_links(
            document,
            project_path,
            links_index,
            ambiguous_links,
            preparation["link_load_statuses"],
        )

        write(
            u"  Синхронизация с освобождением всех рабочих наборов..."
        )
        synchronize_and_relinquish(document)
        result["synchronized"] = True
        result["success"] = True
    except Exception as project_error:
        result["error"] = safe_text(project_error)
        write(u"  [Критическая ошибка] {}".format(result["error"]))
        write(traceback.format_exc())
    finally:
        dispose_api_object(open_workset_configuration)
        dispose_api_object(open_options)
        dispose_api_object(model_path)
        if document is not None:
            # После успешной синхронизации нужные состояния уже сохранены.
            # При ошибке Close(False) отбросит все несохраненные изменения.
            # Поэтому эта выгрузка освобождает связи только в текущем сеансе.
            write(
                u"  Выгрузка RVT-связей из памяти сеанса "
                u"(без сохранения)..."
            )
            try:
                cleanup_stats = unload_revit_links_for_session_cleanup(
                    document
                )
                result["session_cleanup_failed"] = cleanup_stats["failed"]
                write(
                    u"  Из памяти выгружено RVT-связей: {} из {}.".format(
                        cleanup_stats["unloaded"], cleanup_stats["loaded"]
                    )
                )
            except Exception as cleanup_error:
                result["session_cleanup_failed"] = 1
                write(
                    u"  [Ошибка очистки сеанса] {}".format(
                        safe_text(cleanup_error)
                    )
                )
                write(traceback.format_exc())

            try:
                close_document(document)
                document = None
                write(u"  Документ закрыт.")
            except Exception as close_error:
                result["close_error"] = safe_text(close_error)
                result["success"] = False
                write(
                    u"  [Ошибка закрытия] {}".format(
                        result["close_error"]
                    )
                )
        if (
            preparation is not None
            and preparation["written"]
            and not preparation_committed
        ):
            if document is None:
                try:
                    restore_prepared_transmission_data(
                        project_path, preparation
                    )
                    write(
                        u"  Исходное состояние TransmissionData "
                        u"восстановлено."
                    )
                except Exception as restore_error:
                    result["success"] = False
                    write(
                        u"  [Ошибка восстановления TransmissionData] "
                        u"{}".format(safe_text(restore_error))
                    )
            else:
                write(
                    u"  [Ошибка восстановления TransmissionData] "
                    u"Документ не удалось закрыть."
                )
        dispose_link_preparation(preparation)

    if result["success"]:
        write(u"  Готово.")

    return result


def get_open_document_paths():
    result = set()
    for document in app.Documents:
        try:
            if document.PathName:
                result.add(normalized_path(document.PathName))
        except Exception:
            pass
    return result


def duplicate_links_report(ambiguous_links):
    if not ambiguous_links:
        return None

    lines = [
        u"Одинаковые имена в папке связей. Такие связи будут пропущены:"
    ]
    for filename in sorted(ambiguous_links.keys()):
        lines.append(u"\n{}:".format(filename))
        for path in ambiguous_links[filename]:
            lines.append(u"  {}".format(path))
    return u"\n".join(lines)


def show_scan_errors(project_errors, link_errors):
    for error in project_errors:
        write(u"[Ошибка чтения папки проектов] {}".format(error))
    for error in link_errors:
        write(u"[Ошибка чтения папки связей] {}".format(error))


def release_api_wrappers():
    try:
        GC.Collect()
        GC.WaitForPendingFinalizers()
        GC.Collect()
    except Exception:
        pass


def build_batch_summary(state):
    completed = 0
    partial = 0
    failed = 0
    updated_links = 0
    failed_links = 0
    session_cleanup_errors = 0

    for result in state["results"]:
        session_cleanup_errors += result["session_cleanup_failed"]
        link_stats = result["link_stats"]
        if link_stats:
            if result["synchronized"]:
                updated_links += link_stats["updated"]
            failed_links += (
                link_stats["failed"] + link_stats["restore_failed"]
            )

        link_warnings = bool(
            link_stats
            and (
                link_stats["failed"]
                or link_stats["restore_failed"]
                or link_stats["ambiguous"]
            )
        )
        if not result["success"]:
            failed += 1
        elif link_warnings or result["session_cleanup_failed"]:
            partial += 1
        else:
            completed += 1

    summary = (
        u"Обработка завершена.\n\n"
        u"Успешно: {0}\n"
        u"С предупреждениями: {1}\n"
        u"С ошибкой: {2}\n"
        u"Пропущено открытых: {3}\n"
        u"Обновлено связей: {4}\n"
        u"Ошибок связей: {5}\n"
        u"Ошибок очистки сеанса: {6}\n"
        u"Ошибок чтения подпапок: {7}"
    ).format(
        completed,
        partial,
        failed,
        state["skipped_open"],
        updated_links,
        failed_links,
        session_cleanup_errors,
        state["scan_error_count"],
    )

    if state["fatal_error"]:
        summary += u"\n\nКритическая ошибка очереди:\n{}".format(
            state["fatal_error"]
        )

    has_warnings = bool(
        partial
        or failed
        or state["skipped_open"]
        or state["scan_error_count"]
        or state["fatal_error"]
    )
    return summary, has_warnings


def detach_batch_handler():
    global _BATCH_IDLING_HANDLER

    if _BATCH_IDLING_HANDLER is None:
        return
    try:
        __revit__.Idling -= _BATCH_IDLING_HANDLER
    except Exception as unsubscribe_error:
        write(
            u"[Ошибка отключения обработчика Idling] {}".format(
                safe_text(unsubscribe_error)
            )
        )
    finally:
        _BATCH_IDLING_HANDLER = None


def finish_batch(state):
    global _BATCH_STATE

    detach_batch_handler()
    _BATCH_STATE = None
    summary, has_warnings = build_batch_summary(state)
    write(u"-" * 72)
    write(summary)
    try:
        forms.alert(
            summary,
            title=TITLE,
            warn_icon=has_warnings,
        )
    except Exception as alert_error:
        write(
            u"[Ошибка итогового окна] {}".format(safe_text(alert_error))
        )


def get_residual_batch_link_paths(state):
    current_paths = get_open_document_paths()
    residual_keys = (
        current_paths
        & state["candidate_path_keys"]
        - state["initial_open_document_paths"]
    )
    return sorted(residual_keys)


def process_batch_step(state):
    if state["release_cycles"]:
        if state["release_cycles"] == 2:
            write(
                u"  Возврат управления Revit и освобождение документов "
                u"связей..."
            )
            release_api_wrappers()
        state["release_cycles"] -= 1
        return False

    residual_paths = get_residual_batch_link_paths(state)
    if residual_paths:
        state["residual_waits"] += 1
        if state["residual_waits"] == 1:
            write(
                u"  Ожидание закрытия документов связей в сеансе Revit..."
            )
        if state["residual_waits"] > 20:
            raise RuntimeError(
                u"После закрытия проекта Revit продолжает удерживать файлы "
                u"связей открытыми:\n{}".format(
                    u"\n".join(residual_paths)
                )
            )
        release_api_wrappers()
        state["release_cycles"] = 1
        return False

    state["residual_waits"] = 0
    project_files = state["project_files"]

    while state["index"] < len(project_files):
        project_path = project_files[state["index"]]
        if normalized_path(project_path) not in get_open_document_paths():
            break

        state["skipped_open"] += 1
        state["index"] += 1
        write(u"-" * 72)
        write(
            u"[Пропущено] Проект уже открыт в текущем Revit: {}".format(
                project_path
            )
        )

    if state["index"] >= len(project_files):
        return True

    project_path = project_files[state["index"]]
    write(
        u"Пакет: проект {0} из {1}".format(
            state["index"] + 1, len(project_files)
        )
    )
    state["results"].append(
        process_project(
            project_path,
            state["links_index"],
            state["ambiguous_links"],
        )
    )
    state["index"] += 1
    state["release_cycles"] = 2
    return False


def on_batch_idling(sender, event_args):
    state = _BATCH_STATE
    if state is None:
        detach_batch_handler()
        return

    try:
        if process_batch_step(state):
            finish_batch(state)
            return
        event_args.SetRaiseWithoutDelay()
    except Exception as batch_error:
        state["fatal_error"] = safe_text(batch_error)
        write(
            u"[Критическая ошибка очереди] {}".format(
                state["fatal_error"]
            )
        )
        write(traceback.format_exc())
        finish_batch(state)


def start_batch(state):
    global _BATCH_STATE
    global _BATCH_IDLING_HANDLER

    if _BATCH_STATE is not None:
        raise RuntimeError(u"Пакетная обработка уже выполняется.")

    handler = EventHandler[IdlingEventArgs](on_batch_idling)
    _BATCH_STATE = state
    _BATCH_IDLING_HANDLER = handler
    try:
        __revit__.Idling += handler
    except Exception:
        _BATCH_STATE = None
        _BATCH_IDLING_HANDLER = None
        raise

    write(
        u"Пакетная обработка поставлена в очередь. "
        u"Каждый проект будет выполнен в отдельном цикле Revit."
    )


def main():
    if _BATCH_STATE is not None:
        forms.alert(
            u"Пакетная обработка уже выполняется.",
            title=TITLE,
            warn_icon=True,
        )
        return

    project_directory = forms.pick_folder(
        title=u"Выберите папку с проектами Revit"
    )
    if not project_directory:
        return

    links_directory = forms.pick_folder(
        title=u"Выберите папку со связями для обновления"
    )
    if not links_directory:
        return

    project_directory = os.path.abspath(project_directory)
    links_directory = os.path.abspath(links_directory)

    project_files, project_scan_errors, project_backups = find_rvt_files(
        project_directory
    )
    link_files, link_scan_errors, link_backups = find_rvt_files(
        links_directory
    )

    if not project_files:
        forms.alert(
            u"В выбранной папке проектов не найдено файлов RVT.",
            title=TITLE,
        )
        return

    links_index, ambiguous_links = build_link_index(link_files)
    show_scan_errors(project_scan_errors, link_scan_errors)
    scan_error_count = len(project_scan_errors) + len(link_scan_errors)
    link_path_keys = set(normalized_path(path) for path in link_files)
    shared_role_files = [
        path
        for path in project_files
        if normalized_path(path) in link_path_keys
    ]

    write(u"Папка проектов: {}".format(project_directory))
    write(u"Папка связей: {}".format(links_directory))
    write(u"Найдено проектов: {}".format(len(project_files)))
    write(u"Найдено файлов связей: {}".format(len(link_files)))
    write(
        u"Файлов одновременно в обеих ролях: {}".format(
            len(shared_role_files)
        )
    )
    if project_backups or link_backups:
        write(
            u"Пропущено резервных/временных RVT: {}".format(
                project_backups + link_backups
            )
        )

    details = [
        u"Проектов: {}".format(len(project_files)),
        u"Файлов связей: {}".format(len(link_files)),
        u"Файлов одновременно в обеих ролях: {}".format(
            len(shared_role_files)
        ),
        u"Неоднозначных имен связей: {}".format(
            len(ambiguous_links)
        ),
        u"Ошибок чтения подпапок: {}".format(scan_error_count),
    ]

    confirmed = forms.alert(
        u"Запустить пакетную обработку?",
        title=TITLE,
        sub_msg=(
            u"\n".join(details)
            + u"\n\nИсходные RVT будут перезаписаны центральными "
            u"файлами текущей версии Revit."
        ),
        expanded=duplicate_links_report(ambiguous_links),
        ok=False,
        yes=True,
        no=True,
    )
    if not confirmed:
        return

    initial_open_document_paths = get_open_document_paths()
    candidate_path_keys = set(
        normalized_path(path) for path in links_index.values()
    )
    open_link_candidates = sorted(
        initial_open_document_paths & candidate_path_keys
    )
    if open_link_candidates:
        forms.alert(
            u"Перед запуском закройте документы, выбранные как файлы "
            u"связей.",
            title=TITLE,
            expanded=u"\n".join(open_link_candidates),
            warn_icon=True,
        )
        return

    state = {
        "project_files": project_files,
        "links_index": links_index,
        "ambiguous_links": ambiguous_links,
        "candidate_path_keys": candidate_path_keys,
        "initial_open_document_paths": initial_open_document_paths,
        "scan_error_count": scan_error_count,
        "results": [],
        "index": 0,
        "skipped_open": 0,
        "release_cycles": 0,
        "residual_waits": 0,
        "fatal_error": u"",
    }
    start_batch(state)


if __name__ == "__main__":
    main()
