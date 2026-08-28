# -*- coding: utf-8 -*-
from geometry import distance_point_to_segment
from polygon import ContourNode, point_in_polygon, polygon_area


class ContourTreeException(Exception):
    pass


class ContourTreeResult(object):
    def __init__(self, roots, nodes_by_level, orphan_nodes):
        self.roots = roots
        self.nodes_by_level = nodes_by_level
        self.orphan_nodes = orphan_nodes
        self.all_nodes = []
        self.branch_nodes = []

        for level_nodes in nodes_by_level:
            for node in level_nodes:
                self.all_nodes.append(node)
                if len(node.children) > 1:
                    node.is_branch = True
                    self.branch_nodes.append(node)

    def node_count(self):
        return len(self.all_nodes)

    def branch_count(self):
        return len(self.branch_nodes)

    def transition_count(self):
        count = 0
        for node in self.all_nodes:
            if node.parent is not None:
                count += 1
        return count


class ContourTreeBuilder(object):
    """Builds parent-child links between consecutive offset levels.

    The primary criterion is containment of child sample points in a parent
    polygon. Intersection area and polygon distance are used as fallbacks for
    numerical edge cases.
    """

    def __init__(self, clipper_adapter, tolerance):
        self.adapter = clipper_adapter
        self.tolerance = float(tolerance)

    def build(self, levels):
        if not levels:
            raise ContourTreeException("Нет Offset-уровней для построения дерева контуров.")

        nodes_by_level = []
        for level in levels:
            level_nodes = []
            for index, contour in enumerate(level.contours):
                node = ContourNode(level.level, contour)
                node.component_index = index
                node.id = "C{0}.{1}".format(level.level, index)
                node.portals = []
                node.offset_feet = level.offset_feet
                level_nodes.append(node)
            nodes_by_level.append(level_nodes)

        orphan_nodes = []
        for level_index in range(1, len(nodes_by_level)):
            previous_nodes = nodes_by_level[level_index - 1]
            current_nodes = nodes_by_level[level_index]
            for child in current_nodes:
                parent = self._choose_parent(child, previous_nodes)
                if parent is None:
                    child.is_accessible = False
                    orphan_nodes.append(child)
                else:
                    parent.add_child(child)

        return ContourTreeResult(nodes_by_level[0], nodes_by_level, orphan_nodes)

    def _choose_parent(self, child, parent_candidates):
        best_parent = None
        best_key = None
        samples = self._sample_points(child.polygon)

        for parent in parent_candidates:
            inside_count = 0
            for sample in samples:
                if self._contains_point(sample, parent.polygon):
                    inside_count += 1

            intersection_area = self._intersection_area(parent.polygon, child.polygon)
            distance = self._polygon_distance(parent.polygon, child.polygon)
            key = (inside_count, intersection_area, -distance)

            if best_key is None or key > best_key:
                best_key = key
                best_parent = parent

        if best_key is None:
            return None

        if best_key[0] <= 0 and best_key[1] <= self.tolerance * self.tolerance:
            # Fallback: offset levels should be nested. If containment failed
            # only because of integer rounding, use the nearest previous contour.
            return best_parent

        return best_parent

    def _contains_point(self, point, polygon):
        try:
            return self.adapter.point_in_polygon(point, polygon) > 0
        except Exception:
            return point_in_polygon(point, polygon, self.tolerance) > 0

    def _intersection_area(self, polygon_a, polygon_b):
        try:
            result = self.adapter.intersect([polygon_a], [polygon_b])
        except Exception:
            return 0.0

        area = 0.0
        for polygon in result:
            area += polygon_area(polygon)
        return area

    def _sample_points(self, polygon):
        samples = []
        centroid = self._centroid(polygon)
        samples.append(centroid)

        max_count = min(4, len(polygon))
        for index in range(max_count):
            samples.append(polygon[index])
        return samples

    def _centroid(self, polygon):
        signed_twice_area = 0.0
        cx = 0.0
        cy = 0.0
        count = len(polygon)

        for index in range(count):
            p1 = polygon[index]
            p2 = polygon[(index + 1) % count]
            cross = p1.x * p2.y - p2.x * p1.y
            signed_twice_area += cross
            cx += (p1.x + p2.x) * cross
            cy += (p1.y + p2.y) * cross

        if abs(signed_twice_area) <= self.tolerance * self.tolerance:
            sx = 0.0
            sy = 0.0
            for point in polygon:
                sx += point.x
                sy += point.y
            return polygon[0].__class__(sx / float(count), sy / float(count))

        factor = 1.0 / (3.0 * signed_twice_area)
        return polygon[0].__class__(cx * factor, cy * factor)

    def _polygon_distance(self, polygon_a, polygon_b):
        best = None
        for point in polygon_a:
            distance = self._distance_point_to_polygon(point, polygon_b)
            if best is None or distance < best:
                best = distance
        for point in polygon_b:
            distance = self._distance_point_to_polygon(point, polygon_a)
            if best is None or distance < best:
                best = distance
        if best is None:
            return 0.0
        return best

    def _distance_point_to_polygon(self, point, polygon):
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
