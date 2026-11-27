using System;
using System.Collections.Generic;
using System.Linq;

namespace Redomine.FloorHeating
{
    /// <summary>View-plane coordinates in millimetres. The solver has no Autodesk or UI dependencies.</summary>
    public readonly struct Point2 : IEquatable<Point2>
    {
        public double X
        {
            get;
        }
        public double Y
        {
            get;
        }
        public Point2(double x, double y)
        {
            X = x;
            Y = y;
        }
        internal double this[int axis] => axis == 0 ? X : Y;
        internal Point2 With(int axis, double value) => axis == 0 ? new Point2(value, Y) : new Point2(X, value);
        public static Point2 operator +(Point2 a, Point2 b) => new Point2(a.X + b.X, a.Y + b.Y);
        public static Point2 operator -(Point2 a, Point2 b) => new Point2(a.X - b.X, a.Y - b.Y);
        public static Point2 operator -(Point2 a) => new Point2(-a.X, -a.Y);
        public static Point2 operator *(Point2 a, double scale) => new Point2(a.X * scale, a.Y * scale);
        public static Point2 operator /(Point2 a, double scale) => new Point2(a.X / scale, a.Y / scale);
        internal double Length => Math.Sqrt(X * X + Y * Y);
        internal double Dot(Point2 b) => X * b.X + Y * b.Y;
        internal double Cross(Point2 b) => X * b.Y - Y * b.X;
        internal Point2 Left => new Point2(-Y, X);
        internal Point2 Right => new Point2(Y, -X);
        internal bool Near(Point2 b) => (this - b).Length <= Geometry.Tol;
        public bool Equals(Point2 b) => X.Equals(b.X) && Y.Equals(b.Y);
        public override bool Equals(object other) => other is Point2 p && Equals(p);
        public override int GetHashCode() => unchecked(X.GetHashCode() * 397 ^ Y.GetHashCode());
    }

    public readonly struct Segment2
    {
        public Point2 Start
        {
            get;
        }
        public Point2 End
        {
            get;
        }
        public Segment2(Point2 start, Point2 end)
        {
            Start = start;
            End = end;
        }
        internal Point2 Delta => End - Start;
        internal double Length => Delta.Length;
        internal Point2 Unit => Delta / Length;
        internal bool Horizontal => Math.Abs(Delta.Y) <= Geometry.Tol;
        internal bool Matches(Segment2 other) =>
            (Start.Near(other.Start) && End.Near(other.End)) || (Start.Near(other.End) && End.Near(other.Start));
    }

    /// <summary>Right and left inlets are ordered looking into the room. The solver projects them onto its boundary.</summary>
    public sealed class SpiralRequest
    {
        public Point2[] Polygon
        {
            get; set;
        }
        public Point2 RightStart
        {
            get; set;
        }
        public Point2 LeftStart
        {
            get; set;
        }
        public Point2 EntryDirection
        {
            get; set;
        }
        public bool Clockwise
        {
            get; set;
        }
        public double OffsetMm { get; set; } = 200;
        public double SpacingMm { get; set; } = 150;
        public double MinimumSegmentMm { get; set; } = 1;
        public double MaximumLengthMm { get; set; } = 100000;
        public int MaximumTurns { get; set; } = 100;
        public int MaximumRounds { get; set; } = 500;
        public TimeSpan TimeBudget { get; set; } = TimeSpan.FromSeconds(20);
    }

    public sealed class AreaCoverage
    {
        // For ranking completed routes on the same contour. Each edge is kept
        // separately so a gain along one wall cannot hide a loss along another.
        internal double[] BoundaryLengthsMm { get; set; } = Array.Empty<double>();
        internal double? OuterPassMm { get; set; }
        internal double StripWidthMm { get; set; }
        public double WorkingAreaMm2
        {
            get; internal set;
        }
        public double CoveredAreaMm2
        {
            get; internal set;
        }
        public double Ratio => CoveredAreaMm2 / WorkingAreaMm2;
    }

    public sealed class VariantResult
    {
        internal void Record(SpiralRoute route)
        {
            Connected = route.Connected;
            Coverage = route.Coverage;
            LengthMm = route.LengthMm;
            FullLengthMm = route.FullLengthMm;
        }
        public string Policy
        {
            get; internal set;
        }
        public string Reason
        {
            get; internal set;
        }
        public bool Connected
        {
            get; internal set;
        }
        public bool Selected
        {
            get; internal set;
        }
        public double Seconds
        {
            get; internal set;
        }
        public double FullLengthMm
        {
            get; internal set;
        }
        public double LengthMm
        {
            get; internal set;
        }
        public AreaCoverage Coverage
        {
            get; internal set;
        }
        public int Recoveries
        {
            get; internal set;
        }
        public int DuplicateContinuations
        {
            get; internal set;
        }
    }

    public sealed class RecoveryDetail
    {
        public int FromRound { get; internal set; }
        public int ToRound { get; internal set; }
        public string Reason { get; internal set; }
        public string Turn { get; internal set; }
        public string PartnerTurn { get; internal set; }
        public double? FirstCheckpointMm { get; internal set; }
        public double? SecondCheckpointMm { get; internal set; }
        public string FirstLimitKind { get; internal set; }
        public string SecondLimitKind { get; internal set; }
        public string CheckpointSource { get; internal set; }
        public string AnchorBranch { get; internal set; }
        public Segment2? AnchorSegment { get; internal set; }
    }

    public sealed class SpiralEvent
    {
        public RecoveryDetail Recovery { get; internal set; }
        public string Action
        {
            get; internal set;
        }
        public string Branch
        {
            get; internal set;
        }
        public Segment2 Segment
        {
            get; internal set;
        }
        public int Round
        {
            get; internal set;
        }
        public string Choice
        {
            get; internal set;
        }
        public int SourceStep
        {
            get; internal set;
        }
        public Point2 Origin
        {
            get; internal set;
        }
        public Point2 Direction
        {
            get; internal set;
        }
        public Point2 BoundaryHit
        {
            get; internal set;
        }
        public double AvailableMm
        {
            get; internal set;
        }
        public double BoundaryMm
        {
            get; internal set;
        }
        public string BlockerKind
        {
            get; internal set;
        }
        public Segment2? BlockerSegment
        {
            get; internal set;
        }
        public int? BlockerIndex
        {
            get; internal set;
        }
        internal static SpiralEvent Ray(string side, int round, string choice, Point2 origin, Point2 direction, ProbeResult hit) =>
            new SpiralEvent
            {
                Action = "direction_probe",
                Branch = side,
                Round = round,
                Choice = choice,
                Origin = origin,
                Direction = direction,
                BoundaryHit = hit.BoundaryHit,
                AvailableMm = hit.Available,
                BoundaryMm = hit.Boundary,
                BlockerSegment = hit.Obstacle,
                BlockerIndex = hit.Index,
                BlockerKind = BlockerName(hit.Kind)
            };
        internal static string BlockerName(Blocker? kind)
        {
            switch (kind)
            {
                case Blocker.Boundary: return "boundary";
                case Blocker.Own: return "same_branch";
                case Blocker.Reservation: return "first_turn_reservation";
                case Blocker.Other: return "other_branch";
                case Blocker.Intermediate: return "intermediate_turn";
                default: return null;
            }
        }
        internal SpiralEvent AsRecovery()
        {
            var copy = (SpiralEvent)MemberwiseClone();
            copy.Action = "recovery_" + Action;
            return copy;
        }
        internal Point2[] OldPath, NewPath;
    }

    public sealed class SpiralRoute
    {
        public RecoveryDetail Rollback { get; internal set; }
        public Point2[] Right
        {
            get; internal set;
        }
        public Point2[] Left
        {
            get; internal set;
        }
        public Point2[] Contour
        {
            get; internal set;
        }
        public Point2 RightStart
        {
            get; internal set;
        }
        public Point2 LeftStart
        {
            get; internal set;
        }
        public bool Connected
        {
            get; internal set;
        }
        public string JoinMethod
        {
            get; internal set;
        }
        public Segment2? Bridge
        {
            get; internal set;
        }
        public int RightTurns
        {
            get; internal set;
        }
        public int LeftTurns
        {
            get; internal set;
        }
        public string RightStop
        {
            get; internal set;
        }
        public string LeftStop
        {
            get; internal set;
        }
        public double LengthMm
        {
            get; internal set;
        }
        public double FullLengthMm
        {
            get; internal set;
        }
        public bool LengthLimitHit
        {
            get; internal set;
        }
        public bool TimeLimitHit
        {
            get; internal set;
        }
        public AreaCoverage Coverage
        {
            get; internal set;
        }
        public string Policy
        {
            get; internal set;
        }
        public IReadOnlyList<VariantResult> Attempts
        {
            get; internal set;
        }
        public IReadOnlyList<SpiralEvent> Journal
        {
            get; internal set;
        }
    }
}
