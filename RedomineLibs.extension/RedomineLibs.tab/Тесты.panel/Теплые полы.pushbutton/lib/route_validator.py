# -*- coding: utf-8 -*-
from geometry import distance_point_to_segment, segments_intersect
from polygon import point_in_polygon


class RouteValidationResult(object):
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.total_length = 0.0
        self.intersection_count = 0
        self.too_short_count = 0
        self.min_parallel_distance = None

    def is_valid(self):
        return len(self.errors) == 0


class RouteValidator(object):
    """Validates the first open route before any Revit elements are created."""

    def __init__(self, tolerance, step):
        self.tolerance = float(tolerance)
        self.step = float(step)

    def validate(self, points, boundary_polygon, internal_start_index, internal_end_index):
        result = RouteValidationResult()
        self._validate_lengths(points, result)
        self._validate_inside(points, boundary_polygon, internal_start_index, internal_end_index, result)
        self._validate_intersections(points, internal_start_index, internal_end_index, result)
        self._validate_parallel_distances(points, internal_start_index, internal_end_index, result)
        return result

    def _validate_lengths(self, points, result):
        if len(points) < 2:
            result.errors.append("Маршрут содержит меньше двух точек.")
            return

        for index in range(len(points) - 1):
            length = points[index].distance_to(points[index + 1])
            result.total_length += length
            if length <= self.tolerance:
                result.too_short_count += 1
                result.errors.append("Сегмент {0} имеет нулевую или слишком малую длину.".format(index))

    def _validate_inside(self, points, boundary_polygon, internal_start_index, internal_end_index, result):
        start_index = max(0, int(internal_start_index))
        end_index = min(len(points) - 2, int(internal_end_index))

        for index in range(start_index, end_index + 1):
            start = points[index]
            end = points[index + 1]
            midpoint = start + (end - start) * 0.5
            if point_in_polygon(midpoint, boundary_polygon, self.tolerance) == 0:
                result.errors.append("Внутренний сегмент {0} выходит за границу теплого пола.".format(index))

    def _validate_intersections(self, points, internal_start_index, internal_end_index, result):
        segment_count = len(points) - 1
        for i in range(segment_count):
            a = points[i]
            b = points[i + 1]
            for j in range(i + 1, segment_count):
                if abs(i - j) <= 1:
                    continue
                if (not self._is_internal_segment(i, internal_start_index, internal_end_index) and
                        not self._is_internal_segment(j, internal_start_index, internal_end_index)):
                    continue
                c = points[j]
                d = points[j + 1]

                if self._share_endpoint(a, b, c, d):
                    continue

                if segments_intersect(a, b, c, d, self.tolerance, True):
                    result.intersection_count += 1
                    result.errors.append("Самопересечение маршрута: сегменты {0} и {1}.".format(i, j))

    def _is_internal_segment(self, index, internal_start_index, internal_end_index):
        return int(internal_start_index) <= index <= int(internal_end_index)

    def _validate_parallel_distances(self, points, internal_start_index, internal_end_index, result):
        segment_count = len(points) - 1
        for i in range(segment_count):
            if not self._is_internal_segment(i, internal_start_index, internal_end_index):
                continue
            a = points[i]
            b = points[i + 1]
            ab = b - a
            ab_len = ab.length()
            if ab_len <= self.tolerance:
                continue

            for j in range(i + 1, segment_count):
                if abs(i - j) <= 1:
                    continue
                if not self._is_internal_segment(j, internal_start_index, internal_end_index):
                    continue
                c = points[j]
                d = points[j + 1]
                if self._share_endpoint(a, b, c, d):
                    continue
                if segments_intersect(a, b, c, d, self.tolerance, True):
                    continue
                cd = d - c
                cd_len = cd.length()
                if cd_len <= self.tolerance:
                    continue

                if abs(ab.cross(cd)) > self.tolerance * max(ab_len, cd_len):
                    continue

                distance = min(
                    distance_point_to_segment(a, c, d),
                    distance_point_to_segment(b, c, d),
                    distance_point_to_segment(c, a, b),
                    distance_point_to_segment(d, a, b)
                )
                if result.min_parallel_distance is None or distance < result.min_parallel_distance:
                    result.min_parallel_distance = distance

        if result.min_parallel_distance is not None:
            if result.min_parallel_distance + self.tolerance < self.step:
                result.warnings.append(
                    "Минимальное расстояние между параллельными участками меньше шага: {0:.3f} фт.".format(
                        result.min_parallel_distance
                    )
                )

    def _share_endpoint(self, a, b, c, d):
        return (
            a.almost_equal(c, self.tolerance) or
            a.almost_equal(d, self.tolerance) or
            b.almost_equal(c, self.tolerance) or
            b.almost_equal(d, self.tolerance)
        )
