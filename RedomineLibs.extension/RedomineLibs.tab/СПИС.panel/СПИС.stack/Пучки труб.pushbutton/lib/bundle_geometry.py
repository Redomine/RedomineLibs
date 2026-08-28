# -*- coding: utf-8 -*-
"""Revit-independent geometry used by the pipe bundle command."""

import math
import time


EPSILON = 1.0e-9
MAX_RECORDED_ERRORS = 20
TIME_CHECK_INTERVAL = 100


class BundleAnalysisLimitError(RuntimeError):
    """Raised when a caller-defined analysis safety limit is reached."""


class _AnalysisGuard(object):
    def __init__(
        self,
        diagnostics,
        max_pair_checks,
        timeout_seconds,
        progress_callback,
        progress_interval,
    ):
        self.diagnostics = diagnostics if diagnostics is not None else {}
        self.max_pair_checks = max_pair_checks
        self.timeout_seconds = timeout_seconds
        self.progress_callback = progress_callback
        self.progress_interval = max(1, int(progress_interval))
        self.started_at = time.time()
        self.diagnostics.update({
            "stage": "initializing",
            "elapsed_seconds": 0.0,
            "pair_checks": 0,
            "bounds_overlap_checks": 0,
            "geometry_checks": 0,
            "adjacent_pairs": 0,
            "components": 0,
            "range_events": 0,
            "analysis_errors": 0,
            "errors": [],
        })

    def _notify(self):
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(
                self.diagnostics["stage"],
                self.diagnostics,
            )
        except Exception as error:
            _record_analysis_error(
                self.diagnostics,
                "progress_callback",
                error,
            )

    def checkpoint(self, stage, notify=False):
        self.diagnostics["stage"] = stage
        self.diagnostics["elapsed_seconds"] = max(
            0.0,
            time.time() - self.started_at,
        )
        if (
            self.timeout_seconds is not None
            and self.diagnostics["elapsed_seconds"] > self.timeout_seconds
        ):
            self._notify()
            raise BundleAnalysisLimitError(
                "Bundle analysis exceeded the {:.1f} second safety limit "
                "during {}.".format(self.timeout_seconds, stage)
            )
        if notify:
            self._notify()

    def pair_checked(self):
        self.diagnostics["pair_checks"] += 1
        pair_checks = self.diagnostics["pair_checks"]
        if (
            self.max_pair_checks is not None
            and pair_checks > self.max_pair_checks
        ):
            self.checkpoint("adjacency", notify=True)
            raise BundleAnalysisLimitError(
                "Bundle analysis exceeded the {} pair safety limit.".format(
                    self.max_pair_checks
                )
            )
        if pair_checks % TIME_CHECK_INTERVAL == 0:
            self.checkpoint(
                "adjacency",
                notify=pair_checks % self.progress_interval == 0,
            )

    def range_event_checked(self):
        self.diagnostics["range_events"] += 1
        range_events = self.diagnostics["range_events"]
        if range_events % TIME_CHECK_INTERVAL == 0:
            self.checkpoint(
                "component_ranges",
                notify=range_events % self.progress_interval == 0,
            )


def _record_analysis_error(diagnostics, scope, error, keys=None):
    diagnostics["analysis_errors"] = diagnostics.get("analysis_errors", 0) + 1
    errors = diagnostics.setdefault("errors", [])
    if len(errors) >= MAX_RECORDED_ERRORS:
        return
    message = "{}: {}".format(scope, error)
    if keys:
        message = "{} [{}]".format(
            message,
            ", ".join(str(key) for key in keys),
        )
    errors.append(message)


def _dot_xy(point, direction):
    return point[0] * direction[0] + point[1] * direction[1]


def effective_pipe_radius(outer_diameter, insulation_thickness=0.0):
    outer_diameter = float(outer_diameter)
    insulation_thickness = float(insulation_thickness)
    if outer_diameter <= EPSILON:
        raise ValueError("Pipe outer diameter must be positive.")
    if insulation_thickness < 0.0:
        raise ValueError("Pipe insulation thickness must be non-negative.")
    return outer_diameter * 0.5 + insulation_thickness


def bundle_meets_minimum_dimension(bundle, minimum_dimension):
    minimum_dimension = float(minimum_dimension)
    if minimum_dimension < 0.0:
        raise ValueError("Minimum bundle dimension must be non-negative.")
    return (
        bundle.width >= minimum_dimension - EPSILON
        or bundle.height >= minimum_dimension - EPSILON
    )


def make_pipe_source_key(pipe_id, link_instance_ids=()):
    pipe_id_text = str(pipe_id)
    instance_ids = tuple(str(value) for value in link_instance_ids)
    if not instance_ids:
        return "H:{}".format(pipe_id_text)
    return "L[{}]:{}".format("/".join(instance_ids), pipe_id_text)


def apply_transform_chain(point, transforms):
    transformed = point
    for transform in transforms:
        transformed = transform.OfPoint(transformed)
    return transformed


def _normalize_xy(x_value, y_value):
    length = math.hypot(x_value, y_value)
    if length <= EPSILON:
        raise ValueError("Segment has no horizontal projection.")
    return x_value / length, y_value / length


def _canonical_direction(x_value, y_value):
    direction = _normalize_xy(x_value, y_value)
    if (
        direction[0] < -EPSILON
        or (abs(direction[0]) <= EPSILON and direction[1] < 0.0)
    ):
        return -direction[0], -direction[1]
    return direction


def _perpendicular(direction):
    return -direction[1], direction[0]


def _interpolate(first, second, fraction):
    return (
        first[0] + (second[0] - first[0]) * fraction,
        first[1] + (second[1] - first[1]) * fraction,
        first[2] + (second[2] - first[2]) * fraction,
    )


class PipeSegment(object):
    def __init__(self, key, start, end, radius, payload=None):
        self.key = key
        self.start = tuple(float(value) for value in start)
        self.end = tuple(float(value) for value in end)
        self.radius = float(radius)
        self.payload = payload

        if self.radius <= EPSILON:
            raise ValueError("Pipe radius must be positive.")

        delta_x = self.end[0] - self.start[0]
        delta_y = self.end[1] - self.start[1]
        self.plan_length = math.hypot(delta_x, delta_y)
        if self.plan_length <= EPSILON:
            raise ValueError("Pipe must have a horizontal projection.")

        raw_direction = _normalize_xy(delta_x, delta_y)
        self.direction = _canonical_direction(delta_x, delta_y)
        if raw_direction[0] * self.direction[0] + raw_direction[1] * self.direction[1] < 0.0:
            self.start, self.end = self.end, self.start

    def projection_range(self, direction):
        first = _dot_xy(self.start, direction)
        second = _dot_xy(self.end, direction)
        return min(first, second), max(first, second)

    def point_at_station(self, direction, station):
        first_station = _dot_xy(self.start, direction)
        second_station = _dot_xy(self.end, direction)
        denominator = second_station - first_station
        if abs(denominator) <= EPSILON:
            raise ValueError("Pipe is perpendicular to bundle direction.")
        fraction = (station - first_station) / denominator
        return _interpolate(self.start, self.end, fraction)

    def expanded_bounds(self, max_gap, height_tolerance):
        plan_margin = self.radius + max_gap
        return (
            min(self.start[0], self.end[0]) - plan_margin,
            max(self.start[0], self.end[0]) + plan_margin,
            min(self.start[1], self.end[1]) - plan_margin,
            max(self.start[1], self.end[1]) + plan_margin,
            min(self.start[2], self.end[2]) - height_tolerance,
            max(self.start[2], self.end[2]) + height_tolerance,
        )


class PipeBundle(object):
    def __init__(self, segments, start, end, width, height):
        self.segments = tuple(segments)
        self.pipe_keys = tuple(sorted(segment.key for segment in segments))
        self.start = tuple(start)
        self.end = tuple(end)
        self.width = float(width)
        self.height = float(height)

    @property
    def length(self):
        return math.sqrt(sum(
            (self.end[index] - self.start[index]) ** 2
            for index in range(3)
        ))


def _average_direction(segments):
    x_value = sum(segment.direction[0] for segment in segments)
    y_value = sum(segment.direction[1] for segment in segments)
    return _canonical_direction(x_value, y_value)


def _sample_stations(start_station, end_station):
    return (
        start_station,
        (start_station + end_station) * 0.5,
        end_station,
    )


def _segments_are_adjacent(
    first,
    second,
    max_gap,
    height_tolerance,
    minimum_overlap,
    cosine_tolerance,
):
    direction_dot = (
        first.direction[0] * second.direction[0]
        + first.direction[1] * second.direction[1]
    )
    if direction_dot < cosine_tolerance:
        return False

    direction = _average_direction((first, second))
    first_range = first.projection_range(direction)
    second_range = second.projection_range(direction)
    overlap_start = max(first_range[0], second_range[0])
    overlap_end = min(first_range[1], second_range[1])
    if overlap_end - overlap_start < minimum_overlap - EPSILON:
        return False

    perpendicular = _perpendicular(direction)
    for station in _sample_stations(overlap_start, overlap_end):
        first_point = first.point_at_station(direction, station)
        second_point = second.point_at_station(direction, station)
        cross_distance = abs(
            _dot_xy(first_point, perpendicular)
            - _dot_xy(second_point, perpendicular)
        )
        height_difference = abs(first_point[2] - second_point[2])
        if height_difference > height_tolerance + EPSILON:
            return False

        outside_gap = math.hypot(cross_distance, height_difference) - (
            first.radius + second.radius
        )
        if outside_gap > max_gap + EPSILON:
            return False

    return True


def _bounds_overlap(first, second):
    return not (
        first[1] < second[0]
        or second[1] < first[0]
        or first[3] < second[2]
        or second[3] < first[2]
        or first[5] < second[4]
        or second[5] < first[4]
    )


def _build_adjacency(
    segments,
    max_gap,
    height_tolerance,
    minimum_overlap,
    cosine_tolerance,
    guard,
):
    adjacency = dict((index, set()) for index in range(len(segments)))
    bounds = [
        segment.expanded_bounds(max_gap, height_tolerance)
        for segment in segments
    ]
    ordered = sorted(range(len(segments)), key=lambda index: bounds[index][0])

    for position, first_index in enumerate(ordered):
        first_bounds = bounds[first_index]
        for second_position in range(position + 1, len(ordered)):
            second_index = ordered[second_position]
            second_bounds = bounds[second_index]
            if second_bounds[0] > first_bounds[1]:
                break
            guard.pair_checked()
            if not _bounds_overlap(first_bounds, second_bounds):
                continue
            guard.diagnostics["bounds_overlap_checks"] += 1
            try:
                guard.diagnostics["geometry_checks"] += 1
                is_adjacent = _segments_are_adjacent(
                    segments[first_index],
                    segments[second_index],
                    max_gap,
                    height_tolerance,
                    minimum_overlap,
                    cosine_tolerance,
                )
            except Exception as error:
                _record_analysis_error(
                    guard.diagnostics,
                    "adjacency",
                    error,
                    (
                        segments[first_index].key,
                        segments[second_index].key,
                    ),
                )
                continue
            if not is_adjacent:
                continue
            adjacency[first_index].add(second_index)
            adjacency[second_index].add(first_index)
            guard.diagnostics["adjacent_pairs"] += 1

    return adjacency


def _connected_components(indexes, adjacency):
    remaining = set(indexes)
    components = []
    while remaining:
        seed = remaining.pop()
        component = [seed]
        stack = [seed]
        while stack:
            current = stack.pop()
            for neighbour in adjacency[current]:
                if neighbour not in remaining:
                    continue
                remaining.remove(neighbour)
                component.append(neighbour)
                stack.append(neighbour)
        components.append(component)
    return components


def _split_height_bands(
    indexes,
    segments,
    direction,
    station,
    height_tolerance,
):
    ordered = sorted(
        (
            segments[index].point_at_station(direction, station)[2],
            index,
        )
        for index in indexes
    )
    if (
        len(ordered) < 2
        or ordered[-1][0] - ordered[0][0]
        <= height_tolerance + EPSILON
    ):
        return [[item[1] for item in ordered]]

    middle_position = len(ordered) * 0.5
    split_position = max(
        range(len(ordered) - 1),
        key=lambda position: (
            ordered[position + 1][0] - ordered[position][0],
            -abs((position + 1) - middle_position),
        ),
    ) + 1
    return _split_height_bands(
        [item[1] for item in ordered[:split_position]],
        segments,
        direction,
        station,
        height_tolerance,
    ) + _split_height_bands(
        [item[1] for item in ordered[split_position:]],
        segments,
        direction,
        station,
        height_tolerance,
    )


def _component_ranges(
    component,
    segments,
    adjacency,
    minimum_overlap,
    height_tolerance,
    guard=None,
):
    component_segments = [segments[index] for index in component]
    direction = _average_direction(component_segments)
    ranges = dict(
        (index, segments[index].projection_range(direction))
        for index in component
    )
    events = sorted(set(
        station
        for interval in ranges.values()
        for station in interval
    ))
    candidates = []

    for event_index in range(len(events) - 1):
        if guard is not None:
            guard.range_event_checked()
        start_station = events[event_index]
        end_station = events[event_index + 1]
        if end_station - start_station <= EPSILON:
            continue
        middle = (start_station + end_station) * 0.5
        active = [
            index for index in component
            if ranges[index][0] - EPSILON <= middle <= ranges[index][1] + EPSILON
        ]
        if len(active) < 2:
            continue

        height_bands = _split_height_bands(
            active,
            segments,
            direction,
            middle,
            height_tolerance,
        )
        for height_band in height_bands:
            for connected in _connected_components(height_band, adjacency):
                if len(connected) < 2:
                    continue
                keys = tuple(
                    sorted(segments[index].key for index in connected)
                )
                candidates.append(
                    (keys, start_station, end_station, tuple(connected))
                )

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    merged = []
    for candidate in candidates:
        if (
            merged
            and merged[-1][0] == candidate[0]
            and abs(merged[-1][2] - candidate[1]) <= EPSILON
        ):
            previous = merged[-1]
            merged[-1] = (
                previous[0],
                previous[1],
                candidate[2],
                previous[3],
            )
        else:
            merged.append(candidate)

    return [
        (direction, item[1], item[2], item[3])
        for item in merged
        if item[2] - item[1] >= minimum_overlap - EPSILON
    ]


def _profile_at_station(bundle_segments, direction, station):
    perpendicular = _perpendicular(direction)
    points = [
        segment.point_at_station(direction, station)
        for segment in bundle_segments
    ]
    left = min(
        _dot_xy(point, perpendicular) - segment.radius
        for point, segment in zip(points, bundle_segments)
    )
    right = max(
        _dot_xy(point, perpendicular) + segment.radius
        for point, segment in zip(points, bundle_segments)
    )
    bottom = min(
        point[2] - segment.radius
        for point, segment in zip(points, bundle_segments)
    )
    top = max(
        point[2] + segment.radius
        for point, segment in zip(points, bundle_segments)
    )
    return {
        "center_cross": (left + right) * 0.5,
        "center_height": (bottom + top) * 0.5,
        "width": right - left,
        "height": top - bottom,
    }


def _make_bundle(segments, direction, start_station, end_station):
    perpendicular = _perpendicular(direction)
    stations = _sample_stations(start_station, end_station)
    profiles = [
        _profile_at_station(segments, direction, station)
        for station in stations
    ]
    start_profile = profiles[0]
    end_profile = profiles[-1]
    start = (
        direction[0] * start_station
        + perpendicular[0] * start_profile["center_cross"],
        direction[1] * start_station
        + perpendicular[1] * start_profile["center_cross"],
        start_profile["center_height"],
    )
    end = (
        direction[0] * end_station
        + perpendicular[0] * end_profile["center_cross"],
        direction[1] * end_station
        + perpendicular[1] * end_profile["center_cross"],
        end_profile["center_height"],
    )
    return PipeBundle(
        segments,
        start,
        end,
        max(profile["width"] for profile in profiles),
        max(profile["height"] for profile in profiles),
    )


def find_pipe_bundles(
    segments,
    max_gap,
    height_tolerance,
    angle_tolerance_radians,
    minimum_overlap,
    diagnostics=None,
    max_pair_checks=None,
    timeout_seconds=None,
    progress_callback=None,
    progress_interval=10000,
):
    """Return maximal spans with one unchanged set of adjacent pipes.

    Every start or end of a pipe splits the longitudinal range.  As a result,
    a smaller bundle begins only where the larger bundle stops containing all
    of its pipes; the two bundle definitions never overlap.
    """
    segments = list(segments)
    if max_gap < 0.0 or height_tolerance < 0.0 or minimum_overlap <= 0.0:
        raise ValueError("Bundle tolerances must be non-negative.")
    if not 0.0 <= angle_tolerance_radians < math.pi * 0.5:
        raise ValueError("Angle tolerance must be less than 90 degrees.")
    if max_pair_checks is not None and max_pair_checks <= 0:
        raise ValueError("Pair safety limit must be positive.")
    if timeout_seconds is not None and timeout_seconds <= 0.0:
        raise ValueError("Analysis timeout must be positive.")

    guard = _AnalysisGuard(
        diagnostics,
        max_pair_checks,
        timeout_seconds,
        progress_callback,
        progress_interval,
    )
    guard.diagnostics["segment_count"] = len(segments)
    guard.checkpoint("starting", notify=True)
    if len(segments) < 2:
        guard.checkpoint("complete", notify=True)
        return []

    cosine_tolerance = math.cos(angle_tolerance_radians)
    try:
        adjacency = _build_adjacency(
            segments,
            max_gap,
            height_tolerance,
            minimum_overlap,
            cosine_tolerance,
            guard,
        )
    except BundleAnalysisLimitError:
        raise
    except Exception as error:
        _record_analysis_error(guard.diagnostics, "build_adjacency", error)
        guard.checkpoint("failed", notify=True)
        raise

    guard.checkpoint("adjacency_complete", notify=True)
    connected_indexes = [
        index for index in range(len(segments))
        if adjacency[index]
    ]
    if not connected_indexes:
        guard.checkpoint("complete", notify=True)
        return []

    bundles = []
    components = _connected_components(connected_indexes, adjacency)
    guard.diagnostics["components"] = len(components)
    guard.checkpoint("components", notify=True)
    for component_number, component in enumerate(components, 1):
        guard.diagnostics["current_component"] = component_number
        guard.diagnostics["current_component_size"] = len(component)
        guard.checkpoint("component_ranges")
        if len(component) < 2:
            continue
        try:
            component_ranges = _component_ranges(
                component,
                segments,
                adjacency,
                minimum_overlap,
                height_tolerance,
                guard,
            )
        except BundleAnalysisLimitError:
            raise
        except Exception as error:
            _record_analysis_error(
                guard.diagnostics,
                "component_ranges",
                error,
                tuple(segments[index].key for index in component),
            )
            continue

        for direction, start_station, end_station, indexes in component_ranges:
            bundle_segments = [segments[index] for index in indexes]
            try:
                bundles.append(_make_bundle(
                    bundle_segments,
                    direction,
                    start_station,
                    end_station,
                ))
            except Exception as error:
                _record_analysis_error(
                    guard.diagnostics,
                    "make_bundle",
                    error,
                    tuple(segment.key for segment in bundle_segments),
                )
        guard.diagnostics["bundle_count"] = len(bundles)
        guard.checkpoint("component_complete")

    bundles.sort(key=lambda bundle: (
        bundle.start[2],
        bundle.start[0],
        bundle.start[1],
        bundle.pipe_keys,
    ))
    guard.diagnostics["bundle_count"] = len(bundles)
    guard.checkpoint("complete", notify=True)
    return bundles
