using System;
using System.Collections.Generic;
using static System.Math;
using static Redomine.FloorHeating.Geometry;

namespace Redomine.FloorHeating
{
    internal sealed partial class Search
    {
        // An inner lap turns at the intersection of two equally offset wall
        // lines. Reversing the walk gives the same intersection.
        private bool FollowsReflexCorner(Point2 corner, Point2 incoming, Point2 outgoing)
        {
            foreach (int i in reflexEdges)
            {
                var before = tangents[i];
                var after = tangents[Next(i)];
                if (!(incoming.Near(before) && outgoing.Near(after)) &&
                    !(incoming.Near(-after) && outgoing.Near(-before)))
                    continue;
                var shift = corner - edges[i].End;
                double inset = shift.Dot(incoming);
                if (inset < -Tol || Abs(inset + shift.Dot(outgoing)) > Tol)
                    continue;
                // A distant vertex can share the same bisector. It cannot
                // support this offset if another wall lies inside its square.
                if (inset <= Tol || !Array.Exists(edges, s =>
                    High(s, 0) > corner.X - inset + Tol && Low(s, 0) < corner.X + inset - Tol &&
                    High(s, 1) > corner.Y - inset + Tol && Low(s, 1) < corner.Y + inset - Tol))
                    return true;
            }
            return false;
        }

        // The partner stays on the same side of both the incoming and the
        // outgoing leg, including at a concave bend or a reversed winding.
        private Point2 PartnerCorner(int side, Point2 corner, Point2 incoming, Point2 outgoing) =>
            corner + (incoming.Left + outgoing.Left) * (side == 0 ? spacing : -spacing);

        private bool DetourCorner(List<Segment2> segments, int i, out Point2 corner, out Segment2 removed)
        {
            corner = default;
            removed = default;
            var a = segments[i];
            var middle = segments[i + 1];
            var back = segments[i + 2];
            var last = segments[i + 3];
            if (a.Horizontal == last.Horizontal || middle.Horizontal != last.Horizontal || back.Horizontal != a.Horizontal)
                return false;
            // A grid recovery can overshoot the corner by one pitch and return
            // to the outgoing line. Match the same shape in either traversal.
            double along = a.Delta.Dot(back.Delta), across = middle.Delta.Dot(last.Delta);
            bool overshot = along < 0 && across > 0 && back.Length <= spacing + Tol;
            bool reversed = along > 0 && across < 0 && middle.Length <= spacing + Tol;
            if (!overshot && !reversed)
                return false;
            corner = a.Horizontal ? new Point2(last.End.X, a.Start.Y) : new Point2(a.Start.X, last.End.Y);
            removed = overshot ? back : middle;
            return (corner - a.Start).Dot(a.Unit) >= minimum && (last.End - corner).Dot(last.Unit) >= minimum;
        }

        private bool HasPairedCorner(Branch partner, Point2 expected, Point2 incoming, Point2 outgoing)
        {
            var segments = partner.Segments;
            for (int i = 0; i + 1 < segments.Count; i++)
            {
                var a = segments[i];
                if (Abs((expected - a.Start).Cross(incoming)) > Tol || !a.Unit.Near(incoming))
                    continue;
                if (a.End.Near(expected) && segments[i + 1].Unit.Near(outgoing))
                    return true;
                if (i + 3 < segments.Count && segments[i + 3].Unit.Near(outgoing) &&
                    DetourCorner(segments, i, out var corner, out _) && corner.Near(expected))
                    return true;
            }
            return false;
        }

        private bool StraightenOffsetCorner(Pair pair, Branch b, int i, Segment2? bridge,
                                            bool final, List<SpiralEvent> log)
        {
            if (!DetourCorner(b.Segments, i, out var corner, out var removed))
                return false;
            var a = b.Segments[i];
            var last = b.Segments[i + 3];
            if (!FollowsReflexCorner(corner, a.Unit, last.Unit) &&
                !HasPairedCorner(pair[1 - b.Side], PartnerCorner(b.Side, corner, a.Unit, last.Unit), a.Unit, last.Unit))
                return false;
            if ((i == 0 && (corner - a.End).Dot(a.Unit) > Tol && !SegmentInside(new Segment2(a.End, corner), contour)) ||
                !SegmentInside(new Segment2(a.Start, corner), i == 0 ? polygon : contour) ||
                !SegmentInside(new Segment2(corner, last.End), contour))
                return false;

            var path = new List<Point2>(b.Vertices);
            path.RemoveRange(i + 1, 3);
            path.Insert(i + 1, corner);
            return ReplaceDetour(pair, b, path, bridge, final, log, "offset_corner_straightened", corner, removed, 2);
        }

        // A recovery can also step sideways and then rejoin the same straight
        // pass. Only remove a one-pitch excursion when the direct section has
        // an outer wall or a parallel pipe supporting it for its full length.
        private bool StraightenOffsetRun(Pair pair, Branch b, int i, Segment2? bridge,
                                         bool final, List<SpiralEvent> log)
        {
            var a = b.Segments[i];
            var step = b.Segments[i + 1];
            var cap = b.Segments[i + 2];
            var back = b.Segments[i + 3];
            var last = b.Segments[i + 4];
            if (step.Horizontal == a.Horizontal || cap.Horizontal != a.Horizontal || last.Horizontal != a.Horizontal ||
                a.Delta.Dot(cap.Delta) <= 0 || a.Delta.Dot(last.Delta) <= 0 ||
                step.Length > spacing + Tol || !step.Delta.Near(-back.Delta))
                return false;
            var shortcut = new Segment2(a.End, last.Start);
            int axis = a.Horizontal ? 0 : 1;
            bool Covers(Segment2 support, Point2 shift) =>
                support.Horizontal == a.Horizontal &&
                Abs(support.Start[1 - axis] - shortcut.Start[1 - axis] - shift[1 - axis]) <= Tol &&
                Low(support, axis) <= Low(shortcut, axis) + Tol && High(support, axis) >= High(shortcut, axis) - Tol;
            var outside = -Inward(a.Unit) * spacing;
            bool supported = Array.Exists(edges, edge => Covers(edge, default));
            for (int side = 0; side < 2 && !supported; side++)
                for (int j = 0; j < pair[side].Segments.Count && !supported; j++)
                    if ((side != b.Side || j < i || j > i + 4) && Covers(pair[side].Segments[j], outside))
                        supported = true;
            if (!supported || !SegmentInside(shortcut, contour) ||
                !SegmentInside(new Segment2(a.Start, last.End), i == 0 ? polygon : contour))
                return false;
            var path = new List<Point2>(b.Vertices);
            path.RemoveRange(i + 1, 4);
            return ReplaceDetour(pair, b, path, bridge, final, log, "offset_run_straightened", a.End, step, 4);
        }

        private bool ReplaceDetour(Pair pair, Branch b, List<Point2> path, Segment2? bridge,
                                   bool final, List<SpiralEvent> log, string action, Point2 corner, Segment2 removed, int turns)
        {
            var partner = pair[1 - b.Side];
            // During growth the two tips need clearance until they actually meet.
            if (!final && !b.Tip.Near(partner.Tip) && partner.Segments.Count > 0 &&
                Clearance(new Segment2(path[path.Count - 2], b.Tip), partner.Segments[partner.Segments.Count - 1]) < spacing - Tol)
                return false;
            if (!Clear(b.Side == 0 ? path : partner.Vertices, b.Side == 1 ? path : partner.Vertices, bridge, final))
                return false;
            var previous = b.Vertices;
            var boundary = BoundaryCoverage(pair);
            b.SetPath(path);
            if (!Preserves(BoundaryCoverage(pair), boundary))
            {
                b.SetPath(previous);
                return false;
            }
            b.Turns = Max(0, b.Turns - turns);
            if (b.Stop == "turn_limit" && b.Turns < maxTurns)
                b.Stop = null;
            log?.Add(new SpiralEvent { Action = action, Branch = SideName(b.Side),
                                       Origin = corner, Segment = removed });
            return true;
        }

        private void AlignOffsetCorners(Pair pair, Segment2? bridge, bool final, bool tipsOnly, List<SpiralEvent> log)
        {
            bool changed;
            do
            {
                changed = false;
                for (int side = 0; side < 2; side++)
                {
                    var b = pair[side];
                    for (int i = tipsOnly ? Max(0, b.Segments.Count - 5) : 0; i + 3 < b.Segments.Count; i++)
                        if ((i + 4 < b.Segments.Count && StraightenOffsetRun(pair, b, i, bridge, final, log)) ||
                            StraightenOffsetCorner(pair, b, i, bridge, final, log))
                            changed = true;
                }
            } while (changed);
        }
    }
}
