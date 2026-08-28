# -*- coding: utf-8 -*-
from geometry import (
    Point2D,
    GeometryException,
    ensure_ccw,
    merge_collinear_segments,
    polygon_area,
    remove_closing_duplicate,
    remove_duplicate_points,
    segments_intersect,
    distance_point_to_segment
)


class PolygonException(Exception):
    pass


class OffsetLevel(object):
    def __init__(self, level, offset_feet, contours):
        self.level = int(level)
        self.offset_feet = float(offset_feet)
        self.contours = contours

    def component_count(self):
        return len(self.contours)


class ContourNode(object):
    def __init__(self, level, polygon):
        self.level = int(level)
        self.polygon = polygon
        self.area = polygon_area(polygon)
        self.parent = None
        self.children = []
        self.entry_portal = None
        self.exit_portal = None
        self.is_accessible = True
        self.is_branch = False

    def add_child(self, child):
        child.parent = self
        self.children.append(child)


class PolygonNormalizer(object):
    """Builds a valid orthogonal polygon from unordered Revit line segments."""

    def __init__(self, tolerance):
        self.tolerance = float(tolerance)

    def normalize_curves(self, curves, coordinate_system):
        segments = self._curves_to_segments(curves, coordinate_system)
        if len(segments) < 4:
            raise PolygonException("Для границы нужно выбрать минимум 4 отрезка.")

        ordered = self._order_segments(segments)
        ordered = remove_duplicate_points(ordered, self.tolerance, True)
        ordered = merge_collinear_segments(ordered, self.tolerance, True)

        if len(ordered) < 4:
            raise PolygonException("После нормализации осталось меньше 4 вершин.")

        self._check_orthogonal(ordered)
        self._check_self_intersections(ordered)

        area = polygon_area(ordered)
        if area <= self.tolerance * self.tolerance:
            raise PolygonException("Площадь выбранной границы слишком мала.")

        return ensure_ccw(ordered)

    def _curves_to_segments(self, curves, coordinate_system):
        result = []
        for curve in curves:
            curve_type_name = curve.GetType().Name
            if curve_type_name != "Line":
                raise PolygonException(
                    "Этап 1 поддерживает только прямые ModelLine/DetailLine. Найден тип: {0}.".format(curve_type_name)
                )
            start = coordinate_system.to_2d(curve.GetEndPoint(0))
            end = coordinate_system.to_2d(curve.GetEndPoint(1))
            if start.distance_to(end) <= self.tolerance:
                raise PolygonException("В выбранной границе найден отрезок нулевой длины.")
            result.append([start, end, False])
        return result

    def _order_segments(self, segments):
        ordered = [segments[0][0], segments[0][1]]
        segments[0][2] = True
        used_count = 1
        current = segments[0][1]

        while used_count < len(segments):
            found_index = -1
            found_point = None
            for index, segment in enumerate(segments):
                if segment[2]:
                    continue
                start = segment[0]
                end = segment[1]
                if current.almost_equal(start, self.tolerance):
                    found_index = index
                    found_point = end
                    break
                if current.almost_equal(end, self.tolerance):
                    found_index = index
                    found_point = start
                    break

            if found_index < 0:
                raise PolygonException(
                    "Не удалось собрать замкнутую цепочку: разрыв возле точки {0}.".format(current)
                )

            segments[found_index][2] = True
            ordered.append(found_point)
            current = found_point
            used_count += 1

        if not current.almost_equal(ordered[0], self.tolerance):
            raise PolygonException("Выбранные линии не образуют замкнутый контур.")

        return remove_closing_duplicate(ordered, self.tolerance)

    def _check_orthogonal(self, points):
        for index in range(len(points)):
            start = points[index]
            end = points[(index + 1) % len(points)]
            dx = abs(end.x - start.x)
            dy = abs(end.y - start.y)
            if dx > self.tolerance and dy > self.tolerance:
                raise PolygonException(
                    "Контур должен быть ортогональным. Наклонный сегмент: {0} -> {1}.".format(start, end)
                )

    def _check_self_intersections(self, points):
        count = len(points)
        for i in range(count):
            a = points[i]
            b = points[(i + 1) % count]
            for j in range(i + 1, count):
                if abs(i - j) <= 1:
                    continue
                if i == 0 and j == count - 1:
                    continue
                c = points[j]
                d = points[(j + 1) % count]
                if segments_intersect(a, b, c, d, self.tolerance, True):
                    raise PolygonException(
                        "Контур самопересекается на сегментах {0}-{1} и {2}-{3}.".format(i, i + 1, j, j + 1)
                    )


def point_in_polygon(point, polygon, tolerance):
    """Ray casting test. Returns 2 on boundary, 1 inside, 0 outside."""

    points = remove_closing_duplicate(polygon, tolerance)
    count = len(points)
    if count < 3:
        return 0

    for index in range(count):
        start = points[index]
        end = points[(index + 1) % count]
        if distance_point_to_segment(point, start, end) <= tolerance:
            return 2

    inside = False
    j = count - 1
    for i in range(count):
        pi = points[i]
        pj = points[j]
        crosses = ((pi.y > point.y) != (pj.y > point.y))
        if crosses:
            x_at_y = (pj.x - pi.x) * (point.y - pi.y) / (pj.y - pi.y) + pi.x
            if point.x < x_at_y:
                inside = not inside
        j = i

    if inside:
        return 1
    return 0


def segment_intersects_polygon(start, end, polygon, tolerance):
    if point_in_polygon(start, polygon, tolerance) > 0:
        return True
    if point_in_polygon(end, polygon, tolerance) > 0:
        return True

    count = len(polygon)
    for index in range(count):
        border_start = polygon[index]
        border_end = polygon[(index + 1) % count]
        if segments_intersect(start, end, border_start, border_end, tolerance, True):
            return True
    return False


def min_distance_to_polygon_boundary(point, polygon):
    best = None
    for index in range(len(polygon)):
        start = polygon[index]
        end = polygon[(index + 1) % len(polygon)]
        distance = distance_point_to_segment(point, start, end)
        if best is None or distance < best:
            best = distance
    if best is None:
        return 0.0
    return best


def polygon_bounds(points):
    min_x = None
    min_y = None
    max_x = None
    max_y = None
    for point in points:
        if min_x is None or point.x < min_x:
            min_x = point.x
        if max_x is None or point.x > max_x:
            max_x = point.x
        if min_y is None or point.y < min_y:
            min_y = point.y
        if max_y is None or point.y > max_y:
            max_y = point.y
    return min_x, min_y, max_x, max_y
