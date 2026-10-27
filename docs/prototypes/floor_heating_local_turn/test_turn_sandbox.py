# -*- coding: utf-8 -*-
from __future__ import absolute_import

import unittest

from turn_sandbox import (build_paired_route, build_sandbox_route,
                          classify_open_ends, _turn_option)


POLYGON = ((0, 0), (3000, 0), (3000, 2000), (0, 2000))
START = (100, 0)


class SandboxRouteTests(unittest.TestCase):
    def test_turn_limit_counts_actual_turns(self):
        for maximum in (0, 1, 3):
            route = build_sandbox_route(POLYGON, START, maximum)
            turns = [event for event in route["journal"]
                     if event["action"] == "turn"]
            self.assertEqual(maximum, route["turns_used"])
            self.assertEqual(maximum, len(turns))
            self.assertEqual("turn_limit", route["stop_reason"])
            self.assertEqual(route["vertices"][-1],
                             tuple(route["journal"][-1]["at"]))

    def test_natural_stop_precedes_large_limit(self):
        route = build_sandbox_route(POLYGON, START, 100)
        self.assertEqual("no_available_turn", route["stop_reason"])
        self.assertLess(route["turns_used"], 100)

    def test_every_turn_has_candidate_evidence(self):
        route = build_sandbox_route(POLYGON, START, 2)
        events = route["journal"]
        self.assertEqual(list(range(1, len(events) + 1)),
                         [event["step"] for event in events])
        for index, event in enumerate(events):
            if event["action"] == "turn":
                self.assertEqual("turn_candidates", events[index - 1]["action"])
                self.assertEqual(2, len(events[index - 1]["candidates"]))

    def test_paired_entries_have_different_depths(self):
        route = build_paired_route(POLYGON, (500, 0), (350, 0), (0, 1), 0)
        self.assertEqual((500.0, 200.0), route["branches"]["right"]["vertices"][1])
        self.assertEqual((350.0, 350.0), route["branches"]["left"]["vertices"][1])
        self.assertGreater(len(route["branches"]["right"]["vertices"]), 2)
        self.assertGreater(len(route["branches"]["left"]["vertices"]), 2)
        self.assertEqual(0, route["turns_used"])
        self.assertEqual("turn_limit", route["stop_reason"])

    def test_clockwise_entry_depths_are_mirrored(self):
        route = build_paired_route(POLYGON, (500, 0), (350, 0), (0, 1),
                                   0, clockwise=True)
        self.assertEqual((350.0, 200.0), route["branches"]["left"]["vertices"][1])
        self.assertEqual((500.0, 350.0), route["branches"]["right"]["vertices"][1])

    def test_paired_turn_budget_applies_to_each_branch(self):
        route = build_paired_route(POLYGON, (300, 0), (0, 0), (0, 1), 3)
        turns = [event for event in route["journal"] if event["action"] == "turn"]
        self.assertEqual(6, len(turns))
        self.assertEqual(6, route["turns_used"])
        self.assertEqual({"right": 3, "left": 3}, route["turns_by_branch"])
        self.assertEqual("turn_limit", route["stop_reason"])

    def test_branches_advance_in_pairs_and_reserve_next_corner(self):
        route = build_paired_route(POLYGON, (300, 0), (0, 0), (0, 1), 4)
        straights = [(event["branch"], event["round"])
                     for event in route["journal"]
                     if event["action"] == "straight"]
        self.assertEqual([("right", number) for number in range(1, 6)],
                         straights[::2])
        self.assertEqual([("left", number) for number in range(1, 6)],
                         straights[1::2])
        self.assertEqual({"right": 4, "left": 4}, route["turns_by_branch"])
        reservations = [event for event in route["journal"]
                        if event["action"] == "reserve_next_leg"]
        self.assertTrue(reservations)
        self.assertTrue(all(event["turn_clearance"] for event in reservations))

    def test_short_leg_into_closed_corridor_is_rejected(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        obstacles = (((0, 800), (1000, 800)),
                     ((350, 600), (350, 700)),
                     ((650, 600), (650, 700)))
        option = _turn_option((500, 500), (0, 1), contour, [], obstacles,
                              150, 30, 75)
        self.assertEqual(150, option["available_mm"])
        self.assertEqual([0.0, 0.0], option["follow_up_available_mm"])
        self.assertFalse(option["eligible"])
        self.assertEqual("terminal_stub_without_exit",
                         option["rejection_reason"])

    def test_pair_lookahead_yields_one_pitch_for_both_routes(self):
        polygon = ((4200, 3000), (4200, 0), (0, 0), (0, 3000))
        route = build_paired_route(polygon, (415.25, 75),
                                   (115.25, 75), (0, 1), 5)
        self.assertEqual({"right": 5, "left": 5},
                         route["turns_by_branch"])
        adjustments = [event for event in route["journal"]
                       if event["action"] == "yield_for_first_corner"]
        self.assertEqual(1, len(adjustments))
        self.assertEqual(150, adjustments[0]["original_mm"] -
                         adjustments[0]["resulting_mm"])
        rejected = [event for event in route["journal"]
                    if event["action"] == "paired_lookahead" and
                    not event["accepted"]]
        self.assertEqual("first_next_turn_blocked", rejected[0]["reason"])

    def test_entry_correction_does_not_retrace_itself(self):
        route = build_paired_route(POLYGON, (300, 0), (0, 0), (0, 1), 3)
        left = route["branches"]["left"]["vertices"]
        self.assertEqual(6, len(left))
        self.assertGreater(left[2][0], left[1][0])
        self.assertEqual(left[1][1], left[2][1])
        self.assertGreater(left[2][0] - left[1][0], 500)
        actions = [event["action"] for event in route["journal"]]
        self.assertIn("entry_direction_adjustment", actions)
        self.assertTrue(any(event.get("merged_with_previous")
                            for event in route["journal"]
                            if event["action"] == "straight" and
                            event.get("branch") == "left"))

    def test_right_left_follow_open_end_direction(self):
        north = [{"point": (350, 0), "direction_vector": (0, 100)},
                 {"point": (500, 0), "direction_vector": (0, 100)}]
        right, left, direction, spacing = classify_open_ends(north)
        self.assertEqual((500, 0), right["point"])
        self.assertEqual((350, 0), left["point"])
        self.assertEqual((0.0, 1.0), direction)
        self.assertEqual(150, spacing)
        south = [{"point": (350, 0), "direction_vector": (0, -100)},
                 {"point": (500, 0), "direction_vector": (0, -100)}]
        right, left, direction, spacing = classify_open_ends(south)
        self.assertEqual((350, 0), right["point"])
        self.assertEqual((500, 0), left["point"])

    def test_paired_route_is_translation_invariant(self):
        original = build_paired_route(POLYGON, (300, 0), (0, 0), (0, 1), 3)
        delta = (1127, -683)
        shifted_polygon = [(point[0] + delta[0], point[1] + delta[1])
                           for point in POLYGON]
        shifted = build_paired_route(shifted_polygon,
                                     (300 + delta[0], delta[1]),
                                     (delta[0], delta[1]), (0, 1), 3)
        for side in ("right", "left"):
            expected = [(point[0] + delta[0], point[1] + delta[1])
                        for point in original["branches"][side]["vertices"]]
            actual = shifted["branches"][side]["vertices"]
            self.assertEqual(len(expected), len(actual))
            for first, second in zip(expected, actual):
                self.assertAlmostEqual(first[0], second[0], places=6)
                self.assertAlmostEqual(first[1], second[1], places=6)


if __name__ == "__main__":
    unittest.main()
