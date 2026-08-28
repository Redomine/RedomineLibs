# -*- coding: utf-8 -*-
from geometry import project_point_to_segment


class Portal(object):
    def __init__(self, portal_id, parent_node, child_node, entry_point, exit_point, width):
        self.id = portal_id
        self.parent_node = parent_node
        self.child_node = child_node
        self.entry_point = entry_point
        self.exit_point = exit_point
        self.width = float(width)
        self.direction = (exit_point - entry_point).normalized()
        self.is_valid = True
        self.reason = ""

    def as_polyline(self):
        return [self.entry_point, self.exit_point]


class PortalBuilder(object):
    """Finds provisional transition portals between adjacent offset contours."""

    def __init__(self, tolerance, expected_step):
        self.tolerance = float(tolerance)
        self.expected_step = float(expected_step)

    def build(self, tree_result):
        portals = []
        counter = 0

        for level_nodes in tree_result.nodes_by_level:
            for child in level_nodes:
                if child.parent is None:
                    continue

                entry_point, exit_point, width = self._closest_points(child.parent.polygon, child.polygon)
                portal = Portal("P{0}".format(counter), child.parent, child, entry_point, exit_point, width)
                self._validate_portal(portal)

                child.entry_portal = portal
                child.parent.exit_portal = portal
                child.parent.portals.append(portal)
                child.portals.append(portal)
                portals.append(portal)
                counter += 1

        return portals

    def _validate_portal(self, portal):
        if portal.width <= self.tolerance:
            portal.is_valid = False
            portal.reason = "нулевая ширина перехода"
            return

        if self.expected_step > 0.0:
            if portal.width + self.tolerance < self.expected_step * 0.5:
                portal.is_valid = False
                portal.reason = "переход уже половины шага"
            elif portal.width > self.expected_step * 2.5:
                portal.reason = "переход существенно шире шага, требуется уточнение на этапе маршрута"

    def _closest_points(self, parent_polygon, child_polygon):
        best = None

        for point in child_polygon:
            projection, parameter, distance = self._project_to_polygon(point, parent_polygon)
            if best is None or distance < best[2]:
                best = (projection, point, distance)

        for point in parent_polygon:
            projection, parameter, distance = self._project_to_polygon(point, child_polygon)
            if best is None or distance < best[2]:
                best = (point, projection, distance)

        if best is None:
            return parent_polygon[0], child_polygon[0], parent_polygon[0].distance_to(child_polygon[0])
        return best[0], best[1], best[2]

    def _project_to_polygon(self, point, polygon):
        best = None
        for index in range(len(polygon)):
            start = polygon[index]
            end = polygon[(index + 1) % len(polygon)]
            projection, parameter, distance = project_point_to_segment(point, start, end)
            if best is None or distance < best[2]:
                best = (projection, parameter, distance)
        return best
