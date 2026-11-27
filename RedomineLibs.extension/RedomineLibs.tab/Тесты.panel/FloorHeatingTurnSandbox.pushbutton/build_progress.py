# -*- coding: utf-8 -*-
"""Progress and cancellation on the Revit thread during a batch of regions."""
import time
from route_timing import timing_scope

_callback = None
_status = None
REFRESH_SECONDS = 0.2
# Three solver variants followed by one modelling stage per region.
MODEL_START = 3.0 / 4.0


class BuildCancelled(Exception):
    pass


def checkpoint():
    if _callback is not None:
        _callback()


def report_stage(text, fraction=None):
    if _status is not None:
        _status(text, fraction)


def report_model_step(text, done, total):
    report_stage(text, MODEL_START + (1.0 - MODEL_START) * done / total)


class _RevitInputLock(object):
    def __init__(self, owner_handle):
        from ctypes import c_void_p, windll
        self.user32 = windll.user32
        self.owner = c_void_p(owner_handle.ToInt64())
        self.thread_id = self.user32.GetWindowThreadProcessId(self.owner, None)
        self.disabled = []

    def acquire(self, progress_handle):
        from ctypes import WINFUNCTYPE, c_int, c_void_p
        windows = []

        @WINFUNCTYPE(c_int, c_void_p, c_void_p)
        def collect(hwnd, unused):
            windows.append(c_void_p(hwnd))
            return 1

        # The pyRevit bar shares the editor thread and must still receive clicks.
        if not self.thread_id or not self.user32.EnumThreadWindows(self.thread_id, collect, None):
            raise RuntimeError(u"Не удалось определить окна Revit для блокировки ввода.")
        for hwnd in windows:
            if hwnd.value == progress_handle.ToInt64():
                continue
            if (hwnd.value == self.owner.value or self.user32.IsWindowVisible(hwnd)) and self.user32.IsWindowEnabled(hwnd):
                self.disabled.append(hwnd)
                self.user32.EnableWindow(hwnd, False)
                if self.user32.IsWindowEnabled(hwnd):
                    raise RuntimeError(u"Не удалось заблокировать ввод в окно Revit.")

    def release(self):
        for hwnd in reversed(self.disabled):
            if self.user32.GetWindowThreadProcessId(hwnd, None) == self.thread_id:
                self.user32.EnableWindow(hwnd, True)
        self.disabled = []


class BuildProgress(object):
    def __init__(self, total, bar=None, owner_handle=None):
        self.total = total
        self.bar = bar
        self.owner_handle = owner_handle
        self.input_lock = None
        self.completed = 0
        self.region_title = u""
        self.stage_text = u""
        self.stage_fraction = 0.0
        self.next_refresh = 0.0

    def __enter__(self):
        global _callback, _status
        if self.bar is not None and self.owner_handle is not None:
            from System.Windows.Interop import WindowInteropHelper
            self.input_lock = _RevitInputLock(self.owner_handle)
            try:
                self.input_lock.acquire(WindowInteropHelper(self.bar).Handle)
            except BaseException:
                self.input_lock.release()
                raise
        self.previous, self.previous_status = _callback, _status
        _callback, _status = self.poll, self.stage
        return self

    def __exit__(self, kind, value, traceback):
        global _callback, _status
        _callback, _status = self.previous, self.previous_status
        if self.input_lock is not None:
            self.input_lock.release()

    def _report(self):
        value = int(100.0 * (self.completed + self.stage_fraction) / self.total)
        with timing_scope("progress_ui"):
            self.bar.update_progress(value, 100)

    def poll(self, force=False):
        if self.bar is None:
            return
        now = time.time()
        if not force and now < self.next_refresh:
            return
        self.next_refresh = now + REFRESH_SECONDS
        if self.bar.cancelled or not self.bar.IsVisible:
            raise BuildCancelled()
        self._report()  # pyRevit processes the Cancel button while updating the bar.
        if self.bar.cancelled or not self.bar.IsVisible:
            raise BuildCancelled()

    def region(self, text):
        self.region_title = text
        self.stage_text = u""
        self.stage_fraction = 0.0
        self.stage(u"Подготовка области", 0.0)

    def stage(self, text, fraction=None):
        if fraction is not None:
            self.stage_fraction = max(self.stage_fraction, min(0.99, max(0.0, fraction)))
        changed = text != self.stage_text
        self.stage_text = text
        if changed and self.bar is not None:
            title = u"{0} — {1}".format(self.region_title, text)
            self.bar.title = title.replace("{", "{{").replace("}", "}}") + u" [{value}%]"
        self.poll(force=changed)

    def finish_region(self):
        self.completed += 1
        self.stage_fraction = 0.0
        if self.bar is not None:
            self.bar.title = u"Обработано областей {0}/{1} [{{value}}%]".format(self.completed, self.total)
            self._report()
