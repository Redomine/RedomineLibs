# -*- coding: utf-8 -*-
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if not isinstance(ROOT, type(u"")):
    ROOT = ROOT.decode(sys.getfilesystemencoding() or "mbcs")
BUNDLE = os.path.join(ROOT, "RedomineLibs.extension", "RedomineLibs.tab",
                      u"Тесты.panel", "FloorHeatingTurnSandbox.pushbutton")
sys.path.insert(0, BUNDLE)
import build_progress as progress
from route_timing import RouteProfile


class ProgressBar(object):
    def __init__(self):
        self.titles = []
        self.IsVisible = False
        self.cancel_on_report = False
        self.cancelled = False

    def __enter__(self):
        self.IsVisible = True
        return self

    def __exit__(self, *args):
        self.IsVisible = False

    def update_progress(self, value, maximum):
        assert self.IsVisible and 0 <= value <= maximum == 100
        self.titles.append(self.title.format(value=value, max_value=maximum))
        self.cancelled = self.cancel_on_report


class ProgressTests(unittest.TestCase):
    def test_closing_bar_with_escape_cancels_work(self):
        with ProgressBar() as view, progress.BuildProgress(1, view) as bar:
            view.IsVisible = False
            with self.assertRaises(progress.BuildCancelled):
                bar.poll(True)
            self.assertFalse(view.titles)

    def test_percentage_and_titles_across_two_regions(self):
        with RouteProfile() as profile, ProgressBar() as view, progress.BuildProgress(2, view) as bar:
            bar.region(u"Область {А}")
            self.assertIn(u"Область {А}", view.titles[-1])
            self.assertTrue(view.titles[-1].endswith("[0%]"))
            progress.report_model_step(u"Построение труб", 0, 5)
            self.assertTrue(view.titles[-1].endswith("[37%]"))
            bar.finish_region()
            bar.region(u"Область Б")
            self.assertTrue(view.titles[-1].endswith("[50%]"))
            progress.report_model_step(u"Проверка соединений", 5, 5)
            self.assertTrue(view.titles[-1].endswith("[99%]"))
            bar.finish_region()
            self.assertTrue(view.titles[-1].endswith("[100%]"))
        self.assertFalse(view.IsVisible)
        self.assertIn("progress_ui", [row["name"] for row in profile.report()["functions_by_self_seconds"]])

    def test_dispatcher_cancellation_closes_window_and_restores_callbacks(self):
        with self.assertRaises(progress.BuildCancelled):
            with ProgressBar() as view, progress.BuildProgress(1, view) as bar:
                view.cancel_on_report = True
                bar.region(u"Область")
        self.assertFalse(view.IsVisible)
        self.assertIsNone(progress._callback)
        self.assertIsNone(progress._status)

    def test_frequent_checkpoints_refresh_only_at_the_ui_interval(self):
        clock = progress.time.time
        now = [100.0]
        progress.time.time = lambda: now[0]
        try:
            with ProgressBar() as view, progress.BuildProgress(1, view) as bar:
                bar.region(u"Область")
                for i in range(10000):
                    progress.checkpoint()
                self.assertEqual(len(view.titles), 1)
                now[0] += progress.REFRESH_SECONDS
                progress.checkpoint()
                self.assertEqual(len(view.titles), 2)
        finally:
            progress.time.time = clock


if __name__ == "__main__":
    unittest.main()
