# -*- coding: utf-8 -*-
from __future__ import absolute_import

import io
import json
import os
import unittest

from vector_walk import build_vector_route


class SavedPairedReturnTests(unittest.TestCase):
    def test_narrow_corridor_keeps_pipe_segment_counts(self):
        path = os.path.join(os.path.dirname(__file__),
                            "paired_return_case.json")
        with io.open(path, "r", encoding="utf-8") as stream:
            case = json.load(stream)
        parameters = case["input"]
        route = build_vector_route(
            parameters["polygon"], parameters["right_start"],
            parameters["left_start"], parameters["entry_direction"],
            parameters["max_turns"],
            offset_mm=parameters["offset_mm"],
            spacing_mm=parameters["spacing_mm"],
            min_segment_mm=parameters["min_segment_mm"],
            clockwise=parameters["clockwise"])
        expected = case["expected"]["segments_by_branch"]
        for side in ("right", "left"):
            self.assertEqual(expected[side],
                             len(route["branches"][side]["vertices"]) - 1)


if __name__ == "__main__":
    unittest.main()
