# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import json
import os
import shutil
import sys
import traceback

from System import Environment
from System.Collections.Generic import List
from pyrevit import DB, HOST_APP, script


BRIDGE_ROOT = os.path.join(
    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
    "RvtMcp",
    "pyrevit-bridge")
PROCESS_ID = os.getpid()
PENDING_PATH = os.path.join(BRIDGE_ROOT, "pending-{0}.json".format(PROCESS_ID))


def _read_json(path):
    with io.open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def _write_json_atomic(path, value):
    temp_path = path + ".tmp"
    with io.open(temp_path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
    if os.path.exists(path):
        os.remove(path)
    os.rename(temp_path, path)


def _same_path(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _ensure_not_open(path):
    for document in HOST_APP.app.Documents:
        try:
            if _same_path(document.PathName, path):
                raise RuntimeError("The requested file is already open in this Revit session: {0}".format(path))
        except RuntimeError:
            raise
        except Exception:
            continue


def _build_workset_configuration(model_path, mode, requested_names):
    if mode == "all":
        return DB.WorksetConfiguration(DB.WorksetConfigurationOption.OpenAllWorksets), []
    if mode == "none":
        return DB.WorksetConfiguration(DB.WorksetConfigurationOption.CloseAllWorksets), []
    if mode == "last_viewed":
        return DB.WorksetConfiguration(DB.WorksetConfigurationOption.OpenLastViewed), []

    previews = list(DB.WorksharingUtils.GetUserWorksetInfo(model_path))
    by_name = dict((preview.Name.lower(), preview) for preview in previews)
    missing = [name for name in requested_names if name.lower() not in by_name]
    if missing:
        raise RuntimeError("Worksets were not found: {0}".format(", ".join(missing)))

    ids = List[DB.WorksetId]()
    for name in requested_names:
        ids.Add(by_name[name.lower()].Id)
    configuration = DB.WorksetConfiguration(DB.WorksetConfigurationOption.CloseAllWorksets)
    configuration.Open(ids)
    return configuration, [preview.Name for preview in previews]


def _document_summary(document):
    worksets = []
    if document.IsWorkshared:
        for workset in DB.FilteredWorksetCollector(document).OfKind(DB.WorksetKind.UserWorkset):
            worksets.append({"name": workset.Name, "isOpen": bool(workset.IsOpen)})
    return {
        "title": document.Title,
        "path": document.PathName,
        "isFamilyDocument": bool(document.IsFamilyDocument),
        "isWorkshared": bool(document.IsWorkshared),
        "isDetached": bool(getattr(document, "IsDetached", False)),
        "worksets": worksets
    }


def _unload_links_for_open_document(document):
    rows = []
    link_types = list(DB.FilteredElementCollector(document).OfClass(DB.RevitLinkType))
    for link_type in link_types:
        name = link_type.Name
        try:
            if not DB.RevitLinkType.IsLoaded(document, link_type.Id):
                rows.append({"name": name, "status": "already_unloaded"})
                continue
            if document.IsWorkshared and not bool(getattr(document, "IsDetached", False)):
                changed = link_type.UnloadLocally(None)
                status = "unloaded_locally"
            else:
                changed = link_type.Unload(None)
                status = "unloaded_in_open_document"
            rows.append({"name": name, "status": status if changed else "unchanged"})
        except Exception as error:
            rows.append({"name": name, "status": "failed", "error": str(error)})
    return rows


def _open_model(request):
    path = request["path"]
    _ensure_not_open(path)
    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    options = DB.OpenOptions()
    if request.get("detach", False):
        options.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndPreserveWorksets

    mode = request.get("worksetMode", "all")
    configuration, available_names = _build_workset_configuration(
        model_path,
        mode,
        request.get("worksetNames", []))
    options.SetOpenWorksetsConfiguration(configuration)
    ui_document = HOST_APP.uiapp.OpenAndActivateDocument(model_path, options, False)
    document = ui_document.Document
    links = []
    if request.get("unloadLinksAfterOpen", False):
        links = _unload_links_for_open_document(document)
    result = _document_summary(document)
    result["availableWorksetsBeforeOpen"] = available_names
    result["links"] = links
    return result


def _open_family(request):
    path = request["path"]
    _ensure_not_open(path)
    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    options = DB.OpenOptions()
    ui_document = HOST_APP.uiapp.OpenAndActivateDocument(model_path, options, False)
    if not ui_document.Document.IsFamilyDocument:
        raise RuntimeError("Revit opened the file, but it is not a family document.")
    return _document_summary(ui_document.Document)


def _execute_pyrevit_command(request):
    command_path = os.path.abspath(request["commandPath"])
    if not command_path.lower().endswith(".pushbutton") or not os.path.isdir(command_path):
        raise RuntimeError("commandPath must be an existing .pushbutton directory.")
    script_path = os.path.join(command_path, "script.py")
    if not os.path.isfile(script_path):
        raise RuntimeError("The .pushbutton directory does not contain script.py.")
    if request.get("requiresSelection", False):
        ui_document = HOST_APP.uiapp.ActiveUIDocument
        if ui_document is None or ui_document.Selection.GetElementIds().Count == 0:
            raise RuntimeError("This pyRevit command requires at least one selected element.")

    command_globals = dict(globals())
    command_globals.update({
        "__name__": "__main__",
        "__file__": script_path,
        "__commandpath__": command_path,
        "__commandname__": os.path.basename(command_path)[:-len(".pushbutton")],
        "__shiftclick__": False,
        "__forceddebugmode__": False
    })
    previous_path = list(sys.path)
    try:
        library_path = os.path.join(command_path, "lib")
        if os.path.isdir(library_path) and library_path not in sys.path:
            sys.path.insert(0, library_path)
        if command_path not in sys.path:
            sys.path.insert(0, command_path)
        execfile(script_path, command_globals)
    finally:
        sys.path[:] = previous_path
    return {
        "commandPath": command_path,
        "scriptPath": script_path,
        "requiresSelection": bool(request.get("requiresSelection", False))
    }


def _dialog_events(run_id):
    path = os.path.join(BRIDGE_ROOT, "dialogs-{0}.jsonl".format(run_id))
    if not os.path.exists(path):
        return []
    events = []
    with io.open(path, "r", encoding="utf-8") as stream:
        for line in stream:
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    return events


def main():
    if not os.path.exists(PENDING_PATH):
        return

    request = _read_json(PENDING_PATH)
    run_id = request.get("runId")
    if not run_id:
        raise RuntimeError("Bridge request does not contain runId.")

    running_path = os.path.join(BRIDGE_ROOT, "running-{0}.json".format(run_id))
    active_path = os.path.join(BRIDGE_ROOT, "active-{0}.json".format(PROCESS_ID))
    result_path = os.path.join(BRIDGE_ROOT, "result-{0}.json".format(run_id))
    shutil.move(PENDING_PATH, running_path)
    _write_json_atomic(active_path, {"runId": run_id, "operation": request.get("operation")})

    response = {"runId": run_id, "operation": request.get("operation")}
    try:
        if request.get("operation") == "open_model":
            data = _open_model(request)
        elif request.get("operation") == "open_family":
            data = _open_family(request)
        elif request.get("operation") == "execute_pyrevit_command":
            data = _execute_pyrevit_command(request)
        else:
            raise RuntimeError("Unsupported bridge operation: {0}".format(request.get("operation")))
        response.update({"status": "completed", "success": True, "data": data})
    except Exception as error:
        response.update({
            "status": "failed",
            "success": False,
            "error": str(error),
            "traceback": traceback.format_exc()
        })
    finally:
        response["dialogs"] = _dialog_events(run_id)
        _write_json_atomic(result_path, response)
        for path in (running_path, active_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass


main()
