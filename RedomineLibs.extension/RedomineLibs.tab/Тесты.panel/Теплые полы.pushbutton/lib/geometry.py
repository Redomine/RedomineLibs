# -*- coding: utf-8 -*-
import math

try:
    import clr
    clr.AddReference("RevitAPI")
    from Autodesk.Revit.DB import XYZ, Plane
except Exception:
    XYZ = None
    Plane = None


class GeometryException(Exception):
    pass


class UnitConverter(object):
    """Conversions between user millimeters and Revit internal feet."""

    MM_PER_FOOT = 304.8

    @staticmethod
    def mm_to_feet(value_mm):
        return float(value_mm) / UnitConverter.MM_PER_FOOT

    @staticmethod
    def feet_to_mm(value_feet):
        return float(value_feet) * UnitConverter.MM_PER_FOOT

    @staticmethod
    def meters_to_feet(value_m):
        return float(value_m) / 0.3048

    @staticmethod
    def feet_to_meters(value_feet):
        return float(value_feet) * 0.3048


class Point2D(object):
    """Small 2D point/vector type used for all polygon calculations."""

    __slots__ = ("x", "y")

    def __init__(self, x=0.0, y=0.0):
        self.x = float(x)
        self.y = float(y)

    def __repr__(self):
        return "Point2D({0:.6f}, {1:.6f})".format(self.x, self.y)

    def clone(self):
        return Point2D(self.x, self.y)

    def __add__(self, other):
        return Point2D(self.x + other.x, self.y + other.y)

    def __sub__(self, other):
        return Point2D(self.x - other.x, self.y - other.y)

    def __mul__(self, value):
        return Point2D(self.x * float(value), self.y * float(value))

    def __rmul__(self, value):
        return self.__mul__(value)

    def __div__(self, value):
        return Point2D(self.x / float(value), self.y / float(value))

    def dot(self, other):
        return self.x * other.x + self.y * other.y

    def cross(self, other):
        return self.x * other.y - self.y * other.x

    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y)

    def distance_to(self, other):
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def normalized(self):
        length = self.length()
        if length <= 0.0:
            return Point2D(0.0, 0.0)
        return Point2D(self.x / length, self.y / length)

    def almost_equal(self, other, tolerance):
        return self.distance_to(other) <= tolerance


def almost_equal(a, b, tolerance):
    return abs(float(a) - float(b)) <= tolerance


def remove_closing_duplicate(points, tolerance):
    result = list(points)
    while len(result) > 1 and result[0].almost_equal(result[-1], tolerance):
        result.pop()
    return result


def remove_duplicate_points(points, tolerance, closed):
    """Removes consecutive duplicates while preserving polygon order."""

    result = []
    for point in points:
        if not result or not point.almost_equal(result[-1], tolerance):
            result.append(point)

    if closed:
        result = remove_closing_duplicate(result, tolerance)
    return result


def signed_area(points):
    pts = remove_closing_duplicate(points, 0.0)
    if len(pts) < 3:
        return 0.0
    area = 0.0
    count = len(pts)
    for index in range(count):
        p1 = pts[index]
        p2 = pts[(index + 1) % count]
        area += p1.x * p2.y - p2.x * p1.y
    return area / 2.0


def polygon_area(points):
    return abs(signed_area(points))


def ensure_ccw(points):
    if signed_area(points) < 0.0:
        result = list(points)
        result.reverse()
        return result
    return list(points)


def ensure_cw(points):
    if signed_area(points) > 0.0:
        result = list(points)
        result.reverse()
        return result
    return list(points)


def is_collinear(prev_point, point, next_point, tolerance):
    v1 = point - prev_point
    v2 = next_point - point
    scale = max(1.0, v1.length(), v2.length())
    return abs(v1.cross(v2)) <= tolerance * scale


def merge_collinear_segments(points, tolerance, closed):
    """Collapses consecutive collinear edges after the chain is ordered."""

    pts = remove_duplicate_points(points, tolerance, closed)
    if closed:
        changed = True
        while changed and len(pts) > 3:
            changed = False
            count = len(pts)
            for index in range(count):
                prev_point = pts[(index - 1) % count]
                point = pts[index]
                next_point = pts[(index + 1) % count]
                if point.almost_equal(prev_point, tolerance) or point.almost_equal(next_point, tolerance):
                    del pts[index]
                    changed = True
                    break
                if is_collinear(prev_point, point, next_point, tolerance):
                    del pts[index]
                    changed = True
                    break
        return pts

    if len(pts) <= 2:
        return pts

    result = [pts[0]]
    for index in range(1, len(pts) - 1):
        prev_point = result[-1]
        point = pts[index]
        next_point = pts[index + 1]
        if not is_collinear(prev_point, point, next_point, tolerance):
            result.append(point)
    result.append(pts[-1])
    return result


def distance_point_to_segment(point, start, end):
    segment = end - start
    length2 = segment.dot(segment)
    if length2 <= 0.0:
        return point.distance_to(start)
    parameter = (point - start).dot(segment) / length2
    if parameter < 0.0:
        parameter = 0.0
    elif parameter > 1.0:
        parameter = 1.0
    projection = start + segment * parameter
    return point.distance_to(projection)


def project_point_to_segment(point, start, end):
    segment = end - start
    length2 = segment.dot(segment)
    if length2 <= 0.0:
        return start.clone(), 0.0, point.distance_to(start)
    parameter = (point - start).dot(segment) / length2
    if parameter < 0.0:
        parameter = 0.0
    elif parameter > 1.0:
        parameter = 1.0
    projection = start + segment * parameter
    return projection, parameter, point.distance_to(projection)


def project_point_to_polyline(point, points, closed):
    """Projects a point onto the nearest segment of an open or closed polyline."""

    if len(points) < 2:
        raise GeometryException("Недостаточно точек для проекции на полилинию.")

    segment_count = len(points) if closed else len(points) - 1
    best = None
    for index in range(segment_count):
        start = points[index]
        end = points[(index + 1) % len(points)]
        projection, parameter, distance = project_point_to_segment(point, start, end)
        if best is None or distance < best["distance"]:
            best = {
                "point": projection,
                "segment_index": index,
                "parameter": parameter,
                "distance": distance
            }
    return best


def split_polyline_at_point(points, point, closed, tolerance):
    """Inserts a projected point into a polyline and returns the new point list."""

    pts = remove_closing_duplicate(points, tolerance) if closed else list(points)
    projection = project_point_to_polyline(point, pts, closed)
    projected_point = projection["point"]
    segment_index = projection["segment_index"]

    result = []
    for index, existing_point in enumerate(pts):
        result.append(existing_point)
        if index == segment_index:
            next_point = pts[(index + 1) % len(pts)]
            if (not projected_point.almost_equal(existing_point, tolerance) and
                    not projected_point.almost_equal(next_point, tolerance)):
                result.append(projected_point)
    return result


def _find_point_index(points, point, tolerance):
    for index, existing_point in enumerate(points):
        if existing_point.almost_equal(point, tolerance):
            return index
    return -1


def get_sub_polyline(points, start_point, end_point, forward, tolerance):
    """Returns a chain between two points on a closed polyline."""

    pts = split_polyline_at_point(points, start_point, True, tolerance)
    pts = split_polyline_at_point(pts, end_point, True, tolerance)

    start_index = _find_point_index(pts, start_point, tolerance)
    end_index = _find_point_index(pts, end_point, tolerance)
    if start_index < 0 or end_index < 0:
        raise GeometryException("Не удалось разбить контур в точках портала.")

    result = [pts[start_index]]
    index = start_index
    guard = 0
    while index != end_index:
        if forward:
            index = (index + 1) % len(pts)
        else:
            index = (index - 1) % len(pts)
        result.append(pts[index])
        guard += 1
        if guard > len(pts) + 2:
            raise GeometryException("Ошибка обхода полилинии между точками.")
    return result


def reverse_polyline(points):
    result = list(points)
    result.reverse()
    return result


def connect_polylines(polylines, tolerance):
    result = []
    for polyline in polylines:
        if not polyline:
            continue
        for point in polyline:
            if result and point.almost_equal(result[-1], tolerance):
                continue
            result.append(point)
    return result


def _orientation(a, b, c, tolerance):
    value = (b - a).cross(c - a)
    scale = max(1.0, a.distance_to(b), b.distance_to(c), a.distance_to(c))
    if abs(value) <= tolerance * scale:
        return 0
    if value > 0.0:
        return 1
    return -1


def _on_segment(a, b, p, tolerance):
    if _orientation(a, b, p, tolerance) != 0:
        return False
    return (min(a.x, b.x) - tolerance <= p.x <= max(a.x, b.x) + tolerance and
            min(a.y, b.y) - tolerance <= p.y <= max(a.y, b.y) + tolerance)


def segments_intersect(a, b, c, d, tolerance, include_touch):
    o1 = _orientation(a, b, c, tolerance)
    o2 = _orientation(a, b, d, tolerance)
    o3 = _orientation(c, d, a, tolerance)
    o4 = _orientation(c, d, b, tolerance)

    if o1 != o2 and o3 != o4:
        return True

    if include_touch:
        if o1 == 0 and _on_segment(a, b, c, tolerance):
            return True
        if o2 == 0 and _on_segment(a, b, d, tolerance):
            return True
        if o3 == 0 and _on_segment(c, d, a, tolerance):
            return True
        if o4 == 0 and _on_segment(c, d, b, tolerance):
            return True
    return False


def _xyz_subtract(a, b):
    return XYZ(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def _xyz_length(vector):
    return math.sqrt(vector.X * vector.X + vector.Y * vector.Y + vector.Z * vector.Z)


def _xyz_normalized(vector):
    length = _xyz_length(vector)
    if length <= 0.0:
        raise GeometryException("Нулевой XYZ-вектор.")
    return XYZ(vector.X / length, vector.Y / length, vector.Z / length)


def _xyz_scaled(vector, value):
    return XYZ(vector.X * value, vector.Y * value, vector.Z * value)


class PlaneCoordinateSystem(object):
    """Transforms Revit XYZ points to a stable local 2D coordinate system."""

    def __init__(self, origin, x_axis, y_axis, normal, tolerance):
        self.origin = origin
        self.x_axis = x_axis
        self.y_axis = y_axis
        self.normal = normal
        self.tolerance = tolerance

    @staticmethod
    def from_curves(curves, tolerance):
        if XYZ is None:
            raise GeometryException("Revit API недоступен: невозможно создать локальную систему координат.")

        points = []
        vectors = []
        for curve in curves:
            start = curve.GetEndPoint(0)
            end = curve.GetEndPoint(1)
            points.append(start)
            points.append(end)
            vector = _xyz_subtract(end, start)
            if _xyz_length(vector) > tolerance:
                vectors.append(vector)

        if len(points) < 3 or len(vectors) < 2:
            raise GeometryException("Контур должен содержать минимум две непараллельные линии.")

        origin = points[0]
        x_axis = _xyz_normalized(vectors[0])
        normal = None
        for vector in vectors[1:]:
            cross = x_axis.CrossProduct(vector)
            if _xyz_length(cross) > tolerance:
                normal = _xyz_normalized(cross)
                break

        if normal is None:
            raise GeometryException("Все выбранные линии коллинеарны; плоскость контура не определяется.")

        y_axis = _xyz_normalized(normal.CrossProduct(x_axis))
        system = PlaneCoordinateSystem(origin, x_axis, y_axis, normal, tolerance)

        for point in points:
            distance = abs(system.distance_to_plane(point))
            if distance > tolerance:
                raise GeometryException(
                    "Выбранные линии не лежат в одной плоскости. Отклонение: {0:.6f} фт.".format(distance)
                )
        return system

    def distance_to_plane(self, xyz_point):
        vector = _xyz_subtract(xyz_point, self.origin)
        return vector.DotProduct(self.normal)

    def to_2d(self, xyz_point):
        vector = _xyz_subtract(xyz_point, self.origin)
        return Point2D(vector.DotProduct(self.x_axis), vector.DotProduct(self.y_axis))

    def to_xyz(self, point):
        if XYZ is None:
            raise GeometryException("Revit API недоступен: невозможно создать XYZ.")
        x_part = _xyz_scaled(self.x_axis, point.x)
        y_part = _xyz_scaled(self.y_axis, point.y)
        return XYZ(
            self.origin.X + x_part.X + y_part.X,
            self.origin.Y + x_part.Y + y_part.Y,
            self.origin.Z + x_part.Z + y_part.Z
        )

    def create_revit_plane(self):
        if Plane is None:
            raise GeometryException("Revit API недоступен: невозможно создать Plane.")
        return Plane.CreateByNormalAndOrigin(self.normal, self.origin)
