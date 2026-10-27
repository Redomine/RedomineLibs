# -*- coding: utf-8 -*-
from __future__ import absolute_import

import unittest

from vector_walk import (_abandoned_segments, _can_travel, _finish_route,
                         _intermediate_checkpoints, _next_reservation,
                         _recovery_checkpoints,
                         _paired_turn_room, _recovery_distance_allowed,
                         _sequential_jog_without_parallel_overlap, _walk_one,
                         arrowhead_segments, build_vector_route,
                         limit_route_length, probe_ray, route_length_mm)


class VectorWalkTests(unittest.TestCase):
    def test_length_limit_counts_both_branches_and_keeps_complete_pair(self):
        route = {
            "branches": {
                "right": {"vertices": [(0, 0), (100, 0), (200, 0)],
                          "turns_used": 0, "stop_reason": None},
                "left": {"vertices": [(0, 150), (100, 150), (200, 150)],
                         "turns_used": 0, "stop_reason": None}},
            "journal": [], "turns_by_branch": {"right": 0, "left": 0},
            "turns_used": 0, "stop_reason": {"right": None, "left": None},
            "join": {"connected": True, "bridge": [[200, 0], [200, 150]]},
            "rollback": None, "abandoned_segments": []}
        limited = limit_route_length(route, 250.0)
        self.assertTrue(limited["length_limit_hit"])
        self.assertEqual(200.0, limited["length_mm"])
        self.assertEqual(200.0, route_length_mm(limited["branches"]))
        self.assertEqual([(0, 0), (100, 0)],
                         limited["branches"]["right"]["vertices"])
        self.assertEqual([(0, 150), (100, 150)],
                         limited["branches"]["left"]["vertices"])
        self.assertFalse(limited["join"]["connected"])
        self.assertEqual("length_limit_reached",
                         limited["journal"][-1]["action"])
        self.assertTrue(route["join"]["connected"])
        complete = limit_route_length(route, 400.0)
        self.assertFalse(complete["length_limit_hit"])
        self.assertEqual(400.0, complete["length_mm"])

    def test_length_limit_does_not_leave_one_branch_a_turn_ahead(self):
        route = {
            "branches": {
                "right": {"vertices": [(0, 0), (100, 0),
                                        (100, 100), (200, 100)]},
                "left": {"vertices": [(0, 150), (100, 150),
                                       (200, 150), (300, 150)]}},
            "journal": [], "turns_by_branch": {"right": 2, "left": 0},
            "turns_used": 2, "stop_reason": {"right": None, "left": None},
            "join": {"connected": False}, "rollback": None,
            "abandoned_segments": []}
        limited = limit_route_length(route, 450.0)
        self.assertEqual({"right": 0, "left": 0},
                         limited["turns_by_branch"])
        self.assertEqual(300.0, limited["length_mm"])

    def test_clockwise_entry_near_wall_aligns_both_pipes(self):
        polygon = ((3600, 0), (0, 0), (0, 4900), (900, 4900),
                   (900, 5900), (3050, 5900), (3050, 4900),
                   (3600, 4900), (3600, 3500), (4600, 3500),
                   (4600, 1700), (3600, 1700))
        route = build_vector_route(polygon, (415, 75), (115, 75),
                                   (0, 1), 100, clockwise=True)
        aligned = [event for event in route["journal"] if
                   event["action"] == "paired_entry_alignment"]
        self.assertEqual(1, len(aligned))
        self.assertEqual([200, 200], aligned[0]["first_target"])
        self.assertEqual([350, 350], aligned[0]["second_target"])
        self.assertEqual((115, 75), route["branches"]["left"]["vertices"][0])
        self.assertEqual((200, 75), route["branches"]["left"]["vertices"][1])
        self.assertEqual((415, 75), route["branches"]["right"]["vertices"][0])
        self.assertEqual((350, 75), route["branches"]["right"]["vertices"][1])
        first_pipe_x = route["branches"]["right"]["vertices"][1][0]
        returning = []
        left_vertices = route["branches"]["left"]["vertices"]
        for start, end in zip(left_vertices[1:], left_vertices[2:]):
            if (abs(start[0] - end[0]) < 0.001 and
                    start[0] > first_pipe_x and
                    min(start[1], end[1]) <= 200 and
                    max(start[1], end[1]) > 200):
                returning.append(start[0] - first_pipe_x)
        self.assertTrue(returning)
        self.assertAlmostEqual(150, min(returning))
        self.assertTrue(route["join"]["connected"])
        self.assertEqual(route["turns_by_branch"]["left"],
                         route["turns_by_branch"]["right"])
        self.assertGreater(route["turns_by_branch"]["left"], 1)

    def test_entry_jog_exemption_requires_no_parallel_overlap(self):
        self.assertTrue(_sequential_jog_without_parallel_overlap(
            ((0, 0), (0, 100)), ((50, 100), (50, 200))))
        self.assertFalse(_sequential_jog_without_parallel_overlap(
            ((0, 0), (0, 200)), ((50, 200), (50, 0))))

    def test_recovery_uses_boundary_endpoint_but_leaves_pipe_margin(self):
        self.assertTrue(_recovery_distance_allowed(600, 600,
                                                   "boundary", 1))
        self.assertFalse(_recovery_distance_allowed(600, 600,
                                                    "other_branch", 1))
        self.assertTrue(_recovery_distance_allowed(500, 600,
                                                   "other_branch", 1))

    def test_recovery_anchors_checkpoint_to_transverse_pipe_edge(self):
        contour = ((-1000, -1000), (1000, -1000),
                   (1000, 1000), (-1000, 1000))
        original = {
            "right": {"segments": [((0, 0), (0, 600))],
                      "heading": (0, 1), "last_blocker": "other_branch"},
            "left": {"segments": [((-400, 530), (-100, 530)),
                                  ((-150, 0), (-150, 600))],
                     "heading": (0, 1), "last_blocker": "other_branch"}}
        checkpoints = _recovery_checkpoints(
            original, "right", contour, 150,
            (("right", (1, 0)), ("forward", (0, 1)),
             ("left", (-1, 0))))
        anchored = [item for item in checkpoints if
                    item["source"] == "pipe_edge" and
                    item["turn"] == "left"]
        self.assertEqual(380, anchored[0]["distance_mm"])
        self.assertEqual("left", anchored[0]["source_branch"])
        self.assertNotEqual(0, anchored[0]["distance_mm"] % 150)

    def test_contour_checkpoint_does_not_add_another_pipe_spacing(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        original = {
            "right": {"segments": [((500, 500), (500, 0))],
                      "heading": (0, -1), "last_blocker": "boundary"},
            "left": {"segments": [((650, 500), (650, 150))],
                     "heading": (0, -1), "last_blocker": "boundary"}}
        checkpoints = _recovery_checkpoints(
            original, "right", contour, 150,
            (("right", (-1, 0)), ("forward", (0, -1)),
             ("left", (1, 0))))
        wall = [item for item in checkpoints if
                item["source"] == "contour_edge" and
                item["distance_mm"] == 500]
        self.assertTrue(wall)

    def test_intermediate_turn_checks_exact_spacing_from_other_pipe(self):
        obstacles = [{"segment": ((950, -200), (950, 200)),
                      "kind": "other_branch"}]
        checkpoints = _intermediate_checkpoints(
            (0, 0), (1, 0), 1200, 150, 1, obstacles)
        self.assertIn((800, "pipe_offset", "other_branch"), checkpoints)
        self.assertNotEqual(0, 800 % 150)

    def test_exact_dictating_distance_is_allowed(self):
        self.assertTrue(_can_travel(150, 150, 1))
        self.assertFalse(_can_travel(149, 150, 1))
        self.assertFalse(_can_travel(150, 150, 150))

    def test_exact_dictating_distance_can_be_reserved(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        branch = {"vertices": [(200, 300)], "segments": [],
                  "heading": (0, 1)}
        obstacles = [{"segment": ((500, 0), (500, 1000)),
                      "kind": "other_branch"}]
        reservation = _next_reservation(branch, contour, obstacles,
                                        150, 200, 1, 4, [], "right")
        self.assertEqual(((200, 300), (350, 300)), reservation)

    def test_reservation_uses_forward_before_opposite_turn(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        cases = ((False, (200, 300), ((350, 0), (350, 1000))),
                 (True, (800, 300), ((650, 0), (650, 1000))))
        for clockwise, point, blocked_side in cases:
            branch = {"vertices": [point], "segments": [],
                      "heading": (0, 1)}
            obstacles = [{"segment": blocked_side,
                          "kind": "other_branch"}]
            journal = []
            reservation = _next_reservation(
                branch, contour, obstacles, 150, 200, 1, 4,
                journal, "right", clockwise)
            self.assertEqual((point, (point[0], point[1] + 150)),
                             reservation)
            self.assertEqual("forward", [event["candidate"] for event in
                                          journal if event["action"] ==
                                          "reserve_first_turn"][0])

    def test_exact_dictating_distance_builds_a_segment(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        branch = {"vertices": [(200, 100), (200, 300)],
                  "segments": [((200, 100), (200, 300))],
                  "heading": (0, 1), "turns_used": 0,
                  "stop_reason": None}
        obstacles = [{"segment": ((500, 0), (500, 1000)),
                      "kind": "other_branch"}]
        journal = []
        self.assertTrue(_walk_one(branch, "right", contour, obstacles,
                                  150, 200, 1, 4, journal, 1))
        self.assertEqual((350, 300), branch["vertices"][-1])
        self.assertEqual(150, journal[-1]["length_mm"])

    def test_boundary_and_pipe_clearance_are_different(self):
        contour = ((200, 200), (800, 200), (800, 800), (200, 800))
        free = probe_ray((300, 300), (1, 0), contour, [], [], 150)
        self.assertEqual(500, free["available_mm"])
        obstacles = [{"segment": ((700, 200), (700, 800)),
                      "kind": "other_branch"}]
        blocked = probe_ray((300, 300), (1, 0), contour, [], obstacles, 150)
        self.assertEqual(250, blocked["available_mm"])
        self.assertEqual("other_branch", blocked["blocker"])

    def test_pair_room_uses_width_and_each_side_clearance(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        right = {"vertices": [(300, 500)], "segments": [],
                 "heading": (0, 1), "returning_from_protrusion": None}
        left = {"vertices": [(550, 500)],
                "segments": [((550, 100), (550, 900))],
                "heading": (0, 1), "returning_from_protrusion": None}
        journal = []
        tight = _paired_turn_room({"right": right, "left": left}, contour,
                                  150, 200, journal, 1)
        self.assertIn("right", tight)
        probe = [event for event in journal if
                 event["action"] == "paired_turn_room_probe" and
                 event["branch"] == "right"][0]
        self.assertEqual({"right": 150, "left": 200},
                         probe["side_margins_mm"])
        self.assertEqual(800, probe["threshold_mm"])
        self.assertEqual(750, probe["measured_width_mm"])

    def test_returning_branch_waits_for_reverse_and_open_room(self):
        contour = ((0, 0), (400, 0), (400, 1000), (0, 1000))
        right = {"vertices": [(200, 500)], "segments": [],
                 "heading": (0, 1),
                 "returning_from_protrusion": (0, 1)}
        left = {"vertices": [(350, 500)], "segments": [],
                "heading": (0, 1),
                "returning_from_protrusion": (0, 1)}
        branches = {"right": right, "left": left}
        self.assertEqual([], _paired_turn_room(branches, contour, 150,
                                               200, [], 1))
        right["heading"] = (0, -1)
        left["heading"] = (0, -1)
        wide = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        right["vertices"][-1] = (500, 500)
        left["vertices"][-1] = (650, 500)
        journal = []
        _paired_turn_room(branches, wide, 150, 200, journal, 2)
        self.assertIsNone(right["returning_from_protrusion"])
        self.assertTrue(any(event["action"] == "protrusion_return_complete"
                            for event in journal))

    def test_exact_loop_width_does_not_trigger_pair_recovery(self):
        contour = ((0, 0), (450, 0), (450, 1000), (0, 1000))
        branches = {side: {"vertices": [(x, 500)], "segments": [],
                           "heading": (0, 1),
                           "returning_from_protrusion": None}
                    for side, x in (("right", 200), ("left", 350))}
        self.assertEqual([], _paired_turn_room(branches, contour, 150,
                                               200, [], 1))

    def test_abandoned_line_excludes_the_retained_red_portion(self):
        journal = [{"step": 1, "action": "segment",
                    "start": [0, 0], "end": [500, 0]},
                   {"step": 2, "action": "segment",
                    "start": [0, 0], "end": [500, 0]}]
        branches = {"right": {"vertices": [(0, 0), (300, 0)]},
                    "left": {"vertices": []}}
        abandoned = _abandoned_segments(journal, branches,
                                        {"bridge": None}, 1)
        self.assertEqual([[[300, 0], [500, 0]]], abandoned)
        self.assertEqual(1, len([event for event in journal if
                                 event["action"] == "abandoned_segment"]))

    def test_abandoned_line_keeps_original_travel_direction(self):
        journal = [{"step": 1, "action": "segment",
                    "start": [500, 0], "end": [0, 0]}]
        branches = {"right": {"vertices": [(0, 0), (300, 0)]},
                    "left": {"vertices": []}}
        self.assertEqual([[[500, 0], [300, 0]]],
                         _abandoned_segments(journal, branches,
                                             {"bridge": None}, 1))

    def test_arrowhead_is_only_at_segment_destination(self):
        wings = arrowhead_segments((150, 0), (0, 0), 150, 1)
        self.assertEqual(2, len(wings))
        for wing_start, tip in wings:
            self.assertEqual([0, 0], tip)
            self.assertEqual(wing_start[0], abs(wing_start[1]))
            self.assertGreater(wing_start[0], 0)

    def test_blocked_right_direction_can_fall_back_to_forward(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        obstacle = [{"segment": ((500, 300), (500, 700)),
                     "kind": "other_branch"}]
        right = probe_ray((350, 500), (1, 0), contour, [], obstacle, 150)
        forward = probe_ray((350, 500), (0, 1), contour, [], obstacle, 150)
        self.assertEqual(0, right["available_mm"])
        self.assertEqual(500, forward["available_mm"])

    def test_left_is_used_after_right_and_forward_are_blocked(self):
        polygon = ((0, 0), (3000, 0), (3000, 2000), (0, 2000))
        route = build_vector_route(polygon, (500, 0), (350, 0),
                                   (0, 1), 5)
        first = [event for event in route["journal"]
                 if event["action"] == "segment" and
                 event["branch"] == "right"]
        self.assertEqual("right", first[0]["choice"])
        self.assertEqual("left", first[1]["choice"])
        round_two = [event["candidate"] for event in route["journal"]
                     if event["action"] == "direction_probe" and
                     event["branch"] == "right" and event["round"] == 2]
        self.assertEqual(["right", "forward", "left"], round_two)

    def test_built_entry_reaches_offset_before_spiral(self):
        polygon = ((0, 0), (4000, 0), (4000, 3000), (0, 3000))
        route = build_vector_route(polygon, (400, 75), (100, 75),
                                   (0, 1), 3)
        self.assertEqual((400, 75), route["branches"]["right"]["vertices"][0])
        self.assertEqual((400, 200), route["branches"]["right"]["vertices"][1])
        self.assertEqual((100, 75), route["branches"]["left"]["vertices"][0])
        self.assertEqual((250, 75), route["branches"]["left"]["vertices"][1])
        self.assertEqual((250, 350), route["branches"]["left"]["vertices"][2])
        built = [event for event in route["journal"] if
                 event["action"] == "entry_segment"]
        self.assertEqual({"right", "left"},
                         {event["branch"] for event in built})
        self.assertEqual({"right": 3, "left": 3}, route["turns_by_branch"])

    def test_second_branch_keeps_first_turn_space_at_turn_limit(self):
        polygon = ((0, 0), (4000, 0), (4000, 3000), (0, 3000))
        route = build_vector_route(polygon, (400, 75), (100, 75),
                                   (0, 1), 4)
        reservations = [event for event in route["journal"]
                        if event["action"] == "reserve_first_turn"]
        self.assertEqual("left", reservations[-1]["candidate"])
        self.assertIn(tuple(reservations[-1]["start"]),
                      route["branches"]["right"]["vertices"])
        self.assertTrue(route["join"]["connected"])

    def test_built_entry_jog_does_not_use_spiral_turn_limit(self):
        polygon = ((0, 0), (4000, 0), (4000, 3000), (0, 3000))
        route = build_vector_route(polygon, (400, 75), (100, 75),
                                   (0, 1), 4)
        entry_turns = [event for event in route["journal"]
                       if event["action"] == "entry_turn"]
        self.assertEqual(["left"], [event["branch"] for event in entry_turns])
        self.assertFalse(entry_turns[0]["counted_in_walk"])
        self.assertEqual({"right": 4, "left": 4}, route["turns_by_branch"])
        self.assertEqual(5, len(route["branches"]["right"]["vertices"]) - 1)
        self.assertEqual(6, len(route["branches"]["left"]["vertices"]) - 1)

    def test_parallel_ends_are_joined_by_adjusting_last_leg(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        branches = {"right": {"vertices": [(200, 800), (200, 300)]},
                    "left": {"vertices": [(350, 800), (350, 450)]}}
        journal = []
        join = _finish_route(branches, contour, 150, 1, journal)
        self.assertTrue(join["connected"])
        self.assertEqual((350, 300), branches["left"]["vertices"][-1])
        self.assertEqual([[200, 300], [350, 300]], join["bridge"])

    def test_perpendicular_ends_meet_at_last_line_intersection(self):
        contour = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
        branches = {"right": {"vertices": [(200, 200), (800, 200)]},
                    "left": {"vertices": [(600, 800), (600, 400)]}}
        join = _finish_route(branches, contour, 150, 1, [])
        self.assertTrue(join["connected"])
        self.assertIsNone(join["bridge"])
        self.assertEqual((600, 200), branches["right"]["vertices"][-1])
        self.assertEqual((600, 200), branches["left"]["vertices"][-1])

    def test_forward_stops_at_first_available_side_turn_checkpoint(self):
        contour = ((0, 0), (2000, 0), (2000, 2000), (0, 2000))
        branch = {"vertices": [(500, 100), (500, 300)],
                  "segments": [((500, 100), (500, 300))],
                  "heading": (0, 1), "turns_used": 0,
                  "stop_reason": None}
        obstacles = [{"segment": ((650, 300), (650, 450)),
                      "kind": "other_branch"}]
        journal = []
        self.assertTrue(_walk_one(branch, "right", contour, obstacles,
                                  150, 200, 1, 2, journal, 1))
        self.assertEqual((500, 600), branch["vertices"][-1])
        checkpoints = [event for event in journal if
                       event["action"] == "intermediate_turn_probe" and
                       event["candidate"] == "right"]
        self.assertEqual([150, 300],
                         [event["distance_mm"] for event in checkpoints])
        self.assertEqual([False, True],
                         [event["turn_available"] for event in checkpoints])

    def test_clockwise_checks_left_first(self):
        contour = ((0, 0), (2000, 0), (2000, 2000), (0, 2000))
        branch = {"vertices": [(1000, 100), (1000, 300)],
                  "segments": [((1000, 100), (1000, 300))],
                  "heading": (0, 1), "turns_used": 0,
                  "stop_reason": None}
        journal = []
        _walk_one(branch, "left", contour, [], 150, 200, 1, 2,
                  journal, 1, clockwise=True)
        first_probe = [event for event in journal if
                       event["action"] == "direction_probe"][0]
        self.assertEqual("left", first_probe["candidate"])

    def test_own_dead_end_uses_last_available_alternate_turn(self):
        contour = ((0, 0), (2000, 0), (2000, 2000), (0, 2000))
        vertices = [(200, 1000), (800, 1000), (800, 100),
                    (500, 100), (500, 300)]
        branch = {"vertices": list(vertices),
                  "segments": list(zip(vertices, vertices[1:])),
                  "heading": (0, 1), "turns_used": 0,
                  "stop_reason": None, "pending_turn": None}
        obstacles = [{"segment": ((650, 300), (650, 1000)),
                      "kind": "other_branch"},
                     {"segment": ((200, 850), (350, 850)),
                      "kind": "other_branch"}]
        journal = []
        self.assertTrue(_walk_one(branch, "right", contour, obstacles,
                                  150, 200, 1, 3, journal, 1))
        self.assertEqual((500, 700), branch["vertices"][-1])
        self.assertEqual("left", branch["pending_turn"])
        selected = [event for event in journal if
                    event["action"] == "backtrack_turn_selected"]
        self.assertEqual(400, selected[-1]["to_distance_mm"])
        self.assertTrue(any(event["action"] == "intermediate_turn_probe" and
                            event.get("checkpoint_source") == "pipe_offset" and
                            event.get("distance_mm") == 400 for event in journal))
        self.assertTrue(_walk_one(branch, "right", contour, obstacles,
                                  150, 200, 1, 3, journal, 2))
        second_move = [event for event in journal if
                       event["action"] == "segment"][-1]
        self.assertEqual("left", second_move["choice"])

    def test_pair_recovers_at_shared_turn_when_one_branch_stops(self):
        polygon = ((0, 0), (2000, 0), (2000, 1500), (0, 1500))
        route = build_vector_route(polygon, (700, 75), (550, 75),
                                   (0, 1), 8)
        self.assertTrue(route["join"]["connected"])
        self.assertEqual("paired_turn_recovery", route["rollback"]["reason"])
        self.assertEqual("left", route["rollback"]["turn"])
        recovery_segments = [event for event in route["journal"] if
                             event["action"] == "recovery_segment"]
        self.assertGreaterEqual(len(recovery_segments), 2)
        self.assertEqual({"right", "left"},
                         {event["branch"] for event in recovery_segments[-2:]})
        self.assertTrue(all(event["choice"] == route["rollback"]["turn"]
                            for event in recovery_segments[-2:]))
        self.assertTrue(any(event["action"] == "paired_turn_room_recovery_selected"
                            for event in route["journal"]))
        self.assertGreater(len(route["abandoned_segments"]), 0)
        self.assertEqual(len(route["abandoned_segments"]),
                         len([event for event in route["journal"] if
                              event["action"] == "abandoned_segment"]))

    def test_recovery_lead_and_catch_up_are_separate_rounds(self):
        polygon = ((0, 0), (2000, 0), (2000, 1500), (0, 1500))
        route = build_vector_route(polygon, (700, 75), (550, 75),
                                   (0, 1), 8)
        journal = route["journal"]
        skips = [event for event in journal if
                 event["action"] == "recovery_lead_skip_first"]
        self.assertEqual(1, len(skips))
        catch_round = skips[0]["round"]
        lead_moves = [event for event in journal if
                      event["action"] == "segment" and
                      event.get("round") == catch_round - 1]
        self.assertEqual({"right": "forward", "left": "forward"},
                         {event["branch"]: event["choice"]
                          for event in lead_moves})
        catch_moves = [event for event in journal if
                       event["action"] == "segment" and
                       event.get("round") == catch_round]
        self.assertEqual(["left"], [event["branch"] for event in catch_moves])
        self.assertTrue(route["join"]["connected"])
        self.assertEqual(route["turns_by_branch"]["right"],
                         route["turns_by_branch"]["left"])

    def test_final_pair_does_not_reserve_a_future_turn(self):
        polygon = ((1000, 2100), (0, 2100), (0, 3300),
                   (4100, 3300), (4100, 0), (1000, 0))
        route = build_vector_route(polygon, (1400, 75), (1100, 75),
                                   (0, 1), 100)
        journal = route["journal"]
        terminal = [event for event in journal if
                    event["action"] == "terminal_pair_selected"]
        self.assertEqual(1, len(terminal))
        self.assertTrue(route["join"]["connected"])
        self.assertEqual(150, abs(route["join"]["bridge"][0][1] -
                                  route["join"]["bridge"][1][1]))
        final_round = terminal[0]["round"]
        final_segments = [event for event in journal if
                          event["action"] == "segment" and
                          event.get("round") == final_round]
        self.assertEqual({"right", "left"},
                         {event["branch"] for event in final_segments})
        self.assertEqual(150, [event["length_mm"] for event in
                               final_segments if event["branch"] == "left"][0])
        self.assertFalse(any(event["action"] == "recovery_continuation_rollback"
                             for event in journal))

    def test_pair_recovery_tries_straight_between_side_turns(self):
        polygon = ((0, 3400), (1500, 3400), (1500, 4500),
                   (2700, 4500), (2700, 3400), (4600, 3400),
                   (4600, 0), (0, 0))
        cases = ((polygon, (400, 75), (100, 75), False, "right"),
                 (tuple((4600 - x, y) for x, y in reversed(polygon)),
                  (4500, 75), (4200, 75), True, "left"))
        for contour, right_start, left_start, clockwise, preferred in cases:
            route = build_vector_route(contour, right_start, left_start,
                                       (0, 1), 100, clockwise=clockwise)
            journal = route["journal"]
            selected = [index for index, event in enumerate(journal) if
                        event["action"] == "pair_forward_recovery"]
            self.assertTrue(selected)
            index = selected[0]
            probes = [event for event in journal[:index] if
                      event["action"] == "pair_recovery_checkpoint_probe" and
                      event["round"] == 11]
            self.assertTrue(probes)
            self.assertTrue(all(event["candidate"] == preferred
                                for event in probes))
            self.assertEqual("forward", route["rollback"]["turn"])
            rejected = [position for position, event in enumerate(journal) if
                        event["action"] == "rejected_dead_end_opening" and
                        position > index]
            self.assertTrue(rejected)
            self.assertTrue(any(event["action"] == "segment" and
                                event.get("choice") == "forward" and
                                event.get("length_mm", 0) > 150 and
                                position > rejected[0]
                                for position, event in enumerate(journal)))
            self.assertTrue(route["join"]["connected"])

    def test_clockwise_route_mirrors_counterclockwise_route(self):
        cases = (
            (((0, 0), (4000, 0), (4000, 3000), (0, 3000)),
             (1850, 2925), (2150, 2925), (0, -1), 60),
            (((0, 0), (2000, 0), (2000, 1500), (0, 1500)),
             (700, 75), (550, 75), (0, 1), 8),
            (((0, 3400), (1500, 3400), (1500, 4500),
              (2700, 4500), (2700, 3400), (4600, 3400),
              (4600, 0), (0, 0)),
             (400, 75), (100, 75), (0, 1), 100))
        for polygon, right_start, left_start, direction, limit in cases:
            width = max(point[0] for point in polygon)
            mirror = lambda point: (width - point[0], point[1])
            mirrored_polygon = tuple(mirror(point) for point in
                                     reversed(polygon))
            regular = build_vector_route(polygon, right_start, left_start,
                                         direction, limit)
            clockwise = build_vector_route(
                mirrored_polygon, mirror(left_start), mirror(right_start),
                (-direction[0], direction[1]), limit, clockwise=True)
            self.assertEqual(regular["join"]["connected"],
                             clockwise["join"]["connected"])
            for side, mirrored_side in (("right", "left"),
                                        ("left", "right")):
                self.assertEqual(regular["turns_by_branch"][side],
                                 clockwise["turns_by_branch"][mirrored_side])
                vertices = regular["branches"][side]["vertices"]
                mirrored = clockwise["branches"][mirrored_side]["vertices"]
                self.assertEqual(len(vertices), len(mirrored))
                for first, second in zip(vertices, mirrored):
                    expected = mirror(first)
                    self.assertAlmostEqual(expected[0], second[0], places=5)
                    self.assertAlmostEqual(expected[1], second[1], places=5)

    def test_tight_pair_continues_when_next_two_turns_are_valid(self):
        polygon = ((0, 0), (6000, 0), (6000, 4200), (0, 4200))
        route = build_vector_route(polygon, (2850, 4125), (3150, 4125),
                                   (0, -1), 60)
        deferred = [event for event in route["journal"] if
                    event["action"] == "paired_turn_room_recovery_deferred"]
        self.assertGreater(len(deferred), 0)
        for event in deferred:
            probes = [item for item in route["journal"] if
                      item["action"] == "pair_next_step_probe" and
                      item["round"] == event["round"] + 1]
            self.assertTrue(any(item["first_moved"] and
                                item["second_moved"] and
                                item["connected"] for item in probes))
        self.assertTrue(route["join"]["connected"])
        self.assertEqual(route["turns_by_branch"]["right"],
                         route["turns_by_branch"]["left"])

if __name__ == "__main__":
    unittest.main()
