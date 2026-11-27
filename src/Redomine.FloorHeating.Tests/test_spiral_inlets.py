# -*- coding: utf-8 -*-
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUNDLE = os.path.join(ROOT, "RedomineLibs.extension", "RedomineLibs.tab",
                      u"Тесты.panel", "FloorHeatingTurnSandbox.pushbutton")
sys.path.insert(0, BUNDLE)
from floor_heating_pair import spiral_inlets


def pipe_end(identifier, x, y, dx, dy):
    return {"pipe_id": identifier, "point": (x, y), "direction_vector": (dx, dy)}


class SpiralInletTests(unittest.TestCase):
    # Both inlets enter the left wall, on opposite sides of a collinear vertex.
    walls = [(0, 0), (4200, 0), (4200, 3200), (0, 3200), (0, 1500)]
    pair = [pipe_end(1, 120, 1400, 500, 0), pipe_end(2, 120, 1700, 500, 0)]

    def test_extra_endpoints_do_not_change_the_pair(self):
        clutter = [pipe_end(3, 900, 450, 1100, 0),      # another penetration depth
                   pipe_end(4, 2250, 200, 0, 600),      # an unrelated entry face
                   pipe_end(5, 1800, 1050, 400, 0),     # entirely inside
                   pipe_end(6, 4200, 1100, 4700, 0),    # terminates on the exit face
                   pipe_end(7, 1900, 900, 0, 0)]        # vertical on plan
        for polygon in (self.walls, self.walls[::-1]):
            selected = spiral_inlets(clutter + self.pair[::-1], polygon)
            self.assertEqual(set([1, 2]), set(end["pipe_id"] for end in selected))

    def test_staggered_inlets_and_legacy_internal_feeds(self):
        staggered = [self.pair[0], pipe_end(2, 255, 1700, 500, 0)]
        self.assertEqual(staggered, spiral_inlets(staggered, self.walls))
        internal = [pipe_end(1, 600, 1400, 300, 0), pipe_end(2, 650, 1700, 300, 0)]
        self.assertEqual(internal, spiral_inlets(internal, self.walls))
        self.assertEqual(internal, spiral_inlets(internal + [pipe_end(5, 4200, 2100, 4800, 0)], self.walls))

    def test_genuinely_ambiguous_feeds_accept_explicit_selection(self):
        triple = self.pair + [pipe_end(3, 120, 1950, 500, 0)]
        with self.assertRaises(ValueError):
            spiral_inlets(triple, self.walls)
        self.assertEqual(self.pair, spiral_inlets(triple, self.walls, {1, 2}))

    def test_unrelated_entries_on_another_wall(self):
        side = [pipe_end(5, 2000, 250, 0, 520), pipe_end(6, 2600, 790, 0, 1100)]
        self.assertEqual(self.pair, spiral_inlets(side + self.pair, self.walls))
        side[1] = pipe_end(6, 2600, 250, 0, 550)
        with self.assertRaises(ValueError):
            spiral_inlets(self.pair + side, self.walls)
        self.assertEqual(self.pair, spiral_inlets(self.pair + side, self.walls, {1, 2}))

    def test_through_pipes_ending_on_the_exit_face_are_not_inlets(self):
        through = [pipe_end(i, 4200, y, 4500, 0) for i, y in enumerate((1100, 1300), 11)]
        with self.assertRaises(ValueError):
            spiral_inlets(through, self.walls)

    def test_pipe_crossing_both_arms_is_not_an_inlet(self):
        polygon = [(0, 0), (5000, 0), (5000, 4000), (3000, 4000),
                   (3000, 900), (1000, 900), (1000, 4000), (0, 4000)]
        local = [pipe_end(1, 3100, 1500, 600, 0), pipe_end(2, 3100, 1700, 600, 0)]
        transits = [pipe_end(i, 3100, y, 3500, 0) for i, y in enumerate((2000, 2200), 3)]
        self.assertEqual(local, spiral_inlets(transits + local, polygon))
