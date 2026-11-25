# -*- coding: utf-8 -*-
"""Area covered by pipe strips inside an orthogonal working contour.

Each straight pipe covers a strip one spacing wide, without end caps.
Overlaps are counted once. This is a geometric coverage metric, not a heat
transfer simulation. A sweep clips the union of strips to the offset contour.
"""
from __future__ import division
from route_timing import timed

# Conservative empirical boundary: 149 successful recent runs, maximum area
# coverage of historically selected alternatives 94.4678%. Round upward to
# the next whole percent; accept at most 5% uncovered working area.
DEFAULT_COVERAGE_THRESHOLD = 0.95


def _union(intervals):
    result = []
    for low, high in sorted(intervals):
        if high <= low:
            continue
        if result and low <= result[-1][1]:
            result[-1] = (result[-1][0], max(high, result[-1][1]))
        else:
            result.append((low, high))
    return result


@timed("area_coverage")
def measure_coverage(route, spacing, tolerance=0.001):
    contour = route.get("contour") or []
    if len(contour) < 4 or spacing <= 0:
        return {"measurable": False, "reason": "missing_contour_or_spacing"}
    edges = list(zip(contour, contour[1:] + contour[:1]))
    if any(abs(b[0]-a[0]) > tolerance and abs(b[1]-a[1]) > tolerance
           for a, b in edges):
        return {"measurable": False, "reason": "nonorthogonal_contour"}
    strips = []
    half = spacing / 2.0
    segments = [segment for branch in route.get("branches", {}).values()
                for segment in zip(branch["vertices"], branch["vertices"][1:])]
    if route.get("join", {}).get("bridge"):
        segments.append(route["join"]["bridge"])
    for a, b in segments:
        if abs(a[1]-b[1]) <= tolerance:
            strips.append((min(a[0], b[0]), max(a[0], b[0]), a[1]-half, a[1]+half))
        elif abs(a[0]-b[0]) <= tolerance:
            strips.append((a[0]-half, a[0]+half, min(a[1], b[1]), max(a[1], b[1])))
        else:
            return {"measurable": False, "reason": "nonorthogonal_pipe"}
    low_x, high_x = min(p[0] for p in contour), max(p[0] for p in contour)
    cuts = sorted(set([p[0] for p in contour] + [x for strip in strips
                       for x in strip[:2] if low_x < x < high_x]))
    area, covered = 0.0, 0.0
    for left, right in zip(cuts, cuts[1:]):
        middle = (left+right) / 2.0
        crossings = sorted(a[1] + (middle-a[0])*(b[1]-a[1])/(b[0]-a[0])
                           for a, b in edges if (a[0] <= middle < b[0]) or
                           (b[0] <= middle < a[0]))
        if len(crossings) % 2:
            return {"measurable": False, "reason": "invalid_contour_slice"}
        spans = list(zip(crossings[::2], crossings[1::2]))
        runs = _union([(s[2], s[3]) for s in strips if s[0] <= middle < s[1]])
        area += (right-left)*sum(b-a for a, b in spans)
        covered += (right-left)*sum(max(0.0, min(b, end)-max(a, start))
                                    for a, b in spans for start, end in runs)
    if area <= tolerance*tolerance:
        return {"measurable": False, "reason": "empty_working_area"}
    covered = max(0.0, min(area, covered))
    return {"measurable": True, "ratio": covered/area,
            "working_area_mm2": area, "covered_area_mm2": covered,
            "uncovered_area_mm2": area-covered,
            "strip_width_mm": spacing, "method": "clipped_strip_union_no_end_caps"}
