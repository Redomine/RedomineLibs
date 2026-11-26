using System;
using System.Collections.Generic;
using System.Linq;
using static Redomine.FloorHeating.Geometry;
using static System.Math;

namespace Redomine.FloorHeating
{
    internal sealed partial class Search
    {
        private readonly TimeSpan timeBudget;
        private readonly Point2[] polygon, contour;
        private readonly Segment2[] edges;
        private readonly Point2[] tangents;
        private readonly int[] reflexEdges;
        private readonly double[] phases, edgeLengths;
        private readonly double spacing, offset, minimum, maximumLength;
        private readonly int first, second, maxTurns, maxRounds;
        private readonly bool clockwise;
        private readonly Point2 normal, rightStart, leftStart;
        private static string SideName(int side) => side == 0 ? "right" : "left";
        private static string TurnName(Turn turn) => turn == Turn.Forward ? "forward" : turn == Turn.Left ? "left" : "right";
        private Direction[] Ordinary(Point2 heading, bool winding) => winding
            ? new[] { new Direction(Turn.Left, heading.Left), new Direction(Turn.Forward, heading), new Direction(Turn.Right, heading.Right) }
            : new[] { new Direction(Turn.Right, heading.Right), new Direction(Turn.Forward, heading), new Direction(Turn.Left, heading.Left) };

        internal Search(SpiralRequest input)
        {
            timeBudget = input.TimeBudget;
            maximumLength = input.MaximumLengthMm;
            polygon = (Point2[])input.Polygon.Clone();
            spacing = input.SpacingMm;
            offset = input.OffsetMm;
            minimum = input.MinimumSegmentMm;
            clockwise = input.Clockwise;
            first = clockwise ? 1 : 0;
            second = 1 - first;
            maxTurns = input.MaximumTurns;
            maxRounds = input.MaximumRounds;
            normal = input.EntryDirection;
            contour = Offset(polygon, offset);
            (rightStart, leftStart) = Starts(input.RightStart, input.LeftStart, normal, polygon, offset, spacing);
            var ordered = (SignedArea(contour) < 0) == clockwise ? contour : contour.Reverse().ToArray();
            edges = Segments(ordered, true).ToArray();
            tangents = edges.Select(s => s.Unit).ToArray();
            reflexEdges = Enumerable.Range(0, edges.Length).Where(i =>
                (clockwise ? 1 : -1) * tangents[i].Cross(tangents[Next(i)]) > Tol).ToArray();
            edgeLengths = edges.Select(s => s.Length).ToArray();
            phases = new double[edges.Length];
            for (int i = 1; i < edges.Length; i++)
                phases[i] = phases[i - 1] + edgeLengths[i - 1];
        }
        private int Next(int edge) => (edge + 1) % edges.Length;
        private Anchor OnEdge(Point2 p, int i)
        {
            double along = Clamp((p - edges[i].Start).Dot(tangents[i]), 0, edgeLengths[i]);
            var foot = edges[i].Start + tangents[i] * along;
            return new Anchor { Edge = i, Along = along, Foot = foot, Distance = (p - foot).Length, Phase = phases[i] + along };
        }
        private Anchor Project(Point2 p, Anchor? previous = null, double reach = 0, bool forceNext = false)
        {
            if (!previous.HasValue)
            {
                var best = OnEdge(p, 0);
                for (int i = 1; i < edges.Length; i++)
                {
                    var a = OnEdge(p, i);
                    if (a.Distance < best.Distance || (a.Distance == best.Distance && a.Phase < best.Phase))
                        best = a;
                }
                return best;
            }
            var prior = previous.Value;
            int edge = prior.Edge;
            if (forceNext)
            {
                var n = OnEdge(p, Next(edge));
                n.Phase = prior.Phase + edgeLengths[edge] - prior.Along + n.Along;
                return n;
            }
            var current = OnEdge(p, edge);
            current.Backward = current.Along < prior.Along - Tol;
            current.Phase = prior.Phase + Max(0, current.Along - prior.Along);
            if (!current.Backward && current.Along + reach >= edgeLengths[edge] - Tol)
            {
                var n = OnEdge(p, Next(edge));
                if (n.Distance < current.Distance - Tol)
                {
                    n.Phase = current.Phase + edgeLengths[edge] - current.Along + n.Along;
                    return n;
                }
            }
            return current;
        }
        private Anchor? Support(Point2 point, Point2 heading, Anchor prior)
        {
            Anchor? best = null;
            for (int i = 0; i < edges.Length; i++)
            {
                var tangent = tangents[i];
                if (!tangent.Near(heading))
                    continue;
                double along = (point - edges[i].Start).Dot(tangent);
                if (along < -Tol || along > edgeLengths[i] + Tol)
                    continue;
                var a = OnEdge(point, i);
                var delta = point - a.Foot;
                double cross = tangent.Cross(delta);
                if (clockwise ? cross > Tol : cross < -Tol)
                    continue;
                if (a.Distance > Tol && Boundary(point, -delta / a.Distance, contour) < a.Distance - Tol)
                    continue;
                if (!best.HasValue || a.Distance < best.Value.Distance)
                {
                    a.Phase = prior.Phase;
                    best = a;
                }
            }
            return best;
        }
        private List<Direction> Directions(Branch branch) =>
            Directions(branch.Tip, branch.Heading, branch.Guided, branch.Rebind, ref branch.Anchor, branch.Reserve);
        private List<Direction> Directions(Point2 point, Point2 heading, bool guided, bool rebind, ref Anchor? stored, Anchor? reserve)
        {
            var ordinary = Ordinary(heading, clockwise);
            if (!guided)
                return ordinary.ToList();
            var a = stored ?? Project(point);
            if (rebind && a.Distance >= 2 * spacing - Tol)
            {
                var support = Support(point, heading, a);
                if (support.HasValue)
                {
                    a = support.Value;
                    stored = a;
                }
            }
            bool atCorner = Project(point, a, 0, true).Phase <= a.Phase + Tol && a.Distance <= 2 * spacing + Tol;
            var ranked = new List<(int priority, double progress, int order, Direction d)>();
            var deferred = new List<Direction>();
            for (int i = 0; i < ordinary.Length; i++)
            {
                var d = ordinary[i];
                var target = point + d.Vector * spacing;
                var p = Project(target, a, spacing);
                if (p.Backward)
                {
                    var next = Project(target, a, spacing, true);
                    if (next.Phase + Tol >= a.Phase && next.Distance <= a.Distance + spacing + Tol)
                        deferred.Add(d);
                    continue;
                }
                double progress = p.Phase - a.Phase;
                int priority = atCorner && d.Vector.Equals(tangents[Next(a.Edge)]) ? 0 : progress > Tol ? 1 : 2;
                ranked.Add((priority, -progress, i, d));
            }
            var result = ranked.OrderBy(x => x.priority).ThenBy(x => x.progress).ThenBy(x => x.order).Select(x => x.d).ToList();
            result.AddRange(deferred);
            if (reserve.HasValue)
                foreach (var d in Directions(point, heading, true, rebind, ref reserve, null))
                    if (!result.Any(other => other.Turn == d.Turn))
                        result.Add(d);
            return result;
        }
        private static List<Obstacle> Obstacles(Branch branch) =>
            branch.Segments.Select(s => new Obstacle(s, Blocker.Other)).ToList();
        private static void AddReservation(List<Obstacle> obstacles, Segment2? reserve)
        {
            if (reserve.HasValue)
                obstacles.Add(new Obstacle(reserve.Value, Blocker.Reservation));
        }
        private double RayObstacle(Point2 p, Point2 direction, int axis, Segment2 s)
        {
            int cross = 1 - axis;
            double separation = Max(0, Max(Low(s, cross) - p[cross], p[cross] - High(s, cross)));
            if (separation >= spacing - Tol)
                return double.PositiveInfinity;
            double extension = Sqrt(Max(0, spacing * spacing - separation * separation));
            double low = Low(s, axis) - extension, high = High(s, axis) + extension;
            if (direction[axis] > 0)
                return high <= p[axis] + Tol ? double.PositiveInfinity : Max(0, low - p[axis]);
            return low >= p[axis] - Tol ? double.PositiveInfinity : Max(0, p[axis] - high);
        }
        private ProbeResult Probe(Point2 p, Point2 direction, List<Segment2> own, List<Obstacle> other)
        {
            Poll();
            double boundary = Boundary(p, direction, contour);
            var result = new ProbeResult { Available = boundary, Boundary = boundary, Kind = Blocker.Boundary, BoundaryHit = p + direction * boundary };
            if (boundary <= Tol)
            {
                result.Available = 0;
                result.BoundaryHit = p;
                return result;
            }
            int axis = Abs(direction.X) > Tol ? 0 : 1;
            for (int i = 0; i < other.Count; i++)
            {
                var o = other[i];
                double distance = RayObstacle(p, direction, axis, o.Segment);
                if (distance < result.Available - Tol)
                {
                    result.Available = distance;
                    result.Kind = o.Kind;
                    result.Obstacle = o.Segment;
                    result.Index = i;
                }
            }
            for (int i = 0; i < own.Count - 2; i++)
            {
                double distance = RayObstacle(p, direction, axis, own[i]);
                if (distance < result.Available - Tol)
                {
                    result.Available = distance;
                    result.Kind = Blocker.Own;
                    result.Obstacle = own[i];
                    result.Index = i;
                }
            }
            return result;
        }
        private bool CanTravel(ProbeResult probe) =>
            probe.Available > minimum + Tol && probe.Available + Tol >= (probe.Kind == Blocker.Boundary ? offset : spacing);
        private static bool SameObstacle(Segment2? a, Segment2? b) => a.HasValue && b.HasValue && a.Value.Matches(b.Value);
        private Segment2? Reservation(Branch branch, List<Obstacle> other)
        {
            var choices = Directions(branch);
            if (branch.Pending.HasValue)
                choices = choices.OrderBy(d => d.Turn != branch.Pending).ToList();
            foreach (var d in choices)
            {
                var ray = Probe(branch.Tip, d.Vector, branch.Segments, other);
                if (d.Turn == choices[0].Turn && SameObstacle(ray.Obstacle, branch.Avoid))
                    continue;
                if (CanTravel(ray))
                    return new Segment2(branch.Tip, branch.Tip + d.Vector * Min(spacing, ray.Available));
            }
            return null;
        }
    }
}
