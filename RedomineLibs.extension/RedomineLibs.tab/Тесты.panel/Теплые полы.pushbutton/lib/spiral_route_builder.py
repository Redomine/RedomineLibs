# -*- coding: utf-8 -*-
from geometry import Point2D, connect_polylines, polygon_area, remove_duplicate_points
from polygon import polygon_bounds
from route_validator import RouteValidator


class SpiralRouteException(Exception):
    pass


class RouteCandidate(object):
    def __init__(self, name, route_points, internal_start_index, internal_end_index,
                 feed_levels, return_levels, score):
        self.name = name
        self.route_points = route_points
        self.internal_start_index = internal_start_index
        self.internal_end_index = internal_end_index
        self.feed_levels = feed_levels
        self.return_levels = return_levels
        self.score = float(score)
        self.validation = None
        self.warnings = []

    def total_length(self):
        if self.validation is None:
            return 0.0
        return self.validation.total_length


class SpiralRouteBuilder(object):
    """Builds a first working double-spiral route for one rectangular area.

    Stage 3 deliberately handles only a single rectangular component. Complex
    split trees, edge recesses and protrusions are routed in the next stage.
    """

    def __init__(self, tolerance, step, min_bend_radius):
        self.tolerance = float(tolerance)
        self.step = float(step)
        self.pitch = float(step) * 2.0
        self.min_bend_radius = float(min_bend_radius)

    def build(self, boundary_polygon, levels, tree_result, supply_info, return_info, max_length):
        self._assert_single_rectangular_component(levels, tree_result)

        rects_by_level = self._get_rectangles(levels)
        feed_level_numbers, return_level_numbers = self._choose_route_levels(rects_by_level)

        candidates = []
        connection_modes = ["selected", "endpoint", "safe"]
        for transform_mode in range(8):
            for feed_mode in connection_modes:
                for return_mode in connection_modes:
                    candidate = self._build_candidate(
                        transform_mode,
                        feed_mode,
                        return_mode,
                        boundary_polygon,
                        rects_by_level,
                        feed_level_numbers,
                        return_level_numbers,
                        supply_info,
                        return_info
                    )
                    self._validate_candidate(candidate, boundary_polygon, max_length)
                    candidates.append(candidate)

        valid_candidates = []
        for candidate in candidates:
            if candidate.validation is not None and candidate.validation.is_valid():
                valid_candidates.append(candidate)

        if not valid_candidates:
            details = []
            for candidate in candidates:
                if candidate.validation is None:
                    details.append("{0}: нет результата проверки".format(candidate.name))
                else:
                    details.append("{0}: {1}".format(candidate.name, "; ".join(candidate.validation.errors)))
            raise SpiralRouteException(
                "Не удалось построить прямоугольную улитку без пересечений.\n{0}".format("\n".join(details))
            )

        valid_candidates.sort(key=lambda item: item.score)
        return valid_candidates[0], candidates

    def _assert_single_rectangular_component(self, levels, tree_result):
        if tree_result.branch_count() > 0:
            raise SpiralRouteException("Этап 3 поддерживает только область без ветвления Offset-контуров.")
        if len(tree_result.orphan_nodes) > 0:
            raise SpiralRouteException("В дереве Offset есть узлы без родителя; требуется этап 4.")

        for level in levels:
            if level.component_count() != 1:
                raise SpiralRouteException(
                    "Этап 3 поддерживает только один компонент на каждом уровне. На C{0}: {1}.".format(
                        level.level,
                        level.component_count()
                    )
                )

    def _get_rectangles(self, levels):
        result = {}
        for level in levels:
            contour = level.contours[0]
            rect = self._rectangle_from_contour(contour)
            result[level.level] = rect
        return result

    def _rectangle_from_contour(self, contour):
        if len(contour) != 4:
            raise SpiralRouteException(
                "Этап 3 строит маршрут только по прямоугольнику. Контур содержит {0} вершин.".format(len(contour))
            )

        min_x, min_y, max_x, max_y = polygon_bounds(contour)
        for point in contour:
            on_vertical = abs(point.x - min_x) <= self.tolerance or abs(point.x - max_x) <= self.tolerance
            on_horizontal = abs(point.y - min_y) <= self.tolerance or abs(point.y - max_y) <= self.tolerance
            if not (on_vertical and on_horizontal):
                raise SpiralRouteException("Offset-контур не является прямоугольником.")

        if max_x - min_x <= self.tolerance or max_y - min_y <= self.tolerance:
            raise SpiralRouteException("Прямоугольный Offset-контур выродился.")

        return {
            "left": min_x,
            "bottom": min_y,
            "right": max_x,
            "top": max_y,
            "area": (max_x - min_x) * (max_y - min_y)
        }

    def _choose_route_levels(self, rects_by_level):
        level_numbers = sorted(rects_by_level.keys())
        even_levels = []
        odd_levels = []
        for level_number in level_numbers:
            if level_number % 2 == 0:
                even_levels.append(level_number)
            else:
                odd_levels.append(level_number)

        if not even_levels or not odd_levels:
            raise SpiralRouteException("Для улитки нужны как минимум один четный и один нечетный Offset-уровень.")

        min_turn_area = (self.min_bend_radius * 2.0) * (self.min_bend_radius * 2.0)
        deepest_even = None
        deepest_odd = None

        reversed_even = list(even_levels)
        reversed_even.reverse()
        for even_level in reversed_even:
            rect = rects_by_level[even_level]
            if rect["area"] < min_turn_area:
                continue

            previous_odd = even_level - 1
            next_odd = even_level + 1
            if previous_odd in rects_by_level and rects_by_level[previous_odd]["area"] >= min_turn_area:
                deepest_even = even_level
                deepest_odd = previous_odd
                break
            if next_odd in rects_by_level and rects_by_level[next_odd]["area"] >= min_turn_area:
                deepest_even = even_level
                deepest_odd = next_odd
                break

        if deepest_even is None or deepest_odd is None:
            raise SpiralRouteException("Не найдено место для центрального разворота с заданным радиусом.")

        feed_levels = []
        for level_number in even_levels:
            if level_number <= deepest_even:
                feed_levels.append(level_number)

        return_levels = []
        for level_number in odd_levels:
            if level_number <= deepest_odd:
                return_levels.append(level_number)

        if not feed_levels or not return_levels:
            raise SpiralRouteException("Не удалось подобрать четные и нечетные витки для маршрута.")

        return feed_levels, return_levels

    def _build_candidate(self, transform_mode, feed_mode, return_mode, boundary_polygon, rects_by_level, feed_levels,
                         return_levels, supply_info, return_info):
        boundary_rect = self._bounds_rect(boundary_polygon)
        base_side = "bottom"
        mirror_mode = transform_mode
        if transform_mode >= 4:
            base_side = "left"
            mirror_mode = transform_mode - 4

        feed_gap = self._side_gap_for_point(
            supply_info["boundary_end"],
            boundary_rect,
            base_side,
            mirror_mode,
            self.pitch
        )
        return_gap = self._side_gap_for_point(
            return_info["boundary_end"],
            boundary_rect,
            base_side,
            mirror_mode,
            0.0
        )

        feed_inward = self._build_rectangular_spiral(rects_by_level, feed_levels, feed_gap, base_side)
        return_inward = self._build_rectangular_spiral(rects_by_level, return_levels, return_gap, base_side)

        feed_inward = self._transform_points(feed_inward, boundary_rect, mirror_mode)
        return_inward = self._transform_points(return_inward, boundary_rect, mirror_mode)
        return_outward = list(return_inward)
        return_outward.reverse()

        feed_boundary_point = self._nearest_boundary_point(feed_inward[0], boundary_rect)
        return_boundary_point = self._nearest_boundary_point(return_outward[-1], boundary_rect)

        supply_to_spiral = self._build_feed_connection(
            feed_mode,
            supply_info,
            feed_boundary_point,
            feed_inward[0]
        )
        center_turn = [
            feed_inward[-1],
            return_outward[0]
        ]
        spiral_to_return = self._build_return_connection(
            return_mode,
            return_outward[-1],
            return_boundary_point,
            return_info
        )

        route = connect_polylines(
            [supply_to_spiral, feed_inward, center_turn, return_outward, spiral_to_return],
            self.tolerance
        )

        route = remove_duplicate_points(route, self.tolerance, False)
        internal_start_index = self._find_point_index(route, feed_inward[0])
        internal_end_index = self._find_point_index(route, return_outward[-1]) - 1
        if internal_start_index < 0:
            internal_start_index = max(0, len(supply_to_spiral) - 2)
        if internal_end_index < internal_start_index:
            internal_end_index = max(internal_start_index, len(route) - len(spiral_to_return))

        connection_score = (
            supply_info["boundary_end"].distance_to(feed_inward[0]) +
            feed_inward[-1].distance_to(return_outward[0]) +
            return_outward[-1].distance_to(return_info["boundary_end"])
        )
        connection_score += self._connection_mode_penalty(feed_mode)
        connection_score += self._connection_mode_penalty(return_mode)

        candidate = RouteCandidate(
            "rect_transform_{0}_{1}_{2}".format(transform_mode, feed_mode, return_mode),
            route,
            internal_start_index,
            internal_end_index,
            feed_levels,
            return_levels,
            connection_score
        )
        if feed_mode == "safe":
            candidate.warnings.append("Подача не подключена к выбранной линии: безопасный вход построен у внешнего контура.")
        elif feed_mode == "endpoint":
            candidate.warnings.append("Подача подключена только к ближайшему к границе концу выбранной линии.")

        if return_mode == "safe":
            candidate.warnings.append("Обратка не подключена к выбранной линии: безопасный выход построен у внешнего контура.")
        elif return_mode == "endpoint":
            candidate.warnings.append("Обратка подключена только к ближайшему к границе концу выбранной линии.")

        return candidate

    def _build_feed_connection(self, mode, supply_info, feed_boundary_point, feed_start):
        if mode == "selected":
            return [supply_info["free_end"], supply_info["boundary_end"], feed_start]
        if mode == "endpoint":
            return [supply_info["boundary_end"], feed_start]
        return [feed_boundary_point, feed_start]

    def _build_return_connection(self, mode, return_end, return_boundary_point, return_info):
        if mode == "selected":
            return [return_end, return_info["boundary_end"], return_info["free_end"]]
        if mode == "endpoint":
            return [return_end, return_info["boundary_end"]]
        return [return_end, return_boundary_point]

    def _connection_mode_penalty(self, mode):
        if mode == "selected":
            return 0.0
        if mode == "endpoint":
            return 1000.0
        return 5000.0

    def _build_rectangular_spiral(self, rects_by_level, level_numbers, start_gap, base_side):
        points = []
        for index, level_number in enumerate(level_numbers):
            rect = rects_by_level[level_number]
            left = rect["left"]
            bottom = rect["bottom"]
            right = rect["right"]
            top = rect["top"]

            if base_side == "left":
                if index == 0:
                    gap = float(start_gap)
                    max_gap = max(0.0, (top - bottom) - self.tolerance)
                    if gap > max_gap:
                        gap = max_gap
                    if gap < self.tolerance:
                        gap = self.tolerance
                    points.append(Point2D(left, bottom + gap))

                points.append(Point2D(left, top))
                points.append(Point2D(right, top))
                points.append(Point2D(right, bottom))

                if index < len(level_numbers) - 1:
                    next_rect = rects_by_level[level_numbers[index + 1]]
                    points.append(Point2D(next_rect["left"], bottom))
            else:
                if index == 0:
                    gap = float(start_gap)
                    max_gap = max(0.0, (right - left) - self.tolerance)
                    if gap > max_gap:
                        gap = max_gap
                    if gap < self.tolerance:
                        gap = self.tolerance
                    points.append(Point2D(left + gap, bottom))

                points.append(Point2D(right, bottom))
                points.append(Point2D(right, top))
                points.append(Point2D(left, top))

                if index < len(level_numbers) - 1:
                    next_rect = rects_by_level[level_numbers[index + 1]]
                    points.append(Point2D(left, next_rect["bottom"]))

        return remove_duplicate_points(points, self.tolerance, False)

    def _nearest_boundary_point(self, point, boundary_rect):
        left = boundary_rect["left"]
        right = boundary_rect["right"]
        bottom = boundary_rect["bottom"]
        top = boundary_rect["top"]

        distances = [
            [abs(point.y - bottom), Point2D(self._clamp(point.x, left, right), bottom)],
            [abs(point.x - left), Point2D(left, self._clamp(point.y, bottom, top))],
            [abs(point.y - top), Point2D(self._clamp(point.x, left, right), top)],
            [abs(point.x - right), Point2D(right, self._clamp(point.y, bottom, top))]
        ]
        distances.sort(key=lambda item: item[0])
        return distances[0][1]

    def _clamp(self, value, minimum, maximum):
        if value < minimum:
            return minimum
        if value > maximum:
            return maximum
        return value

    def _find_point_index(self, points, target):
        for index, point in enumerate(points):
            if point.almost_equal(target, self.tolerance):
                return index
        return -1

    def _transform_points(self, points, boundary_rect, mode):
        result = []
        left = boundary_rect["left"]
        right = boundary_rect["right"]
        bottom = boundary_rect["bottom"]
        top = boundary_rect["top"]

        for point in points:
            x = point.x
            y = point.y
            if mode == 1:
                x = left + right - point.x
            elif mode == 2:
                y = bottom + top - point.y
            elif mode == 3:
                x = left + right - point.x
                y = bottom + top - point.y
            result.append(Point2D(x, y))
        return result

    def _side_gap_for_point(self, point, boundary_rect, base_side, mirror_mode, default_gap):
        base_point = self._inverse_transform_point(point, boundary_rect, mirror_mode)
        left = boundary_rect["left"]
        right = boundary_rect["right"]
        bottom = boundary_rect["bottom"]
        top = boundary_rect["top"]

        if base_side == "left":
            gap = base_point.y - bottom
            max_gap = (top - bottom) - self.tolerance
        else:
            gap = base_point.x - left
            max_gap = (right - left) - self.tolerance

        if gap <= self.tolerance or gap >= max_gap:
            gap = float(default_gap)
        if gap < self.tolerance:
            gap = self.tolerance
        if gap > max_gap:
            gap = max_gap
        return gap

    def _inverse_transform_point(self, point, boundary_rect, mode):
        left = boundary_rect["left"]
        right = boundary_rect["right"]
        bottom = boundary_rect["bottom"]
        top = boundary_rect["top"]

        x = point.x
        y = point.y
        if mode == 1:
            x = left + right - point.x
        elif mode == 2:
            y = bottom + top - point.y
        elif mode == 3:
            x = left + right - point.x
            y = bottom + top - point.y
        return Point2D(x, y)

    def _bounds_rect(self, polygon):
        min_x, min_y, max_x, max_y = polygon_bounds(polygon)
        return {
            "left": min_x,
            "bottom": min_y,
            "right": max_x,
            "top": max_y
        }

    def _validate_candidate(self, candidate, boundary_polygon, max_length):
        validator = RouteValidator(self.tolerance, self.step)
        candidate.validation = validator.validate(
            candidate.route_points,
            boundary_polygon,
            candidate.internal_start_index,
            candidate.internal_end_index
        )

        if max_length > 0.0 and candidate.validation.total_length > max_length:
            candidate.validation.warnings.append(
                "Длина контура превышает заданный лимит: {0:.1f} м.".format(
                    candidate.validation.total_length * 0.3048
                )
            )

        candidate.score += candidate.validation.intersection_count * 1000000.0
        candidate.score += candidate.validation.too_short_count * 10000.0
        if len(candidate.validation.errors) > 0:
            candidate.score += len(candidate.validation.errors) * 1000000.0
