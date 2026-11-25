# -*- coding: utf-8 -*-
"""Exact build-scoped caches of small geometry queries, never route choices."""
from functools import wraps
_active = None
_MISSING = object()


class FrozenContour(tuple):
    """Immutable coordinate value with a precomputed hash, never object identity."""
    def __new__(cls, points):
        result = tuple.__new__(cls, (tuple(point) for point in points))
        result._coordinate_hash = tuple.__hash__(result)
        return result

    def __hash__(self):
        return self._coordinate_hash


def contour_key(contour):
    return contour if isinstance(contour, FrozenContour) else tuple(map(tuple, contour))


class RouteGeometryCache(object):
    # Memory budget only. Eviction recomputes the same geometry without pruning.
    def __init__(self, capacity=16384):
        self.capacity = capacity
        self.boundaries = {}
        self.projections = {}
        self.joins = {}
        self.hits = 0
        self.misses = 0
        self.clears = 0
        self.previous = None

    def __enter__(self):
        global _active
        self.previous, _active = _active, self
        return self

    def __exit__(self, kind, value, traceback):
        global _active
        _active = self.previous
        self.boundaries.clear()
        self.projections.clear()
        self.joins.clear()

    def stats(self):
        return {"hits": self.hits, "misses": self.misses,
                "capacity_per_query": self.capacity, "clears": self.clears}

    def store(self, values, key, result):
        if len(values) >= self.capacity:
            values.clear()
            self.clears += 1
        values[key] = result


def memoized_boundary(function):
    @wraps(function)
    def cached(origin, direction, contour):
        cache = _active
        if cache is None:
            return function(origin, direction, contour)
        key = (tuple(origin), tuple(direction), contour_key(contour))
        result = cache.boundaries.get(key, _MISSING)
        if result is not _MISSING:
            cache.hits += 1
            return result
        cache.misses += 1
        result = function(origin, direction, contour)
        cache.store(cache.boundaries, key, result)
        return result
    return cached


def memoized_projection(function):
    @wraps(function)
    def cached(point, contour, clockwise, previous=None, corner_reach=0.0, force_next=False):
        cache = _active
        if cache is None:
            return function(point, contour, clockwise, previous, corner_reach, force_next)
        prior = ((previous["edge"], previous["phase"], previous["along"])
                 if previous is not None and "edge" in previous else None)
        key = (tuple(point), contour_key(contour), clockwise, prior, corner_reach, force_next)
        result = cache.projections.get(key, _MISSING)
        if result is not _MISSING:
            cache.hits += 1
            return dict(result) if result is not None else None
        cache.misses += 1
        result = function(point, contour, clockwise, previous, corner_reach, force_next)
        cache.store(cache.projections, key, dict(result) if result is not None else None)
        return result
    return cached



def memoized_join(function):
    @wraps(function)
    def cached(right, left, bridge, contour, spacing, min_segment):
        cache = _active
        if cache is None:
            return function(right, left, bridge, contour, spacing, min_segment)
        key = (tuple(map(tuple, right)), tuple(map(tuple, left)),
               tuple(map(tuple, bridge)) if bridge is not None else None,
               contour_key(contour), spacing, min_segment)
        result = cache.joins.get(key)
        if result is not None:
            cache.hits += 1
            return result
        cache.misses += 1
        result = function(right, left, bridge, contour, spacing, min_segment)
        cache.store(cache.joins, key, result)
        return result
    return cached
