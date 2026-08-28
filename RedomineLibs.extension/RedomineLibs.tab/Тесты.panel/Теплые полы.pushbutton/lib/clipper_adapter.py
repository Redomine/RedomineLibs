# -*- coding: utf-8 -*-
import os

from geometry import Point2D, ensure_ccw, merge_collinear_segments, polygon_area, remove_duplicate_points


class ClipperUnavailableException(Exception):
    pass


class ClipperOperationException(Exception):
    pass


class ClipperAdapter(object):
    """Thin wrapper around Clipper2Lib.dll.

    Revit geometry is stored in feet. Clipper2 works with integer coordinates,
    so local feet are multiplied by scale before every operation.
    """

    def __init__(self, dll_path, scale):
        self.dll_path = dll_path
        self.scale = float(scale)
        self._load_library()

    def _load_library(self):
        if not os.path.exists(self.dll_path):
            raise ClipperUnavailableException(
                "Не найдена Clipper2 DLL.\nПоложите файл Clipper2Lib.dll сюда:\n{0}".format(self.dll_path)
            )

        try:
            import clr
            try:
                clr.AddReference("netstandard")
            except Exception:
                pass
            clr.AddReferenceToFileAndPath(self.dll_path)
            import Clipper2Lib
        except Exception as exc:
            raise ClipperUnavailableException(
                "Не удалось загрузить Clipper2Lib.dll:\n{0}\n\nПуть DLL:\n{1}".format(exc, self.dll_path)
            )

        required_names = ["Point64", "Path64", "Paths64", "Clipper", "JoinType", "EndType", "FillRule"]
        for name in required_names:
            if not hasattr(Clipper2Lib, name):
                raise ClipperUnavailableException(
                    "В DLL не найден тип Clipper2Lib.{0}. Проверьте, что используется Clipper2 для .NET.".format(name)
                )

        self.lib = Clipper2Lib
        self.Point64 = Clipper2Lib.Point64
        self.Path64 = Clipper2Lib.Path64
        self.Paths64 = Clipper2Lib.Paths64
        self.Clipper = Clipper2Lib.Clipper
        self.JoinType = Clipper2Lib.JoinType
        self.EndType = Clipper2Lib.EndType
        self.FillRule = Clipper2Lib.FillRule
        self.ClipType = getattr(Clipper2Lib, "ClipType", None)
        self.ClipperOffset = getattr(Clipper2Lib, "ClipperOffset", None)

    def _to_int(self, value):
        return int(round(float(value) * self.scale))

    def _from_int(self, value):
        return float(value) / self.scale

    def _make_point64(self, point):
        return self.Point64(self._to_int(point.x), self._to_int(point.y))

    def _point_x(self, point64):
        if hasattr(point64, "X"):
            return point64.X
        return point64.x

    def _point_y(self, point64):
        if hasattr(point64, "Y"):
            return point64.Y
        return point64.y

    def _to_path64(self, polygon):
        path = self.Path64()
        cleaned = remove_duplicate_points(polygon, 1.0 / self.scale, True)
        for point in cleaned:
            path.Add(self._make_point64(point))
        return path

    def _to_paths64(self, polygons):
        paths = self.Paths64()
        for polygon in polygons:
            if len(polygon) >= 3:
                paths.Add(self._to_path64(polygon))
        return paths

    def _from_path64(self, path):
        result = []
        for point64 in path:
            result.append(Point2D(self._from_int(self._point_x(point64)), self._from_int(self._point_y(point64))))
        result = remove_duplicate_points(result, 1.0 / self.scale, True)
        result = merge_collinear_segments(result, 1.0 / self.scale, True)
        if len(result) >= 3:
            result = ensure_ccw(result)
        return result

    def _from_paths64(self, paths):
        result = []
        for path in paths:
            polygon = self._from_path64(path)
            if len(polygon) >= 3 and polygon_area(polygon) > 0.0:
                result.append(polygon)
        return result

    def offset(self, polygons, delta_feet):
        """Offsets closed polygons. Negative delta moves the contour inward."""

        paths = self._to_paths64(polygons)
        delta_scaled = float(delta_feet) * self.scale

        try:
            result = self.Clipper.InflatePaths(
                paths,
                delta_scaled,
                self.JoinType.Miter,
                self.EndType.Polygon,
                2.0
            )
            return self._from_paths64(result)
        except Exception:
            if self.ClipperOffset is None:
                raise

        try:
            offsetter = self.ClipperOffset()
            offsetter.AddPaths(paths, self.JoinType.Miter, self.EndType.Polygon)
            solution = self.Paths64()
            offsetter.Execute(delta_scaled, solution)
            return self._from_paths64(solution)
        except Exception as exc:
            raise ClipperOperationException("Ошибка Offset в Clipper2: {0}".format(exc))

    def union(self, polygons):
        try:
            result = self.Clipper.Union(self._to_paths64(polygons), self.FillRule.NonZero)
            return self._from_paths64(result)
        except Exception as exc:
            raise ClipperOperationException("Ошибка Union в Clipper2: {0}".format(exc))

    def difference(self, subject_polygons, clip_polygons):
        try:
            result = self.Clipper.Difference(
                self._to_paths64(subject_polygons),
                self._to_paths64(clip_polygons),
                self.FillRule.NonZero
            )
            return self._from_paths64(result)
        except Exception as exc:
            raise ClipperOperationException("Ошибка Difference в Clipper2: {0}".format(exc))

    def intersect(self, subject_polygons, clip_polygons):
        try:
            result = self.Clipper.Intersect(
                self._to_paths64(subject_polygons),
                self._to_paths64(clip_polygons),
                self.FillRule.NonZero
            )
            return self._from_paths64(result)
        except Exception as exc:
            raise ClipperOperationException("Ошибка Intersect в Clipper2: {0}".format(exc))

    def point_in_polygon(self, point, polygon):
        try:
            result = self.Clipper.PointInPolygon(self._make_point64(point), self._to_path64(polygon))
        except Exception as exc:
            raise ClipperOperationException("Ошибка PointInPolygon в Clipper2: {0}".format(exc))

        text = str(result).lower()
        if "inside" in text:
            return 1
        if "on" in text or "boundary" in text:
            return 2
        return 0
