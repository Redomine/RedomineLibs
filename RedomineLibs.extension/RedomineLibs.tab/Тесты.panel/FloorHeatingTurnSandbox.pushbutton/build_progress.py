# -*- coding: utf-8 -*-
"""Scoped cooperative cancellation shared by all route builders."""
import time
from route_timing import timing_scope

_callback = None
_status = None


class BuildCancelled(Exception):
    pass


def checkpoint():
    if _callback is not None:
        _callback()


def report_stage(text, fraction=None):
    if _status is not None:
        _status(text, fraction)


class _NativeProgress(object):
    """Adapt pyRevit's bundled window to the platform progress API."""
    def __init__(self):
        from pyrevit import forms
        self.window = forms.ProgressBar(title=u"Тёплый пол [{value}%]",
                                        cancellable=True, step=1)
        self.window.cancel_b.Content = u"Отмена"

    def Show(self):
        self.window.__enter__()

    def Dispose(self):
        self.window.__exit__(None, None, None)

    def Report(self, value):
        # Also pumps WPF input, allowing Cancel during a synchronous build.
        self.window.update_progress(value, 100)

    @property
    def IsCancellationRequested(self):
        return self.window.cancelled

    @property
    def DisplayTitleFormat(self):
        return self.window._title

    @DisplayTitleFormat.setter
    def DisplayTitleFormat(self, value):
        # pyRevit 4.8's public setter rejects Python 2 unicode strings.
        self.window._title = value.replace("{0}", "{value}")


class BuildProgress(object):
    def __init__(self, total):
        self.total = total
        self.completed = 0
        self.dialog = None
        self.previous = None
        self.previous_status = None
        self.region_title = u""
        self.last_poll = 0
        self.stage_text = u""
        self.stage_started = 0
        self.stage_fraction = 0.0
        self.last_title_second = -1

    def __enter__(self):
        global _callback, _status
        try:
            import clr
            import glob
            import os
            import devexpress_libs
            devexpress_libs.load_assemblies()
            # IronPython needs the theme loaded before the platform window.
            for path in glob.glob(os.path.join(os.path.dirname(devexpress_libs.__file__),
                    "libs", "DevExpress.Xpf.Themes.Win10Light.*.dll")):
                clr.AddReferenceToFileAndPath(path)
            clr.AddReference("dosymep.Bim4Everyone.dll")
            clr.AddReference("dosymep.SimpleServices.dll")
            from dosymep.Bim4Everyone.SimpleServices import ServicesProvider
            from dosymep.SimpleServices import IProgressDialogService
            self.dialog = ServicesProvider.GetPlatformService[IProgressDialogService]()
            self.dialog.StepValue = 1
            self.dialog.MaxValue = 100
            self.progress = self.dialog.CreateProgress()
            self.token = self.dialog.CreateCancellationToken()
            self.dialog.DisplayTitleFormat = u"Тёплый пол: построение [{0}%]"
            self.dialog.Show()
        except Exception:
            if self.dialog is not None:
                try:
                    self.dialog.Dispose()
                except Exception:
                    pass
            # A missing platform theme must not prevent route construction.
            self.dialog = _NativeProgress()
            self.progress = self.dialog
            self.token = self.dialog
            self.dialog.Show()
        try:
            self.previous_status = _status
            _status = self.stage
            self.previous = _callback
            _callback = self.poll
            return self
        except Exception:
            self.dialog.Dispose()
            raise

    def __exit__(self, kind, value, traceback):
        global _callback, _status
        _callback = self.previous
        _status = self.previous_status
        self.dialog.Dispose()

    def poll(self, force=False):
        now = time.time()
        if not force and now-self.last_poll < 0.1:
            return
        with timing_scope("progress_ui"):
            self.last_poll = now
            elapsed = max(0, int(now - self.stage_started))
            if elapsed != self.last_title_second:
                self.last_title_second = elapsed
                self.dialog.DisplayTitleFormat = (
                    self.region_title + u": " + self.stage_text +
                    u" | {0} мин {1:02d} с [{{0}}%]".format(elapsed // 60, elapsed % 60))
            value = int(100.0 * (self.completed + self.stage_fraction) / self.total)
            self.progress.Report(min(99, value))
            if self.token.IsCancellationRequested:
                raise BuildCancelled()

    def region(self, text):
        self.region_title = text
        self.stage_fraction = 0.0
        self.stage(u"Расчёт маршрута", 0.0)

    def stage(self, text, fraction=None):
        if fraction is None and text == u"Отрисовка":
            fraction = 0.95
        if fraction is not None:
            self.stage_fraction = max(self.stage_fraction, min(0.99, max(0.0, fraction)))
        self.stage_text = text
        self.stage_started = time.time()
        self.last_title_second = -1
        self.poll(True)

    def finish_region(self):
        self.completed += 1
        self.stage_fraction = 0.0
        self.progress.Report(int(100.0*self.completed/self.total))
