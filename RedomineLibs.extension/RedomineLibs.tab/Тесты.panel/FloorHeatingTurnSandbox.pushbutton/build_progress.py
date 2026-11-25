# -*- coding: utf-8 -*-
"""Scoped, throttled console progress for synchronous pyRevit calculations."""
from __future__ import print_function
import time
from route_timing import timing_scope

_callback = None
_status = None
# Console writes are expensive in pyRevit too: print stages and a sparse heartbeat.
CONSOLE_INTERVAL_SECONDS = 5.0


class BuildCancelled(Exception):
    pass


def checkpoint():
    if _callback is not None:
        _callback()


def report_stage(text, fraction=None):
    if _status is not None:
        _status(text, fraction)


class BuildProgress(object):
    def __init__(self, total):
        self.total = total
        self.completed = 0
        self.previous = None
        self.previous_status = None
        self.region_title = u""
        self.stage_text = u""
        self.stage_started = 0.0
        self.stage_fraction = 0.0
        self.last_poll = 0.0

    def __enter__(self):
        global _callback, _status
        self.previous, self.previous_status = _callback, _status
        _callback, _status = self.poll, self.stage
        return self

    def __exit__(self, kind, value, traceback):
        global _callback, _status
        _callback, _status = self.previous, self.previous_status

    def poll(self, force=False):
        now = time.time()
        if not force and now - self.last_poll < CONSOLE_INTERVAL_SECONDS:
            return
        with timing_scope("progress_console"):
            elapsed = max(0, int(now - self.stage_started))
            value = min(99, int(100.0 * (self.completed + self.stage_fraction) / self.total))
            print(u"{0}: {1} | {2} мин {3:02d} с [{4}%]".format(
                self.region_title, self.stage_text, elapsed // 60, elapsed % 60, value))
            self.last_poll = time.time()

    def region(self, text):
        self.region_title = text
        self.stage_text = u""
        self.stage_fraction = 0.0
        self.stage(u"Расчёт маршрута", 0.0)

    def stage(self, text, fraction=None):
        previous_fraction = self.stage_fraction
        if fraction is None and text == u"Отрисовка":
            fraction = 0.95
        if fraction is not None:
            self.stage_fraction = max(self.stage_fraction, min(0.99, max(0.0, fraction)))
        if text == self.stage_text and self.stage_fraction == previous_fraction:
            self.poll()
            return
        self.stage_text = text
        self.stage_started = time.time()
        self.poll(True)

    def finish_region(self):
        self.completed += 1
        self.stage_fraction = 0.0
        with timing_scope("progress_console"):
            print(u"{0}: готово [{1}%]".format(
                self.region_title, int(100.0 * self.completed / self.total)))
