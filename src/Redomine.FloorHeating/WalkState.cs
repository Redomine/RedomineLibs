using System;
using System.Collections.Generic;
using System.Linq;

namespace Redomine.FloorHeating
{
    internal enum Turn
    {
        Forward, Left, Right
    }
    internal enum Blocker
    {
        Boundary, Other, Own, Reservation, Intermediate
    }
    internal readonly struct Direction
    {
        internal readonly Turn Turn;
        internal readonly Point2 Vector;
        internal Direction(Turn turn, Point2 vector)
        {
            Turn = turn;
            Vector = vector;
        }
    }
    internal struct Anchor
    {
        internal int Edge;
        internal double Phase, Along, Distance;
        internal Point2 Foot;
        internal bool Backward;
    }
    internal sealed class Branch
    {
        internal readonly int Side;
        internal List<Point2> Vertices;
        internal List<Segment2> Segments;
        internal Point2 Heading;
        internal Anchor? Anchor, Reserve;
        internal Point2? ReturnDirection, Returning;
        internal Segment2? Avoid, LastObstacle;
        internal Blocker? LastBlocker;
        internal Turn? Pending, Only;
        internal bool Guided, Rebind;
        internal int Turns;
        internal string Stop;
        internal Point2 Tip => Vertices[Vertices.Count - 1];
        internal Branch(int side, List<Point2> vertices, Point2 heading)
        {
            Side = side;
            Heading = heading;
            SetPath(vertices);
        }
        internal void SetPath(List<Point2> vertices)
        {
            Vertices = vertices;
            Segments = Geometry.Segments(vertices).ToList();
        }
        internal void SetTip(Point2 tip)
        {
            Vertices[Vertices.Count - 1] = tip;
            if (Segments.Count > 0)
                Segments[Segments.Count - 1] = new Segment2(Vertices[Vertices.Count - 2], tip);
        }
        internal void Append(Point2 target)
        {
            var s = new Segment2(Tip, target);
            if (Segments.Count > 0)
            {
                var prev = Segments[Segments.Count - 1];
                if (Math.Abs(prev.Delta.Cross(s.Delta)) <= Geometry.Tol && prev.Delta.Dot(s.Delta) > Geometry.Tol)
                {
                    SetTip(target);
                    return;
                }
            }
            Vertices.Add(target);
            Segments.Add(s);
        }
        internal Branch Clone()
        {
            var b = (Branch)MemberwiseClone();
            b.Vertices = new List<Point2>(Vertices);
            b.Segments = new List<Segment2>(Segments);
            return b;
        }
        internal bool SameState(Branch b) =>
            Heading.Equals(b.Heading) && Turns == b.Turns && Stop == b.Stop && Guided == b.Guided && Rebind == b.Rebind &&
            Pending == b.Pending && Only == b.Only && LastBlocker == b.LastBlocker && Nullable.Equals(Anchor, b.Anchor) &&
            Nullable.Equals(Reserve, b.Reserve) && Nullable.Equals(ReturnDirection, b.ReturnDirection) &&
            Nullable.Equals(Returning, b.Returning) && Nullable.Equals(Avoid, b.Avoid) && Nullable.Equals(LastObstacle, b.LastObstacle) &&
            Vertices.SequenceEqual(b.Vertices);
    }
    internal sealed class Pair
    {
        internal Branch Right, Left;
        internal Branch this[int side] => side == 0 ? Right : Left;
        internal Pair(Branch right, Branch left)
        {
            Right = right;
            Left = left;
        }
        internal Pair Clone() => new Pair(Right.Clone(), Left.Clone());
        internal bool SameState(Pair other) => Right.SameState(other.Right) && Left.SameState(other.Left);
    }
    internal sealed class Join
    {
        internal readonly string Method;
        internal readonly Segment2? Bridge;
        internal Join(string method, Segment2? bridge = null)
        {
            Method = method;
            Bridge = bridge;
        }
    }
    internal sealed class Candidate
    {
        internal Pair Pair;
        internal Join Join;
        internal List<SpiralEvent> Log;
        internal Candidate(Pair pair, Join join, List<SpiralEvent> log)
        {
            Pair = pair;
            Join = join;
            Log = log;
        }
        internal Candidate Clone() => new Candidate(Pair.Clone(), Join, new List<SpiralEvent>(Log));
    }
    internal readonly struct Obstacle
    {
        internal readonly Segment2 Segment;
        internal readonly Blocker Kind;
        internal Obstacle(Segment2 s, Blocker kind)
        {
            Segment = s;
            Kind = kind;
        }
    }
    internal struct ProbeResult
    {
        internal double Available, Boundary;
        internal Blocker Kind;
        internal Segment2? Obstacle;
        internal int? Index;
        internal Point2 BoundaryHit;
    }
}
