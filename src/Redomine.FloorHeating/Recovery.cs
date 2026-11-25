using System;
using System.Collections.Generic;
using System.Linq;
using static System.Math;
using static Redomine.FloorHeating.Geometry;

namespace Redomine.FloorHeating
{
    internal sealed partial class Search
    {
        private sealed class Recovery
        {
            internal Pair Pair, Source;
            internal int Round;
            internal Turn Turn;
            internal List<SpiralEvent> Log;
            internal SpiralEvent Event;
            internal Recovery(Pair pair, Pair source, int round, Turn turn, List<SpiralEvent> log,
                              string action, RecoveryDetail detail)
            {
                Pair = pair;
                Source = source;
                Round = round;
                Turn = turn;
                Log = log;
                detail.FromRound = round;
                detail.ToRound = round + 1;
                detail.Turn = TurnName(turn);
                Event = new SpiralEvent { Action = action, Recovery = detail };
            }
        }
        private struct RecoveryPoint
        {
            internal double Distance;
            internal Turn? Turn;
            internal int? Side;
            internal Segment2? Edge;
        }
        private sealed class Budget
        {
            internal int Remaining;
            internal Budget(int remaining)
            {
                Remaining = remaining;
            }
            internal bool Take()
            {
                if (Remaining <= 0)
                    return false;
                Remaining--;
                return true;
            }
        }
        private List<Obstacle> FollowerObstacles(Pair p, bool extra = false)
        {
            var leader = p[first];
            var partner = Obstacles(p[second]);
            var reserve = Reservation(leader, partner);
            var obstacles = Obstacles(leader);
            AddReservation(obstacles, reserve);
            if (extra && reserve.HasValue && leader.Guided && !leader.Pending.HasValue)
                foreach (var d in Directions(leader))
                {
                    if (d.Turn == Turn.Forward || d.Vector.Equals(reserve.Value.Unit))
                        continue;
                    var ray = Probe(leader.Tip, d.Vector, leader.Segments, partner);
                    if (CanTravel(ray))
                        AddReservation(obstacles, new Segment2(leader.Tip, leader.Tip + d.Vector * Min(spacing, ray.Available)));
                }
            return obstacles;
        }
        private (Turn? leader, Turn? follower) MovePair(Pair p, List<SpiralEvent> log, int round, bool always = false, bool extras = false, bool reserve = true)
        {
            var leader = Walk(p[first], Obstacles(p[second]), log, round);
            if (!leader.HasValue && !always)
                return (leader, null);
            var follower = Walk(p[second], reserve ? FollowerObstacles(p, extras) : Obstacles(p[first]), log, round);
            return (leader, follower);
        }
        private bool Tight(Pair p)
        {
            bool tight = false;
            for (int side = 0; side < 2; side++)
            {
                var b = p[side];
                var other = Obstacles(p[1 - side]);
                double width = Probe(b.Tip, b.Heading.Right, b.Segments, other).Available +
                             Probe(b.Tip, b.Heading.Left, b.Segments, other).Available;
                bool narrow = width + Tol < 3 * spacing;
                if (b.Returning.HasValue && b.Heading.Dot(b.Returning.Value) < -Tol && !narrow)
                    b.Returning = null;
                if (narrow && !b.Returning.HasValue)
                    tight = true;
            }
            return tight;
        }
        private bool HasNext(Pair p, int round)
        {
            var t = p.Clone();
            var move = MovePair(t, null, round + 1);
            return move.leader.HasValue && move.follower.HasValue && Finish(t, false) != null && !Tight(t);
        }
        private Candidate Terminal(Pair p, int round)
        {
            var t = p.Clone();
            var log = new List<SpiralEvent>();
            var move = MovePair(t, log, round + 1, reserve: false);
            if (!move.leader.HasValue || !move.follower.HasValue || t.Right.Turns != t.Left.Turns)
                return null;
            if (t.Right.Turns < maxTurns && t.Left.Turns < maxTurns)
                Tight(t);
            var join = Finish(t);
            return join == null ? null : new Candidate(t, join, log);
        }
        private IEnumerable<RecoveryPoint> RecoveryPoints(Pair p, List<Direction> directions)
        {
            var b = p[first];
            var seg = b.Segments.Last();
            int axis = Abs(b.Heading.X) > Tol ? 0 : 1, cross = 1 - axis;
            var sources = new List<(Segment2 segment, int? side)>();
            foreach (var branch in new[] { p.Right, p.Left })
                for (int i = 0; i < branch.Segments.Count - 1; i++)
                    sources.Add((branch.Segments[i], branch.Side));
            sources.AddRange(Segments(contour, true).Select(s => (s, (int?)null)));
            var anchors = new List<(RecoveryPoint point, int rank)>();
            int rank = 0;
            foreach (var d in directions.Where(x => x.Turn != Turn.Forward))
            {
                foreach (var source in sources)
                {
                    var s = source.segment;
                    if (Abs(s.Delta[axis]) > Tol || Max((s.Start[cross] - seg.Start[cross]) * d.Vector[cross],
                                                     (s.End[cross] - seg.Start[cross]) * d.Vector[cross]) <= Tol)
                        continue;
                    double clearance = source.side.HasValue ? spacing : 0;
                    double distance = (s.Start[axis] - b.Heading[axis] * clearance - seg.Start[axis]) * b.Heading[axis];
                    anchors.Add((new RecoveryPoint { Distance = distance, Turn = d.Turn, Edge = s, Side = source.side }, rank));
                }
                rank++;
            }
            var result = new List<RecoveryPoint>();
            foreach (var a in anchors.OrderByDescending(x => x.point.Distance).ThenBy(x => x.rank))
                if (!result.Any(x => x.Turn == a.point.Turn && Abs(x.Distance - a.point.Distance) <= Tol))
                    result.Add(a.point);
            double limit = seg.Length + (b.LastBlocker == Blocker.Boundary ? Tol : -Tol);
            IEnumerable<RecoveryPoint> Enumerate()
            {
                foreach (var point in result)
                    yield return point;
                for (double n = Floor(limit / spacing); n >= 1; n--)
                {
                    Poll();
                    yield return new RecoveryPoint { Distance = n * spacing };
                }
            }
            return Enumerate();
        }
        private bool Recoverable(double d, Branch b)
        {
            double length = b.Segments.Last().Length;
            return d > minimum + Tol && (b.LastBlocker == Blocker.Boundary ? d <= length + Tol : d < length - minimum - Tol);
        }
        private IEnumerable<Recovery> SideRecoveries(Pair p, int round, Direction direction, IEnumerable<RecoveryPoint> points)
        {
            var a = p[first];
            var b = p[second];
            int axis = Abs(a.Heading.X) > Tol ? 0 : 1;
            var seen = new HashSet<(Point2, Point2)>();
            foreach (var item in points)
            {
                if ((item.Turn.HasValue && item.Turn != direction.Turn) || !Recoverable(item.Distance, a))
                    continue;
                var pa = a.Segments.Last().Start + a.Heading * item.Distance;
                double distance = (pa[axis] - a.Heading[axis] * spacing - b.Segments.Last().Start[axis]) * b.Heading[axis];
                if (!Recoverable(distance, b))
                    continue;
                var pb = b.Segments.Last().Start + b.Heading * distance;
                if (!seen.Add((pa, pb)))
                    continue;
                var t = p.Clone();
                foreach (int side in new[] { first, second })
                {
                    var branch = t[side];
                    branch.SetTip(side == first ? pa : pb);
                    branch.Anchor = Project(branch.Tip);
                    branch.Stop = null;
                    branch.Pending = direction.Turn;
                }
                var log = new List<SpiralEvent>();
                var moved = MovePair(t, log, round + 1);
                if (moved.leader == direction.Turn && moved.follower == direction.Turn && Finish(t, false) != null)
                    yield return new Recovery(t, p, round, direction.Turn, log, "pair_turn_recovery", new RecoveryDetail
                    {
                        Reason = "paired_turn_recovery",
                        FirstCheckpointMm = item.Distance,
                        SecondCheckpointMm = distance,
                        FirstLimitKind = SpiralEvent.BlockerName(a.LastBlocker),
                        SecondLimitKind = SpiralEvent.BlockerName(b.LastBlocker),
                        CheckpointSource = item.Side.HasValue ? "pipe_edge" : item.Edge.HasValue ? "contour_edge" : "segment_grid",
                        AnchorBranch = item.Side.HasValue ? SideName(item.Side.Value) : null,
                        AnchorSegment = item.Edge
                    });
            }
        }
        private Recovery ForwardRecovery(Pair p, int round)
        {
            var t = p.Clone();
            if (t.Right.Turns >= maxTurns || t.Left.Turns >= maxTurns)
                return null;
            t.Right.Stop = t.Left.Stop = null;
            t.Right.Pending = t.Left.Pending = Turn.Forward;
            var log = new List<SpiralEvent>();
            var moved = MovePair(t, log, round + 1);
            if (moved.leader != Turn.Forward || moved.follower != Turn.Forward)
                return null;
            for (int side = 0; side < 2; side++)
            {
                var prior = p[side];
                var directions = Directions(prior);
                if (directions.Count > 0)
                    t[side].Avoid = Probe(prior.Tip, directions[0].Vector, prior.Segments, Obstacles(p[1 - side])).Obstacle;
            }
            return new Recovery(t, p, round, Turn.Forward, log, "pair_forward_recovery",
                                new RecoveryDetail { Reason = "paired_forward_recovery" });
        }
        private IEnumerable<Recovery> Recoveries(List<(int round, Pair pair)> snapshots, List<SpiralEvent> history,
                                                bool requireJoin, HashSet<int> exhausted = null)
        {
            var rejected = new HashSet<(int, string)>(history.Where(e => e.Action == "segment" && e.Branch == SideName(first))
                                                   .Select(e => (e.Round, e.Choice)));
            for (int i = snapshots.Count - 1; i >= 0; i--)
            {
                var (round, saved) = snapshots[i];
                if (exhausted != null && exhausted.Contains(round))
                    continue;
                var p = saved.Clone();
                var a = p[first];
                var b = p[second];
                var directions = Directions(a);
                var followers = Directions(b).Select(x => (Turn?)x.Turn).ToList();
                followers.Insert(0, null);
                foreach (var d in directions)
                {
                    if (rejected.Contains((round + 1, TurnName(d.Turn))))
                        continue;
                    var lead = a.Clone();
                    lead.Stop = null;
                    lead.Pending = lead.Only = d.Turn;
                    var leadLog = new List<SpiralEvent>();
                    var partner = Obstacles(b);
                    if (Walk(lead, partner, leadLog, round + 1) != d.Turn)
                        continue;
                    var reserve = Reservation(lead, partner);
                    var obstacles = Obstacles(lead);
                    AddReservation(obstacles, reserve);
                    foreach (var turn in followers)
                    {
                        var follow = b.Clone();
                        follow.Stop = null;
                        if (turn.HasValue)
                            follow.Only = turn;
                        var log = new List<SpiralEvent>(leadLog);
                        if (!Walk(follow, obstacles, log, round + 1).HasValue)
                            continue;
                        var t = first == 0 ? new Pair(lead.Clone(), follow) : new Pair(follow, lead.Clone());
                        if (!requireJoin || Finish(t, false) != null)
                            yield return new Recovery(t, p, round, d.Turn, log, "pair_vertex_recovery", new RecoveryDetail
                            {
                                Reason = "paired_vertex_recovery", PartnerTurn = turn.HasValue ? TurnName(turn.Value) : null
                            });
                    }
                }
                var points = (a.Segments.Count > 0 && b.Segments.Count > 0 && Max(a.Turns, b.Turns) < maxTurns && a.Heading.Near(b.Heading))
                    ? RecoveryPoints(p, directions) : null;
                foreach (var d in directions)
                {
                    if (d.Turn == Turn.Forward)
                    {
                        var f = ForwardRecovery(p, round);
                        if (f != null)
                            yield return f;
                    }
                    else if (points != null)
                    {
                        foreach (var t in SideRecoveries(p, round, d, points))
                            yield return t;
                    }
                }
                exhausted?.Add(round);
            }
        }
        private void AddRecoveryLog(List<SpiralEvent> target, Recovery recovery)
        {
            target.Add(recovery.Event);
            foreach (var e in recovery.Log)
                target.Add(e.AsRecovery());
        }
        private (Pair pair, Pair snapshot, List<SpiralEvent> log) LeadAndCatch(Pair p, int round)
        {
            var t = p.Clone();
            var log = new List<SpiralEvent>();
            foreach (int side in new[] { first, second })
            {
                var b = t[side];
                var other = side == first ? Obstacles(t[second]) : FollowerObstacles(t);
                var ray = Probe(b.Tip, b.Heading, b.Segments, other);
                if (!CanTravel(ray))
                    return (null, null, null);
                b.Pending = Turn.Forward;
                if (Walk(b, other, log, round, ray.Available) != Turn.Forward)
                    return (null, null, null);
            }
            var paired = t.Clone();
            return Walk(t[second], FollowerObstacles(t), log, round + 1).HasValue ? (t, paired, log) : (null, null, null);
        }

        private Candidate Complete(Pair p, List<(int round, Pair pair)> snapshots, List<SpiralEvent> history, int round, Budget budget = null)
        {
            double Normalized(Candidate c)
            {
                if (c?.Join == null)
                    return -1;
                var copy = new Candidate(c.Pair.Clone(), c.Join, new List<SpiralEvent>());
                Simplify(copy);
                return Length(copy.Pair, copy.Join);
            }
            var closed = p.Clone();
            var best = new Candidate(closed, Finish(closed), new List<SpiralEvent>());
            double bestLength = Normalized(best);
            var terminal = Terminal(p, round);
            double terminalLength = Normalized(terminal);
            if (terminal != null && terminalLength >= bestLength)
            {
                best = terminal;
                bestLength = terminalLength;
            }
            var boundary = BoundaryCoverage(p);
            var states = new Dictionary<(int, Point2, Point2), List<Pair>>();
            budget = budget ?? new Budget(maxRounds);
            foreach (var recovery in Recoveries(snapshots, history, false))
            {
                if (budget.Remaining <= 0)
                    break;
                var t = recovery.Pair;
                if (recovery.Turn != Turn.Forward)
                {
                    for (int side = 0; side < 2; side++)
                        t[side].Returning = recovery.Source[side].Heading;
                }
                int start = recovery.Round + 2;
                // The tips select a bucket; only equality of both complete branch states
                // permits skipping a continuation. A different blocker can change the next turn.
                var key = (start, t[first].Tip, t[second].Tip);
                if (!states.TryGetValue(key, out var bucket))
                    states[key] = bucket = new List<Pair>();
                if (bucket.Any(x => x.SameState(t)))
                {
                    metrics.DuplicateContinuations++;
                    continue;
                }
                bucket.Add(t.Clone());
                budget.Take();
                metrics.Recoveries++;
                var log = new List<SpiralEvent>(history);
                AddRecoveryLog(log, recovery);
                var c = Continue(t, log, start, false, new Budget(maxRounds));
                if (c.Join == null || !Clear(c.Pair.Right.Vertices, c.Pair.Left.Vertices, c.Join.Bridge) ||
                   !Preserves(BoundaryCoverage(c.Pair), boundary))
                    continue;
                // A shorter recovery may close within the user's length limit even
                // when a longer continuation is preferred for further growth.
                RememberFinish(c);
                double length = Normalized(c);
                if (length > bestLength + Tol)
                {
                    bestLength = length;
                    best = new Candidate(c.Pair, c.Join, log.GetRange(history.Count, log.Count - history.Count));
                }
            }
            if (best.Join == null)
                return null;
            history.AddRange(best.Log);
            return new Candidate(best.Pair, best.Join, history);
        }

        private Candidate Continue(Pair p, List<SpiralEvent> log, int start, bool alternatives = true, Budget budget = null)
        {
            var snapshots = new List<(int round, Pair pair)> { (start - 1, p.Clone()) };
            var exhausted = new HashSet<int>();
            var last = p.Clone();
            var lastJoin = Finish(last);
            void Save(Pair trial, Join join)
            {
                if (join != null)
                {
                    last = trial;
                    lastJoin = join;
                }
            }
            if (start < maxRounds && p[first].Returning.HasValue && p[second].Returning.HasValue)
            {
                var lead = LeadAndCatch(p, start);
                if (lead.pair != null)
                {
                    p = lead.pair;
                    snapshots.Add((start, lead.snapshot));
                    log.AddRange(lead.log);
                    var trial = p.Clone();
                    Save(trial, Finish(trial));
                    start += 2;
                }
            }
            bool diverged = false;
            for (int round = start; round <= maxRounds; round++)
            {
                if (budget != null && !budget.Take())
                    break;
                var move = MovePair(p, log, round, always: true);
                bool a = move.leader.HasValue, b = move.follower.HasValue;
                if (a && b)
                    snapshots.Add((round, p.Clone()));
                var trial = p.Clone();
                var join = Finish(trial);
                if (join != null && a != b && p[first].Turns == p[second].Turns &&
                    !Walk(p[a ? first : second].Clone(), Obstacles(p[a ? second : first]), null, round + 1).HasValue)
                {
                    var leader = trial[first];
                    var tip = trial[second].Tip;
                    var turn = clockwise ? p[first].Heading.Right : p[first].Heading.Left;
                    double distance = (tip - leader.Tip).Dot(turn);
                    if (distance > minimum + Tol && (leader.Tip + turn * distance).Near(tip))
                    {
                        var direct = trial.Clone();
                        direct[first].Append(tip);
                        if (Clear(direct.Right.Vertices, direct.Left.Vertices, null, true))
                        {
                            direct[first].Turns++;
                            trial = direct;
                            join = new Join("terminal_turn");
                        }
                    }
                    return new Candidate(trial, join, log);
                }
                if (a || b)
                    Save(trial, join);
                bool tight = a && b && p.Right.Turns < maxTurns && p.Left.Turns < maxTurns && Tight(p);
                if (tight || !(a && b))
                {
                    if (HasNext(p, round))
                        tight = false;
                    else
                    {
                        if (alternatives && (budget == null || !(a && b)))
                        {
                            var complete = Complete(p, snapshots, log, round, budget);
                            if (complete != null)
                                return complete;
                        }
                        var terminal = Terminal(p, round);
                        if (terminal != null)
                        {
                            log.AddRange(terminal.Log);
                            return new Candidate(terminal.Pair, terminal.Join, log);
                        }
                    }
                }
                if (tight)
                {
                    var rec = Recoveries(snapshots, log, true, exhausted).FirstOrDefault();
                    if (rec != null)
                    {
                        if (rec.Turn != Turn.Forward)
                        {
                            for (int side = 0; side < 2; side++)
                                rec.Pair[side].Returning = p[side].Heading;
                        }
                        AddRecoveryLog(log, rec);
                        p = rec.Pair;
                        snapshots.Clear();
                        exhausted.Clear();
                        var copy = p.Clone();
                        Save(copy, Finish(copy));
                        continue;
                    }
                }
                if (!b || (p[first].Stop != null && p[second].Stop != null))
                {
                    diverged = a != b;
                    break;
                }
            }
            var final = Finish(p);
            if (final != null && !diverged)
                return new Candidate(p, final, log);
            if (lastJoin != null)
            {
                last.Right.Stop = last.Left.Stop = "pair_rollback";
                return new Candidate(last, lastJoin, log);
            }
            return new Candidate(p, null, log);
        }
    }
}
