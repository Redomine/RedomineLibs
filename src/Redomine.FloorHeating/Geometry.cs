using System;
using System.Collections.Generic;
using System.Linq;
using static System.Math;

namespace Redomine.FloorHeating
{
    internal static class Geometry
    {
        internal const double Tol = 0.001;
        internal static bool Finite(double value) => !double.IsInfinity(value) && !double.IsNaN(value);
        internal static double Low(Segment2 s, int axis) => Min(s.Start[axis], s.End[axis]);
        internal static double High(Segment2 s, int axis) => Max(s.Start[axis], s.End[axis]);
        internal static double Clamp(double x, double low, double high) => Max(low, Min(x, high));
        internal static IEnumerable<Segment2> Segments(IReadOnlyList<Point2> points, bool closed = false)
        {
            for (int i = 1; i < points.Count; i++)
                yield return new Segment2(points[i - 1], points[i]);
            if (closed && points.Count > 1)
                yield return new Segment2(points[points.Count - 1], points[0]);
        }
        internal static double SignedArea(IReadOnlyList<Point2> points)
        {
            var origin = points[0];
            return Segments(points, true).Sum(s => (s.Start - origin).Cross(s.End - origin)) / 2;
        }
        internal static bool On(Point2 p, Segment2 s)
        {
            int axis = s.Horizontal ? 0 : 1;
            return Abs(p[1 - axis] - s.Start[1 - axis]) <= Tol && p[axis] >= Low(s, axis) - Tol && p[axis] <= High(s, axis) + Tol;
        }
        internal static bool Inside(Point2 p, IReadOnlyList<Point2> polygon)
        {
            bool inside = false;
            for (int i = 0; i < polygon.Count; i++)
            {
                var a = polygon[i];
                var b = polygon[(i + 1) % polygon.Count];
                if (On(p, new Segment2(a, b)))
                    return true;
                if ((a.Y > p.Y) != (b.Y > p.Y) && a.X + (p.Y - a.Y) * (b.X - a.X) / (b.Y - a.Y) > p.X)
                    inside = !inside;
            }
            return inside;
        }
        internal static double Clearance(Segment2 a, Segment2 b)
        {
            double dx = Max(0, Max(Low(a, 0) - High(b, 0), Low(b, 0) - High(a, 0)));
            double dy = Max(0, Max(Low(a, 1) - High(b, 1), Low(b, 1) - High(a, 1)));
            return Sqrt(dx * dx + dy * dy);
        }
        internal static void ValidateLoop(Point2[] points)
        {
            if (points.Length < 4 || points.Any(p => !Finite(p.X) || !Finite(p.Y)))
                throw new ArgumentException("invalid_boundary");
            var edges = Segments(points, true).ToArray();
            for (int i = 0; i < edges.Length; i++)
            {
                var s = edges[i];
                if (s.Length <= Tol || (Abs(s.Delta.X) > Tol && Abs(s.Delta.Y) > Tol))
                    throw new ArgumentException("boundary_must_be_orthogonal");
                for (int j = i + 2; j < edges.Length; j++)
                {
                    if (i == 0 && j == edges.Length - 1)
                        continue;
                    if (Clearance(s, edges[j]) <= Tol)
                        throw new ArgumentException("self_intersecting_contour");
                }
                var next = edges[(i + 1) % edges.Length];
                if (s.Horizontal == next.Horizontal && s.Delta.Dot(next.Delta) < 0)
                    throw new ArgumentException("boundary_reverses_direction");
            }
            if (Abs(SignedArea(points)) <= Tol * Tol)
                throw new ArgumentException("empty_working_area");
        }
        internal static Point2[] Offset(Point2[] polygon, double offset)
        {
            ValidateLoop(polygon);
            // Remove redundant collinear vertices before intersecting offset edges.
            var corners = Enumerable.Range(0, polygon.Length).Where(i =>
                new Segment2(polygon[(i + polygon.Length - 1) % polygon.Length], polygon[i]).Horizontal !=
                new Segment2(polygon[i], polygon[(i + 1) % polygon.Length]).Horizontal).Select(i => polygon[i]).ToArray();
            double winding = Sign(SignedArea(corners));
            var edges = Segments(corners, true).ToArray();
            var working = new Point2[edges.Length];
            for (int i = 0; i < edges.Length; i++)
            {
                var a = edges[(i + edges.Length - 1) % edges.Length];
                var b = edges[i];
                var pa = a.End + a.Unit.Left * (winding * offset);
                var pb = b.Start + b.Unit.Left * (winding * offset);
                working[i] = a.Horizontal ? new Point2(pb.X, pa.Y) : new Point2(pa.X, pb.Y);
            }
            ValidateLoop(working);
            for (int i = 0; i < working.Length; i++)
            {
                var s = new Segment2(working[i], working[(i + 1) % working.Length]);
                if (edges[i].Delta.Dot(s.Delta) <= 0 || !Inside(s.Start, polygon) || !SegmentInside(s, polygon))
                    throw new ArgumentException("offset_collapses_contour");
            }
            return working;
        }
        internal static double Boundary(Point2 p, Point2 direction, IReadOnlyList<Point2> contour)
        {
            int axis = Abs(direction.X) > Tol ? 0 : 1, cross = 1 - axis;
            double best = double.PositiveInfinity;
            for (int i = 0; i < contour.Count; i++)
            {
                var a = contour[i];
                var b = contour[(i + 1) % contour.Count];
                if (Abs(a[axis] - b[axis]) > Tol || p[cross] < Min(a[cross], b[cross]) - Tol || p[cross] > Max(a[cross], b[cross]) + Tol)
                    continue;
                double d = (a[axis] - p[axis]) / direction[axis];
                if (d > Tol && d < best)
                    best = d;
            }
            return double.IsInfinity(best) ? 0 : best;
        }
        internal static bool SegmentInside(Segment2 seg, IReadOnlyList<Point2> contour)
        {
            if (!Inside(seg.Start, contour) || !Inside(seg.End, contour))
                return false;
            int axis = seg.Horizontal ? 0 : 1;
            if (Abs(seg.Delta[1 - axis]) > Tol)
                return false;
            var cuts = new List<double> { Low(seg, axis), High(seg, axis) };
            foreach (var p in contour)
            {
                if (p[axis] > cuts[0] && p[axis] < cuts[1])
                    cuts.Add(p[axis]);
            }
            cuts.Sort();
            for (int i = 1; i < cuts.Count; i++)
            {
                if (!Inside(seg.Start.With(axis, (cuts[i] + cuts[i - 1]) / 2), contour))
                    return false;
            }
            return true;
        }
        internal static Point2 Entry(Point2 start, Point2 direction, double depth, Point2[] polygon)
        {
            int axis = Abs(direction.X) > Tol ? 0 : 1;
            var back = -direction;
            double best = double.PositiveInfinity;
            foreach (var edge in Segments(polygon, true))
            {
                if (Abs(edge.Delta[axis]) > Tol || start[1 - axis] < Low(edge, 1 - axis) - Tol || start[1 - axis] > High(edge, 1 - axis) + Tol)
                    continue;
                double d = (edge.Start[axis] - start[axis]) / back[axis];
                if (d >= -Tol)
                    best = Min(best, Max(d, 0));
            }
            if (double.IsInfinity(best))
                throw new ArgumentException("entry_boundary_not_found_behind_open_end");
            return start + back * best + direction * depth;
        }
        internal static (Point2 right, Point2 left) Starts(Point2 a, Point2 b, Point2 normal, Point2[] polygon, double offset, double spacing)
        {
            var middle = (a + b) * 0.5;
            var right = normal.Right;
            double depth = Boundary(middle, -normal, polygon);
            if (depth <= Tol && !Segments(polygon, true).Any(e => On(middle, e)))
                throw new ArgumentException("entry_boundary_not_found");
            var center = middle - normal * depth;
            double l = Boundary(center, -right, polygon), r = Boundary(center, right, polygon), required = offset + spacing / 2;
            if (l + r < 2 * required - Tol)
                throw new ArgumentException("not_enough_room_for_inlets");
            double shift = Max(required - l, 0);
            if (r - shift < required - Tol)
                shift = r - required;
            center += right * shift;
            return (center + right * (spacing / 2), center - right * (spacing / 2));
        }
    }
}
