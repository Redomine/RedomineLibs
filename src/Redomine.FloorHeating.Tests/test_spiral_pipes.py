# -*- coding: utf-8 -*-
"""Offline tests for converting the two solver branches to one pipe chain."""
import importlib.util
import math
from pathlib import Path
import sys
import unittest
from collections import namedtuple
from unittest.mock import Mock, patch


class RegenerationFailedException(Exception):
    pass


def load_writer():
    root = Path(__file__).resolve().parents[2]
    bundle = root / "RedomineLibs.extension/RedomineLibs.tab/Тесты.panel/FloorHeatingTurnSandbox.pushbutton"
    spec = importlib.util.spec_from_file_location("spiral_pipes", bundle / "spiral_pipes.py")
    module = importlib.util.module_from_spec(spec)
    dependencies = {name: Mock() for name in (
        "clr", "dosymep", "System", "Autodesk.Revit.DB.ExtensibleStorage",
        "Autodesk.Revit.DB.Plumbing", "pyrevit")}
    dependencies["Autodesk.Revit.Exceptions"] = Mock(RegenerationFailedException=RegenerationFailedException)
    with patch.dict(sys.modules, dependencies), patch.object(sys, "path", [str(bundle)] + sys.path):
        spec.loader.exec_module(module)
    return module


writer = load_writer()


class Point(namedtuple("Point", "x y z")):
    def DistanceTo(self, other):
        return math.dist(self, other)


class PipeChainTests(unittest.TestCase):
    profiles = {"right": Mock(z=10), "left": Mock(z=12)}
    paths = {"right": {"vertices": [(0, 0), (5, 0), (5, 4)]},
             "left": {"vertices": [(0, 6), (5, 6)]}}

    def runs(self, bridge, first="right", paths=None):
        return writer._runs(paths or self.paths, {"bridge": bridge}, self.profiles,
                            first, lambda p, z: Point(p[0], p[1], z), 0.01)

    def test_chain_preserves_both_inlets_and_the_elevation_at_each_end_of_the_bridge(self):
        for first in ("right", "left"):
            second = "right" if first == "left" else "left"
            for bridge in ([(5, 4), (5, 6)], [(5, 6), (5, 4)]):
                runs = self.runs(bridge, first)
                self.assertEqual(Point(*self.paths[first]["vertices"][0], self.profiles[first].z), runs[0].start)
                self.assertEqual(Point(*self.paths[second]["vertices"][0], self.profiles[second].z), runs[-1].end)
                self.assertTrue(all(a.end == b.start for a, b in zip(runs, runs[1:])))
                link, = [r for r in runs if r.part == "bridge"]
                self.assertIs(self.profiles[first], link.profile)
                self.assertEqual((self.profiles[first].z, self.profiles[second].z), (link.start.z, link.end.z))

    def test_shared_tip_creates_a_riser_only_when_the_elevations_differ(self):
        paths = {"right": {"vertices": [(0, 0), (5, 0)]}, "left": {"vertices": [(5, 5), (5, 0)]}}
        self.assertEqual(["right", "bridge", "left"], [r.part for r in self.runs(None, paths=paths)])
        with patch.object(self, "profiles", {"right": Mock(z=10), "left": Mock(z=10)}):
            self.assertEqual(["right", "left"], [r.part for r in self.runs(None, paths=paths)])

    def test_stale_bridge_and_uncoupled_tips_are_rejected(self):
        for bridge in (None, [(0, 0), (5, 6)]):
            with self.assertRaises(ValueError):
                self.runs(bridge)

    def test_length_limited_route_writes_no_model_elements(self):
        doc = Mock()
        pipes, fittings = writer.build_pipes(doc, None, self.paths, {"connected": False}, {}, "left", None, 100)
        self.assertFalse(any(pipes.values()))
        self.assertEqual([], fittings)
        self.assertEqual([], doc.mock_calls)

    def test_fitting_must_connect_different_ports_to_both_new_pipes(self):
        ports = [Mock(ConnectorType=writer.DB.ConnectorType.End) for _ in range(2)]
        fitting = Mock()
        fitting.MEPModel.ConnectorManager.Connectors = list(reversed(ports))
        a, b = Mock(), Mock()
        a.IsConnectedTo.side_effect = lambda c: c is ports[0]
        b.IsConnectedTo.side_effect = lambda c: c is ports[1]
        self.assertTrue(writer._joins_ends(a, b, fitting))
        b.IsConnectedTo.side_effect = lambda c: c is ports[0]
        self.assertFalse(writer._joins_ends(a, b, fitting))
