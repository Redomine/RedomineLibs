# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import json
import os

from System import Environment
from pyrevit import EXEC_PARAMS


BRIDGE_ROOT = os.path.join(
    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
    "RvtMcp",
    "pyrevit-bridge")
ACTIVE_PATH = os.path.join(BRIDGE_ROOT, "active-{0}.json".format(os.getpid()))


def _read_json(path):
    with io.open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def _append_event(run_id, value):
    path = os.path.join(BRIDGE_ROOT, "dialogs-{0}.jsonl".format(run_id))
    with io.open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + u"\n")


if os.path.exists(ACTIVE_PATH):
    state = _read_json(ACTIVE_PATH)
    event_args = EXEC_PARAMS.event_args
    dialog_id = getattr(event_args, "DialogId", "") or ""
    message = getattr(event_args, "Message", "") or ""
    searchable = (dialog_id + " " + message).lower()

    macro_tokens = (
        "macro", "macros", u"макрос", u"макросы"
    )
    coordination_tokens = (
        "coordination review", "coordination", u"координац", u"проверка координации"
    )
    allowed = any(token in searchable for token in macro_tokens + coordination_tokens)
    dismissed = False
    if allowed:
        try:
            dismissed = bool(event_args.OverrideResult(1))
        except Exception:
            dismissed = False

    _append_event(state.get("runId"), {
        "dialogId": dialog_id,
        "message": message,
        "allowlisted": allowed,
        "dismissed": dismissed
    })
