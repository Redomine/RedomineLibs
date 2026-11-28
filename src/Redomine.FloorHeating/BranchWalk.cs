using System;
using System.Collections.Generic;
using System.Linq;
using static System.Math;
using static Redomine.FloorHeating.Geometry;

namespace Redomine.FloorHeating
{
    internal sealed partial class Search
    {
        private IEnumerable<double> Checkpoints(Point2 point, Point2 direction, double travel, List<Obstacle> other)
        {
            int axis = Abs(direction.X) > Tol ? 0 : 1;
            var pipeOffsets = new List<double>();
            foreach (var obstacle in other)
            {
                var s = obstacle.Segment;
                if (Abs(s.Delta[axis]) > Tol)
                    continue;
                foreach (int sign in new[] { -1, 1 })
                {
                    double d = (s.Start[axis] + sign * spacing - point[axis]) * direction[axis];
                    if (d + Tol >= spacing && d < travel - Tol)
                        pipeOffsets.Add(d);
                }
            }
            // Merge exact obstacle offsets with the spacing grid. Usually the first legal
            // turn ends this iterator long before the far end of the ray.
            pipeOffsets.Sort();
            int pipe = 0;
            double grid = spacing;
            double last = double.NegativeInfinity;
            while (pipe < pipeOffsets.Count || grid + minimum < travel - Tol)
            {
                Poll();
                double distance;
                if (pipe < pipeOffsets.Count && (grid + minimum >= travel - Tol || pipeOffsets[pipe] <= grid))
                    distance = pipeOffsets[pipe++];
                else
                {
                    distance = grid;
                    grid += spacing;
                }
                if (distance - last > Tol)
                {
                    last = distance;
                    yield return last;
                }
            }
        }
        private bool PreservesExit(List<Segment2> trial, Point2 sample, Point2 turn, List<Obstacle> other)
        {
            var partner = other.Where(o => o.Kind == Blocker.Other).Select(o => o.Segment).ToList();
            if (partner.Count == 0)
                return true;
            var end = partner[partner.Count - 1];
            if (end.Length <= Tol)
                return true;
            var obstacles = trial.Select(s => new Obstacle(s, Blocker.Other)).ToList();
            var reserve = new Segment2(sample, sample + turn * spacing);
            bool hadExit = false;
            foreach (var direction in Ordinary(end.Unit, false))
            {
                var d = direction.Vector;
                if (!CanTravel(Probe(end.End, d, partner, obstacles)))
                    continue;
                hadExit = true;
                if (double.IsInfinity(RayObstacle(end.End, d, Abs(d.X) > Tol ? 0 : 1, reserve)))
                    return true;
                obstacles.Add(new Obstacle(reserve, Blocker.Reservation));
                bool available = CanTravel(Probe(end.End, d, partner, obstacles));
                obstacles.RemoveAt(obstacles.Count - 1);
                if (available)
                    return true;
            }
            return !hadExit;
        }
        private List<Direction> SampleDirections(Branch branch, Point2 sample, Point2 heading, ref Anchor? anchor) =>
            Directions(sample, heading, branch.Guided, branch.Rebind, ref anchor, branch.Reserve);

        private bool LeavesPairedBend(Branch branch, Point2 direction, List<Obstacle> other, List<Direction> choices)
        {
            if (branch.Segments.Count == 0)
                return false;
            var prior = branch.Segments[branch.Segments.Count - 1];
            if (!prior.Unit.Near(branch.Heading))
                return false;
            var expected = -direction;
            var bend = PartnerCorner(branch.Side, branch.Tip, branch.Heading, expected);
            int axis = prior.Horizontal ? 0 : 1;
            for (int i = other.Count - 2; i >= 0; i--)
            {
                var a = other[i];
                var b = other[i + 1];
                if (a.Kind != Blocker.Other || b.Kind != Blocker.Other || !a.Segment.End.Near(bend) ||
                    !a.Segment.Unit.Near(branch.Heading) || !b.Segment.Unit.Near(expected) ||
                    Min(High(prior, axis), High(a.Segment, axis)) <= Max(Low(prior, axis), Low(a.Segment, axis)) + Tol)
                    continue;
                // A narrow wall projection can accommodate only the outer
                // branch. Let either branch follow the perimeter independently.
                if (Array.Exists(edges, wall => On(bend, wall) || On(branch.Tip, wall)) ||
                    !choices.Any(d => d.Vector.Near(expected)))
                    return false;
                // The other branch has already rounded this very corner. A
                // forced opposite turn would send the branches into different arms.
                var paired = Probe(branch.Tip, expected, branch.Segments, other);
                return CanTravel(paired) && paired.Available >= spacing - Tol;
            }
            return false;
        }

        private Turn? Walk(Branch b, List<Obstacle> other, List<SpiralEvent> log, int round, double minimumTurnRun = 0)
        {
            if (b.Stop != null)
                return null;
            if (b.Turns >= maxTurns)
            {
                b.Stop = "turn_limit";
                return null;
            }
            var point = b.Tip;
            var choices = Directions(b);
            int wall = -1;
            if (offset == 0 && b.Side == first && b.Guided)
            {
                wall = OutgoingEdge(point);
                if (wall >= 0)
                {
                    var along = tangents[wall];
                    if (!choices.Any(d => d.Vector.Near(along)))
                        choices.AddRange(Ordinary(b.Heading, clockwise).Where(d => d.Vector.Near(along)));
                    choices = choices.OrderByDescending(d => d.Vector.Near(along)).ToList();
                }
            }
            var probes = new Dictionary<Turn, ProbeResult>();
            if (b.Guided)
            {
                foreach (var d in choices)
                    probes[d.Turn] = Probe(point, d.Vector, b.Segments, other);
                if (!probes.Values.Any(CanTravel))
                {
                    var partner = other.LastOrDefault(o => o.Kind == Blocker.Other).Segment;
                    if (partner.Length > Tol)
                        foreach (var d in Ordinary(b.Heading, clockwise))
                            if (!choices.Any(c => c.Turn == d.Turn) && d.Vector.Near(partner.Unit))
                                choices.Add(d);
                }
            }
            Point2? tangent = b.Guided && b.Anchor.HasValue ? tangents[b.Anchor.Value.Edge] : (Point2?)null;
            bool blockedTangent = false;
            var requested = b.Only ?? b.Pending;
            var ordinaryChoices = choices;
            if (b.Pending.HasValue)
            {
                choices = choices.OrderBy(d => d.Turn != b.Pending).ToList();
                b.Pending = null;
            }
            if (b.Only.HasValue)
            {
                choices = choices.Where(d => d.Turn == b.Only).ToList();
                b.Only = null;
            }
            foreach (var choice in choices)
            {
                var direction = choice.Vector;
                var turn = choice.Turn;
                if (!probes.TryGetValue(turn, out var ray))
                    ray = Probe(point, direction, b.Segments, other);
                if (log != null)
                    log.Add(SpiralEvent.Ray(SideName(b.Side), round, TurnName(turn), point, direction, ray));
                if (turn == choices[0].Turn && b.Avoid.HasValue)
                {
                    if (SameObstacle(ray.Obstacle, b.Avoid))
                        continue;
                    b.Avoid = null;
                }
                if (!CanTravel(ray))
                {
                    if (tangent.HasValue && direction.Near(tangent.Value) && ray.Kind != Blocker.Boundary)
                        blockedTangent = true;
                    continue;
                }
                if (turn != Turn.Forward && turn == requested && LeavesPairedBend(b, direction, other, ordinaryChoices))
                    continue;
                bool onWall = wall >= 0 && direction.Near(tangents[wall]);
                double travel = onWall ? Min(ray.Available, (edges[wall].End - point).Dot(direction)) : ray.Available;
                if (onWall && travel < minimum)
                    continue;
                double fallbackDistance = 0;
                Turn? fallback = null;
                bool chosen = false;
                foreach (double at in onWall ? Enumerable.Empty<double>() : Checkpoints(point, direction, travel, other))
                {
                    var sample = point + direction * at;
                    var trial = new List<Segment2>(b.Segments) { new Segment2(point, sample) };
                    Anchor? sampleAnchor = b.Guided ? Project(sample, b.Anchor ?? Project(point), spacing) : (Anchor?)null;
                    var sideChoices = SampleDirections(b, sample, direction, ref sampleAnchor).Where(d => d.Turn != Turn.Forward).ToList();
                    for (int i = 0; i < sideChoices.Count; i++)
                    {
                        var side = sideChoices[i];
                        var nextRay = Probe(sample, side.Vector, trial, other);
                        if (turn == Turn.Forward && i == 0 && b.Avoid.HasValue)
                        {
                            if (SameObstacle(nextRay.Obstacle, b.Avoid))
                                continue;
                            b.Avoid = null;
                        }
                        if (!CanTravel(nextRay))
                            continue;
                        bool obstacleReturn = false, phaseFallback = false;
                        if (b.Guided)
                        {
                            var a = sampleAnchor.Value;
                            var target = sample + side.Vector * Min(spacing, nextRay.Available);
                            var p = Project(target, a, spacing);
                            bool corner = side.Vector.Near(tangents[Next(a.Edge)]) && a.Distance <= 2 * spacing + Tol &&
                                        Project(sample, a, 0, true).Phase <= a.Phase + Tol;
                            obstacleReturn = turn != Turn.Forward && side.Vector.Near(b.Heading) && IsPipe(b.LastBlocker);
                            if (!obstacleReturn && p.Backward)
                                continue;
                            phaseFallback = !obstacleReturn && !corner;
                            if (!PreservesExit(trial, sample, side.Vector, other))
                                continue;
                        }
                        if (b.Turns + (turn == Turn.Forward ? 0 : 1) >= maxTurns || at + Tol < minimumTurnRun)
                            continue;
                        if (phaseFallback || (i == 1 && !obstacleReturn))
                        {
                            if (!fallback.HasValue || at > fallbackDistance + Tol)
                            {
                                fallback = side.Turn;
                                fallbackDistance = at;
                            }
                        }
                        else
                        {
                            travel = at;
                            chosen = true;
                            if (obstacleReturn)
                                b.Pending = side.Turn;
                        }
                        break;
                    }
                    if (chosen)
                        break;
                }
                if (!chosen && fallback.HasValue)
                {
                    var endpoint = point + direction * ray.Available;
                    var trial = new List<Segment2>(b.Segments) { new Segment2(point, endpoint) };
                    Anchor? a = b.Guided ? Project(endpoint, b.Anchor ?? Project(point), spacing) : (Anchor?)null;
                    if (!SampleDirections(b, endpoint, direction, ref a).Any(d => CanTravel(Probe(endpoint, d.Vector, trial, other))))
                    {
                        travel = fallbackDistance;
                        b.Pending = fallback;
                    }
                }
                var end = point + direction * travel;
                var current = b.Anchor ?? Project(point);
                var projected = Project(end, current, spacing);
                if (b.Guided && projected.Backward && !b.Reserve.HasValue)
                {
                    var next = Project(end, current, spacing, true);
                    if (next.Phase + Tol >= current.Phase && next.Distance <= current.Distance + spacing + Tol)
                        projected = next;
                    else
                        projected = Support(end, direction, current) ?? projected;
                }
                Anchor? reserveTarget = b.Reserve.HasValue ? Project(end, b.Reserve, spacing) : (Anchor?)null;
                if (b.Guided && projected.Backward && (!reserveTarget.HasValue || reserveTarget.Value.Backward))
                    continue;
                bool hadReserve = b.Reserve.HasValue, selected = false;
                if (hadReserve)
                {
                    bool keepCurrent = b.ReturnDirection.HasValue && direction.Near(b.ReturnDirection.Value) && !projected.Backward;
                    if (!keepCurrent && ((turn == (clockwise ? Turn.Right : Turn.Left) && !reserveTarget.Value.Backward) || projected.Backward))
                    {
                        b.Anchor = b.Reserve;
                        projected = reserveTarget.Value;
                        selected = true;
                    }
                    else
                        selected = keepCurrent || reserveTarget.Value.Backward || turn != Turn.Forward;
                    if (selected)
                    {
                        b.Reserve = null;
                        b.ReturnDirection = null;
                    }
                }
                var prior = b.Anchor ?? Project(point);
                bool pipeCorner = turn != Turn.Forward && b.Guided && IsPipe(b.LastBlocker) && direction.Near(tangents[Next(prior.Edge)]);
                bool defer = pipeCorner && blockedTangent && chosen && !hadReserve;
                b.Anchor = selected || projected.Edge != prior.Edge ? projected : Project(end, prior, spacing, pipeCorner && !defer);
                if (defer)
                {
                    b.ReturnDirection = tangent;
                    b.Reserve = Project(end, prior, spacing, true);
                }
                else if (hadReserve && b.Reserve.HasValue)
                    b.Reserve = reserveTarget;
                b.Append(end);
                b.Heading = direction;
                bool reached = Abs(travel - ray.Available) <= Tol;
                b.LastBlocker = reached ? ray.Kind : Blocker.Intermediate;
                b.LastObstacle = reached ? ray.Obstacle : null;
                if (turn != Turn.Forward)
                    b.Turns++;
                log?.Add(new SpiralEvent { Action = "segment", Branch = SideName(b.Side), Round = round, Choice = TurnName(turn), Segment = new Segment2(point, end) });
                if (b.Turns >= maxTurns)
                    b.Stop = "turn_limit";
                return turn;
            }
            b.Stop = "all_directions_blocked";
            return null;
        }
        private static bool IsPipe(Blocker? b) => b == Blocker.Other || b == Blocker.Own || b == Blocker.Reservation;
    }
}
