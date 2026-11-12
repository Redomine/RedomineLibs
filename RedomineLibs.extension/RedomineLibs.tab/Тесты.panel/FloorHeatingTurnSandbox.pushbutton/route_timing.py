# -*- coding: utf-8 -*-
"""Aggregated wall-clock timings for the synchronous route calculation.

Inclusive time contains nested measured calls; self time excludes them.
Self time still includes uninstrumented work and instrumentation overhead.
No per-call events or geometry are retained.
"""
import time
from functools import wraps

try:
    from System.Diagnostics import Stopwatch
    def _clock():
        return float(Stopwatch.GetTimestamp()) / Stopwatch.Frequency
    CLOCK_NAME = "System.Diagnostics.Stopwatch"
except ImportError:
    _clock = getattr(time, "perf_counter", None)
    if _clock is None:
        # CPython 2.7 on Windows: high-resolution elapsed wall time.
        _clock = time.clock
    CLOCK_NAME = "perf_counter" if hasattr(time, "perf_counter") else "time.clock"

_active = None


class RouteProfile(object):
    def __init__(self, clock=None):
        self.clock = clock or _clock
        self.nodes = {}
        self.stack = []
        self.previous = None

    def enter(self, name):
        started = self.clock()
        path = (self.stack[-1][0] if self.stack else ()) + (name,)
        if path not in self.nodes:
            self.nodes[path] = {"calls": 0, "inclusive_seconds": 0.0,
                                "self_seconds": 0.0, "max_seconds": 0.0}
        self.stack.append([path, started, 0.0])

    def leave(self):
        ended = self.clock()
        path, started, children = self.stack.pop()
        elapsed = ended - started
        node = self.nodes[path]
        node["calls"] += 1
        node["inclusive_seconds"] += elapsed
        node["self_seconds"] += elapsed - children
        node["max_seconds"] = max(node["max_seconds"], elapsed)
        if self.stack:
            self.stack[-1][2] += elapsed

    def __enter__(self):
        global _active
        self.previous = _active
        self.enter("route")
        _active = self
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        global _active
        try:
            self.leave()
        finally:
            _active = self.previous

    def report(self):
        if self.stack:
            raise ValueError("Timing report requires a completed calculation")
        rows = []
        for path, node in self.nodes.items():
            row = dict(node)
            row["path"] = list(path)
            row["mean_seconds"] = node["inclusive_seconds"] / node["calls"]
            rows.append(row)
        rows.sort(key=lambda row: (-row["self_seconds"], row["path"]))
        functions = {}
        for row in rows:
            name = row["path"][-1]
            if name not in functions:
                functions[name] = {"name": name, "calls": 0,
                                   "inclusive_seconds": 0.0,
                                   "self_seconds": 0.0, "max_seconds": 0.0}
            item = functions[name]
            for key in ("calls", "inclusive_seconds", "self_seconds"):
                item[key] += row[key]
            item["max_seconds"] = max(item["max_seconds"], row["max_seconds"])
        summary = sorted(functions.values(), key=lambda item: (-item["self_seconds"], item["name"]))
        for item in summary:
            item["mean_seconds"] = item["inclusive_seconds"] / item["calls"]
        return {"schema_version": 1, "clock": CLOCK_NAME,
                "total_seconds": self.nodes[("route",)]["inclusive_seconds"],
                "accounted_self_seconds": sum(row["self_seconds"] for row in rows),
                "semantics": "inclusive includes children; sum self_seconds only; "
                             "self includes uninstrumented work and timing overhead",
                "functions_by_self_seconds": summary,
                "by_self_seconds": rows}


class timing_scope(object):
    def __init__(self, name):
        self.name = name
        self.profile = None

    def __enter__(self):
        self.profile = _active
        if self.profile is not None:
            self.profile.enter(self.name)

    def __exit__(self, exc_type, exc_value, traceback):
        if self.profile is not None:
            self.profile.leave()


def timed(name):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            profile = _active
            if profile is None:
                return function(*args, **kwargs)
            profile.enter(name)
            try:
                return function(*args, **kwargs)
            finally:
                profile.leave()
        return measured
    return decorate
