using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using static System.Math;
using static Redomine.FloorHeating.Geometry;

namespace Redomine.FloorHeating
{
    public static class SpiralSolver
    {
        /// <summary>Build, validate, cap and score a two-branch spiral in view-plane millimetres.</summary>
        public static SpiralRoute Calculate(SpiralRequest request) => Calculate(request, null);
        public static SpiralRoute Calculate(SpiralRequest request, Action<string, int> progress)
        {
            var clock = Stopwatch.StartNew();
            if (request == null || request.Polygon == null)
                throw new ArgumentNullException(nameof(request));
            if (!Finite(request.OffsetMm) || request.OffsetMm < 0 || !Finite(request.SpacingMm) || request.SpacingMm <= 0 ||
               !Finite(request.MaximumLengthMm) || request.MaximumLengthMm <= 0 || !Finite(request.MinimumSegmentMm) ||
               request.MinimumSegmentMm < 0 || request.MaximumRounds <= 0 || request.MaximumTurns < 0 || request.TimeBudget <= TimeSpan.Zero)
                throw new ArgumentException("invalid_geometry_parameters");
            foreach (var p in new[] { request.RightStart, request.LeftStart, request.EntryDirection })
                if (!Finite(p.X) || !Finite(p.Y))
                    throw new ArgumentException("invalid_inlet_coordinates");
            var n = request.EntryDirection;
            if (!(Abs(n.X) == 1 && n.Y == 0 || Abs(n.Y) == 1 && n.X == 0))
                throw new ArgumentException("entry_direction_must_be_orthogonal_unit_vector");
            return new Search(request).Calculate(clock, progress);
        }
    }
    internal sealed partial class Search
    {
        private Stopwatch clock;
        private int checks;
        private VariantResult metrics;
        private SpiralRoute safeFinish;
        private double[] requiredBoundary;
        private bool guardHeading;
        private sealed class SearchExpired : Exception
        {
        }
        private void Poll(bool force = false)
        {
            if ((force || (++checks & 127) == 0) && clock.Elapsed >= timeBudget)
                throw new SearchExpired();
        }
        internal static bool Better(SpiralRoute a, SpiralRoute b)
        {
            if (a == null)
                return false;
            if (b == null)
                return true;
            if (a.Connected != b.Connected)
                return a.Connected;
            return a.Coverage.CoveredAreaMm2 > b.Coverage.CoveredAreaMm2 + Tol * Tol;
        }
        private double[] VisibleBoundary(SpiralRoute r) => BoundaryCoverage(new Pair(
            new Branch(0, r.Right.ToList(), normal), new Branch(1, r.Left.ToList(), normal)));
        // Keep a directly drawable, independent incumbent before starting more speculative work.
        private void RememberFinish(Candidate candidate)
        {
            var shown = Display(candidate, false);
            if (shown?.Connected != true || !Better(shown, safeFinish) ||
               (guardHeading && !PairedHeading(shown)) ||
               (requiredBoundary != null && !Preserves(VisibleBoundary(shown), requiredBoundary)))
                return;
            safeFinish = Display(candidate);
            safeFinish.Policy = metrics.Policy;
        }
        internal SpiralRoute Calculate(Stopwatch timer, Action<string, int> progress)
        {
            clock = timer;
            SpiralRoute best = null;
            double[] required = null;
            var attempts = new List<VariantResult>();
            bool timedOut = false;
            var policies = new[] { "contour_progress", "heading_priority", "inner_support" };
            for (int index = 0; index < policies.Length; index++)
            {
                metrics = new VariantResult { Policy = policies[index] };
                attempts.Add(metrics);
                guardHeading = index == 1 && best?.Connected == true;
                requiredBoundary = best?.Connected == true ? required : null;
                var started = clock.Elapsed;
                try
                {
                    Poll(true);
                    progress?.Invoke(metrics.Policy, index + 1);
                    var candidate = Build(index != 1, index == 2);
                    if (candidate?.Join == null)
                    {
                        metrics.Reason = "no_valid_orthogonal_join";
                        continue;
                    }
                    candidate = FinalizeCandidate(candidate);
                    var result = Display(candidate);
                    if (result == null)
                    {
                        metrics.Reason = "invalid_visible_geometry";
                        continue;
                    }
                    result.Policy = metrics.Policy;
                    metrics.Connected = result.Connected;
                    metrics.Coverage = result.Coverage;
                    metrics.LengthMm = result.LengthMm;
                    metrics.FullLengthMm = result.FullLengthMm;
                    if (guardHeading && !PairedHeading(result))
                    {
                        metrics.Reason = "unpaired_heading_route";
                        continue;
                    }
                    var boundary = VisibleBoundary(result);
                    if (best != null && best.Connected == result.Connected && required != null && !Preserves(boundary, required))
                    {
                        metrics.Reason = "boundary_coverage_lost";
                        continue;
                    }
                    if (!Better(result, best))
                    {
                        metrics.Reason = "no_additional_coverage";
                        continue;
                    }
                    if (best == null || best.Connected != result.Connected)
                        required = boundary;
                    best = result;
                    if (result.Connected && result.Coverage.Ratio >= 0.95)
                        break;
                }
                catch (SearchExpired) { timedOut = true; metrics.Reason = "time_limit"; break; }
                finally { metrics.Seconds = (clock.Elapsed - started).TotalSeconds; }
            }
            if (Better(safeFinish, best) && (best?.Connected != true || required == null ||
                                            Preserves(VisibleBoundary(safeFinish), required)))
            {
                best = safeFinish;
                var saved = attempts.Find(a => a.Policy == best.Policy);
                saved.Connected = true;
                saved.LengthMm = best.LengthMm;
                saved.FullLengthMm = best.FullLengthMm;
                saved.Coverage = best.Coverage;
                saved.Reason = timedOut ? "time_limit_saved_finish" : "saved_connected_finish";
            }
            if (timedOut && best?.Connected != true)
                throw new TimeoutException("За отведённое время замыкание не найдено.");
            if (best == null)
                throw new InvalidOperationException("Не удалось построить допустимое замыкание улитки.");
            best.TimeLimitHit = timedOut;
            foreach (var a in attempts)
                a.Selected = a.Policy == best.Policy;
            best.Attempts = attempts;
            return best;
        }
        private bool PairedHeading(SpiralRoute route)
        {
            var l = route.Left;
            var r = route.Right;
            for (int i = 1; i < Min(l.Length, r.Length) - 2; i++)
            {
                var a = new Segment2(l[i], l[i + 1]);
                var b = new Segment2(r[i], r[i + 1]);
                if (!a.Unit.Near(b.Unit) || Abs(Clearance(a, b) - spacing) > Tol)
                    return false;
            }
            return true;
        }
        private Point2[] EntryTargets(Point2[] starts)
        {
            var a = Entry(starts[first], normal, offset, polygon);
            var b = Entry(starts[second], normal, offset + spacing, polygon);
            var result = new Point2[2];
            if (Inside(a, contour) && Inside(b, contour))
            {
                result[first] = a;
                result[second] = b;
                return result;
            }
            var inward = -Ordinary(normal, clockwise)[0].Vector;
            int cross = Abs(normal.Y) > Tol ? 0 : 1, along = 1 - cross;
            if (!Inside(a, contour))
            {
                var candidates = Segments(contour, true).Where(s => Abs(s.Delta[cross]) <= Tol && a[along] >= Low(s, along) - Tol &&
                    a[along] <= High(s, along) + Tol && (s.Start[cross] - a[cross]) * inward[cross] >= -Tol)
                    .Select(s => a.With(cross, s.Start[cross])).Where(p => Inside(p, contour)).OrderBy(p => (p - a).Length).ToList();
                if (candidates.Count == 0)
                    throw new ArgumentException("entry_has_no_offset_contour_on_inward_side");
                a = candidates[0];
            }
            b = b.With(cross, a[cross] + inward[cross] * spacing);
            if (!Inside(b, contour))
                throw new ArgumentException("paired_entry_spacing_outside_offset_contour");
            result[first] = a;
            result[second] = b;
            return result;
        }
        private Pair Initial(bool guided, bool rebind, List<SpiralEvent> log)
        {
            var starts = new[] { rightStart, leftStart };
            var targets = EntryTargets(starts);
            if (guided)
            {
                var a = Project(targets[first]);
                var tangent = tangents[a.Edge];
                double distance = Boundary(targets[first], tangent, contour);
                if (a.Distance <= Tol && distance > minimum + Tol && distance < spacing - Tol)
                {
                    var n = Project(targets[first] + tangent * distance, a, spacing, true);
                    if (n.Along <= Tol && n.Distance <= Tol)
                    {
                        starts[0] += tangent * distance;
                        starts[1] += tangent * distance;
                        targets = EntryTargets(starts);
                    }
                }
            }
            Branch Make(int side)
            {
                var start = starts[side];
                var target = targets[side];
                var lateral = target - normal * ((target - start).Dot(normal));
                var path = new List<Point2> { start };
                if (!start.Near(lateral))
                    path.Add(lateral);
                if (!path.Last().Near(target))
                    path.Add(target);
                log.Add(new SpiralEvent { Action = "entry", Branch = SideName(side), Segment = new Segment2(start, target) });
                var b = new Branch(side, path, normal) { Guided = guided, Rebind = rebind };
                foreach (var s in b.Segments)
                    log.Add(new SpiralEvent { Action = "entry_segment", Branch = SideName(side), Segment = s });
                if (guided)
                    b.Anchor = Project(b.Tip);
                return b;
            }
            return new Pair(Make(0), Make(1));
        }
        private Candidate Build(bool guided, bool rebind)
        {
            var log = new List<SpiralEvent>();
            var p = Initial(guided, rebind, log);
            var snapshots = new List<(int round, Pair pair)>();
            Candidate last = null, completed = null;
            int lastClosedRound = 0, lastTriedRound = 0;
            for (int round = 1; round <= maxRounds; round++)
            {
                Poll();
                lastTriedRound = round;
                var move = MovePair(p, log, round, always: true, extras: true);
                bool a = move.leader.HasValue, b = move.follower.HasValue;
                if (a && b)
                {
                    snapshots.Add((round, p.Clone()));
                    var trial = p.Clone();
                    var join = Finish(trial);
                    if (join != null)
                    {
                        last = new Candidate(trial, join, log);
                        lastClosedRound = round;
                        RememberFinish(last);
                    }
                    if (p.Right.Turns < maxTurns && p.Left.Turns < maxTurns && Tight(p) && !HasNext(p, round))
                    {
                        completed = Complete(p, snapshots, log, round);
                        if (completed != null)
                            break;
                    }
                }
                if (!a || !b || (p.Right.Stop != null && p.Left.Stop != null))
                    break;
                if (round == maxRounds)
                {
                    p.Right.Stop = p.Right.Stop ?? "round_limit";
                    p.Left.Stop = p.Left.Stop ?? "round_limit";
                }
            }
            if (completed == null)
            {
                var join = Finish(p);
                if (join != null)
                    completed = new Candidate(p, join, log);
                else
                {
                    var rec = Recoveries(snapshots, log, true).FirstOrDefault();
                    if (rec != null)
                    {
                        AddRecoveryLog(log, rec);
                        completed = Continue(rec.Pair, log, rec.Round + 2);
                    }
                    if (completed?.Join == null && last != null)
                    {
                        completed = last;
                        last.Pair.Right.Stop = last.Pair.Left.Stop = "pair_rollback";
                        log.Add(new SpiralEvent
                        {
                            Action = "pair_rollback",
                            Recovery = new RecoveryDetail
                            {
                                Reason = "last_connectable_pair", FromRound = lastTriedRound, ToRound = lastClosedRound
                            }
                        });
                    }
                }
            }
            if (completed?.Join != null)
                Simplify(completed, true);
            return completed;
        }
    }
}
