# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import json
import os
import re
import shutil
import sys
import traceback

from System import Activator, AppDomain, DateTime, Environment
from System.Collections.Generic import List
from System.IO import FileInfo
from Autodesk.Revit.UI import RevitCommandId
from pyrevit import DB, HOST_APP

try:
    import builtins as builtins_module
except ImportError:
    import __builtin__ as builtins_module


BRIDGE_ROOT = os.path.join(
    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
    "RvtMcp",
    "pyrevit-bridge")
PROCESS_ID = os.getpid()
PENDING_PATH = os.path.join(BRIDGE_ROOT, "pending-{0}.json".format(PROCESS_ID))

FLOOR_HEATING_LAB_ROOT = os.path.join(
    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
    "RvtMcp",
    "floor-heating-lab")
FLOOR_HEATING_LAB_COMMAND = "floorheatinglab.pushbutton"
FLOOR_HEATING_LAB_RESULT = os.path.join(
    FLOOR_HEATING_LAB_ROOT, "result-{0}.json".format(PROCESS_ID))


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


def _next_local_model_path(central_path):
    documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments)
    if not documents or not os.path.isdir(documents):
        raise RuntimeError("The current user's Documents folder is unavailable.")

    base_name = os.path.splitext(os.path.basename(central_path))[0]
    user_name = getattr(HOST_APP, "username", None) or HOST_APP.app.Username or "user"
    safe_user_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(user_name)).strip(" .") or "user"
    local_name = "{0}_{1}_{2}".format(
        base_name,
        safe_user_name,
        DateTime.Now.ToString("yyyyMMdd_HHmmss"))
    candidate = os.path.join(documents, local_name + ".rvt")
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(documents, "{0}_{1}.rvt".format(local_name, suffix))
        suffix += 1
    return candidate


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
    detach = request.get("detach", False)
    local_path = None
    if detach:
        options.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndPreserveWorksets
    else:
        local_path = _next_local_model_path(path)
        local_model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(local_path)
        DB.WorksharingUtils.CreateNewLocal(model_path, local_model_path)
        model_path = local_model_path

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
    result["sourcePath"] = path
    result["localPath"] = local_path
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


def _find_loaded_pyrevit_command(script_path):
    runtime_type = None
    assemblies = list(AppDomain.CurrentDomain.GetAssemblies())
    for assembly in assemblies:
        runtime_type = assembly.GetType(
            "PyRevitLabs.PyRevit.Runtime.ScriptCommand", False)
        if runtime_type is not None:
            break
    if runtime_type is None:
        raise RuntimeError("The pyRevit runtime is not loaded in this Revit process.")

    script_data_field = runtime_type.GetField("ScriptData")
    if script_data_field is None:
        raise RuntimeError("The loaded pyRevit runtime does not expose ScriptData.")

    for assembly in assemblies:
        try:
            command_types = assembly.GetTypes()
        except Exception as error:
            command_types = getattr(error, "Types", None) or []
        for command_type in command_types:
            if (command_type is None or command_type.IsAbstract
                    or command_type == runtime_type
                    or not runtime_type.IsAssignableFrom(command_type)):
                continue
            try:
                instance = Activator.CreateInstance(command_type)
                script_data = script_data_field.GetValue(instance)
                loaded_script_path = getattr(script_data, "ScriptPath", None)
                if not _same_path(loaded_script_path, script_path):
                    continue
                return {
                    "commandId": str(getattr(script_data, "CommandControlId", "")),
                    "commandUniqueName": str(getattr(script_data, "CommandUniqueId", ""))
                }
            except Exception:
                continue
    return None


def _result_signature(path):
    if not os.path.isfile(path):
        return None
    info = FileInfo(path)
    return "{0}:{1}".format(info.LastWriteTimeUtc.Ticks, info.Length)


def _is_floor_heating_lab(command_path):
    return os.path.basename(command_path).lower() == FLOOR_HEATING_LAB_COMMAND


def _execfile(path, globals_dict=None, locals_dict=None):
    execfile = getattr(builtins_module, "execfile", None)
    if execfile is not None:
        execfile(path, globals_dict, locals_dict)
        return
    with open(path, "rb") as stream:
        code = compile(stream.read(), path, "exec")
    exec(code, globals_dict, locals_dict)


def _run_floor_heating_lab_script(script_path):
    """Run FloorHeatingLab script.py inline instead of relying on PostCommand.

    The button is driven by the request file and only proves success by rewriting
    result-<pid>.json, so a posted command is not observable. Executing the script
    here keeps the run synchronous and verifiable without waiting on the UI.
    """
    before = _result_signature(FLOOR_HEATING_LAB_RESULT)
    namespace = {
        "__revit__": HOST_APP.uiapp,
        "__file__": script_path,
        "__name__": "__main__",
        "__builtins__": builtins_module.__dict__,
    }
    _execfile(script_path, namespace)
    after = _result_signature(FLOOR_HEATING_LAB_RESULT)
    if after is None:
        raise RuntimeError(
            "FloorHeatingLab ran without writing its result file: {0}".format(
                FLOOR_HEATING_LAB_RESULT))
    if before is not None and after == before:
        raise RuntimeError(
            "FloorHeatingLab ran but did not refresh its result file: {0}".format(
                FLOOR_HEATING_LAB_RESULT))
    return {
        "status": "executed",
        "commandPath": os.path.abspath(os.path.dirname(script_path)),
        "scriptPath": script_path,
        "resultPath": FLOOR_HEATING_LAB_RESULT,
        "resultStampBefore": before,
        "resultStampAfter": after,
    }


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

    if _is_floor_heating_lab(command_path):
        if _find_loaded_pyrevit_command(script_path) is None:
            raise RuntimeError(
                "pyRevit did not find a loaded command for commandPath.")
        return _run_floor_heating_lab_script(script_path)

    command = _find_loaded_pyrevit_command(script_path)
    if command is None:
        raise RuntimeError("pyRevit did not find a loaded command for commandPath.")
    command_id_text = command["commandId"]
    if not command_id_text:
        raise RuntimeError("The pyRevit Ribbon button does not expose a Revit command ID.")
    command_id = RevitCommandId.LookupCommandId(command_id_text)
    if command_id is None:
        raise RuntimeError("Revit did not resolve the pyRevit command ID: {0}".format(command_id_text))
    if not HOST_APP.uiapp.CanPostCommand(command_id):
        raise RuntimeError("The pyRevit command cannot be posted in the current Revit context: {0}".format(command_id_text))
    HOST_APP.uiapp.PostCommand(command_id)
    return {
        "status": "posted",
        "commandPath": command_path,
        "scriptPath": script_path,
        "commandId": command_id_text,
        "commandUniqueName": command["commandUniqueName"],
        "requiresSelection": bool(request.get("requiresSelection", False)),
        "outputWindowIdsBefore": request.get("outputWindowIdsBefore", [])
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
