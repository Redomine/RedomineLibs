# -*- coding: utf-8 -*-
from geometry import ensure_ccw, merge_collinear_segments, polygon_area, remove_duplicate_points


class FeatureRecord(object):
    def __init__(self, code, level, component_index, is_accessible, reason):
        self.code = code
        self.level = level
        self.component_index = component_index
        self.is_accessible = is_accessible
        self.reason = reason
        self.area_feet2 = 0.0
        self.width_feet = 0.0


class FeatureAnalysisResult(object):
    def __init__(self, pair_area_contours, features):
        self.pair_area_contours = pair_area_contours
        self.features = features

    def inaccessible_count(self):
        count = 0
        for feature in self.features:
            if not feature.is_accessible:
                count += 1
        return count

    def pair_area_component_count(self):
        return len(self.pair_area_contours)

    def pair_area_total_area(self):
        area = 0.0
        for contour in self.pair_area_contours:
            area += polygon_area(contour)
        return area


class FeatureAnalyzer(object):
    """Stage-2 protrusion and throat diagnostics.

    The robust route decisions will be made later by the branch router. Here we
    build deterministic diagnostics: PairArea availability, split/loss events
    between offset levels, too narrow portals, and too small branch pockets.
    """

    def __init__(self, clipper_adapter, tolerance):
        self.adapter = clipper_adapter
        self.tolerance = float(tolerance)

    def analyze(self, polygon, levels, tree_result, portals, boundary_offset, step, min_bend_radius):
        features = []
        pair_offset = boundary_offset + step * 0.5
        pair_area_contours = self._clean_contours(self.adapter.offset([polygon], -pair_offset))

        self._analyze_pair_area(levels, pair_area_contours, features)
        self._analyze_level_events(levels, features)
        self._analyze_tree_branches(tree_result, features, min_bend_radius)
        self._analyze_portals(portals, features, step)

        return FeatureAnalysisResult(pair_area_contours, features)

    def _clean_contours(self, contours):
        result = []
        min_area = self.tolerance * self.tolerance * 10.0
        for contour in contours:
            points = remove_duplicate_points(contour, self.tolerance, True)
            points = merge_collinear_segments(points, self.tolerance, True)
            if len(points) < 3:
                continue
            points = ensure_ccw(points)
            if polygon_area(points) <= min_area:
                continue
            result.append(points)
        return result

    def _analyze_pair_area(self, levels, pair_area_contours, features):
        if not pair_area_contours:
            record = FeatureRecord(
                "pair_area_empty",
                0,
                -1,
                False,
                "PairArea исчезла: две трубы с заданным шагом не проходят внутри области"
            )
            features.append(record)
            return

        if not levels:
            return

        first_level = levels[0]
        for index, contour in enumerate(first_level.contours):
            area = polygon_area(contour)
            overlap_area = self._overlap_area(contour, pair_area_contours)
            if area <= 0.0:
                continue

            ratio = overlap_area / area
            if ratio < 0.60:
                record = FeatureRecord(
                    "weak_pair_area_overlap",
                    first_level.level,
                    index,
                    False,
                    "область C0 почти не пересекается с PairArea; вероятен недоступный выступ или узкая горловина"
                )
                record.area_feet2 = area
                features.append(record)

    def _analyze_level_events(self, levels, features):
        for index in range(1, len(levels)):
            previous_level = levels[index - 1]
            current_level = levels[index]
            previous_count = previous_level.component_count()
            current_count = current_level.component_count()

            if current_count > previous_count:
                record = FeatureRecord(
                    "split",
                    current_level.level,
                    -1,
                    True,
                    "Offset-уровень разделился: требуется обход ветвей через порталы"
                )
                features.append(record)
            elif current_count < previous_count:
                record = FeatureRecord(
                    "component_loss",
                    current_level.level,
                    -1,
                    True,
                    "часть области исчезла при внутреннем смещении; нужна проверка выступа или узкого коридора"
                )
                features.append(record)

    def _analyze_tree_branches(self, tree_result, features, min_bend_radius):
        min_turn_area = (min_bend_radius * 2.0) * (min_bend_radius * 2.0)

        for node in tree_result.branch_nodes:
            record = FeatureRecord(
                "branch_node",
                node.level,
                node.component_index,
                True,
                "у контура несколько дочерних областей; маршрут должен обходить их рекурсивно"
            )
            record.area_feet2 = node.area
            features.append(record)

        for node in tree_result.all_nodes:
            if node.area < min_turn_area:
                node.is_accessible = False
                record = FeatureRecord(
                    "too_small_for_turn",
                    node.level,
                    node.component_index,
                    False,
                    "компонент меньше минимальной зоны разворота по заданному радиусу"
                )
                record.area_feet2 = node.area
                features.append(record)

    def _analyze_portals(self, portals, features, step):
        for portal in portals:
            if portal.width + self.tolerance < step:
                portal.child_node.is_accessible = False
                record = FeatureRecord(
                    "portal_too_narrow",
                    portal.child_node.level,
                    portal.child_node.component_index,
                    False,
                    "портал между уровнями уже заданного шага трубы"
                )
                record.width_feet = portal.width
                features.append(record)
            elif not portal.is_valid:
                record = FeatureRecord(
                    "portal_warning",
                    portal.child_node.level,
                    portal.child_node.component_index,
                    False,
                    portal.reason
                )
                record.width_feet = portal.width
                features.append(record)

    def _overlap_area(self, contour, other_contours):
        try:
            intersections = self.adapter.intersect([contour], other_contours)
        except Exception:
            return 0.0

        area = 0.0
        for polygon in intersections:
            area += polygon_area(polygon)
        return area
