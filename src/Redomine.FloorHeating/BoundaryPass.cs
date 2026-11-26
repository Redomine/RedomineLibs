using System.Collections.Generic;
using System.Linq;
using static System.Math;
using static Redomine.FloorHeating.Geometry;

namespace Redomine.FloorHeating
{
    internal sealed partial class Search
    {
        private readonly Dictionary<(Point2 right, Point2 left), SpiralRoute> outerPasses =
            new Dictionary<(Point2, Point2), SpiralRoute>();

        private void KeepOuterPass(SpiralRoute route)
        {
            if (!route.Connected)
                return;
            var key = (route.Right[0], route.Left[0]);
            if (outerPasses.TryGetValue(key, out var previous))
            {
                double gain = route.Coverage.BoundaryLengthsMm.Sum() - previous.Coverage.BoundaryLengthsMm.Sum();
                if (gain < -Tol || (Abs(gain) <= Tol && !Better(route, previous)))
                    return;
            }
            outerPasses[key] = route;
        }

        private static Point2? FirstContact(Segment2 a, Segment2 b)
        {
            double x0 = Max(Low(a, 0), Low(b, 0)), x1 = Min(High(a, 0), High(b, 0));
            double y0 = Max(Low(a, 1), Low(b, 1)), y1 = Min(High(a, 1), High(b, 1));
            if (x0 > x1 + Tol || y0 > y1 + Tol)
                return null;
            return new Point2(a.Delta.X < -Tol ? x1 : x0, a.Delta.Y < -Tol ? y1 : y0);
        }

        private static List<Point2> SpliceAt(Point2[] donor, int i, Point2 hit, Point2[] full, int j)
        {
            var path = new List<Point2>(donor.Take(i));
            if (!path[path.Count - 1].Near(hit))
                path.Add(hit);
            path.AddRange(full.Skip(path[path.Count - 1].Near(full[j]) ? j + 1 : j));
            path = SimplifyCollinear(path);
            var segments = Segments(path).ToArray();
            for (int k = 1; k < segments.Length; k++)
                if (segments[k - 1].Unit.Dot(segments[k].Unit) < -1 + Tol)
                    return null;
            return path;
        }

        // Follow the donor's outer pass until it first rejoins the fuller
        // branch. Keeping the suffix also keeps the already validated closure.
        private List<Point2> BoundaryDetour(Point2[] donor, Point2[] full, double spare)
        {
            if (!donor[0].Near(full[0]))
                return null;
            double alongDonor = 0;
            for (int i = 1; i < donor.Length; i++)
            {
                Poll();
                var a = new Segment2(donor[i - 1], donor[i]);
                double alongFull = 0;
                for (int j = 1; j < full.Length; j++)
                {
                    var b = new Segment2(full[j - 1], full[j]);
                    var hit = FirstContact(a, b);
                    if (hit.HasValue)
                    {
                        double extra = alongDonor + (hit.Value - a.Start).Length - alongFull - (hit.Value - b.Start).Length;
                        if (extra > Tol && extra <= spare + Tol)
                        {
                            var path = SpliceAt(donor, i, hit.Value, full, j);
                            if (path != null)
                                return path;
                        }
                    }
                    alongFull += b.Length;
                }
                alongDonor += a.Length;
            }
            return null;
        }

        private SpiralRoute RestoreBoundary(SpiralRoute full, SpiralRoute donor)
        {
            if (full?.Connected != true || donor?.Connected != true ||
                full.Coverage.CoveredAreaMm2 <= donor.Coverage.CoveredAreaMm2 + Tol * Tol ||
                Preserves(full.Coverage.BoundaryLengthsMm, donor.Coverage.BoundaryLengthsMm))
                return full;

            double spare = maximumLength - full.LengthMm;
            var right = BoundaryDetour(donor.Right, full.Right, spare);
            var left = BoundaryDetour(donor.Left, full.Left, spare);
            if (right == null && left == null)
                return full;

            var log = full.Journal.Where(e => e.Action != "abandoned_segment").ToList();
            log.Add(new SpiralEvent { Action = "perimeter_pass_restored" });
            var join = new Join(full.JoinMethod, full.Bridge);
            SpiralRoute best = full;
            void Try(List<Point2> r, List<Point2> l)
            {
                var firstBranch = new Branch(0, r, normal) { Turns = CountTurns(r), Stop = full.RightStop };
                var secondBranch = new Branch(1, l, normal) { Turns = CountTurns(l), Stop = full.LeftStop };
                var candidate = new Candidate(new Pair(firstBranch, secondBranch), join, log);
                var route = Display(candidate, false);
                if (route?.Connected == true && Better(route, best) &&
                    Preserves(route.Coverage.BoundaryLengthsMm, full.Coverage.BoundaryLengthsMm) &&
                    !Preserves(full.Coverage.BoundaryLengthsMm, route.Coverage.BoundaryLengthsMm))
                {
                    best = Display(candidate);
                    best.Policy = full.Policy;
                }
            }
            if (right != null && left != null)
                Try(right, left);
            if (right != null)
                Try(right, full.Left.ToList());
            if (left != null)
                Try(full.Right.ToList(), left);
            return best;
        }
    }
}
