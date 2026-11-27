# -*- coding: utf-8 -*-
"""Per-project calculation journals in the current user's Documents folder."""
import io
import json
import os
import re
import tempfile
from contextlib import contextmanager

from System import Environment
from System.IO import Directory, File, Path


_INVALID_CHARS = frozenset(unicode(c) for c in Path.GetInvalidFileNameChars())
_DEVICE_NAME = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", re.I)


def project_folder(document):
    app = document.Application
    title = document.Title.strip()
    if title.lower().endswith(".rvt"):
        title = title[:-4]
    suffix = u"_" + app.Username if app.Username else None
    if suffix and title.lower().endswith(suffix.lower()):
        title = title[:-len(suffix)]
    name = u"".join(u"_" if c in _INVALID_CHARS else c for c in title).strip().rstrip(" .") or u"Проект"
    if _DEVICE_NAME.match(name):
        name = u"_" + name
    documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments)
    if not documents:
        raise IOError(u"Windows не вернула путь к папке «Документы».")
    return os.path.join(documents, "dosymep", app.VersionNumber, u"Теплый пол", name)


def _stage_log(document, data):
    folder = project_folder(document)
    path = os.path.join(folder, "run-{0}.json".format(data["run_id"]))
    Directory.CreateDirectory(folder)
    fd, temporary = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with io.open(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    except BaseException:
        File.Delete(temporary)
        raise
    return temporary, path


def _finish_log(staged, publish):
    temporary, path = staged
    try:
        if publish:
            if File.Exists(path):
                File.Replace(temporary, path, None)
            else:
                File.Move(temporary, path)
    finally:
        File.Delete(temporary)
    return path


def write_log(document, data):
    return _finish_log(_stage_log(document, data), True)


@contextmanager
def batch_journals(errors):
    # Keep only file paths in memory. Element IDs become public after the Revit commit.
    pending = []
    committed = False
    try:
        yield pending
        committed = True
    finally:
        for region_id, staged in pending:
            try:
                _finish_log(staged, committed)
            except Exception as error:
                errors.append((region_id, u"{0}".format(error)))


class RunJournal(object):
    def __init__(self, document, run_id, region_id, settings, pending=None):
        self.document = document
        self.enabled = settings["save_journal"]
        self.pending = pending
        self.data = {"run_id": run_id, "region_id": region_id, "settings": settings}
        self.write_error = None

    def save(self, data, final=False):
        if self.enabled:
            self.data = data
            try:
                snapshot = dict(data, model_committed=final)
                if final and self.pending is not None:
                    self.pending.append((data["region_id"], _stage_log(self.document, snapshot)))
                else:
                    write_log(self.document, snapshot)
                self.write_error = None
            except Exception as error:
                self.write_error = u"{0}".format(error)

    def record_failure(self, error):
        self.data["error"] = u"{0}".format(error)
        self.save(self.data)
