using System;
using System.Collections.Generic;
using System.Linq;
using static System.Math;
using static Redomine.FloorHeating.Geometry;

namespace Redomine.FloorHeating
{
    internal sealed partial class Search
    {
        private static IEnumerable<(double lo, double hi)> Union(List<(double lo, double hi)> intervals)
        {
            intervals.Sort();
            double start = 0, end = 0;
            bool open = false;
            foreach (var span in intervals)
            {
                if (span.hi <= span.lo)
                    continue;
                if (open && span.lo <= end)
                {
                    end = Max(end, span.hi);
                    continue;
                }
                if (open)
                    yield return (start, end);
                start = span.lo;
                end = span.hi;
                open = true;
            }
            if (open)
                yield return (start, end);
        }
        private static bool Preserves(double[] current, double[] required) =>
            Enumerable.Range(0, current.Length).All(i => current[i] + Tol >= required[i]);
        private double[] BoundaryCoverage(Pair pair)
        {
            var branches = pair.Right.Segments.Concat(pair.Left.Segments).ToArray();
            return Segments(contour, true).Select(edge =>
            {
                int axis = edge.Horizontal ? 0 : 1, cross = 1 - axis;
                var intervals = new List<(double, double)>();
                foreach (var s in branches)
                    if (Abs(s.Delta[cross]) <= Tol && Abs(s.Start[cross] - edge.Start[cross]) <= spacing / 2 + Tol)
                        intervals.Add((Max(Low(edge, axis), Low(s, axis)), Min(High(edge, axis), High(s, axis))));
                return Union(intervals).Sum(x => x.hi - x.lo);
            }).ToArray();
        }
        private AreaCoverage Coverage(IReadOnlyList<Point2> right, IReadOnlyList<Point2> left, Segment2? bridge)
        {
            var pipes = Segments(right).Concat(Segments(left)).ToList();
            if (bridge.HasValue)
                pipes.Add(bridge.Value);
            double low = contour.Min(p => p.X), high = contour.Max(p => p.X), half = spacing / 2;
            var cuts = new SortedSet<double>(contour.Select(p => p.X));
            var strips = new List<(double x0, double x1, double y0, double y1)>();
            foreach (var s in pipes)
            {
                var box = s.Horizontal ? (Low(s, 0), High(s, 0), s.Start.Y - half, s.Start.Y + half)
                                    : (s.Start.X - half, s.Start.X + half, Low(s, 1), High(s, 1));
                strips.Add(box);
                if (box.Item1 > low && box.Item1 < high)
                    cuts.Add(box.Item1);
                if (box.Item2 > low && box.Item2 < high)
                    cuts.Add(box.Item2);
            }
            double area = 0, covered = 0, previous = low;
            foreach (double x in cuts)
            {
                if (x == previous)
                    continue;
                double middle = (x + previous) / 2, width = x - previous;
                var crossings = Segments(contour, true).Where(s => Low(s, 0) <= middle && middle < High(s, 0))
                    .Select(s => s.Start.Y + (middle - s.Start.X) * s.Delta.Y / s.Delta.X).OrderBy(v => v).ToArray();
                if (crossings.Length % 2 != 0)
                    throw new ArgumentException("invalid_contour_slice");
                var spans = Union(strips.Where(s => s.x0 <= middle && middle < s.x1).Select(s => (s.y0, s.y1)).ToList()).ToArray();
                for (int i = 0; i < crossings.Length; i += 2)
                {
                    area += width * (crossings[i + 1] - crossings[i]);
                    foreach (var span in spans)
                        covered += width * Max(0, Min(span.hi, crossings[i + 1]) - Max(span.lo, crossings[i]));
                }
                previous = x;
            }
            if (area <= Tol * Tol)
                throw new ArgumentException("empty_working_area");
            return new AreaCoverage { WorkingAreaMm2 = area, CoveredAreaMm2 = Clamp(covered, 0, area) };
        }
        private static int CountTurns(IReadOnlyList<Point2> points)
        {
            var segments = Segments(points).Where(s => s.Length > Tol).ToList();
            int turns = 0;
            for (int i = 1; i < segments.Count; i++)
            {
                if (Abs(segments[i - 1].Unit.Cross(segments[i].Unit)) > Tol)
                    turns++;
            }
            return turns;
        }
        private SpiralRoute Display(Candidate c, bool diagnostics = true)
        {
            if (c?.Join == null)
                return null;
            var p = c.Pair.Clone();
            for (int side = 0; side < 2; side++)
            {
                var b = p[side];
                var entry = c.Log.FirstOrDefault(e => e.Action == "entry" && e.Branch == SideName(side));
                if (entry == null || b.Vertices.Count < 2)
                    return null;
                var start = entry.Segment.Start;
                var corrected = entry.Segment.End;
                var visible = new List<Point2>(b.Vertices);
                if (!visible[0].Near(start) || !Segments(polygon, true).Any(edge => On(visible[0], edge)))
                {
                    int found = visible.FindIndex(v => v.Near(corrected));
                    if (found >= 0)
                        visible = visible.Skip(found).ToList();
                    else
                    {
                        int i = b.Segments.FindIndex(s => On(corrected, s));
                        if (i < 0)
                            return null;
                        visible = visible.Skip(i + 1).ToList();
                        visible.Insert(0, corrected);
                    }
                }
                double back = Boundary(visible[0], -normal, polygon);
                if (back > Tol)
                    visible.Insert(0, visible[0] - normal * back);
                var leftEntry = c.Log.First(e => e.Action == "entry" && e.Branch == "left");
                var rightEntry = c.Log.First(e => e.Action == "entry" && e.Branch == "right");
                var starts = Starts(rightEntry.Segment.Start, leftEntry.Segment.Start, normal, polygon, offset, spacing);
                var target = side == 0 ? starts.right : starts.left;
                double shift = (target - visible[0]).Dot(normal.Right);
                visible[0] = target;
                if (visible.Count > 1 && Abs((visible[1] - target).Dot(normal)) > Tol)
                    visible[1] += normal.Right * shift;
                b.SetPath(visible);
            }
            var log = diagnostics ? new List<SpiralEvent>(c.Log) : null;
            AlignOffsetCorners(p, c.Join.Bridge, final: true, tipsOnly: false, log: log);
            if (!Clear(p.Right.Vertices, p.Left.Vertices, c.Join.Bridge, true) || Max(p.Right.Turns, p.Left.Turns) > maxTurns)
                return null;
            double total = Length(p, c.Join);
            bool clipped = total > maximumLength + Tol;
            var rv = p.Right.Vertices;
            var lv = p.Left.Vertices;
            if (clipped)
            {
                var kept = new[] { new List<Point2> { rv[0] }, new List<Point2> { lv[0] } };
                double length = 0;
                for (int i = 0; i < Max(p.Right.Segments.Count, p.Left.Segments.Count); i++)
                {
                    double add = 0;
                    for (int side = 0; side < 2; side++)
                    {
                        if (i < p[side].Segments.Count)
                            add += p[side].Segments[i].Length;
                    }
                    if (length + add > maximumLength + Tol)
                        break;
                    for (int side = 0; side < 2; side++)
                    {
                        if (i < p[side].Segments.Count)
                            kept[side].Add(p[side].Segments[i].End);
                    }
                    length += add;
                }
                while (CountTurns(kept[0]) != CountTurns(kept[1]))
                {
                    var longer = kept[CountTurns(kept[0]) > CountTurns(kept[1]) ? 0 : 1];
                    if (longer.Count < 2)
                        break;
                    longer.RemoveAt(longer.Count - 1);
                }
                rv = kept[0];
                lv = kept[1];
                p.Right.SetPath(rv);
                p.Left.SetPath(lv);
            }
            var bridge = clipped ? null : c.Join.Bridge;
            if (diagnostics)
            {
                if (clipped)
                    log.Add(new SpiralEvent { Action = "length_limit_reached" });
                else
                    AddAbandoned(log, p, bridge);
            }
            var area = Coverage(rv, lv, bridge);
            area.BoundaryLengthsMm = BoundaryCoverage(p);
            area.StripWidthMm = spacing;
            return new SpiralRoute
            {
                Right = rv.ToArray(),
                Left = lv.ToArray(),
                RightStart = rightStart,
                LeftStart = leftStart,
                Contour = (Point2[])contour.Clone(),
                Connected = !clipped,
                JoinMethod = clipped ? "length_limit" : c.Join.Method,
                Bridge = bridge,
                RightTurns = clipped ? CountTurns(rv) : p.Right.Turns,
                LeftTurns = clipped ? CountTurns(lv) : p.Left.Turns,
                RightStop = clipped ? "length_limit" : p.Right.Stop,
                LeftStop = clipped ? "length_limit" : p.Left.Stop,
                LengthMm = Length(p, clipped ? null : c.Join),
                FullLengthMm = total,
                Rollback = diagnostics && !clipped ? c.Log.LastOrDefault(e => e.Recovery != null)?.Recovery : null,
                LengthLimitHit = clipped,
                Coverage = area,
                Journal = log
            };
        }
        private void AddAbandoned(List<SpiralEvent> log, Pair pair, Segment2? bridge)
        {
            var occupied = pair.Right.Segments.Concat(pair.Left.Segments).ToList();
            if (bridge.HasValue)
                occupied.Add(bridge.Value);
            int originalCount = log.Count;
            for (int i = 0; i < originalCount; i++)
            {
                var e = log[i];
                if (e.Action != "segment" && e.Action != "recovery_segment" && e.Action != "entry_segment")
                    continue;
                var s = e.Segment;
                if (s.Length <= Tol)
                    continue;
                int axis = s.Horizontal ? 0 : 1, cross = 1 - axis;
                var gaps = new List<(double lo, double hi)> { (Low(s, axis), High(s, axis)) };
                foreach (var other in occupied)
                {
                    if (Abs(other.Start[cross] - s.Start[cross]) > Tol || Abs(other.End[cross] - s.Start[cross]) > Tol)
                        continue;
                    double a = Low(other, axis), b = High(other, axis);
                    var next = new List<(double, double)>();
                    foreach (var g in gaps)
                    {
                        if (b <= g.lo + Tol || a >= g.hi - Tol)
                            next.Add(g);
                        else
                        {
                            if (a > g.lo + Tol)
                                next.Add((g.lo, a));
                            if (b < g.hi - Tol)
                                next.Add((b, g.hi));
                        }
                    }
                    gaps = next;
                    if (gaps.Count == 0)
                        break;
                }
                foreach (var g in gaps)
                {
                    if (g.hi - g.lo <= minimum + Tol)
                        continue;
                    var piece = s.Delta[axis] > 0 ? new Segment2(s.Start.With(axis, g.lo), s.Start.With(axis, g.hi)) :
                                              new Segment2(s.Start.With(axis, g.hi), s.Start.With(axis, g.lo));
                    occupied.Add(piece);
                    log.Add(new SpiralEvent { Action = "abandoned_segment", Branch = e.Branch, Segment = piece, SourceStep = i + 1 });
                }
            }
        }
    }
}
