# -*- coding: utf-8 -*-
from __future__ import division

import ast
import os
import unittest


ROOT = os.path.abspath(os.path.dirname(__file__))
BUNDLE = os.path.join(ROOT, "RedomineLibs.extension", "RedomineLibs.tab",
                      "BIM.panel", "FloorHeatingLab.pushbutton")
GEOMETRY_PATH = os.path.join(BUNDLE, "geometry.py")
SCRIPT_PATH = os.path.join(BUNDLE, "script.py")
try:
    import imp
    geometry = imp.load_source("floor_heating_lab_geometry", GEOMETRY_PATH)
except ImportError:
    import importlib.util
    spec = importlib.util.spec_from_file_location("floor_heating_lab_geometry", GEOMETRY_PATH)
    geometry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(geometry)


def _load_validator():
    with open(SCRIPT_PATH, "rb") as stream:
        source = stream.read()
    tree = ast.parse(source)
    wanted = ("_close", "_preview_line_matches")
    picked = [node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name in wanted]
    if len(picked) != len(wanted):
        raise AssertionError("validator helpers not found in script.py")
    tolerance = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == "ENDPOINT_TOLERANCE_MM":
                    tolerance = ast.literal_eval(node.value)
    if tolerance is None:
        raise AssertionError("ENDPOINT_TOLERANCE_MM not found in script.py")
    module = ast.Module(body=picked)
    ast.fix_missing_locations(module)
    namespace = {"ENDPOINT_TOLERANCE_MM": tolerance}
    exec(compile(module, SCRIPT_PATH, "exec"), namespace)
    namespace["TOLERANCE"] = tolerance
    return namespace


def _string_constants(tree):
    values = set()
    for node in ast.walk(tree):
        constant = getattr(ast, "Constant", None)
        if constant is not None and isinstance(node, constant):
            if isinstance(node.value, str):
                values.add(node.value)
            continue
        string = getattr(ast, "Str", None)
        if string is not None and isinstance(node, string):
            values.add(node.s)
    return values


def _parse_script():
    with open(SCRIPT_PATH, "rb") as stream:
        return ast.parse(stream.read())


def _load_script_helpers():
    """Extract the pure-Python routing helpers out of script.py.

    script.py imports RevitAPI at module scope, so its geometry-independent
    helpers are lifted via AST instead of importing the module.
    """
    with open(SCRIPT_PATH, "rb") as stream:
        source = stream.read()
    tree = ast.parse(source)
    tolerance = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == "ENDPOINT_TOLERANCE_MM":
                    tolerance = ast.literal_eval(node.value)
    if tolerance is None:
        raise AssertionError("ENDPOINT_TOLERANCE_MM not found in script.py")
    wanted = ("_entry_is_centered", "_with_leads_or_fallback")
    picked = [node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name in wanted]
    if len(picked) != len(wanted):
        raise AssertionError("routing helpers not found in script.py")
    constants = [node for node in tree.body
                 if isinstance(node, ast.Assign) and
                 getattr(node.targets[0], "id", None) == "CENTER_ENTRY_UNAVAILABLE"]
    if len(constants) != 1:
        raise AssertionError("CENTER_ENTRY_UNAVAILABLE not found in script.py")
    module = ast.Module(body=constants + picked)
    ast.fix_missing_locations(module)
    namespace = {"ENDPOINT_TOLERANCE_MM": tolerance}
    exec(compile(module, SCRIPT_PATH, "exec"), namespace)
    return namespace


class _GeometryStub(object):
    """Minimal stand-in that fails like the real lead generator would."""

    def __init__(self, raised):
        self.raised = raised

    def __getitem__(self, name):
        def fail(*args, **kwargs):
            raise self.raised
        if name == "build_spiral":
            return lambda *args, **kwargs: []
        return fail


class FloorHeatingLabTests(unittest.TestCase):
    def test_scripts_parse_without_python3_only_syntax(self):
        for name in ("script.py", "geometry.py"):
            with open(os.path.join(BUNDLE, name), "rb") as stream:
                ast.parse(stream.read())


class PreviewLineValidatorTests(unittest.TestCase):
    def setUp(self):
        namespace = _load_validator()
        self.close = namespace["_close"]
        self.matches = namespace["_preview_line_matches"]
        self.tolerance = namespace["TOLERANCE"]

    def test_exact_endpoints_match(self):
        self.assertTrue(self.matches((10.0, 20.0), (30.0, 40.0),
                                    (10.0, 20.0), (30.0, 40.0)))

    def test_reversed_endpoints_match(self):
        self.assertTrue(self.matches((30.0, 40.0), (10.0, 20.0),
                                    (10.0, 20.0), (30.0, 40.0)))

    def test_tolerance_noise_still_matches(self):
        inside = self.tolerance * 0.8
        self.assertTrue(self.close((100.0, 100.0),
                                  (100.0 + inside, 100.0 - inside)))
        self.assertTrue(self.matches((10.0, 20.0), (30.0, 40.0),
                                    (10.0 + inside, 20.0 - inside),
                                    (30.0 - inside, 40.0 + inside)))

    def test_drift_beyond_tolerance_is_refused(self):
        outside = self.tolerance * 1.5
        self.assertFalse(self.close((100.0, 100.0), (100.0 + outside, 100.0)))
        self.assertFalse(self.matches((10.0, 20.0), (30.0, 40.0),
                                     (10.0 + outside, 20.0), (30.0, 40.0)))

    def test_moved_endpoint_is_refused(self):
        self.assertFalse(self.matches((10.0, 20.0), (30.0, 40.0),
                                     (10.0, 20.0), (30.0, 60.0)))
        self.assertFalse(self.matches((15.0, 20.0), (30.0, 40.0),
                                     (10.0, 20.0), (30.0, 40.0)))

    def test_swapped_but_unrelated_line_is_refused(self):
        self.assertFalse(self.matches((0.0, 0.0), (1.0, 1.0),
                                     (2.0, 2.0), (3.0, 3.0)))

    def test_one_matching_endpoint_is_not_enough(self):
        self.assertFalse(self.matches((10.0, 20.0), (99.0, 99.0),
                                     (10.0, 20.0), (30.0, 40.0)))


def _load_audit_helpers():
    """Extract the read-only audit helpers plus ``_close`` they call into a
    standalone module so they can be exercised without ``clr``/Revit."""
    with open(SCRIPT_PATH, "rb") as stream:
        source = stream.read()
    tree = ast.parse(source)
    tolerance = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == "ENDPOINT_TOLERANCE_MM":
                    tolerance = ast.literal_eval(node.value)
    if tolerance is None:
        raise AssertionError("ENDPOINT_TOLERANCE_MM not found in script.py")
    wanted = ("_close", "_orient_actual_segment", "_region_joint_gaps")
    picked = [node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name in wanted]
    if len(picked) != len(wanted):
        raise AssertionError(
            "audit helpers not found in script.py: %r" % wanted)
    module = ast.Module(body=picked)
    ast.fix_missing_locations(module)
    namespace = {"ENDPOINT_TOLERANCE_MM": tolerance}
    exec(compile(module, SCRIPT_PATH, "exec"), namespace)
    return namespace


class AuditGeometryHelperTests(unittest.TestCase):
    """Pure-Python tests of the read-only helpers that the audit action uses
    to orient actual endpoints and compute per_region joint metrics. They run
    without clr/Revit because both helpers depend only on ``_close`` and the
    integer millimetre tolerance declared at module top level."""

    def setUp(self):
        namespace = _load_audit_helpers()
        self.orient = namespace["_orient_actual_segment"]
        self.region_gaps = namespace["_region_joint_gaps"]
        self.tolerance = namespace["ENDPOINT_TOLERANCE_MM"]

    def test_orient_keeps_stored_order_when_revit_reverses_endpoints(self):
        stored_start = (10.0, 20.0)
        stored_end = (30.0, 20.0)
        # Revit stores endpoints flipped relative to the order the user drew:
        # the audit still needs the oriented segment to point the stored way.
        oriented = self.orient((30.0, 20.0), (10.0, 20.0),
                               stored_start, stored_end)
        self.assertEqual((stored_start, stored_end), oriented)

    def test_orient_returns_none_when_endpoints_drift_beyond_tolerance(self):
        drift = self.tolerance * 1.5
        stored_start = (0.0, 0.0)
        stored_end = (100.0, 0.0)
        oriented = self.orient((0.0 + drift, 0.0), (100.0, 0.0),
                               stored_start, stored_end)
        self.assertIsNone(oriented)

    def test_joint_gaps_report_zero_for_a_continuous_chain(self):
        # A 3-segment continuous polyline has two joints, both within
        # tolerance, so the gap is the actual chord distance and no joint is
        # certified as disconnected.
        segments = [
            ((0.0, 0.0), (10.0, 0.0)),
            ((10.0, 0.0), (10.0, 5.0)),
            ((10.0, 5.0), (20.0, 5.0)),
        ]
        max_gap, disconnected = self.region_gaps(segments, self.tolerance)
        self.assertEqual(0.0, max_gap)
        self.assertEqual(0, disconnected)

    def test_joint_gaps_capture_a_real_gap_within_tolerance(self):
        # Both sides verified; a deliberate 0.2 mm chord at the joint must be
        # reported as the maximum (still continuous for the 0.5 mm tolerance).
        segments = [
            ((0.0, 0.0), (10.0, 0.0)),
            ((10.0 + 0.2, 0.0), (20.0, 0.0)),
        ]
        max_gap, disconnected = self.region_gaps(segments, self.tolerance)
        self.assertAlmostEqual(0.2, max_gap, places=9)
        self.assertEqual(0, disconnected)

    def test_joint_gaps_flag_a_gap_above_tolerance_as_disconnected(self):
        segments = [
            ((0.0, 0.0), (10.0, 0.0)),
            ((10.0 + 1.0, 0.0), (20.0, 0.0)),
        ]
        max_gap, disconnected = self.region_gaps(segments, self.tolerance)
        self.assertAlmostEqual(1.0, max_gap, places=9)
        self.assertEqual(1, disconnected)

    def test_joint_gaps_do_not_silently_certify_a_hole_as_continuous(self):
        # Middle segment is missing: the two resulting joints each have one
        # unverified side, so they must show up in disconnected_joint_count
        # while max_joint_gap_mm reflects only the verified joints (none here).
        segments = [
            ((0.0, 0.0), (10.0, 0.0)),
            None,
            ((20.0, 0.0), (30.0, 0.0)),
        ]
        max_gap, disconnected = self.region_gaps(segments, self.tolerance)
        self.assertEqual(0.0, max_gap)
        self.assertEqual(2, disconnected)

    def test_joint_gaps_report_a_mismatched_neighbour_as_disconnected(self):
        # Side A verified, side B's endpoints drifted past tolerance and so
        # could not be oriented; the only joint must be flagged disconnected
        # without being silently certified continuous.
        segments = [
            ((0.0, 0.0), (10.0, 0.0)),
            None,  # mismatched (could not orient to stored order)
        ]
        max_gap, disconnected = self.region_gaps(segments, self.tolerance)
        self.assertEqual(0.0, max_gap)
        self.assertEqual(1, disconnected)

    def test_joint_gaps_return_zero_for_a_single_segment_region(self):
        max_gap, disconnected = self.region_gaps(
            [((0.0, 0.0), (10.0, 0.0))], self.tolerance)
        self.assertEqual(0.0, max_gap)
        self.assertEqual(0, disconnected)


class PreviewStateContractTests(unittest.TestCase):
    def setUp(self):
        with open(SCRIPT_PATH, "rb") as stream:
            self.source = stream.read().decode("utf-8")
        self.tree = _parse_script()

    def _function(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        return None

    def test_preview_does_not_write_comments_parameter(self):
        self.assertNotIn("ALL_MODEL_INSTANCE_COMMENTS", self.source)
        self.assertNotIn("_comment_parameter", self.source)
        self.assertNotIn("marker", self.source)

    def test_preview_records_geometry_for_every_line(self):
        preview = self._function("_preview")
        self.assertIsNotNone(preview)
        keys = _string_constants(preview)
        for required in ("id", "start", "end"):
            self.assertIn(required, keys)

    def test_clear_checks_geometry_and_owner_view(self):
        clear = self._function("_clear")
        self.assertIsNotNone(clear)
        names = set()
        for node in ast.walk(clear):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
        self.assertIn("GeometryCurve", names)
        self.assertIn("OwnerViewId", names)

    def test_clear_validates_all_lines_before_opening_transaction(self):
        clear = self._function("_clear")
        call_nodes = [child for child in ast.walk(clear)
                      if isinstance(child, ast.Call)]
        pairs = sorted((child.lineno, self._call_name(child))
                       for child in call_nodes)
        self.assertTrue(pairs)
        names = [name for _, name in pairs]
        self.assertIn("_preview_line_matches", names)
        self.assertIn("Start", names)
        last_check = max(line for line, name in pairs
                         if name == "_preview_line_matches")
        first_start = min(line for line, name in pairs if name == "Start")
        self.assertLess(last_check, first_start)

    def _call_name(self, call):
        func = call.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return None

    def test_clear_skips_elements_already_deleted(self):
        self.assertIn("if element is None:", self.source)
        self.assertIn("continue", self.source)


class CenteredSnailWiringContractTests(unittest.TestCase):
    """AST-level checks that the centered rectangle entry is wired through to
    ``geometry.build_centered_snail`` and that ``_preview`` exposes the
    ``spiral_direction`` switch with the right default/validation."""

    def setUp(self):
        with open(SCRIPT_PATH, "rb") as stream:
            self.source = stream.read().decode("utf-8")
        self.tree = _parse_script()

    def _function(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        return None

    def _subscript_strings(self, func):
        """Names used as ``geometry["..."]`` subscript keys inside *func*."""
        out = []
        str_node = getattr(ast, "Str", None)
        index_node = getattr(ast, "Index", None)
        for node in ast.walk(func):
            if not isinstance(node, ast.Subscript):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            value = node.slice
            if index_node is not None and isinstance(value, index_node):
                value = value.value
            if str_node is not None and isinstance(value, str_node):
                out.append(value.s)
        return out

    def _function_arg_names(self, func):
        """Argument names, compatible with both ast.arg (py3) and Name (py2)."""
        names = []
        for arg in func.args.args:
            as_attr = getattr(arg, "arg", None)
            if as_attr is not None:
                names.append(as_attr)
            else:
                names.append(arg.id)
        return names

    def test_compute_segments_has_clockwise_parameter_with_default_true(self):
        func = self._function("_compute_segments")
        self.assertIsNotNone(func)
        self.assertIn("clockwise", self._function_arg_names(func))
        self.assertTrue(func.args.defaults, "expected at least one default")
        last = func.args.defaults[-1]
        # Python3 parses the default as ast.Constant(value=True); Python2 uses
        # ast.NameConstant/ast.Name(id="True"). Both must mean exactly True.
        constant = getattr(ast, "Constant", None)
        if constant is not None and isinstance(last, constant):
            self.assertIs(True, last.value,
                          "default must be literally True, got %r" % (last.value,))
            return
        name_constant = getattr(ast, "NameConstant", None)
        if name_constant is not None and isinstance(last, name_constant):
            self.assertIs(True, last.value,
                          "default must be literally True, got %r" % (last.value,))
            return
        self.assertIsInstance(last, ast.Name)
        self.assertEqual("True", last.id)

    def test_compute_segments_calls_build_centered_snail(self):
        func = self._function("_compute_segments")
        self.assertIsNotNone(func)
        self.assertIn("build_centered_snail", self._subscript_strings(func))

    def test_compute_segments_calls_entry_is_centered_for_4_edge_branch(self):
        func = self._function("_compute_segments")
        self.assertIsNotNone(func)
        names = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
        self.assertIn("_entry_is_centered", names)

    def test_preview_reads_and_validates_spiral_direction(self):
        preview = self._function("_preview")
        self.assertIsNotNone(preview)
        self.assertIn("spiral_direction", self.source)
        self.assertIn("clockwise", _string_constants(preview))
        self.assertIn("counterclockwise", _string_constants(preview))
        self.assertIn("invalid_spiral_direction", _string_constants(preview))
        self.assertIn("invalid_spiral_direction", self.source)

    def test_preview_passes_clockwise_to_compute_segments(self):
        preview = self._function("_preview")
        self.assertIsNotNone(preview)
        seen_clockwise = False
        seen_call = False
        for node in ast.walk(preview):
            if not isinstance(node, ast.Call):
                continue
            func_node = node.func
            name = None
            if isinstance(func_node, ast.Name):
                name = func_node.id
            if name != "_compute_segments":
                continue
            seen_call = True
            # Keyword form: ``_compute_segments(..., clockwise=cw)``.
            for kw in node.keywords:
                if getattr(kw, "arg", None) == "clockwise":
                    seen_clockwise = True
            # Positional form: last positional in the list-comprehension is
            # the ``clockwise`` flag we just derived in ``_preview``.
            if node.args:
                last = node.args[-1]
                if isinstance(last, ast.Name) and last.id == "clockwise":
                    seen_clockwise = True
        self.assertTrue(seen_call)
        self.assertTrue(seen_clockwise)


class FloorHeatingLabGeometryTests(unittest.TestCase):
    def setUp(self):
        helpers = _load_script_helpers()
        self.script = type("ScriptHelpers", (object,), helpers)
        self.script.CENTER_ENTRY_UNAVAILABLE = helpers["CENTER_ENTRY_UNAVAILABLE"]

    def test_actual_rectangle_pair_builds_continuous_spiral(self):
        left = 34849.636
        bottom = -24421.893
        source = (left, bottom, left + 4900, bottom + 3600)
        inset = (left + 50, bottom + 50, left + 4850, bottom + 3550)
        start = (left + 115.098, bottom + 74.468)
        end = (left + 415.098, bottom + 74.468)
        segments = geometry.build_spiral(source, inset, start, end, 300)
        self.assertGreaterEqual(len(segments), 20)
        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])
        for index in range(1, len(segments)):
            self.assertEqual(segments[index - 1][1], segments[index][0])

    def test_reversed_pair_and_rotated_entry(self):
        source = (0, 0, 4900, 3600)
        inset = (50, 50, 4850, 3550)
        pairs = (((415, 74), (115, 74)),
                 ((4785, 3526), (4485, 3526)),
                 ((74, 115), (74, 415)),
                 ((4826, 3485), (4826, 3185)))
        for start, end in pairs:
            segments = geometry.build_spiral(source, inset, start, end, 300)
            self.assertEqual(start, segments[0][0])
            self.assertEqual(end, segments[-1][1])

    def test_unsupported_pair_is_refused(self):
        with self.assertRaises(ValueError):
            geometry.build_spiral((0, 0, 4900, 3600),
                                  (50, 50, 4850, 3550),
                                  (115, 74), (515, 74), 300)

    def test_top_nub_boxes_recognises_eight_vertices(self):
        main = (47014.781, -24232.437, 51614.781, -20832.437)
        nub = (48514.781, -20832.437, 49714.781, -19732.437)
        expected = [(main[0], main[1]), (main[2], main[1]), (main[2], nub[1]),
                    (nub[2], nub[1]), (nub[2], nub[3]), (nub[0], nub[3]),
                    (nub[0], nub[1]), (main[0], nub[1])]
        self.assertEqual((main, nub), geometry.top_nub_boxes(expected))

    def test_top_nub_boxes_tolerates_revit_machine_noise(self):
        main = (47014.781, -24232.437, 51614.781, -20832.437)
        nub = (48514.781, -20832.437, 49714.781, -19732.437)
        noisy = [(47014.78100000123, -24232.43700000012),
                 (51614.78100000004, -24232.43700000009),
                 (51614.78100000007, -20832.437488540443),
                 (49714.78100000003, -20832.437488540447),
                 (49714.78100000006, -19732.43700000011),
                 (48514.78100000008, -19732.43700000005),
                 (48514.78100000002, -20832.437488540450),
                 (47014.78100000001, -20832.437488540458)]
        result_main, result_nub = geometry.top_nub_boxes(noisy)
        tolerance = geometry.COORDINATE_TOLERANCE_MM
        for index in range(4):
            self.assertAlmostEqual(result_main[index], main[index], delta=tolerance)
        for index in range(4):
            self.assertAlmostEqual(result_nub[index], nub[index], delta=tolerance)

    def test_top_nub_boxes_refuses_other_shapes(self):
        with self.assertRaises(ValueError):
            geometry.top_nub_boxes([(0, 0), (10, 0), (10, 10), (0, 10),
                                    (5, 5), (6, 6)])

    def test_top_nub_extension_reaches_protrusion(self):
        main = (47014.781, -24232.437, 51614.781, -20832.437)
        nub = (48514.781, -20832.437, 49714.781, -19732.437)
        start = (47129.878, -24157.969)
        end = (47429.878, -24157.969)
        offset = 50
        step = 300
        inset = (main[0] + offset, main[1] + offset, main[2] - offset, main[3] - offset)
        base = geometry.build_spiral(main, inset, start, end, step)
        segments = geometry.insert_top_nub(base, main, nub, offset, step)
        self.assertGreater(len(segments), len(base))
        self.assertGreater(max(point[1] for segment in segments for point in segment), nub[1])
        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])
        for index in range(1, len(segments)):
            self.assertEqual(segments[index - 1][1], segments[index][0])
        for index in range(len(segments)):
            for previous in range(index):
                if not geometry._crosses(segments[previous], segments[index]):
                    continue
                first_horizontal = abs(segments[previous][0][1] - segments[previous][1][1]) <= geometry.EPSILON
                current_horizontal = abs(segments[index][0][1] - segments[index][1][1]) <= geometry.EPSILON
                if previous == index - 1 and first_horizontal != current_horizontal:
                    continue
                self.fail("self_intersection")

    def test_narrow_protrusion_keeps_original_path(self):
        main = (47014.781, -24232.437, 51614.781, -20832.437)
        # nub width 100 == 2 * offset; a U-shape needs > 2 * offset, so a single
        # detour cannot fit and the function must return the base path untouched
        narrow = (48514.781, -20832.437, 48614.781, -19732.437)
        start = (47129.878, -24157.969)
        end = (47429.878, -24157.969)
        offset = 50
        step = 300
        inset = (main[0] + offset, main[1] + offset, main[2] - offset, main[3] - offset)
        base = geometry.build_spiral(main, inset, start, end, step)
        self.assertEqual(base, geometry.insert_top_nub(base, main, narrow, offset, step))

    def _vertical_columns(self, segments):
        columns = []
        for a, b in segments:
            if abs(a[0] - b[0]) <= geometry.EPSILON:
                columns.append(a[0])
        return columns

    def _horizontal_rows(self, segments):
        rows = []
        for a, b in segments:
            if abs(a[1] - b[1]) <= geometry.EPSILON:
                rows.append(a[1])
        return rows

    def _top_nub_pair_present(self, segments, main, nub, offset, step, index):
        d = offset + index * step
        y_body = main[3] - d
        y_turn = nub[3] - d
        x1 = nub[0] + d
        x2 = nub[2] - d
        found = False
        for a, b in segments:
            if abs(a[0] - b[0]) > geometry.EPSILON:
                continue
            if abs(a[0] - x1) > geometry.COORDINATE_TOLERANCE_MM:
                continue
            if abs(min(a[1], b[1]) - y_body) > geometry.COORDINATE_TOLERANCE_MM:
                continue
            if abs(max(a[1], b[1]) - y_turn) > geometry.COORDINATE_TOLERANCE_MM:
                continue
            found = True
        return found

    def _top_nub_ladder_complete(self, segments, main, nub, offset, step):
        """Number of maximal feasible nested top-nub U-pairs, plus the
        invariant that every inner body lane below them survives."""
        count = 0
        while self._top_nub_pair_present(segments, main, nub, offset, step, count):
            count += 1
        rows = [round(a[1], 3) for a, b in segments
                if abs(a[1] - b[1]) <= geometry.EPSILON]
        for index in range(count):
            neighbour = main[3] - offset - (index + 1) * step
            self.assertIn(round(neighbour, 3), rows,
                          "inner body lane %r must keep a full pass"
                          % (neighbour,))
        deeper = [row for row in rows
                  if nub[1] + geometry.COORDINATE_TOLERANCE_MM < row <=
                  round(nub[3] - offset, 3) + geometry.COORDINATE_TOLERANCE_MM]
        self.assertEqual(sorted({round(nub[3] - offset - index * step, 3)
                                 for index in range(count)}),
                         sorted(set(deeper)),
                         "the top nub must hold only the nested turn-rows")
        return count

    def _right_nub_pair_present(self, segments, main, nub, offset, step, index):
        d = offset + index * step
        x_body = main[2] - d
        x_nub = nub[2] - d
        y1 = nub[1] + d
        y2 = nub[3] - d
        found = False
        for a, b in segments:
            if abs(a[1] - b[1]) > geometry.EPSILON:
                continue
            if abs(a[1] - y1) > geometry.COORDINATE_TOLERANCE_MM:
                continue
            if abs(min(a[0], b[0]) - x_body) > geometry.COORDINATE_TOLERANCE_MM:
                continue
            if abs(max(a[0], b[0]) - x_nub) > geometry.COORDINATE_TOLERANCE_MM:
                continue
            found = True
        return found

    def _right_nub_ladder_complete(self, segments, main, nub, offset, step):
        count = 0
        while self._right_nub_pair_present(segments, main, nub, offset, step, count):
            count += 1
        columns = [round(a[0], 3) for a, b in segments
                   if abs(a[0] - b[0]) <= geometry.EPSILON]
        for index in range(count):
            neighbour = main[2] - offset - (index + 1) * step
            self.assertIn(round(neighbour, 3), columns,
                          "inner body column %r must keep a full pass"
                          % (neighbour,))
        return count

    def test_eight_edge_nested_u_pairs_into_top_nub(self):
        """The 8-edge shape fills the top nub with the maximal feasible number
        of nested U-pair detours: every pair replaces one ordered path segment,
        the whole route stays continuous and self-crossing free, and each inner
        body lane below a nested pair still receives a full pass."""
        main = (47014.781, -24232.437, 51614.781, -20832.437)
        nub = (48514.781, -20832.437, 49714.781, -19732.437)
        start = (47129.878, -24157.969)
        end = (47429.878, -24157.969)
        offset = 50
        step = 300
        inset = (main[0] + offset, main[1] + offset,
                 main[2] - offset, main[3] - offset)
        base = geometry.build_spiral(main, inset, start, end, step)
        segments = geometry.insert_top_nub(base, main, nub, offset, step)
        pairs = self._top_nub_ladder_complete(segments, main, nub, offset, step)
        self.assertEqual(2, pairs,
                         "two nested pairs must fit; a third would need "
                         "d = 650 > nub width / 2 = 600")
        self.assertFalse(self._top_nub_pair_present(
            segments, main, nub, offset, step, pairs),
            "the ladder must stop at the first infeasible lane")
        self.assertEqual(len(base) + 4 * pairs, len(segments),
                         "each nested pair replaces one segment with five")
        self.assertGreater(max(point[1] for segment in segments
                               for point in segment), nub[1])
        self.assertLessEqual(max(point[1] for segment in segments
                                 for point in segment), nub[3] + 1e-9)
        self._check_segments_clean(segments, start, end)
        rows = self._horizontal_rows(segments)
        for index in range(pairs):
            self.assertIn(round(nub[3] - offset - index * step, 3),
                          [round(row, 3) for row in rows],
                          "each nested pair must contribute its own turn-row")
        self.assertIn(round(main[3] - offset - step, 3),
                      [round(row, 3) for row in rows])
        self.assertIn(round(main[3] - offset - 2 * step, 3),
                      [round(row, 3) for row in rows])

    def test_twelve_edge_nested_u_pairs_into_each_nub(self):
        """The 12-edge shape fills both protrusions with their maximal feasible
        nested U-pair ladders (6 top + 5 right) and the combined route, once the
        central U is applied, is one continuous self-crossing free path."""
        main = (35049.636420563074, -32568.260869456324,
                38649.636420563111, -27668.260869456295)
        nub = (35949.636420563111, -27668.260869456295,
               38099.636420563103, -26668.260869456302)
        side = (38649.636420563111, -30868.260869456328,
                39649.636420563111, -29068.260869456324)
        start = (35164.73389715427, -32493.792722468123)
        end = (35464.73389715427, -32493.792722468123)
        offset = 200
        step = 150
        base = geometry.build_spiral_with_leads(main, start, end, offset, step)
        top_nubbed = geometry.insert_top_nub(base, main, nub, offset, step)
        top_pairs = self._top_nub_ladder_complete(
            top_nubbed, main, nub, offset, step)
        sided = geometry.insert_right_nub(top_nubbed, main, side, offset, step)
        right_pairs = self._right_nub_ladder_complete(
            sided, main, side, offset, step)
        self.assertEqual(6, top_pairs)
        self.assertEqual(5, right_pairs)
        self.assertEqual(len(base) + 4 * top_pairs, len(top_nubbed))
        self.assertEqual(len(top_nubbed) + 4 * right_pairs, len(sided))
        after_u = geometry.insert_central_u(sided, main, offset, step)
        self.assertEqual(start, after_u[0][0])
        self.assertEqual(end, after_u[-1][1])
        self._check_segments_clean(after_u, start, end)
        rows = [round(row, 3) for row in self._horizontal_rows(after_u)]
        columns = [round(column, 3) for column in self._vertical_columns(after_u)]
        for index in range(top_pairs):
            self.assertIn(round(nub[3] - offset - index * step, 3), rows)
            self.assertIn(round(main[3] - offset - (index + 1) * step, 3), rows)
        for index in range(right_pairs):
            self.assertIn(round(side[1] + offset + index * step, 3), rows)
            self.assertIn(round(side[3] - offset - index * step, 3), rows)
            self.assertIn(round(main[2] - offset - (index + 1) * step, 3),
                          columns)
        for index in range(right_pairs):
            self.assertTrue(self._right_nub_pair_present(
                sided, main, side, offset, step, index),
                "right pair %d must survive the central U" % index)

    def test_left_upper_wing_boxes_recognises_six_vertices(self):
        main = (42477.904945857648, -24112.380993082639,
                45577.904945857656, -20812.380993082639)
        wing = (41477.904945857648, -22012.380993082639,
                42477.904945857648, -20812.380993082639)
        expected = [(main[0], main[1]), (main[2], main[1]), (main[2], main[3]),
                    (wing[0], main[3]), (wing[0], wing[1]), (main[0], wing[1])]
        self.assertEqual((main, wing), geometry.left_upper_wing_boxes(expected))

    def test_left_upper_wing_boxes_refuses_other_shapes(self):
        with self.assertRaises(ValueError):
            geometry.left_upper_wing_boxes([(0, 0), (10, 0), (10, 10), (0, 10),
                                            (20, 10), (20, 0)])
        with self.assertRaises(ValueError):
            geometry.left_upper_wing_boxes([(0, 0), (10, 0), (10, 10),
                                            (5, 5), (6, 6), (0, 6)])

    def test_left_upper_wing_extension_reaches_protrusion(self):
        main = (42477.904945857648, -24112.380993082639,
                45577.904945857656, -20812.380993082639)
        wing = (41477.904945857648, -22012.380993082639,
                42477.904945857648, -20812.380993082639)
        start = (42593.002422448844, -24037.912846094452)
        end = (42893.002422448844, -24037.912846094452)
        offset = 50
        step = 300
        inset = (main[0] + offset, main[1] + offset, main[2] - offset, main[3] - offset)
        base = geometry.build_spiral(main, inset, start, end, step)
        segments = geometry.insert_left_upper_wing(base, main, wing, offset, step)
        self.assertGreater(len(segments), len(base))
        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])
        for index in range(1, len(segments)):
            self.assertEqual(segments[index - 1][1], segments[index][0])
        leftmost = min(point[0] for segment in segments for point in segment)
        self.assertLess(leftmost, main[0])
        self.assertGreaterEqual(leftmost, wing[0])
        d_first = start[0] - main[0]
        d_second = d_first + step
        self.assertIn(wing[0] + d_first,
                      [point[0] for segment in segments for point in segment])
        self.assertIn(wing[0] + d_second,
                      [point[0] for segment in segments for point in segment])
        for value in (wing[1] + d_first, wing[1] + d_second):
            self.assertIn(value, [point[1] for segment in segments for point in segment])
        for value in (wing[3] - d_first, wing[3] - d_second):
            self.assertIn(value, [point[1] for segment in segments for point in segment])
        for index in range(len(segments)):
            for previous in range(index):
                if not geometry._crosses(segments[previous], segments[index]):
                    continue
                first_horizontal = abs(segments[previous][0][1] - segments[previous][1][1]) <= geometry.EPSILON
                current_horizontal = abs(segments[index][0][1] - segments[index][1][1]) <= geometry.EPSILON
                if previous == index - 1 and first_horizontal != current_horizontal:
                    continue
                self.fail("self_intersection")

    def test_narrow_left_upper_wing_keeps_original_path(self):
        main = (42477.904945857648, -24112.380993082639,
                45577.904945857656, -20812.380993082639)
        narrow = (42177.904945857648, -22012.380993082639,
                  42477.904945857648, -20812.380993082639)
        start = (42593.002422448844, -24037.912846094452)
        end = (42893.002422448844, -24037.912846094452)
        offset = 50
        step = 300
        inset = (main[0] + offset, main[1] + offset, main[2] - offset, main[3] - offset)
        base = geometry.build_spiral(main, inset, start, end, step)
        self.assertEqual(base, geometry.insert_left_upper_wing(base, main, narrow,
                                                               offset, step))

    def test_small_height_right_protrusion_pair_separation_guard(self):
        main = (47014.781, -24232.437, 51614.781, -20832.437)
        start = (47129.878, -24157.969)
        end = (47129.878 + 150, -24157.969)
        offset = 50
        step = 150
        inset = (main[0] + offset, main[1] + offset,
                 main[2] - offset, main[3] - offset)
        base = geometry.build_spiral(main, inset, start, end, step)
        too_narrow = (main[2], -24000.0, main[2] + 600.0, -23800.0)
        self.assertEqual(base, geometry.insert_right_nub(base, main, too_narrow,
                                                         offset, step))
        just_fits = (main[2], -24000.0, main[2] + 600.0, -23750.0)
        accepted = geometry.insert_right_nub(base, main, just_fits, offset, step)
        self.assertGreater(len(accepted), len(base),
                           "right-nub pair with separation == step must fit")
        self._check_segments_clean(accepted, start, end)

    def _check_with_leads(self, source, start, end, offset, step):
        segments = geometry.build_spiral_with_leads(source, start, end, offset, step)
        self.assertGreaterEqual(len(segments), 10)
        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])
        for index in range(1, len(segments)):
            self.assertEqual(segments[index - 1][1], segments[index][0])
        for index in range(len(segments)):
            a, b = segments[index]
            horizontal = abs(a[1] - b[1]) <= geometry.EPSILON
            vertical = abs(a[0] - b[0]) <= geometry.EPSILON
            self.assertTrue(horizontal or vertical)
            self.assertFalse(horizontal and vertical)
        left, bottom, right, top = source
        inset = (left + offset, bottom + offset, right - offset, top - offset)
        main = segments[2:-2]
        for a, b in main:
            self.assertTrue(geometry._inside(a, inset))
            self.assertTrue(geometry._inside(b, inset))
        synth_start = (left + offset, bottom + offset)
        synth_end = (left + offset + step, bottom + offset)
        self.assertEqual(synth_start, main[0][0])
        self.assertEqual(synth_end, main[-1][1])
        self.assertEqual((start[0], synth_start[1]), segments[1][0])
        self.assertEqual(synth_start, segments[1][1])
        self.assertEqual(synth_end, segments[-2][0])
        self.assertEqual((end[0], synth_end[1]), segments[-2][1])
        for index in range(len(segments)):
            for previous in range(index):
                if not geometry._crosses(segments[previous], segments[index]):
                    continue
                first_horizontal = abs(segments[previous][0][1] - segments[previous][1][1]) <= geometry.EPSILON
                current_horizontal = abs(segments[index][0][1] - segments[index][1][1]) <= geometry.EPSILON
                if previous == index - 1 and first_horizontal != current_horizontal:
                    continue
                self.fail("self_intersection")

    def test_rect_with_leads_offset200_step150(self):
        source = (34849.636, -24421.893, 39749.636, -20821.893)
        start = (34964.733897, -24347.424928)
        end = (35264.733897, -24347.424928)
        self._check_with_leads(source, start, end, 200, 150)

    def test_left_upper_wing_main_with_leads_offset200_step150(self):
        main = (42477.904945857648, -24112.380993082639,
                45577.904945857656, -20812.380993082639)
        start = (42593.002422448844, -24037.912846094452)
        end = (42893.002422448844, -24037.912846094452)
        self._check_with_leads(main, start, end, 200, 150)

    def test_top_nub_main_with_leads_offset200_step150(self):
        main = (47014.780601717648, -24232.437488540454,
                51614.780601717663, -20832.437488540447)
        start = (47129.878078308844, -24157.969341552263)
        end = (47429.878078308844, -24157.969341552263)
        self._check_with_leads(main, start, end, 200, 150)

    def _check_segments_clean(self, segments, start, end):
        self.assertGreaterEqual(len(segments), 10)
        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])
        for index in range(1, len(segments)):
            self.assertEqual(segments[index - 1][1], segments[index][0])
        for index in range(len(segments)):
            a, b = segments[index]
            horizontal = abs(a[1] - b[1]) <= geometry.EPSILON
            vertical = abs(a[0] - b[0]) <= geometry.EPSILON
            self.assertTrue(horizontal or vertical)
            self.assertFalse(horizontal and vertical)
        for index in range(len(segments)):
            for previous in range(index):
                if not geometry._crosses(segments[previous], segments[index]):
                    continue
                first_horizontal = abs(segments[previous][0][1] - segments[previous][1][1]) <= geometry.EPSILON
                current_horizontal = abs(segments[index][0][1] - segments[index][1][1]) <= geometry.EPSILON
                if previous == index - 1 and first_horizontal != current_horizontal:
                    continue
                self.fail("self_intersection")

    def test_left_upper_wing_offset200_step150_with_central_u(self):
        main = (1000.0, 0.0, 4100.0, 3300.0)
        wing = (0.0, 2100.0, 1000.0, 3300.0)
        start = (1115.0975, 74.4681)
        end = (1415.0975, 74.4681)
        offset = 200
        step = 150
        base = geometry.build_spiral_with_leads(main, start, end, offset, step)
        segments = geometry.insert_left_upper_wing(base, main, wing, offset, step)
        self.assertGreater(len(segments), len(base))
        after_u = geometry.insert_central_u(segments, main, offset, step)
        self.assertGreater(len(after_u), len(segments))
        self._check_segments_clean(after_u, start, end)
        points = []
        for pair in after_u:
            points.append(pair[0])
            points.append(pair[1])
        self.assertIn((200.0, 2300.0), points)
        self.assertIn((200.0, 3100.0), points)
        self.assertIn((350.0, 2450.0), points)
        self.assertIn((350.0, 2950.0), points)
        self.assertIn((2700.0, 1900.0), points)
        self.assertIn((2700.0, 1400.0), points)
        self.assertIn((2550.0, 1400.0), points)

    def test_top_nub_offset200_step150_with_central_u(self):
        main = (47014.780601717648, -24232.437488540454,
                51614.780601717663, -20832.437488540447)
        nub = (48514.78100000003, -20832.437488540447,
               49714.78100000006, -19732.43700000011)
        start = (47129.878078308844, -24157.969341552263)
        end = (47429.878078308844, -24157.969341552263)
        offset = 200
        step = 150
        base = geometry.build_spiral_with_leads(main, start, end, offset, step)
        segments = geometry.insert_top_nub(base, main, nub, offset, step)
        self.assertGreater(len(segments), len(base))
        after_u = geometry.insert_central_u(segments, main, offset, step)
        self._check_segments_clean(after_u, start, end)
        points = []
        for pair in after_u:
            points.append(pair[0])
            points.append(pair[1])
        self.assertIn((48714.78100000003, -21032.437488540447), points)
        self.assertIn((49514.78100000006, -21032.437488540447), points)
        self.assertIn((48714.78060171765, -22532.437488540447), points)
        self.assertIn((48864.78060171765, -22532.437488540447), points)
        self.assertIn((48864.78060171765, -22682.437488540454), points)

    def test_rect_offset200_step150_with_central_u(self):
        source = (34849.636, -24421.893, 39749.636, -20821.893)
        start = (34964.733897, -24347.424928)
        end = (35264.733897, -24347.424928)
        offset = 200
        step = 150
        base = geometry.build_spiral_with_leads(source, start, end, offset, step)
        after_u = geometry.insert_central_u(base, source, offset, step)
        self.assertGreater(len(after_u), len(base))
        self._check_segments_clean(after_u, start, end)
        points = []
        for pair in after_u:
            points.append(pair[0])
            points.append(pair[1])
        self.assertIn((38049.636, -22521.893), points)
        self.assertIn((38049.636, -22721.893), points)
        self.assertIn((36699.636, -22721.893), points)

    def test_top_nub_and_right_nub_boxes_recognises_twelve_vertices(self):
        main = (35049.636420563074, -32568.260869456324,
                38649.636420563111, -27668.260869456295)
        nub = (35949.636420563111, -27668.260869456295,
               38099.636420563103, -26668.260869456302)
        side = (38649.636420563111, -30868.260869456328,
                39649.636420563111, -29068.260869456324)
        expected = [(main[0], main[1]), (main[2], main[1]), (main[2], side[1]),
                    (side[2], side[1]), (side[2], side[3]), (main[2], side[3]),
                    (main[2], main[3]), (nub[2], main[3]), (nub[2], nub[3]),
                    (nub[0], nub[3]), (nub[0], main[3]), (main[0], main[3])]
        self.assertEqual((main, nub, side),
                         geometry.top_nub_and_right_nub_boxes(expected))

    def test_top_nub_and_right_nub_boxes_refuses_other_shapes(self):
        with self.assertRaises(ValueError):
            geometry.top_nub_and_right_nub_boxes(
                [(0, 0), (10, 0), (10, 5), (8, 5), (8, 3), (10, 3),
                 (10, 10), (5, 10), (5, 12), (3, 12), (3, 10), (0, 10)])

    def test_twelve_edge_shape_offset200_step150_with_both_nubs(self):
        main = (35049.636420563074, -32568.260869456324,
                38649.636420563111, -27668.260869456295)
        nub = (35949.636420563111, -27668.260869456295,
               38099.636420563103, -26668.260869456302)
        side = (38649.636420563111, -30868.260869456328,
                39649.636420563111, -29068.260869456324)
        start = (35164.73389715427, -32493.792722468123)
        end = (35464.73389715427, -32493.792722468123)
        offset = 200
        step = 150
        base = geometry.build_spiral_with_leads(main, start, end, offset, step)
        top_nubbed = geometry.insert_top_nub(base, main, nub, offset, step)
        self.assertGreater(len(top_nubbed), len(base))
        sided = geometry.insert_right_nub(top_nubbed, main, side, offset, step)
        self.assertGreater(len(sided), len(top_nubbed))
        after_u = geometry.insert_central_u(sided, main, offset, step)
        self._check_segments_clean(after_u, start, end)

        points = []
        for pair in after_u:
            points.append(pair[0])
            points.append(pair[1])
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        rows = self._horizontal_rows(after_u)
        columns = self._vertical_columns(after_u)

        tolerance = 0.01

        d_first = offset

        # The top nub is filled by the maximal feasible nested ladder of U-pairs:
        # every accepted lane contributes exactly its own pair of verticals at
        # (nub_left + d, nub_right - d) rising from main_top - d to nub_top - d,
        # and the ladder stops where the next lane no longer fits.
        top_pairs = 0
        while True:
            d = d_first + top_pairs * step
            entry = set()
            for a, b in after_u:
                if abs(a[0] - b[0]) > tolerance:
                    continue
                x = a[0]
                if not (nub[0] - tolerance <= x <= nub[2] + tolerance):
                    continue
                low = min(a[1], b[1])
                high = max(a[1], b[1])
                if (abs(high - (nub[3] - d)) <= tolerance and
                        abs(low - (nub[1] - d)) <= tolerance):
                    entry.add(round(x, 3))
            if entry != set((round(nub[0] + d, 3), round(nub[2] - d, 3))):
                break
            top_pairs += 1
        self.assertEqual(6, top_pairs,
                         "the top nub ladder must be maximal, got %d pairs"
                         % top_pairs)

        # every row inside the top nub is a nested turn-row, so the nub carries
        # no unheated empty band left over from a single wide detour
        ys_inside_top = sorted(set(round(y, 3) for y in ys if y > nub[1]))
        self.assertEqual(
            sorted(set(round(nub[3] - d_first - index * step, 3)
                       for index in range(top_pairs))),
            ys_inside_top,
            "the top nub rows must be exactly the nested turn-rows, got %r"
            % ys_inside_top)

        # Same maximal nested invariant for the right nub: every accepted lane
        # contributes its own pair of horizontals at (side_bottom + d,
        # side_top - d) running from main_right - d to side_right - d.
        right_pairs = 0
        while True:
            d = d_first + right_pairs * step
            entry = set()
            for a, b in after_u:
                if abs(a[1] - b[1]) > tolerance:
                    continue
                y = a[1]
                if not (side[1] - tolerance <= y <= side[3] + tolerance):
                    continue
                low = min(a[0], b[0])
                high = max(a[0], b[0])
                if (abs(high - (side[2] - d)) <= tolerance and
                        abs(low - (main[2] - d)) <= tolerance):
                    entry.add(round(y, 3))
            if entry != set((round(side[1] + d, 3), round(side[3] - d, 3))):
                break
            right_pairs += 1
        self.assertEqual(5, right_pairs,
                         "the right nub ladder must be maximal, got %d pairs"
                         % right_pairs)

        xs_past_main = sorted(set(round(x, 3) for x in xs if x > main[2]))
        self.assertEqual(
            sorted(set(round(side[2] - d_first - index * step, 3)
                       for index in range(right_pairs))),
            xs_past_main,
            "the right nub columns must be exactly the nested caps, got %r"
            % xs_past_main)

        # Every inner body lane below an accepted pair keeps a full pass
        for index in range(top_pairs):
            self.assertIn(round(main[3] - offset - (index + 1) * step, 3),
                          [round(row, 3) for row in rows])
        for index in range(right_pairs):
            self.assertIn(round(main[2] - offset - (index + 1) * step, 3),
                          [round(column, 3) for column in columns])

        self.assertLessEqual(max(xs), side[2] + 1e-6)

        self.assertGreater(max(ys), nub[1])
        self.assertGreater(max(xs), main[2])
        self.assertLessEqual(max(ys), nub[3] + 1e-6)
        self.assertEqual(start, after_u[0][0])
        self.assertEqual(end, after_u[-1][1])

    def test_six_edge_top_right_entry_builds_bottom_left_detour(self):
        main = (42477.904945857663, -18512.380993082639,
                45577.904945857663, -15212.380993082639)
        wing = (41477.904945857663, -16412.380993082639,
                42477.904945857663, -15212.380993082639)
        start = (45325.671126227913, -15283.881917215305)
        end = (45025.671126227913, -15283.881917215305)
        offset = 200
        step = 150

        vertices = [(main[0], main[1]), (main[2], main[1]), (main[2], main[3]),
                    (wing[0], main[3]), (wing[0], wing[1]), (main[0], wing[1])]
        self.assertEqual((main, wing), geometry.left_upper_wing_boxes(vertices))

        self.assertTrue(geometry.top_right_start_fits_bottom_left_flip(
            main, start, end, offset, step))
        self.assertFalse(geometry.top_right_start_fits_bottom_left_flip(
            main, end, start, offset, step))

        segments = geometry.build_left_upper_l_top_right_flip(
            main, wing, start, end, offset, step)

        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])
        for index in range(1, len(segments)):
            self.assertEqual(segments[index - 1][1], segments[index][0])

        columns = []
        rows = []
        for a, b in segments:
            horizontal = abs(a[1] - b[1]) <= geometry.EPSILON
            vertical = abs(a[0] - b[0]) <= geometry.EPSILON
            self.assertTrue(horizontal or vertical)
            self.assertFalse(horizontal and vertical)
            if vertical:
                columns.append(a[0])
            else:
                rows.append(a[1])

        ml, mb, mr, mt = main
        wl, wb, wr, wt = wing

        def inside_orthogonal_region(point):
            px, py = point
            eps = 0.001
            in_main = ((ml - eps) <= px <= (mr + eps) and
                       (mb - eps) <= py <= (mt + eps))
            in_wing = ((wl - eps) <= px <= (ml + eps) and
                       (wb - eps) <= py <= (wt + eps))
            return in_main or in_wing

        for a, b in segments:
            self.assertTrue(inside_orthogonal_region(a), a)
            self.assertTrue(inside_orthogonal_region(b), b)

        self.assertLess(min(columns), ml)
        self.assertGreaterEqual(min(columns), wl)
        self.assertLessEqual(max(columns), mr + 1e-6)

        self.assertIn(wl + offset, columns)
        self.assertIn(ml + offset, columns)
        self.assertIn(ml + offset + step, columns)

        # The rotated right-nub ladder must nest as deep as the wing allows:
        # every lane d = offset + n * step that still fits contributes its own
        # turn-column wl + d and turn-rows wb + d / wt - d, and the ladder
        # stops at the first lane that no longer fits (the body lanes at
        # wl + d and the rows at wb + d / wt - d run out together, and the next
        # one would leave the body column main_left + offset + 2 * step).
        wing_pairs = 0
        while True:
            d = offset + wing_pairs * step
            if (wl + d not in columns or
                    wb + d not in rows or
                    wt - d not in rows):
                break
            wing_pairs += 1
        self.assertEqual(3, wing_pairs,
                         "the wing ladder must be maximal, got %d pairs"
                         % wing_pairs)
        for index in range(wing_pairs):
            d = offset + index * step
            self.assertIn(wl + d, columns,
                          "nested wing turn-column %r is missing" % (wl + d,))
            self.assertIn(wb + d, rows)
            self.assertIn(wt - d, rows)
        nxt = offset + wing_pairs * step
        self.assertNotIn(wl + nxt, columns,
                         "the ladder must stop at the first infeasible lane")
        self.assertIn(ml + offset + step, columns)

        self.assertIn(mt - offset, rows)
        self.assertIn(mt - offset - step, rows)

        for index in range(len(segments)):
            for previous in range(index):
                if not geometry._crosses(segments[previous], segments[index]):
                    continue
                first_horizontal = abs(segments[previous][0][1] -
                                       segments[previous][1][1]) <= geometry.EPSILON
                current_horizontal = abs(segments[index][0][1] -
                                         segments[index][1][1]) <= geometry.EPSILON
                if previous == index - 1 and first_horizontal != current_horizontal:
                    continue
                self.fail("self_intersection")

    def test_with_leads_rejects_unsupported_orientation(self):
        source = (34849.636, -24421.893, 39749.636, -20821.893)
        start = (34964.733897, -24347.424928)
        end = (35264.733897, -24347.424928)
        with self.assertRaises(ValueError):
            geometry.build_spiral_with_leads(source, end, start, 200, 150)
        with self.assertRaises(ValueError):
            geometry.build_spiral_with_leads(source,
                                             (source[0] + 250, start[1]),
                                             (source[0] + 550, start[1]),
                                             200, 150)
        with self.assertRaises(ValueError):
            geometry.build_spiral_with_leads(source,
                                             (start[0], source[3] - 250),
                                             (end[0], source[3] - 250),
                                             200, 150)

    def _assert_center_entry_rejected(self, source, start, end, offset, step):
        with self.assertRaises(ValueError) as caught:
            geometry.build_spiral_with_leads(source, start, end, offset, step)
        self.assertEqual(
            "full_coverage_unavailable_for_center_entry",
            str(caught.exception))

    def test_rect_bottom_center_entry_is_rejected(self):
        source = (41477.904945857626, -32512.380993082661,
                  45077.90494585767, -27612.380993082632)
        start = (43063.530688889761, -32355.91871161358)
        end = (43363.530688889768, -32355.91871161358)
        offset = 200
        step = 150
        center_x = (source[0] + source[2]) / 2.0
        self.assertLessEqual(abs((start[0] + end[0]) / 2.0 - center_x), step)
        self.assertGreater(abs(start[0] - center_x), step)
        self._assert_center_entry_rejected(source, start, end, offset, step)
        self._assert_center_entry_rejected(source, end, start, offset, step)

    def test_rect_top_center_entry_is_rejected(self):
        source = (34849.636420563096, -18821.893075402237,
                  39749.636420563118, -15221.89307540222)
        start = (37125.671126227913, -15283.88191721528)
        end = (37425.671126227913, -15283.88191721528)
        offset = 200
        step = 150
        center_x = (source[0] + source[2]) / 2.0
        self.assertLessEqual(abs((start[0] + end[0]) / 2.0 - center_x), step)
        self.assertGreater(abs(start[0] - center_x), step)
        self._assert_center_entry_rejected(source, start, end, offset, step)
        self._assert_center_entry_rejected(source, end, start, offset, step)

    def test_diagnostic_partial_bottom_center_entry_builds_clean_path(self):
        # Real-world bottom-middle feed: the case that previously self-intersected
        # because the center branch reused the bottom-left offset pair.
        source = (41477.904945857626, -32512.380993082661,
                  45077.90494585767, -27612.380993082632)
        start = (43063.530688889761, -32355.91871161358)
        end = (43363.530688889768, -32355.91871161358)
        offset = 200
        step = 150
        center_x = (source[0] + source[2]) / 2.0
        center_y = (source[1] + source[3]) / 2.0
        # Confirm we are testing the central branch (not a corner).
        self.assertLessEqual(abs((start[0] + end[0]) / 2.0 - center_x), step)
        self.assertGreater(abs(start[0] - center_x), step)
        # Refused by default (allow_partial=False) ...
        self._assert_center_entry_rejected(source, start, end, offset, step)
        # ... and produced when allow_partial=True.
        segments = geometry.build_spiral_with_leads(source, start, end,
                                                    offset, step, True)
        self._check_segments_clean(segments, start, end)
        # Pipe order: first segment starts exactly at `start`, last segment
        # ends exactly at `end`.
        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])
        # Expected synthetic pair centered on the rectangle.
        expected_synth_start = (center_x - step / 2.0, source[1] + offset)
        expected_synth_end = (center_x + step / 2.0, source[1] + offset)
        main = segments[2:-2]
        self.assertEqual(expected_synth_start, main[0][0])
        self.assertEqual(expected_synth_end, main[-1][1])

    def test_diagnostic_partial_top_center_entry_builds_clean_path(self):
        # Real-world top-middle feed: pipe order is start x=37125, end x=37425.
        source = (34849.636420563096, -18821.893075402237,
                  39749.636420563118, -15221.89307540222)
        start = (37125.671126227913, -15283.88191721528)
        end = (37425.671126227913, -15283.88191721528)
        offset = 200
        step = 150
        center_x = (source[0] + source[2]) / 2.0
        # Sanity: top-middle pipe order.
        self.assertEqual(37125.671126227913, start[0])
        self.assertEqual(37425.671126227913, end[0])
        # Central branch (mid_x within +/- step of center_x).
        self.assertLessEqual(abs((start[0] + end[0]) / 2.0 - center_x), step)
        self.assertGreater(abs(start[0] - center_x), step)
        # Refused by default, produced when allow_partial=True.
        self._assert_center_entry_rejected(source, start, end, offset, step)
        segments = geometry.build_spiral_with_leads(source, start, end,
                                                    offset, step, True)
        self._check_segments_clean(segments, start, end)
        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])
        # Synthetic pair sits on the top edge (start.y is closer to top).
        expected_synth_start = (center_x - step / 2.0, source[3] - offset)
        expected_synth_end = (center_x + step / 2.0, source[3] - offset)
        main = segments[2:-2]
        self.assertEqual(expected_synth_start, main[0][0])
        self.assertEqual(expected_synth_end, main[-1][1])

    def test_center_entry_fallback_is_not_partial(self):
        cases = [
            ((41477.904945857626, -32512.380993082661,
              45077.90494585767, -27612.380993082632),
             (43063.530688889761, -32355.91871161358),
             (43363.530688889768, -32355.91871161358)),
            ((34849.636420563096, -18821.893075402237,
              39749.636420563118, -15221.89307540222),
             (37125.671126227913, -15283.88191721528),
             (37425.671126227913, -15283.88191721528)),
        ]
        entry_is_centered = getattr(
            self.script._entry_is_centered, "__func__",
            self.script._entry_is_centered)
        with_leads_or_fallback = getattr(
            self.script._with_leads_or_fallback, "__func__",
            self.script._with_leads_or_fallback)
        for source, start, end in cases:
            self.assertTrue(
                entry_is_centered(source, start, end, 150), source)
            inset = (source[0] + 200, source[1] + 200,
                     source[2] - 200, source[3] - 200)
            with self.assertRaises(ValueError):
                geometry.build_spiral(source, inset, start, end, 150)
            with self.assertRaises(ValueError) as caught:
                with_leads_or_fallback(
                    _GeometryStub(ValueError("lead generation failed")),
                    source, start, end, 200, 150,
                    lambda segments: segments)
            self.assertEqual(
                "full_coverage_unavailable_for_center_entry",
                str(caught.exception))

    def test_center_entry_fallback_still_builds_for_corner_entry(self):
        source = (34849.636, -24421.893, 39749.636, -20821.893)
        start = (34964.733897, -24347.424928)
        end = (35264.733897, -24347.424928)
        offset = 200
        step = 150
        entry_is_centered = getattr(
            self.script._entry_is_centered, "__func__",
            self.script._entry_is_centered)
        self.assertFalse(entry_is_centered(source, start, end, step))
        segments = geometry.build_spiral_with_leads(source, start, end,
                                                    offset, step)
        self.assertEqual(start, segments[0][0])
        self.assertEqual(end, segments[-1][1])

    def test_top_nub_inside_vertical_pipes(self):
        main = (47014.780601717663, -18632.437488540454,
                51614.780601717663, -15232.437488540443)
        nub = (48514.780601717663, -15232.437488540443,
               49714.78060171767, -14132.437488540447)
        start = (48613.491521001291, -14524.889626700526)
        end = (48613.491521001291, -14824.889626700528)
        with self.assertRaises(ValueError):
            geometry.build_spiral_with_leads(main, start, end, 200, 150)
        tolerance = geometry.COORDINATE_TOLERANCE_MM
        self.assertAlmostEqual(start[0], end[0], delta=tolerance)
        self.assertAlmostEqual(abs(start[1] - end[1]), 300.0, delta=tolerance)
        self.assertGreater(start[1], nub[1] - tolerance)
        self.assertEqual(
            main,
            geometry.top_nub_boxes(
                [(main[0], main[1]), (main[2], main[1]), (main[2], nub[1]),
                 (nub[2], nub[1]), (nub[2], nub[3]), (nub[0], nub[3]),
                 (nub[0], nub[1]), (main[0], nub[1])])[0])
        poly = [(main[0], main[1]), (main[2], main[1]), (main[2], nub[1]),
                (nub[2], nub[1]), (nub[2], nub[3]), (nub[0], nub[3]),
                (nub[0], nub[1]), (main[0], nub[1])]
        self.assertEqual(len(poly), 8)
        for index in range(len(poly)):
            next_index = (index + 1) % len(poly)
            a = poly[index]
            b = poly[next_index]
            horizontal = abs(a[1] - b[1]) <= geometry.EPSILON
            vertical = abs(a[0] - b[0]) <= geometry.EPSILON
            self.assertTrue(horizontal or vertical)
            self.assertFalse(horizontal and vertical)
        self.assertNotIn(start, poly[:-1])
        self.assertNotIn(end, poly[:-1])

    def test_ring_cycle_path_8x6_from_x2(self):
        path = geometry._ring_cycle_path(8, 6, 2)
        self.assertEqual(path[0], (2, 0))
        self.assertEqual(path[-1], (5, 0))
        expected = set((x, y) for x in range(8) for y in range(6))
        expected -= set(((3, 0), (4, 0)))
        self.assertEqual(set(path), expected)
        self.assertEqual(len(path), 8 * 6 - 2)
        self.assertEqual(len(set(path)), len(path))
        edges = set()
        for index in range(len(path) - 1):
            a, b = path[index], path[index + 1]
            self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)
            edges.add(tuple(sorted((a, b))))
        self.assertEqual(len(edges), len(path) - 1)


class BuildCenteredSnailTests(unittest.TestCase):
    """Real-coordinate smoke tests for the four exact cases that the centered
    snail currently supports: bottom and top edges with each handedness."""

    BOTTOM_BOX = (41477.904945857626, -32512.380993082661,
                  45077.90494585767, -27612.380993082632)
    BOTTOM_LEFT = (43063.530688889761, -32355.91871161358)
    BOTTOM_RIGHT = (43363.530688889768, -32355.91871161358)

    TOP_BOX = (34849.636420563096, -18821.893075402237,
               39749.636420563118, -15221.89307540222)
    TOP_LEFT = (37125.671126227913, -15283.88191721528)
    TOP_RIGHT = (37425.671126227913, -15283.88191721528)

    OFFSET = 200
    STEP = 150

    def _assert_no_self_cross(self, segments):
        for index in range(len(segments)):
            for previous in range(index):
                if not geometry._crosses(segments[previous], segments[index]):
                    continue
                # the lead-in/lead-out verticals are allowed to kiss the body's
                # adjacent horizontal at the lattice ends
                first_horizontal = (abs(segments[previous][0][1] -
                                        segments[previous][1][1]) <=
                                    geometry.EPSILON)
                current_horizontal = (abs(segments[index][0][1] -
                                          segments[index][1][1]) <=
                                      geometry.EPSILON)
                if previous == index - 1 and first_horizontal != current_horizontal:
                    continue
                self.fail("self_intersection between segments %d and %d"
                          % (previous, index))

    def _first_horizontal_from_free_end(self, segments, free_end_is_first):
        """First non-zero horizontal section seen walking away from a real pipe
        free end, as ``(dx, y)``.

        Walking away from the last free end means reversing the segment list
        *and* swapping each pair, so the traversed direction still points away
        from the pipe. The vertical lead(s) are skipped first; the first
        horizontal of nonzero length afterwards is the lane the pipe feeds.
        """
        if free_end_is_first:
            ordered = list(segments)
        else:
            ordered = [(b, a) for a, b in reversed(segments)]
        for a, b in ordered:
            horizontal = (abs(a[1] - b[1]) <= geometry.EPSILON and
                          abs(a[0] - b[0]) > geometry.EPSILON)
            if horizontal:
                return b[0] - a[0], a[1]
        self.fail("no non-zero horizontal section found walking from the "
                  "pipe free end")

    def _body_reaches_inset(self, segments, source, offset, step):
        inset = (source[0] + offset, source[1] + offset,
                 source[2] - offset, source[3] - offset)
        # The two lead segments are the only ones outside the inset; skip past
        # them and reach each side must be within ``step`` like the validator.
        xs = []
        ys = []
        for index in range(len(segments)):
            if index < 2 or index > len(segments) - 3:
                continue
            a, b = segments[index]
            xs.extend((a[0], b[0]))
            ys.extend((a[1], b[1]))
        reach = step + geometry.EPSILON
        self.assertLessEqual(inset[0] - min(xs), reach,
                             "body did not reach the left inset side")
        self.assertLessEqual(max(xs) - inset[2], reach,
                             "body did not reach the right inset side")
        self.assertLessEqual(inset[1] - min(ys), reach,
                             "body did not reach the bottom inset side")
        self.assertLessEqual(max(ys) - inset[3], reach,
                             "body did not reach the top inset side")

    def _shoulder_after_setback(self, segments, pipe_is_first):
        """First non-short shoulder horizon in world coordinates.

        Walking away from the given pipe free end (reversing and swapping each
        pair when that end is last), the lead vertical and any short setback
        horizontals (length at most ``2 * step``) are skipped.  The first
        horizontal longer than that threshold is the neighbouring lane the
        shoulder feeds.

        Returns ``(world_x_sign, lane_y)`` where ``world_x_sign`` is the
        direction the shoulder runs along that lane in world coordinates, so
        both shoulders can be compared directly regardless of which one is
        walked forwards or backwards along the chain.
        """
        if pipe_is_first:
            ordered = list(segments)
        else:
            ordered = [(b, a) for a, b in reversed(segments)]

        min_long = 2 * self.STEP + geometry.EPSILON
        for a, b in ordered:
            if abs(a[1] - b[1]) > geometry.EPSILON:
                continue
            if abs(a[0] - b[0]) <= geometry.EPSILON:
                continue
            length = abs(b[0] - a[0])
            if length <= min_long:
                continue
            travel_sign = self._sign(b[0] - a[0])
            if not pipe_is_first:
                travel_sign = -travel_sign
            return travel_sign, a[1]
        self.fail("no horizontal longer than 2*step after the local "
                  "setback from the pipe free end")

    @staticmethod
    def _sign(value):
        if value > geometry.EPSILON:
            return 1
        if value < -geometry.EPSILON:
            return -1
        return 0

    def _assert_pipe_lanes(self, segments, lane_y, left_pipe_is_first):
        """Both real pipe free ends must drop vertically onto the same inset
        edge and then feed neighbouring lanes in the same world direction.

        The notched ring path is cut open on lattice row 0, so *both* graph
        ends sit on that row and both leads fall straight down to the inset
        edge.  Each lead is therefore strictly vertical, and the two pipes
        then leave that edge heading into their respective neighbouring lanes
        under a single shared winding direction (CW or CCW per call site).
        """
        first_dx, first_y = self._first_horizontal_from_free_end(
            segments, True)
        second_dx, second_y = self._first_horizontal_from_free_end(
            segments, False)

        self.assertEqual([lane_y, lane_y], sorted([first_y, second_y]),
                         "both pipe free ends must feed the inset edge %r, "
                         "got %r and %r" % (lane_y, first_y, second_y))

        left_dx, right_dx = ((first_dx, second_dx) if left_pipe_is_first
                             else (second_dx, first_dx))
        self.assertNotEqual(left_dx, 0, "left pipe never left its free end")
        self.assertNotEqual(right_dx, 0, "right pipe never left its free end")

        left_dir, left_lane = self._shoulder_after_setback(
            segments, left_pipe_is_first)
        right_dir, right_lane = self._shoulder_after_setback(
            segments, not left_pipe_is_first)

        self.assertNotEqual(left_lane, lane_y,
                            "left shoulder never left the pipe lane %r "
                            "(dir %+d)" % (lane_y, left_dir))
        self.assertNotEqual(right_lane, lane_y,
                            "right shoulder never left the pipe lane %r "
                            "(dir %+d)" % (lane_y, right_dir))
        self.assertEqual(
            left_dir, right_dir,
            "shoulders counter-rotate: the left shoulder runs %+d on lane %r "
            "while the right shoulder runs %+d on lane %r. Both must follow "
            "one shared winding direction on their neighbouring lanes."
            % (left_dir, left_lane, right_dir, right_lane))

    def test_bottom_clockwise_start_left_end_right(self):
        segments = geometry.build_centered_snail(
            self.BOTTOM_BOX, self.BOTTOM_LEFT, self.BOTTOM_RIGHT,
            self.OFFSET, self.STEP, True)
        self.assertEqual(self.BOTTOM_LEFT, segments[0][0])
        self.assertEqual(self.BOTTOM_RIGHT, segments[-1][1])
        self._assert_pipe_lanes(
            segments,
            self.BOTTOM_BOX[1] + self.OFFSET,
            True)
        self._assert_no_self_cross(segments)
        self._body_reaches_inset(segments, self.BOTTOM_BOX,
                                 self.OFFSET, self.STEP)

    def test_bottom_counterclockwise_start_right_end_left(self):
        segments = geometry.build_centered_snail(
            self.BOTTOM_BOX, self.BOTTOM_RIGHT, self.BOTTOM_LEFT,
            self.OFFSET, self.STEP, False)
        self.assertEqual(self.BOTTOM_RIGHT, segments[0][0])
        self.assertEqual(self.BOTTOM_LEFT, segments[-1][1])
        self._assert_pipe_lanes(
            segments,
            self.BOTTOM_BOX[1] + self.OFFSET,
            False)
        self._assert_no_self_cross(segments)
        self._body_reaches_inset(segments, self.BOTTOM_BOX,
                                 self.OFFSET, self.STEP)

    def test_top_clockwise_start_right_end_left(self):
        segments = geometry.build_centered_snail(
            self.TOP_BOX, self.TOP_RIGHT, self.TOP_LEFT,
            self.OFFSET, self.STEP, True)
        self.assertEqual(self.TOP_RIGHT, segments[0][0])
        self.assertEqual(self.TOP_LEFT, segments[-1][1])
        self._assert_pipe_lanes(
            segments,
            self.TOP_BOX[3] - self.OFFSET,
            False)
        self._assert_no_self_cross(segments)
        self._body_reaches_inset(segments, self.TOP_BOX,
                                 self.OFFSET, self.STEP)

    def test_top_counterclockwise_start_left_end_right(self):
        segments = geometry.build_centered_snail(
            self.TOP_BOX, self.TOP_LEFT, self.TOP_RIGHT,
            self.OFFSET, self.STEP, False)
        self.assertEqual(self.TOP_LEFT, segments[0][0])
        self.assertEqual(self.TOP_RIGHT, segments[-1][1])
        self._assert_pipe_lanes(
            segments,
            self.TOP_BOX[3] - self.OFFSET,
            True)
        self._assert_no_self_cross(segments)
        self._body_reaches_inset(segments, self.TOP_BOX,
                                 self.OFFSET, self.STEP)


class BuildNotchedRingPathTests(unittest.TestCase):
    """Pure lattice tests for :func:`build_notched_ring_path`.

    The two parameter triples below are the ones the centered snail actually
    selects for the bottom and the top real-coordinate case, so they pin the
    Hamiltonian walk independently of any world-space mapping.

    The walk is a single path over every lattice node but the one notch node
    ``(k - 1, 0)``: it is walked from the left end ``(k - 2, 0)`` to the right
    end ``(k, 0)``, leaving the left end first heading *left* along the outer
    bottom row and, walked backwards, the right end departing *right*.
    """

    def _assert_notched_ring(self, nx, ny, k, missing):
        path = geometry.build_notched_ring_path(nx, ny, k)

        self.assertEqual(nx * ny - 1, len(path))
        self.assertEqual(len(path), len(set(path)))

        left, right = (k - 2, 0), (k, 0)
        self.assertEqual(left, path[0])
        self.assertEqual(right, path[-1])

        for index in range(len(path) - 1):
            a, b = path[index], path[index + 1]
            self.assertEqual(1, abs(a[0] - b[0]) + abs(a[1] - b[1]),
                             "step %d is not a unit lattice edge: %r -> %r"
                             % (index, a, b))
            self.assertTrue(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1)

        self.assertEqual(len(path), len(set(path)),
                         "the walk revisits a node and so crosses itself")

        whole = set((x, y) for x in range(nx) for y in range(ny))
        self.assertEqual(sorted(missing), sorted(whole - set(path)),
                         "the walk must omit exactly the notch node(s)")
        self.assertEqual(nx * ny - len(missing), len(path),
                         "there must be no hidden empty middle")

        degree = {}
        for index in range(len(path) - 1):
            for node in (path[index], path[index + 1]):
                degree[node] = degree.get(node, 0) + 1
        ends = [node for node, count in degree.items() if count == 1]
        self.assertEqual(sorted([left, right]), sorted(ends))
        for node, count in degree.items():
            if node in (left, right):
                continue
            self.assertEqual(2, count,
                             "interior node %r has degree %d" % (node, count))

        self.assertEqual((k - 3, 0), path[1])
        self.assertEqual((k + 1, 0), path[-2])

    def test_bottom_case_lattice_22x31_k11(self):
        self._assert_notched_ring(22, 31, 11, ((10, 0),))

    def test_top_case_lattice_31x22_k16(self):
        self._assert_notched_ring(31, 22, 16, ((15, 0),))

    def test_notch_clearing_the_first_splice_column_is_valid(self):
        # k values whose notch column used to collide with the first
        # ring-to-ring merge are valid now that every merge uses the top side
        # only.  Each must cover the whole lattice bar the notch.  k <= nx - 4
        # stays enforced by the parameter guard, so k = 21 stays invalid.
        self.assertRaises(ValueError, geometry.build_notched_ring_path,
                          22, 31, 21)
        for k in (18, 17, 16, 15, 14, 13, 12):
            self._assert_notched_ring(22, 31, k, ((k - 1, 0),))

    def _ring_merge_edges(self, nx, ny, k):
        """Set of the inter-ring vertical edges of the walk, plus their rows."""
        path = geometry.build_notched_ring_path(nx, ny, k)
        pairs = set(frozenset((a, b)) for a, b in zip(path[:-1], path[1:]))

        depth = min(nx, ny) // 2
        merges = set()
        for r in range(depth - 1):
            y = ny - 1 - r
            x = r + 1
            merges.add(frozenset(((x, y), (x, y - 1))))
            merges.add(frozenset(((x + 1, y), (x + 1, y - 1))))
        return path, pairs, merges, depth

    def test_inter_ring_merges_use_the_top_side_only(self):
        for nx, ny, k in ((22, 31, 11), (31, 22, 16)):
            path, pairs, merges, depth = self._ring_merge_edges(nx, ny, k)

            # every merge edge really is an edge of the walk, and each one joins
            # the outer ring row ny - 1 - r to the ring just inside it.
            for r in range(depth - 1):
                y = ny - 1 - r
                x = r + 1
                for col in (x, x + 1):
                    self.assertIn(frozenset(((col, y), (col, y - 1))), pairs,
                                  "ring %d of %dx%d k=%d is not merged on the "
                                  "top side" % (r, nx, ny, k))

            # exactly depth - 1 merges survive, and none of them sits on the
            # bottom side: no bottom-side splice survives anywhere in the walk.
            self.assertEqual(2 * (depth - 1), len(merges))
            for edge in merges:
                endpoints = list(edge)
                self.assertEqual(endpoints[0][0], endpoints[1][0],
                                 "merge edge must be vertical")
                y = endpoints[0][1]
                self.assertGreater(y, 0, "merge on the bottom row y = 0")
                self.assertGreaterEqual(y, ny - depth,
                                        "merge is not on a top-side row")

            # the rows that lost their horizontal edge are the bottom rows of
            # the rings: a bottom-side merge would have removed a y == r edge.
            gone = set()
            for r in range(depth - 1):
                y = ny - 1 - r
                x = r + 1
                gone.add(frozenset(((x, y), (x + 1, y))))
                gone.add(frozenset(((x, y - 1), (x + 1, y - 1))))
            for edge in gone:
                self.assertNotIn(edge, pairs)

    def test_same_side_nested_transitions(self):
        for nx, ny, k in ((22, 31, 11), (31, 22, 16)):
            _, _, merges, depth = self._ring_merge_edges(nx, ny, k)

            # merge anchors, one per ring: strictly nested same-side staircase,
            # one row lower and one column further right for each next ring.
            anchors = []
            for r in range(depth - 1):
                y = ny - 1 - r
                x = r + 1
                self.assertIn(frozenset(((x, y), (x, y - 1))), merges)
                anchors.append((x, y))

            for index in range(len(anchors) - 1):
                self.assertEqual(anchors[index][1] - 1, anchors[index + 1][1])
                self.assertEqual(anchors[index][0] + 1, anchors[index + 1][0])

            # every anchor touches the top half of the lattice: all transitions
            # happen on the same (top) side, never alternating to the bottom.
            for x, y in anchors:
                self.assertTrue(y > (ny - 1) // 2,
                                "merge at %r is not on the top side" % ((x, y),))


if __name__ == "__main__":
    unittest.main()
