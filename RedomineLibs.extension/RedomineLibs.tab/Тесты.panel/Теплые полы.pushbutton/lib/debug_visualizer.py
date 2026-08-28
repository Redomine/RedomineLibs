# -*- coding: utf-8 -*-
import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import Line, SketchPlane


class RevitCurveCreatorException(Exception):
    pass


class RevitCurveCreator(object):
    """Creates Revit curves from local 2D polylines.

    Input geometry is in local feet. Revit receives XYZ points in internal feet.
    Transactions are intentionally managed by the caller.
    """

    def __init__(self, doc, view, coordinate_system, tolerance_feet):
        self.doc = doc
        self.view = view
        self.coordinate_system = coordinate_system
        self.tolerance_feet = float(tolerance_feet)
        self._sketch_plane = None

    def _get_sketch_plane(self):
        if self._sketch_plane is None:
            plane = self.coordinate_system.create_revit_plane()
            self._sketch_plane = SketchPlane.Create(self.doc, plane)
        return self._sketch_plane

    def _create_line(self, start_point, end_point):
        if start_point.distance_to(end_point) <= self.tolerance_feet:
            return None
        start_xyz = self.coordinate_system.to_xyz(start_point)
        end_xyz = self.coordinate_system.to_xyz(end_point)
        return Line.CreateBound(start_xyz, end_xyz)

    def create_model_polyline(self, points, closed):
        curves = []
        line_points = list(points)
        if closed and line_points:
            line_points.append(line_points[0])

        sketch_plane = self._get_sketch_plane()
        for index in range(len(line_points) - 1):
            line = self._create_line(line_points[index], line_points[index + 1])
            if line is None:
                continue
            curves.append(self.doc.Create.NewModelCurve(line, sketch_plane))
        return curves

    def create_detail_polyline(self, points, closed):
        curves = []
        line_points = list(points)
        if closed and line_points:
            line_points.append(line_points[0])

        for index in range(len(line_points) - 1):
            line = self._create_line(line_points[index], line_points[index + 1])
            if line is None:
                continue
            curves.append(self.doc.Create.NewDetailCurve(self.view, line))
        return curves


class DebugVisualizer(object):
    def __init__(self, doc, view, coordinate_system, tolerance_feet):
        self.creator = RevitCurveCreator(doc, view, coordinate_system, tolerance_feet)

    def draw_closed_polygons(self, polygons, create_mode):
        created = []
        for polygon in polygons:
            if create_mode == "detail":
                created.extend(self.creator.create_detail_polyline(polygon, True))
            else:
                created.extend(self.creator.create_model_polyline(polygon, True))
        return created

    def draw_open_polylines(self, polylines, create_mode):
        created = []
        for polyline in polylines:
            if create_mode == "detail":
                created.extend(self.creator.create_detail_polyline(polyline, False))
            else:
                created.extend(self.creator.create_model_polyline(polyline, False))
        return created

    def draw_offset_levels(self, levels, create_mode):
        created = []
        for level in levels:
            created.extend(self.draw_closed_polygons(level.contours, create_mode))
        return created
