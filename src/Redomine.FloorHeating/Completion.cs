using System;
using System.Collections.Generic;
using System.Linq;
using static System.Math;
using static Redomine.FloorHeating.Geometry;

namespace Redomine.FloorHeating
{
    internal sealed partial class Search
    {
        private Point2 Inward(Point2 heading) => clockwise ? heading.Right : heading.Left;
        private double Available(Pair p, int side, Point2 direction) =>
            Probe(p[side].Tip, direction, p[side].Segments, Obstacles(p[1 - side])).Available;
        private static void AppendVertex(Branch branch, Point2 point)
        {
            branch.Segments.Add(new Segment2(branch.Tip, point));
            branch.Vertices.Add(point);
        }
        private static List<Point2> SimplifyCollinear(List<Point2> points)
        {
            var result = new List<Point2>();
            foreach (var p in points)
            {
                while (result.Count >= 2)
                {
                    var a = result[result.Count - 2];
                    var b = result.Last();
                    if (Abs((b - a).Cross(p - b)) > Tol || (b - a).Dot(p - b) <= 0)
                        break;
                    result.RemoveAt(result.Count - 1);
                }
                result.Add(p);
            }
            return result;
        }
        private Candidate EntryBypass(Candidate route)
        {
            var seed = route.Pair.Clone();
            foreach (var b in new[] { seed.Right, seed.Left })
            {
                b.SetPath(SimplifyCollinear(b.Vertices));
                if (b.Vertices.Count < 3)
                    return route;
            }
            var segments = seed[first].Segments;
            var run = segments.Last();
            var resume = segments[segments.Count - 2].Unit;
            if (run.Length > 2 * spacing + Tol || !run.Unit.Near(normal) || Abs(run.Unit.Dot(resume)) > Tol)
                return route;
            var follower = seed[second].Segments.Last();
            var budget = new Budget(maxRounds);
            for (int step = 1; step * spacing <= follower.Length - minimum; step++)
            {
                if (budget.Remaining <= 0)
                    break;
                var t = seed.Clone();
                t[second].SetTip(follower.End - follower.Unit * (step * spacing));
                bool possible = true;
                foreach (var b in new[] { t.Right, t.Left })
                {
                    if (b.Segments.Last().Length <= Tol)
                    {
                        possible = false;
                        break;
                    }
                    b.Heading = b.Segments.Last().Unit;
                    var options = Ordinary(b.Heading, clockwise).Where(x => x.Vector.Near(resume)).ToList();
                    if (options.Count == 0)
                    {
                        possible = false;
                        break;
                    }
                    b.Stop = null;
                    b.Pending = b.Only = options[0].Turn;
                    b.Guided = false;
                }
                if (!possible)
                    continue;
                var candidate = Continue(t, new List<SpiralEvent>(), 1, false, budget);
                if (candidate.Join == null)
                    continue;
                Simplify(candidate);
                if (!Clear(candidate.Pair.Right.Vertices, candidate.Pair.Left.Vertices, candidate.Join.Bridge) ||
                    Length(candidate.Pair, candidate.Join) <= Length(route.Pair, route.Join) + 2 * spacing + Tol)
                    continue;
                candidate.Log.InsertRange(0, route.Log);
                candidate.Log.Add(new SpiralEvent { Action = "entry_bypass_completed" });
                return candidate;
            }
            return route;
        }
        private Candidate InwardBypass(Candidate route)
        {
            var original = route.Pair.Clone();
            foreach (var e in route.Log.AsEnumerable().Reverse().Where(e => e.Action == "pair_corner_simplified"))
            {
                var b = original[e.Branch == "right" ? 0 : 1];
                for (int i = 0; i <= b.Vertices.Count - e.NewPath.Length; i++)
                {
                    if (!e.NewPath.Select((p, j) => p.Near(b.Vertices[i + j])).All(v => v))
                        continue;
                    var restored = new List<Point2>(b.Vertices);
                    restored.RemoveRange(i, e.NewPath.Length);
                    restored.InsertRange(i, e.OldPath);
                    b.SetPath(restored);
                    break;
                }
            }
            var best = route;
            double bestLength = Length(route.Pair, route.Join);
            var budget = new Budget(maxRounds);
            var f = original[first].Vertices;
            var g = original[second].Vertices;
            for (int i = 2; i < f.Count - 1; i++)
            {
                var s = new Segment2(f[i - 1], f[i]);
                var next = new Segment2(f[i], f[i + 1]);
                if (s.Length <= minimum || next.Length <= minimum)
                    continue;
                var inward = Inward(s.Unit);
                if (!next.Unit.Near(-inward))
                    continue;
                var cap = i + 2 < f.Count ? new Segment2(f[i + 1], f[i + 2]) : default;
                bool wallPass = cap.Length > minimum && cap.Unit.Near(s.Unit) && OnBoundaryEdge(next);
                for (int j = 2; j < g.Count; j++)
                {
                    var partner = new Segment2(g[j - 1], g[j]);
                    if (partner.Length <= minimum || !s.Unit.Near(partner.Unit) || Abs((partner.Start - s.End).Dot(inward) - spacing) > Tol)
                        continue;
                    var end = s.End - s.Unit * spacing + inward * spacing;
                    // Pair the follower with the return lane of the wall pass.
                    // Try cutting at the mouth only if that pass cannot continue.
                    if (!wallPass || !TryFrom(end + cap.Delta, next.Length))
                        TryFrom(end, 0);
                    if (budget.Remaining <= 0)
                        return best;

                    bool TryFrom(Point2 followerEnd, double returnDepth)
                    {
                        if (!On(followerEnd, partner))
                            return false;
                        var prefix = original.Clone();
                        prefix[first].SetPath(f.Take(i + 1).ToList());
                        prefix[second].SetPath(g.Take(j).ToList());
                        if (!prefix[second].Tip.Near(followerEnd))
                            prefix[second].Append(followerEnd);
                        var forward = Probe(s.End, s.Unit, prefix[first].Segments, Obstacles(prefix[second]));
                        if (forward.Kind == Blocker.Boundary || forward.Available > 2 * spacing + Tol)
                            return false;
                        if (returnDepth > 0)
                        {
                            prefix[first].Append(next.End);
                            prefix[first].Append(cap.End);
                        }
                        var candidate = GrowInward(prefix, s.Unit, returnDepth, bestLength, budget, route.Log);
                        if (candidate == null)
                            return false;
                        candidate.Log.Add(new SpiralEvent { Action = "inward_bypass_completed" });
                        best = candidate;
                        bestLength = Length(candidate.Pair, candidate.Join);
                        return true;
                    }
                }
            }
            return best;
        }
        private Candidate GrowInward(Pair prefix, Point2 resume, double returnDepth, double bestLength,
            Budget budget, List<SpiralEvent> history)
        {
            var inward = Inward(resume);
            var turn = Ordinary(inward, clockwise).First(d => d.Vector.Near(resume)).Turn;
            ProbeResult Ray(int side) => Probe(prefix[side].Tip, inward, prefix[side].Segments, Obstacles(prefix[1 - side]));
            var leaderRay = Ray(first);
            var followerRay = Ray(second);
            double available = Min(leaderRay.Available - returnDepth, followerRay.Available);
            var required = BoundaryCoverage(prefix);
            Candidate best = null;
            for (int step = 1; step * spacing <= available + Tol && budget.Take(); step++)
            {
                Branch Advance(int side)
                {
                    var source = prefix[side];
                    double distance = step * spacing + (side == first ? returnDepth : 0);
                    var ray = side == first ? leaderRay : followerRay;
                    bool reached = distance >= ray.Available - Tol;
                    // Reservations and blockers from the discarded tail no longer describe this tip.
                    var b = new Branch(side, new List<Point2>(source.Vertices), inward)
                    {
                        Guided = true, Rebind = source.Rebind, Pending = turn, Only = turn,
                        LastBlocker = reached ? ray.Kind : Blocker.Intermediate,
                        LastObstacle = reached ? ray.Obstacle : null
                    };
                    b.Append(b.Tip + inward * distance);
                    b.Anchor = Project(b.Tip);
                    b.Turns = CountTurns(b.Vertices);
                    return b;
                }
                var pair = new Pair(Advance(0), Advance(1));
                if (Max(pair.Right.Turns, pair.Left.Turns) >= maxTurns || !Clear(pair.Right.Vertices, pair.Left.Vertices, null))
                    continue;
                var candidate = Continue(pair, new List<SpiralEvent>(), 1, false, budget);
                double length = Length(candidate.Pair, candidate.Join);
                if (candidate.Join == null || length <= bestLength + 2 * spacing + Tol ||
                    !Clear(candidate.Pair.Right.Vertices, candidate.Pair.Left.Vertices, candidate.Join.Bridge) ||
                    !Preserves(BoundaryCoverage(candidate.Pair), required))
                    continue;
                candidate.Log.InsertRange(0, history);
                if (Display(candidate, false)?.Connected != true)
                    continue;
                best = candidate;
                bestLength = length;
            }
            return best;
        }
        private void RemoveReturnJogs(Candidate c)
        {
            var required = BoundaryCoverage(c.Pair);
            for (int side = 0; side < 2; side++)
            {
                var b = c.Pair[side];
                int i = 1;
                while (i + 3 < b.Vertices.Count)
                {
                    var v = b.Vertices;
                    var a = v[i];
                    var d = v[i + 3];
                    var cap = new Segment2(v[i + 1], v[i + 2]);
                    var shortcut = new Segment2(a, d);
                    int axis = cap.Horizontal ? 0 : 1;
                    bool axisAligned = (Abs(a.X - d.X) <= Tol && Abs(cap.Delta.X) <= Tol) || (Abs(a.Y - d.Y) <= Tol && Abs(cap.Delta.Y) <= Tol);
                    if (!axisAligned || shortcut.Length <= minimum || !SegmentInside(shortcut, contour) ||
                       c.Pair[1 - side].Segments.Any(s => Abs(s.Delta[1 - axis]) <= Tol &&
                            Min(High(s, axis), High(cap, axis)) - Max(Low(s, axis), Low(cap, axis)) > Tol && Abs(Clearance(cap, s) - spacing) <= Tol))
                    {
                        i++;
                        continue;
                    }
                    var proposed = new List<Point2>(v);
                    proposed.RemoveRange(i + 1, 2);
                    b.SetPath(proposed);
                    if (!Clear(c.Pair.Right.Vertices, c.Pair.Left.Vertices, c.Join.Bridge) || !Preserves(BoundaryCoverage(c.Pair), required))
                    {
                        b.SetPath(v);
                        i++;
                        continue;
                    }
                    b.Turns = CountTurns(proposed);
                    c.Log.Add(new SpiralEvent { Action = "return_jog_removed", Branch = SideName(side) });
                }
            }
        }
        private bool CrossesInlets(Candidate route)
        {
            int axis = Abs(normal.X) > Tol ? 1 : 0;
            var starts = route.Log.Where(e => e.Action == "entry").Select(e => e.Segment.Start).ToArray();
            double lo = starts.Min(p => p[axis]), hi = starts.Max(p => p[axis]);
            return new[] { route.Pair.Right, route.Pair.Left }.All(b => b.Segments.Any(s => Abs(s.Delta[1 - axis]) <= Tol &&
                Low(s, axis) <= lo + Tol && High(s, axis) >= hi - Tol && (s.Start - starts[0]).Dot(normal) > offset + Tol));
        }
        private Candidate TerminalPair(Candidate route)
        {
            var p = route.Pair;
            var f = p[first].Vertices;
            var g = p[second].Vertices;
            bool preserve = CrossesInlets(route);
            var boundary = BoundaryCoverage(p);
            for (int i = f.Count - 1; i > 1; i--)
            {
                var a = new Segment2(f[i - 1], f[i]);
                if (a.Length <= minimum)
                    continue;
                for (int j = g.Count - 1; j > 1; j--)
                {
                    var b = new Segment2(g[j - 1], g[j]);
                    if (b.Length <= minimum || !a.Unit.Near(b.Unit) || Abs((b.Start - a.Start).Dot(Inward(a.Unit)) - spacing) > Tol ||
                       Abs(Clearance(a, b) - spacing) > Tol)
                        continue;
                    var seed = p.Clone();
                    seed[first].SetPath(f.Take(i).ToList());
                    seed[second].SetPath(g.Take(j).ToList());
                    Candidate best = null;
                    double length = Length(p, route.Join) + 2 * spacing;
                    for (int trim = 0; trim * spacing <= a.Length; trim++)
                    {
                        var t = seed.Clone();
                        var heading = a.Unit;
                        for (int turn = 0; turn <= maxTurns; turn++)
                        {
                            double delta = (t[second].Tip - t[first].Tip).Dot(heading);
                            double advance = Min(Available(t, first, heading), Available(t, second, heading) + spacing + delta);
                            if (turn == 0)
                                advance -= trim * spacing;
                            double follow = advance - spacing - delta;
                            if (Min(advance, follow) < Max(spacing, minimum) - Tol)
                                break;
                            AppendVertex(t[first], t[first].Tip + heading * advance);
                            AppendVertex(t[second], t[second].Tip + heading * follow);
                            if (!Clear(t.Right.Vertices, t.Left.Vertices, null))
                                break;
                            var closed = t.Clone();
                            var join = Finish(closed);
                            if (join != null)
                            {
                                foreach (var branch in new[] { closed.Right, closed.Left })
                                {
                                    branch.Turns = CountTurns(branch.Vertices);
                                    branch.Heading = heading;
                                    branch.Stop = "paired_terminal_cap";
                                }
                                double total = Length(closed, join);
                                if (Max(closed.Right.Turns, closed.Left.Turns) <= maxTurns && total > length + Tol &&
                                   (!preserve || Preserves(BoundaryCoverage(closed), boundary)))
                                {
                                    best = new Candidate(closed, join, new List<SpiralEvent>(route.Log));
                                    length = total;
                                }
                            }
                            heading = Inward(heading);
                        }
                    }
                    if (best != null)
                    {
                        best.Log.Add(new SpiralEvent { Action = "terminal_pair_completed" });
                        return best;
                    }
                }
            }
            return route;
        }
        private Candidate FinalizeCandidate(Candidate route)
        {
            var fallback = route;
            if (Display(route, false) == null)
                return route;
            route = route.Clone();
            AlignOffsetCorners(route.Pair, route.Join.Bridge, final: true, tipsOnly: false, log: route.Log);
            route = EntryBypass(InwardBypass(route));
            var simplified = route.Clone();
            Simplify(simplified);
            RemoveReturnJogs(simplified);
            var best = TerminalPair(simplified);
            var refined = Display(best, false);
            if (refined == null)
                return fallback;
            var original = Display(fallback, false);
            return Better(original, refined) ? fallback : best;
        }
    }
}
