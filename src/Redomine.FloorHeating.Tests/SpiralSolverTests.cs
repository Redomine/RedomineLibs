using System;
using System.Linq;
using Xunit;

namespace Redomine.FloorHeating.Tests
{
    public class SpiralSolverTests
    {
        private static Point2 P(double x, double y) => new Point2(x, y);

        private static SpiralRequest Room() => new SpiralRequest
        {
            Polygon = new[] { P(0, 0), P(4200, 0), P(4200, 3000), P(0, 3000) },
            RightStart = P(2050, 100),
            LeftStart = P(1850, 100),
            EntryDirection = P(0, 1),
            OffsetMm = 200,
            SpacingMm = 200,
            MinimumSegmentMm = 0.8
        };

        [Fact]
        public void ClosureRequiresEndpointsOrMatchingBridgeInEitherOrder()
        {
            var right = P(100, 500);
            var left = P(300, 500);
            Assert.False(Search.EndsMeet(right, left, null));
            Assert.True(Search.EndsMeet(right, right, null));
            Assert.True(Search.EndsMeet(right, left, new Segment2(right, left)));
            Assert.True(Search.EndsMeet(right, left, new Segment2(left, right)));
            Assert.False(Search.EndsMeet(right, left, new Segment2(P(99, 500), left)));
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void ReturnedCircuitHasTheReportedLengthCoverageAndBoundaryHits(bool clockwise)
        {
            var request = Room();
            request.Clockwise = clockwise;
            var result = SpiralSolver.Calculate(request);
            Assert.True(result.Connected);
            Assert.True(Search.EndsMeet(result.Right.Last(), result.Left.Last(), result.Bridge));
            double length = Geometry.Segments(result.Right).Concat(Geometry.Segments(result.Left)).Sum(s => s.Length)
                            + (result.Bridge?.Length ?? 0);
            Assert.Equal(length, result.LengthMm, 7);
            Assert.Equal((4200 - 400) * (3000 - 400), result.Coverage.WorkingAreaMm2, 6);
            Assert.InRange(result.Coverage.Ratio, 0.0, 1.0);
            var probes = result.Journal.Where(e => e.Action.EndsWith("direction_probe")).ToList();
            Assert.Contains(probes, e => e.BlockerSegment.HasValue);
            foreach (var e in probes)
                Assert.True(e.BoundaryHit.Near(e.Origin + e.Direction * e.BoundaryMm));
            var recovery = result.Journal.LastOrDefault(e => e.Recovery != null);
            Assert.Same(recovery?.Recovery, result.Rollback);
        }

        [Fact]
        public void LengthCapProducesOnlyThePairedVisiblePrefix()
        {
            var request = Room();
            request.MaximumLengthMm = 450;
            var result = SpiralSolver.Calculate(request);
            Assert.True(result.LengthLimitHit);
            Assert.False(result.Connected);
            Assert.Null(result.Bridge);
            Assert.Null(result.Rollback);
            Assert.True(result.FullLengthMm > request.MaximumLengthMm);
            double drawn = Geometry.Segments(result.Right).Concat(Geometry.Segments(result.Left)).Sum(s => s.Length);
            Assert.Equal(drawn, result.LengthMm, 7);
            Assert.InRange(drawn, 0, request.MaximumLengthMm);
            Assert.Equal(result.RightTurns, result.LeftTurns);
            Assert.DoesNotContain(result.Journal, e => e.Action.StartsWith("abandoned_segment"));
        }

        [Fact]
        public void ConnectedCandidatesOutrankClippedOnesAndAreaBreaksTies()
        {
            SpiralRoute Candidate(bool connected, double area, double length) => new SpiralRoute
            {
                Connected = connected,
                Coverage = new AreaCoverage { WorkingAreaMm2 = 100, CoveredAreaMm2 = area },
                FullLengthMm = length,
                LengthMm = length
            };
            var open = Candidate(false, 99, 2000);
            var closed = Candidate(true, 90, 1000);
            var shorter = Candidate(true, 94, 950);
            Assert.True(Search.Better(closed, open));
            Assert.False(Search.Better(open, closed));
            Assert.True(Search.Better(shorter, closed));
            Assert.False(Search.Better(closed, shorter));
        }

        [Fact]
        public void AreaGainMustPayForShorterPassesAlongEachWall()
        {
            SpiralRoute Route(double area, params double[] wallLengths) => new SpiralRoute
            {
                Connected = true,
                Coverage = new AreaCoverage
                {
                    CoveredAreaMm2 = area,
                    BoundaryLengthsMm = wallLengths,
                    StripWidthMm = 200
                }
            };
            var original = Route(1000000, 500, 900);
            // Losing 100 mm of parallel pipe puts a 100 x 100 mm strip at
            // stake. Extra pipe along the other wall does not cancel that loss.
            Assert.False(Search.Better(Route(1005000, 400, 1300), original));
            Assert.True(Search.Better(Route(1050000, 400, 1300), original));
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void SideEntryThroughANarrowWingContinuesIntoTheMainRoom(bool quarterTurn)
        {
            const double step = 150;
            Point2 InView(double x, double y) => quarterTurn
                ? P(10000 - y * step, -3000 + x * step)
                : P(x * step, y * step);
            var request = new SpiralRequest
            {
                Polygon = new[] { InView(7, 0), InView(25, 0), InView(25, 18),
                                  InView(0, 18), InView(0, 12), InView(7, 12) },
                RightStart = InView(0.5, 15.8),
                LeftStart = InView(0.5, 17.3),
                EntryDirection = quarterTurn ? P(0, 1) : P(1, 0),
                OffsetMm = step,
                SpacingMm = step,
                MinimumSegmentMm = 0.8,
                Clockwise = false
            };
            var route = SpiralSolver.Calculate(request);
            Assert.True(route.Connected);
            Assert.True(Search.EndsMeet(route.Right.Last(), route.Left.Last(), route.Bridge));
            var center = InView(16, 9);
            var sample = new Segment2(center, center);
            foreach (var branch in new[] { route.Right, route.Left })
                Assert.Contains(Geometry.Segments(branch), s => Geometry.Clearance(s, sample) <= 2 * step);
        }

        [Fact]
        public void ConcaveRoomUsesOnlyTheWorkingContourAfterBothInlets()
        {
            var request = Room();
            request.Polygon = new[] { P(0,0), P(4200,0), P(4200,3000), P(2700,3000),
                                      P(2700,2300), P(900,2300), P(900,3000), P(0,3000) };
            var result = SpiralSolver.Calculate(request);
            Assert.True(result.Connected);
            foreach (var branch in new[] { result.Right, result.Left })
                Assert.All(Geometry.Segments(branch).Skip(1), segment =>
                    Assert.True(Geometry.SegmentInside(segment, result.Contour)));
        }

        [Fact]
        public void ZeroTimeRemainingDoesNotReturnAnOpenRouteAsASuccess()
        {
            var request = Room();
            request.TimeBudget = TimeSpan.FromTicks(1);
            Assert.Throws<TimeoutException>(() => SpiralSolver.Calculate(request));
        }

        [Fact]
        public void UnsupportedContourAndCollapsedOffsetFailBeforeTheWalk()
        {
            var request = Room();
            request.Polygon[2] = P(4100, 3000);
            Assert.Throws<ArgumentException>(() => SpiralSolver.Calculate(request));
            request = Room();
            request.OffsetMm = 1600;
            Assert.Throws<ArgumentException>(() => SpiralSolver.Calculate(request));
        }
    }
}
