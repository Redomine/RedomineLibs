using System;
using System.Collections.Generic;
using System.Linq;
using static System.Math;
using static Redomine.FloorHeating.Geometry;

namespace Redomine.FloorHeating
{
    internal sealed partial class Search
    {
        internal static bool EndsMeet(Point2 right, Point2 left, Segment2? bridge) =>
            bridge.HasValue ? (right.Near(bridge.Value.Start) && left.Near(bridge.Value.End)) ||
                              (right.Near(bridge.Value.End) && left.Near(bridge.Value.Start)) : right.Near(left);

        private bool Clear(List<Point2> r, List<Point2> l, Segment2? bridge, bool final = false, double? min = null)
        {
            if (r.Count < 2 || l.Count < 2)
                return false;
            if (final && !EndsMeet(r[r.Count - 1], l[l.Count - 1], bridge))
                return false;
            var runs = new List<(Segment2 s, int side, int i, bool last)>(r.Count + l.Count);
            for (int side = 0; side < 2; side++)
            {
                var points = side == 0 ? r : l;
                for (int i = 1; i < points.Count; i++)
                {
                    var s = new Segment2(points[i - 1], points[i]);
                    bool last = i == points.Count - 1;
                    if (s.Length == 0 || s.Length < (min ?? minimum) - (final ? 0 : Tol) || (Abs(s.Delta.X) > Tol && Abs(s.Delta.Y) > Tol))
                        return false;
                    if (last && (!Inside(s.End, contour) || s.Length > Boundary(s.Start, s.Unit, contour) + Tol))
                        return false;
                    if (final && !SegmentInside(s, i == 1 ? polygon : contour))
                        return false;
                    runs.Add((s, side, i - 1, last));
                }
            }
            if (bridge.HasValue)
            {
                var s = bridge.Value;
                if (s.Length == 0 || s.Length < (min ?? minimum) - (final ? 0 : Tol) || (Abs(s.Delta.X) > Tol && Abs(s.Delta.Y) > Tol) ||
                   s.Length > Boundary(s.Start, s.Unit, contour) + Tol || !SegmentInside(s, contour))
                    return false;
                runs.Add((s, 2, 0, false));
            }
            for (int i = 0; i < runs.Count; i++)
            {
                Poll();
                var a = runs[i];
                for (int j = i + 1; j < runs.Count; j++)
                {
                    var b = runs[j];
                    if (a.side == b.side)
                    {
                        if (b.i - a.i == 1)
                            continue;
                        if (b.i - a.i == 2 && a.s.Horizontal == b.s.Horizontal)
                        {
                            int axis = a.s.Horizontal ? 0 : 1;
                            if (High(a.s, axis) <= Low(b.s, axis) + Tol || High(b.s, axis) <= Low(a.s, axis) + Tol)
                                continue;
                        }
                    }
                    else if ((b.side == 2 && a.last) || (!bridge.HasValue && a.last && b.last))
                    {
                        if (final && a.s.Horizontal == b.s.Horizontal)
                        {
                            int axis = a.s.Horizontal ? 0 : 1;
                            if (Min(High(a.s, axis), High(b.s, axis)) - Max(Low(a.s, axis), Low(b.s, axis)) > Tol)
                                return false;
                        }
                        continue;
                    }
                    if (Clearance(a.s, b.s) < spacing - Tol)
                        return false;
                }
            }
            return true;
        }
        private Join Finish(Pair p, bool apply = true)
        {
            if (p.Right.Segments.Count == 0 || p.Left.Segments.Count == 0)
                return null;
            var r = p.Right.Segments.Last();
            var l = p.Left.Segments.Last();
            var candidates = new List<(string method, Point2 re, Point2 le, Segment2? bridge)>();
            if (r.Horizontal != l.Horizontal)
            {
                var meet = r.Horizontal ? new Point2(l.End.X, r.End.Y) : new Point2(r.End.X, l.End.Y);
                candidates.Add(("intersect_last_lines", meet, meet, null));
            }
            else
            {
                int axis = r.Horizontal ? 0 : 1, cross = 1 - axis;
                if (Abs(r.End[cross] - l.End[cross]) > Tol)
                {
                    var b = l.End.With(axis, r.End[axis]);
                    var a = r.End.With(axis, l.End[axis]);
                    candidates.Add(("align_to_first", r.End, b, new Segment2(r.End, b)));
                    candidates.Add(("align_to_second", a, l.End, new Segment2(a, l.End)));
                }
                else if (r.End.Near(l.End) && r.Unit.Dot(l.Unit) < 0)
                    candidates.Add(("direct", r.End, r.End, null));
            }
            foreach (var c in candidates.OrderBy(x => (r.End - x.re).Length + (l.End - x.le).Length + (x.bridge?.Length ?? 0))
                                       .ThenBy(x => x.method != (first == 0 ? "align_to_first" : "align_to_second")))
            {
                if ((c.re - r.Start).Dot(r.Delta) <= Tol || (c.le - l.Start).Dot(l.Delta) <= Tol)
                    continue;
                var rv = new List<Point2>(p.Right.Vertices);
                rv[rv.Count - 1] = c.re;
                var lv = new List<Point2>(p.Left.Vertices);
                lv[lv.Count - 1] = c.le;
                if (!Clear(rv, lv, c.bridge))
                    continue;
                if (apply)
                {
                    p.Right.SetTip(c.re);
                    p.Left.SetTip(c.le);
                }
                return new Join(c.method, c.bridge);
            }
            return null;
        }
        private static double Length(Pair p, Join join) =>
            p.Right.Segments.Sum(s => s.Length) + p.Left.Segments.Sum(s => s.Length) + (join?.Bridge?.Length ?? 0);

        private void Simplify(Candidate c, bool terminal = false)
        {
            if (c.Join == null)
                return;
            if (terminal)
            {
                for (int side = 0; side < 2; side++)
                {
                    var b = c.Pair[side];
                    while (b.Vertices.Count >= 5 && TryCorner(c, b, b.Vertices.Count - 5, true))
                    {
                    }
                }
            }
            bool changed;
            do
            {
                changed = false;
                foreach (int side in new[] { first, second })
                {
                    var b = c.Pair[side];
                    for (int i = 1; i < b.Vertices.Count - 4; i++)
                        if (TryCorner(c, b, i, false))
                        {
                            changed = true;
                            break;
                        }
                    if (changed)
                        break;
                }
            } while (changed);
        }
        private bool TryCorner(Candidate c, Branch b, int index, bool terminal)
        {
            var points = b.Vertices.GetRange(index, 5);
            var s = Segments(points).ToArray();
            if (s.Any(x => x.Length <= Tol))
                return false;
            var d = s.Select(x => x.Unit).ToArray();
            if (Abs(d[0].Dot(d[1])) > Tol)
                return false;
            bool parallel = d[0].Near(d[2]);
            bool pocket = parallel && d[1].Near(-d[3]);
            bool stair = parallel && d[1].Near(d[3]);
            var partner = c.Pair[1 - b.Side];
            if (terminal)
            {
                if (!stair)
                    return false;
            }
            else
            {
                bool over = d[0].Near(-d[2]) && d[1].Near(d[3]) && s[2].Length <= spacing + Tol && s[0].Length - s[2].Length >= minimum;
                if (!pocket && !(stair && s[2].Length < spacing - Tol) && !over)
                    return false;
                if (pocket)
                {
                    int axis = s[1].Horizontal ? 0 : 1;
                    foreach (var other in partner.Segments)
                    {
                        if (Abs(other.Delta[1 - axis]) > Tol || other.Delta[axis] * d[1][axis] <= Tol)
                            continue;
                        double overlap = Min(High(other, axis), High(s[1], axis)) - Max(Low(other, axis), Low(s[1], axis));
                        if (overlap > Tol && Clearance(other, s[1]) <= spacing + Tol)
                            return false;
                    }
                }
            }
            var corner = points[1] + s[2].Delta;
            if (!terminal && (points[4] - corner).Dot(d[3]) < minimum - Tol)
                return false;
            if (!SegmentInside(new Segment2(points[0], corner), contour) || !SegmentInside(new Segment2(corner, points[4]), contour))
                return false;
            var proposed = new List<Point2>(b.Vertices);
            proposed.RemoveRange(index + 1, 3);
            proposed.Insert(index + 1, corner);
            if (!Clear(b.Side == 0 ? proposed : partner.Vertices, b.Side == 1 ? proposed : partner.Vertices, c.Join.Bridge))
                return false;
            b.SetPath(proposed);
            b.Turns -= 2;
            if (terminal)
                b.Heading = d[3];
            c.Log.Add(new SpiralEvent
            {
                Action = terminal ? "terminal_corner_delayed" : "pair_corner_simplified",
                Branch = SideName(b.Side),
                OldPath = points.ToArray(),
                NewPath = new[] { points[0], corner, points[4] }
            });
            return true;
        }
    }
}
