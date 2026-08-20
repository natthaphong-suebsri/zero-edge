# -*- coding: utf-8 -*-
"""Tests for analysis.rule_scoreboard status classification and Wilson math."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.rule_scoreboard import wilson_low, classify, MIN_N_PROMOTE  # noqa: E402


class WilsonTests(unittest.TestCase):
    def test_zero_n_is_none(self):
        self.assertIsNone(wilson_low(0, 0))

    def test_half_of_large_n_below_50(self):
        # 50/100 -> Wilson lower bound is well below 50%.
        lb = wilson_low(50, 100)
        self.assertLess(lb, 50.0)
        self.assertGreater(lb, 39.0)

    def test_strong_winrate_high_lower_bound(self):
        lb = wilson_low(90, 100)
        self.assertGreater(lb, 80.0)


class ClassifyTests(unittest.TestCase):
    def test_no_fires(self):
        self.assertEqual(classify(0, None, None, None), "NO FIRES")

    def test_negative_ev_rejects(self):
        self.assertEqual(classify(100, 40.0, -5.0, -0.20), "REJECT (netEV<0)")

    def test_strongly_negative_lift_rejects(self):
        # netEV barely positive but lift deeply negative -> still reject.
        self.assertEqual(classify(100, 45.0, -15.0, 0.01), "REJECT (lift<0)")

    def test_small_n_watches(self):
        self.assertEqual(classify(20, 60.0, 12.0, 0.30), "WATCH (n=20)")

    def test_screen_pass_needs_all_gates(self):
        # n>=50, netEV>0, lift>=8 -> SCREEN-PASS
        self.assertEqual(classify(60, 57.0, 12.0, 0.25), "SCREEN-PASS*")

    def test_positive_ev_small_lift_is_neutral(self):
        # n>=50, netEV>0, but lift below the promote bar -> NEUTRAL.
        self.assertEqual(classify(60, 51.0, 3.0, 0.05), "NEUTRAL")

    def test_screen_pass_with_no_skip_cohort(self):
        # lift None (no skip bucket) but netEV>0 and n>=50 -> SCREEN-PASS.
        self.assertEqual(classify(60, 55.0, None, 0.20), "SCREEN-PASS*")


if __name__ == "__main__":
    unittest.main(verbosity=2)
