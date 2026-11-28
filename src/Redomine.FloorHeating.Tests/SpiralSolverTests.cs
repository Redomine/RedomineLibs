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
        public void HostCancellationAtACheckpointStopsTheSearch()
        {
            Assert.Throws<OperationCanceledException>(() =>
                SpiralSolver.Calculate(Room(), null, () => throw new OperationCanceledException()));
        }

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
        [InlineData(false, false)]
        [InlineData(true, false)]
        [InlineData(false, true)]
        [InlineData(true, true)]
        public void ZeroOffsetFollowsTheWholeBoundaryUntilTheInletOpening(bool clockwise, bool recessed)
        {
            var request = Room();
            request.OffsetMm = 0;
            request.Clockwise = clockwise;
            request.MaximumLengthMm = 160000;
            // Deliberately choose wall lengths that are not multiples of the
            // pitch. Turning at a grid checkpoint would miss the actual wall.
            request.Polygon = recessed
                ? new[] { P(0, 0), P(4200, 0), P(4200, 2300), P(5600, 2300), P(5600, 4300), P(0, 4300) }
                : new[] { P(0, 0), P(4100, 0), P(4100, 3300), P(0, 3300) };
            if (recessed)
            {
                request.RightStart = P(80, 1950);
                request.LeftStart = P(80, 2250);
                request.EntryDirection = P(1, 0);
            }
            var result = SpiralSolver.Calculate(request);
            Assert.True(result.Connected);
            Assert.Equal(request.Polygon, result.Contour);
            var walls = Geometry.Segments(request.Polygon, true).ToArray();
            var outer = clockwise ? result.Left : result.Right;
            var pass = Geometry.Segments(outer)
                .TakeWhile(s => walls.Any(w => Geometry.On(s.Start, w) && Geometry.On(s.End, w))).ToArray();
            Assert.Equal(walls.Sum(w => w.Length) - 2 * request.SpacingMm, pass.Sum(s => s.Length), 6);
            Assert.All(request.Polygon, corner => Assert.Contains(pass, s => s.End.Near(corner)));
        }

        [Fact]
        public void InletsOnARecessedWallStayOnThatWall()
        {
            var request = Room();
            request.Polygon = new[] { P(0, 0), P(5000, 0), P(5000, 4000), P(3000, 4000),
                                      P(3000, 1000), P(1000, 1000), P(1000, 4000), P(0, 4000) };
            request.EntryDirection = P(1, 0);
            request.RightStart = P(3000, 2000);
            request.LeftStart = P(3000, 2200);
            request.OffsetMm = 0;
            var route = SpiralSolver.Calculate(request);
            Assert.True(route.Connected);
            Assert.True(route.Right[0].Near(request.RightStart));
            Assert.True(route.Left[0].Near(request.LeftStart));
        }

        [Fact]
        public void EntryCenteredAtAReentrantCornerFitsOnItsActualWall()
        {
            var polygon = new[] { P(0, 0), P(5000, 0), P(5000, 4000), P(3000, 4000),
                                  P(3000, 1000), P(1000, 1000), P(1000, 4000), P(0, 4000) };
            var starts = Geometry.Starts(P(3100, 900), P(3100, 1100), P(1, 0), polygon, 0, 200);
            Assert.True(starts.right.Near(P(3000, 1000)));
            Assert.True(starts.left.Near(P(3000, 1200)));
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void OuterTurnAtAReentrantEntryKeepsRoomForBothElbows(bool mirrored)
        {
            Point2 WallPoint(double x, double y) => P(x, mirrored ? -y : y);
            var request = Room();
            request.Polygon = new[] { WallPoint(0, 0), WallPoint(5000, 0), WallPoint(5000, 4000), WallPoint(3000, 4000),
                                      WallPoint(3000, 1000), WallPoint(1000, 1000), WallPoint(1000, 4000), WallPoint(0, 4000) };
            request.Clockwise = mirrored;
            request.EntryDirection = P(1, 0);
            request.RightStart = WallPoint(3100, mirrored ? 1100 : 900);
            request.LeftStart = WallPoint(3100, mirrored ? 900 : 1100);
            request.OffsetMm = 0;
            var route = SpiralSolver.Calculate(request);
            Assert.True(route.Connected);
            var outer = mirrored ? route.Left : route.Right;
            var walls = Geometry.Segments(request.Polygon, true).ToArray();
            var pass = Geometry.Segments(outer)
                .TakeWhile(s => walls.Any(w => Geometry.On(s.Start, w) && Geometry.On(s.End, w))).ToArray();
            Assert.True(outer[0].Near(WallPoint(3000, 1000 + request.SpacingMm)));
            Assert.Equal(walls.Sum(w => w.Length) - 2 * request.SpacingMm, pass.Sum(s => s.Length), 6);
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

        [Theory]
        [InlineData(false, false)]
        [InlineData(true, false)]
        [InlineData(false, true)]
        [InlineData(true, true)]
        public void RecoveryFollowsTheReentrantCornerOnSuccessiveLaps(bool mirrored, bool sideEntry)
        {
            const double step = 150;
            Point2 InView(double x, double y) => mirrored
                ? P(12000 + y * step, -4500 + x * step) : P(x * step, y * step);
            var right = sideEntry ? InView(0.4, 1.5) : InView(12, 0.4);
            var left = sideEntry ? InView(0.4, 2.5) : InView(11, 0.4);
            var incoming = mirrored ? P(1, 0) : P(0, 1);
            var outgoing = mirrored ? P(0, 1) : P(1, 0);
            var route = SpiralSolver.Calculate(new SpiralRequest
            {
                Polygon = new[] { InView(0, 0), InView(13, 0), InView(13, 22),
                                  InView(29, 22), InView(29, 37), InView(0, 37) },
                RightStart = mirrored ? left : right,
                LeftStart = mirrored ? right : left,
                EntryDirection = sideEntry ? outgoing : incoming,
                Clockwise = mirrored,
                SpacingMm = step, OffsetMm = step, MinimumSegmentMm = 0.8, MaximumLengthMm = 125000
            });

            Assert.True(route.Connected);
            for (int inset = 0; inset < 6; inset++)
            {
                var branch = (inset % 2 == 0) != mirrored ? route.Right : route.Left;
                var corner = InView(12 - inset, 23 + inset);
                Assert.Contains(Enumerable.Range(1, branch.Length - 2), i =>
                    branch[i].Near(corner) &&
                    new Segment2(branch[i - 1], branch[i]).Unit.Near(incoming) &&
                    new Segment2(branch[i], branch[i + 1]).Unit.Near(outgoing));
            }
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void ARealWallProjectionOnePitchDeepKeepsItsPass(bool clockwise)
        {
            const double step = 175;
            Point2 Grid(double x, double y) => P(x * step, y * step);
            var route = SpiralSolver.Calculate(new SpiralRequest
            {
                Polygon = new[] { Grid(0, 0), Grid(24, 0), Grid(24, 16), Grid(15, 16),
                                  Grid(15, 17), Grid(8, 17), Grid(8, 16), Grid(0, 16) },
                RightStart = Grid(11, 0.5), LeftStart = Grid(10, 0.5), EntryDirection = P(0, 1),
                Clockwise = clockwise,
                SpacingMm = step, OffsetMm = step, MinimumSegmentMm = 0.8, MaximumLengthMm = 125000
            });

            Assert.True(route.Connected);
            var outer = clockwise ? route.Left : route.Right;
            Assert.Contains(Geometry.Segments(outer), s => s.Horizontal &&
                Geometry.On(Grid(10, 16), s) && Geometry.On(Grid(13, 16), s));
        }

        [Theory]
        [InlineData(false, false)]
        [InlineData(false, true)]
        [InlineData(true, false)]
        [InlineData(true, true)]
        public void RecoveryKeepsTheReturnPassInAProjectionWithInlets(bool sideEntry, bool mirrored)
        {
            const double pitch = 165, width = 23.5, shoulder = 17.5, left = 7.5, right = 13.5, top = 24;
            Point2 InView(double x, double y)
            {
                // Orthogonal plan in a view with a sub-tolerance rotation.
                const double skew = 3e-14;
                var u = (x - y * skew) * pitch;
                var v = (y + x * skew) * pitch;
                return mirrored ? P(-12345 + v, 70310 + u) : P(-12345 + u, 70310 + v);
            }
            var outward = mirrored ? P(1, 0) : P(0, 1);
            var across = mirrored ? P(0, 1) : P(1, 0);
            var a = sideEntry ? InView(left + 0.35, shoulder + 2.15) : InView(left + 2.65, top - 0.35);
            var b = sideEntry ? InView(left + 0.35, shoulder + 3.65) : InView(left + 4.15, top - 0.35);
            var route = SpiralSolver.Calculate(new SpiralRequest
            {
                Polygon = new[] { InView(0, shoulder), InView(left, shoulder), InView(left, top), InView(right, top),
                                  InView(right, shoulder), InView(width, shoulder), InView(width, 0), InView(0, 0) },
                RightStart = mirrored ? b : a, LeftStart = mirrored ? a : b,
                Clockwise = mirrored, EntryDirection = sideEntry ? across : -outward,
                SpacingMm = pitch, OffsetMm = pitch, MinimumSegmentMm = 0.8, MaximumLengthMm = 130000
            });

            Assert.True(route.Connected);
            Assert.True(Search.EndsMeet(route.Right.Last(), route.Left.Last(), route.Bridge));
            var mouth = new Segment2(InView(left + 1, shoulder - 1), InView(right - 1, shoulder - 1));
            // A top inlet occupies the first two lanes next to the end wall.
            var reach = InView(right - 1, top - (sideEntry ? 1 : 3));
            var outer = Geometry.Segments(mirrored ? route.Left : route.Right).ToArray();
            int pass = Array.FindIndex(outer, s => s.Start.Near(mouth.End) && s.Unit.Near(outward) && Geometry.On(reach, s));
            Assert.True(pass >= 0, "The outer branch must enter the projection from the main room.");
            Assert.Contains(outer.Skip(pass + 1), s => s.Unit.Near(-outward) && Geometry.Clearance(s, mouth) <= Geometry.Tol &&
                (s.End - mouth.Start).Dot(outward) < -Geometry.Tol);
            var center = InView(width / 2, shoulder / 2);
            foreach (var branch in new[] { route.Left, route.Right })
                Assert.Contains(Geometry.Segments(branch), s => Geometry.Clearance(s, new Segment2(center, center)) <= 2 * pitch + Geometry.Tol);
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void StaggeredBranchesTakeTheSameFreeInteriorBend(bool mirrored)
        {
            const double pitch = 160;
            Point2 Grid(double x, double y) => mirrored ? P(-y * pitch, -x * pitch) : P(x * pitch, y * pitch);
            var route = SpiralSolver.Calculate(new SpiralRequest
            {
                Polygon = new[] { Grid(0, 0), Grid(12, 0), Grid(12, 23), Grid(26, 23), Grid(26, 35), Grid(0, 35) },
                RightStart = Grid(0.5, mirrored ? 19.25 : 18.25),
                LeftStart = Grid(0.5, mirrored ? 18.25 : 19.25),
                EntryDirection = mirrored ? P(0, -1) : P(1, 0), Clockwise = !mirrored,
                SpacingMm = pitch, OffsetMm = pitch, MinimumSegmentMm = 0.8, MaximumLengthMm = 80000
            });

            Assert.True(route.Connected);
            // Even when the leader has finished its next leg, both passes
            // through the neck must continue into the upper arm.
            var inner = mirrored ? route.Left : route.Right;
            var outer = mirrored ? route.Right : route.Left;
            Assert.Contains(Geometry.Segments(inner), s => Geometry.On(Grid(6, 18), s) && Geometry.On(Grid(6, 25), s));
            Assert.Contains(Geometry.Segments(outer), s => Geometry.On(Grid(5, 18), s) && Geometry.On(Grid(5, 25), s));
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void OffGridPipeCheckpointKeepsBothPassesStraightOutOfAWing(bool mirrored)
        {
            const double pitch = 160;
            Point2 Grid(double x, double y) => mirrored ? P(-x * pitch, y * pitch) : P(x * pitch, y * pitch);
            var route = SpiralSolver.Calculate(new SpiralRequest
            {
                Polygon = new[] { Grid(0, 0), Grid(18, 0), Grid(18, 8.5), Grid(23, 8.5), Grid(23, 17.5),
                                  Grid(18, 17.5), Grid(18, 25), Grid(15.25, 25), Grid(15.25, 30),
                                  Grid(4.5, 30), Grid(4.5, 25), Grid(0, 25) },
                RightStart = Grid(0.5, mirrored ? 23.75 : 22.75),
                LeftStart = Grid(0.5, mirrored ? 22.75 : 23.75),
                EntryDirection = mirrored ? P(-1, 0) : P(1, 0), Clockwise = !mirrored,
                SpacingMm = pitch, OffsetMm = pitch, MinimumSegmentMm = 0.8, MaximumLengthMm = 80000
            });

            Assert.True(route.Connected);
            var inner = mirrored ? route.Left : route.Right;
            var outer = mirrored ? route.Right : route.Left;
            Assert.Contains(Geometry.Segments(inner), s => Geometry.On(Grid(11.25, 25), s) && Geometry.On(Grid(11.25, 22.25), s));
            Assert.Contains(Geometry.Segments(outer), s => Geometry.On(Grid(12.25, 25), s) && Geometry.On(Grid(12.25, 22.25), s));
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
