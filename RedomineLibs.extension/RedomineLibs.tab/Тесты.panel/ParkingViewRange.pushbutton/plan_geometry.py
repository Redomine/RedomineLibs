# -*- coding: utf-8 -*-
from __future__ import division

from collections import namedtuple
from math import hypot, sqrt

import clr
clr.AddReference("WindowsBase")
clr.AddReference("PresentationCore")

from System.Windows import Point, Rect
from System.Windows.Media import (
    Brushes, FillRule, Geometry, GeometryCombineMode, LineSegment, PathFigure,
    PathGeometry, Pen, PenLineCap, PolyLineSegment, ToleranceType,
)


# Размеры в футах. Допуск булевых операций ~0,03 мм.
BOOLEAN_TOLERANCE = 0.0001
HEIGHT_TOLERANCE = 0.00003
ABSOLUTE = ToleranceType.Absolute
Zone = namedtuple("Zone", "bottom ceiling rings area")
ContourIssue = namedtuple("ContourIssue", "bottom ceiling action ring points")


def polygon(rings):
    geometry = PathGeometry()
    geometry.FillRule = FillRule.EvenOdd
    for points in rings:
        figure = PathFigure()
        figure.StartPoint = points[0]
        figure.IsClosed = True
        segments = PolyLineSegment()
        for point in points[1:]:
            segments.Points.Add(point)
        figure.Segments.Add(segments)
        geometry.Figures.Add(figure)
    return geometry


def combine(first, second, mode):
    return Geometry.Combine(first, second, mode, None, BOOLEAN_TOLERANCE, ABSOLUTE)


def union(first, second):
    if first is None:
        return second
    return combine(first, second, GeometryCombineMode.Union)


def intersection(first, second):
    if not first.Bounds.IntersectsWith(second.Bounds):
        return None
    result = combine(first, second, GeometryCombineMode.Intersect)
    return result if result.GetArea(BOOLEAN_TOLERANCE, ABSOLUTE) > 0 else None


def subtract(first, second):
    result = combine(first, second, GeometryCombineMode.Exclude)
    return result if result.GetArea(BOOLEAN_TOLERANCE, ABSOLUTE) > 0 else None


def group_by_height(faces):
    groups = {}
    for height, outline in faces:
        key = int(round(height / HEIGHT_TOLERANCE))
        if key in groups:
            existing_height, existing_outline = groups[key]
            groups[key] = max(height, existing_height), union(existing_outline, outline)
        else:
            groups[key] = height, outline
    return [groups[key] for key in sorted(groups)]


def partition(lower_faces, upper_faces):
    upper_planes = group_by_height(upper_faces)
    covered = None
    for bottom, outline in reversed(group_by_height(lower_faces)):
        remaining = subtract(outline, covered) if covered is not None else outline
        if remaining is None:
            continue
        covered = union(covered, remaining)
        # Ближайшая верхняя плита получает общую площадь первой.
        for ceiling, upper in upper_planes:
            if ceiling <= bottom + HEIGHT_TOLERANCE:
                continue
            overlap = intersection(remaining, upper)
            if overlap is None:
                continue
            yield bottom, ceiling, overlap
            remaining = subtract(remaining, overlap)
            if remaining is None:
                break
        if remaining is not None:
            yield bottom, None, remaining


def signed_area(points):
    return sum(a.X * b.Y - a.Y * b.X
               for a, b in zip(points, points[1:] + points[:1])) / 2


def figure_points(figure):
    points = [figure.StartPoint]
    for segment in figure.Segments:
        if isinstance(segment, PolyLineSegment):
            points.extend(segment.Points)
        elif isinstance(segment, LineSegment):
            points.append(segment.Point)
        else:
            raise ValueError(u"Контур штриховки содержит неподдерживаемый сегмент.")
    if len(points) > 1 and (points[0] - points[-1]).Length <= BOOLEAN_TOLERANCE:
        points.pop()
    return points


def simplify_ring(raw, min_length):
    points = [raw[0]]
    for point in raw[1:]:
        if (point - points[-1]).Length > min_length:
            points.append(point)
    while len(points) > 1 and (points[0] - points[-1]).Length <= min_length:
        points.pop()
    if len(points) < 3 or abs(signed_area(points)) <= BOOLEAN_TOLERANCE ** 2:
        return None
    return points


def components(geometry, min_length, bottom, ceiling, issues):
    flattened = geometry.GetFlattenedPathGeometry(BOOLEAN_TOLERANCE, ABSOLUTE)
    rings = []
    for figure in flattened.Figures:
        points = figure_points(figure)
        if len(points) < 3 or abs(signed_area(points)) <= BOOLEAN_TOLERANCE ** 2:
            issues.append(ContourIssue(bottom, ceiling, "degenerate", "unknown", points))
        else:
            rings.append(points)
    # Вложенность определяем по исходным контурам, до удаления коротких рёбер.
    rings.sort(key=lambda points: -abs(signed_area(points)))
    contours = [polygon([points]) for points in rings]
    parents, depths = [], []
    for child, points in enumerate(rings):
        parent = None
        for candidate in range(child - 1, -1, -1):
            contour = contours[candidate]
            if (contour.Bounds.Contains(contours[child].Bounds)
                    and all(contour.FillContains(point, BOOLEAN_TOLERANCE, ABSOLUTE)
                            for point in points)):
                parent = candidate
                break
        parents.append(parent)
        depths.append(0 if parent is None else depths[parent] + 1)

    simplified = [simplify_ring(points, min_length) for points in rings]
    for index, outer in enumerate(simplified):
        if depths[index] % 2:
            continue
        if outer is None:
            issues.append(ContourIssue(bottom, ceiling, "skip_zone", "outer", rings[index]))
            continue
        holes = []
        invalid_hole = False
        for child, parent in enumerate(parents):
            if parent != index:
                continue
            hole = simplified[child]
            if hole is not None:
                holes.append(hole)
                continue
            bounds = contours[child].Bounds
            tiny = (bounds.Width <= 2 * min_length and bounds.Height <= 2 * min_length
                    and abs(signed_area(rings[child])) <= min_length ** 2)
            if tiny and child not in parents:
                issues.append(ContourIssue(bottom, ceiling, "omit_hole", "hole", rings[child]))
            else:
                issues.append(ContourIssue(bottom, ceiling, "skip_zone", "hole", rings[child]))
                invalid_hole = True
        if invalid_hole:
            continue
        area = abs(signed_area(outer)) - sum(abs(signed_area(hole)) for hole in holes)
        yield [outer if signed_area(outer) > 0 else list(reversed(outer))] + [
            hole if signed_area(hole) < 0 else list(reversed(hole)) for hole in holes
        ], area


def build_zones(lower_faces, upper_faces, min_length, issues):
    return [
        Zone(bottom, ceiling, rings, area)
        for bottom, ceiling, outline in partition(lower_faces, upper_faces)
        for rings, area in components(outline, min_length, bottom, ceiling, issues)
    ]


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def hull_chain(points):
    chain = []
    for point in points:
        while len(chain) >= 2 and cross(chain[-2], chain[-1], point) <= 0:
            chain.pop()
        chain.append(point)
    return chain


def minimum_width(ring):
    # Минимальная ширина проекции достигается по нормали к ребру выпуклой оболочки.
    points = sorted(set((point.X, point.Y) for point in ring))
    if len(points) < 3:
        return 0
    hull = hull_chain(points)[:-1] + hull_chain(reversed(points))[:-1]
    if len(hull) < 3:
        return 0
    return min(
        max(cross(a, b, point) for point in hull) / hypot(b[0] - a[0], b[1] - a[1])
        for a, b in zip(hull, hull[1:] + hull[:1])
    )


def single_component(outline, bottom, ceiling, min_length):
    issues = []
    parts = list(components(outline, min_length, bottom, ceiling, issues))
    if len(parts) == 1 and not issues:
        rings, area = parts[0]
        return Zone(bottom, ceiling, rings, area)
    return None


def boundary_band(outline, offset):
    pen = Pen(Brushes.Black, 2 * offset)
    return outline.GetWidenedPathGeometry(pen, BOOLEAN_TOLERANCE, ABSOLUTE)


def inset_outline(outline, offset):
    return subtract(outline, boundary_band(outline, offset))


def filter_narrow_zones(zones, min_width, min_length):
    wide = {}
    narrow = []
    results = []
    for index, zone in enumerate(zones):
        width = minimum_width(zone.rings[0])
        if width + BOOLEAN_TOLERANCE < min_width:
            narrow.append((zone, width))
        else:
            outline = polygon(zone.rings)
            # У длинной извилистой полосы общая проекция может быть широкой,
            # хотя отступ от фактических границ полностью убирает её площадь.
            if inset_outline(outline, min_width / 2 - BOOLEAN_TOLERANCE) is None:
                results.append((zone, None, False))
            else:
                wide[index] = (zone, outline, outline.Bounds)

    for zone, width in narrow:
        bridge = polygon(zone.rings)
        expanded = union(bridge, boundary_band(bridge, BOOLEAN_TOLERANCE))
        bridge_bounds = expanded.Bounds
        neighbors = {}
        nearby = [index for index in sorted(wide) if bridge_bounds.IntersectsWith(wide[index][2])]
        for index in nearby:
            candidate, outline, _ = wide[index]
            merged = union(bridge, outline)
            joined = single_component(merged, candidate.bottom, candidate.ceiling, min_length) is not None
            if not joined:
                # Допускаем зазор от округления булевых операций, исключая касание в точке.
                contact = intersection(expanded, outline)
                joined = (contact is not None and contact.GetArea(BOOLEAN_TOLERANCE, ABSOLUTE)
                          > min_length * BOOLEAN_TOLERANCE)
            if joined:
                neighbors.setdefault((candidate.bottom, candidate.ceiling), []).append(index)

        matches = [(heights, indices) for heights, indices in neighbors.items() if len(indices) >= 2]
        absorbed = False
        if len(matches) == 1:
            (bottom, ceiling), indices = matches[0]
            combined = bridge
            for index in indices:
                combined = union(combined, wide[index][1])
            merged = single_component(combined, bottom, ceiling, min_length)
            if merged is None:
                combined = union(combined, expanded)
                for index in nearby:
                    if index not in indices:
                        combined = subtract(combined, wide[index][1])
                        if combined is None:
                            break
                if combined is not None:
                    merged = single_component(combined, bottom, ceiling, min_length)
            if merged is not None:
                for index in indices:
                    del wide[index]
                outline = polygon(merged.rings)
                wide[indices[0]] = (merged, outline, outline.Bounds)
                absorbed = True
        results.append((zone, width, absorbed))
    return [wide[index][0] for index in sorted(wide)], results


def project_to_edge(point, start, end):
    edge = end - start
    delta = point - start
    fraction = (delta.X * edge.X + delta.Y * edge.Y) / edge.LengthSquared
    return start + edge * max(0, min(1, fraction))


def ring_edges(rings):
    return [(start, end) for ring in rings for start, end in zip(ring, ring[1:] + ring[:1])]


def closest_gap(first_edges, second_edges, limit):
    # Вне пересечения минимум достигается в проекции конца одного отрезка на другой.
    best = None
    best_sq = limit ** 2
    for a, b in first_edges:
        for c, d in second_edges:
            if (max(a.X, b.X) + limit < min(c.X, d.X)
                    or max(c.X, d.X) + limit < min(a.X, b.X)
                    or max(a.Y, b.Y) + limit < min(c.Y, d.Y)
                    or max(c.Y, d.Y) + limit < min(a.Y, b.Y)):
                continue
            first, second, delta = b - a, d - c, c - a
            det = first.X * second.Y - first.Y * second.X
            if abs(det) > BOOLEAN_TOLERANCE ** 2:
                t = (delta.X * second.Y - delta.Y * second.X) / det
                u = (delta.X * first.Y - delta.Y * first.X) / det
                if 0 <= t <= 1 and 0 <= u <= 1:
                    point = a + first * t
                    return 0, (point, point)
            for p, q in ((a, project_to_edge(a, c, d)), (b, project_to_edge(b, c, d)),
                         (project_to_edge(c, a, b), c), (project_to_edge(d, a, b), d)):
                distance_sq = (q - p).LengthSquared
                if distance_sq <= best_sq:
                    best_sq = distance_sq
                    best = (p, q)
    if best is not None:
        return sqrt(best_sq), best
    return None


def gap_patches(first_edges, second_edges, endpoints, max_gap, min_length):
    facing = []
    for a, b in first_edges:
        edge = b - a
        length = edge.Length
        tangent = edge / length
        for c, d in second_edges:
            ac, ad = c - a, d - a
            heights = [tangent.X * v.Y - tangent.Y * v.X for v in (ac, ad)]
            if (abs(heights[0] - heights[1]) > BOOLEAN_TOLERANCE
                    or max(abs(value) for value in heights) > max_gap + BOOLEAN_TOLERANCE
                    or min(abs(value) for value in heights) <= BOOLEAN_TOLERANCE):
                continue
            ends = [tangent.X * v.X + tangent.Y * v.Y for v in (ac, ad)]
            lo, hi = max(0, min(ends)), min(length, max(ends))
            if hi - lo > min_length:
                p, q = a + tangent * lo, a + tangent * hi
                facing.append((hi - lo, [p, q, project_to_edge(q, c, d), project_to_edge(p, c, d)]))
    # Закрываем просвет по всей длине пересечения параллельных кромок.
    for _, points in sorted(facing, key=lambda item: -item[0]):
        patch = polygon([points])
        yield patch
        yield union(patch, boundary_band(patch, BOOLEAN_TOLERANCE))

    # При сближении углами связываем только окрестность ближайшей пары точек.
    start, end = endpoints
    if (end - start).Length <= BOOLEAN_TOLERANCE:
        point = start
        offset = max_gap / 4
        yield polygon([[Point(point.X - offset, point.Y - offset),
                        Point(point.X + offset, point.Y - offset),
                        Point(point.X + offset, point.Y + offset),
                        Point(point.X - offset, point.Y + offset)]])
    else:
        figure = PathFigure()
        figure.StartPoint = start
        figure.Segments.Add(LineSegment(end, True))
        path = PathGeometry()
        path.Figures.Add(figure)
        pen = Pen(Brushes.Black, max_gap / 2)
        pen.StartLineCap = pen.EndLineCap = PenLineCap.Square
        yield path.GetWidenedPathGeometry(pen, BOOLEAN_TOLERANCE, ABSOLUTE)


def wide_footprint(outline, min_width):
    # Отступ удаляет участки уже порога. Обратное расширение возвращает
    # кромки широких участков, чтобы обычный край не считался сужением.
    radius = min_width / 2 - BOOLEAN_TOLERANCE
    core = inset_outline(outline, radius)
    if core is None:
        return None
    return intersection(outline, union(core, boundary_band(core, radius)))


def merge_zones_across_gaps(zones, max_gap, min_width, min_length, crop, protected=()):
    outlines = [polygon(zone.rings) for zone in zones]
    bounds = [outline.Bounds for outline in outlines]
    edges = [ring_edges(zone.rings) for zone in zones]
    by_height = {}
    pairs = []
    limit = max_gap + BOOLEAN_TOLERANCE
    for index, zone in enumerate(zones):
        extent = bounds[index]
        nearby = Rect(extent.X - limit, extent.Y - limit,
                      extent.Width + 2 * limit, extent.Height + 2 * limit)
        peers = by_height.setdefault((zone.bottom, zone.ceiling), [])
        for peer in peers:
            if nearby.IntersectsWith(bounds[peer]):
                gap = closest_gap(edges[peer], edges[index], limit)
                if gap is not None:
                    pairs.append((gap[0], peer, index, gap[1]))
        peers.append(index)

    owners = list(range(len(zones)))
    groups = {index: (zone, outlines[index], [index]) for index, zone in enumerate(zones)}
    merged_gaps = []
    removed_dividers = []
    supported = {}
    for distance, first, second, endpoints in sorted(pairs, key=lambda pair: pair[:3]):
        left, right = owners[first], owners[second]
        if left == right or left not in groups or right not in groups:
            continue
        zone, left_outline, left_members = groups[left]
        right_zone, right_outline, right_members = groups[right]
        joined = union(left_outline, right_outline)
        merged = single_component(joined, zone.bottom, zone.ceiling, min_length)
        if merged is not None and distance > BOOLEAN_TOLERANCE:
            # Булев результат может связать участки перемычкой тоньше допуска Revit.
            if len(inset_zone(merged, min_length, [])) != 1:
                merged = None
        dividers = []
        if merged is None:
            for patch in gap_patches(edges[first], edges[second], endpoints, max_gap, min_length):
                patch = intersection(patch, crop)
                if patch is None:
                    continue
                gap_area = subtract(patch, joined)
                if gap_area is None:
                    continue
                for obstacle in protected:
                    if patch is not None and intersection(gap_area, obstacle) is not None:
                        patch = subtract(patch, obstacle)
                dividers = []
                for index in sorted(groups):
                    if patch is None:
                        break
                    if index not in (left, right):
                        other_zone, other, members = groups[index]
                        contact = intersection(gap_area, other)
                        if contact is None:
                            continue
                        removable = len(members) == 1 or other_zone.area < min(zone.area, right_zone.area)
                        if (removable
                                and (other_zone.bottom, other_zone.ceiling) != (zone.bottom, zone.ceiling)
                                and contact.GetArea(BOOLEAN_TOLERANCE, ABSOLUTE) > min_length ** 2):
                            if index not in supported:
                                supported[index] = wide_footprint(other, min_width)
                            wide_part = supported[index]
                            wide_contact = intersection(contact, wide_part) if wide_part is not None else None
                            if (wide_contact is None
                                    or wide_contact.GetArea(BOOLEAN_TOLERANCE, ABSOLUTE) <= min_length ** 2):
                                dividers.append(index)
                                continue
                        patch = subtract(patch, other)
                if patch is not None:
                    merged = single_component(union(joined, patch), zone.bottom, zone.ceiling, min_length)
                    if merged is not None and len(inset_zone(merged, min_length, [])) == 1:
                        break
                    merged = None
        if merged is not None:
            members = left_members + right_members
            for index in members:
                owners[index] = left
            groups[left] = (merged, polygon(merged.rings), members)
            del groups[right]
            for index in dividers:
                removed_dividers.append(groups.pop(index)[0])
            merged_gaps.append((distance, first, second))
    confirmed_gaps = [
        (distance, zones[first], zones[second])
        for distance, first, second in merged_gaps
        if owners[first] in groups and owners[second] in groups
    ]
    return [groups[index][0] for index in sorted(groups)], confirmed_gaps, removed_dividers


def inset_zone(zone, min_length, issues):
    # Убираем полосу по обе стороны каждого ребра. Внутри зоны это размыкает
    # касания контуров и убирает перемычки тоньше допуска построения.
    outline = polygon(zone.rings)
    inset = inset_outline(outline, min_length)
    if inset is None:
        return []
    return [
        Zone(zone.bottom, zone.ceiling, rings, area)
        for rings, area in components(inset, min_length, zone.bottom, zone.ceiling, issues)
    ]
