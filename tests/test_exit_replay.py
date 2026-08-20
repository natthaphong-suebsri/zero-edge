# -*- coding: utf-8 -*-
"""Fixture tests for analysis.exit_replay.

An exit backtest with a sign bug recommends you into the ground, so every
branch of replay_trade is pinned against hand-built trades with a known-correct
answer, plus cohort-level EV/fee math.

Sign conventions under test:
  MFE >= 0 (favorable), MAE <= 0 (adverse), stop/tp distances > 0.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.exit_replay import (  # noqa: E402
    Trade, ExitPolicy, replay_trade, replay_cohort, baseline_net_ev, FEE_RT_PCT,
)


def mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=0.0, mfe=0.0, mae=0.0, hour=12):
    return Trade(direction="short", stop_dist=stop_dist, tp1_dist=tp1_dist,
                 outcome_pct=outcome_pct, mfe=mfe, mae=mae, hour_utc=hour)


class ReplayTradeBranchTests(unittest.TestCase):
    def test_clean_winner_unaffected_by_tighter_stop(self):
        # Winner: ran to +2.0 MFE, barely dipped (-0.2 MAE). tp1=1.5.
        # Tighter stop at 0.6*1.0 = 0.6 -> MAE -0.2 does NOT reach -0.6.
        t = mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=1.5, mfe=2.0, mae=-0.2)
        r = replay_trade(t, ExitPolicy(stop_mult=0.6))
        self.assertEqual(r.bucket, "win")
        self.assertAlmostEqual(r.pess_pct, 1.5)   # +tp1
        self.assertAlmostEqual(r.opt_pct, 1.5)

    def test_clean_loser_loses_less_with_tighter_stop(self):
        # Loser: dug to -2.0 MAE, never reached tp1 (mfe 0.3 < 1.5).
        # Tighter stop 0.6 -> MAE -2.0 <= -0.6 -> stopped at -0.6 (better than -1.0).
        t = mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=-1.0, mfe=0.3, mae=-2.0)
        r = replay_trade(t, ExitPolicy(stop_mult=0.6))
        self.assertEqual(r.bucket, "loss")
        self.assertAlmostEqual(r.pess_pct, -0.6)
        self.assertAlmostEqual(r.opt_pct, -0.6)

    def test_ambiguous_trade_reports_band(self):
        # Reached BOTH: mfe 2.0 >= tp1 1.5 AND mae -1.0 <= -(0.6) new stop.
        t = mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=1.5, mfe=2.0, mae=-1.0)
        r = replay_trade(t, ExitPolicy(stop_mult=0.6))
        self.assertEqual(r.bucket, "ambiguous")
        self.assertAlmostEqual(r.pess_pct, -0.6)   # stop first
        self.assertAlmostEqual(r.opt_pct, 1.5)     # tp1 first

    def test_unresolved_falls_back_to_outcome_pct(self):
        # Neither reached: mfe 1.0 < tp1 1.5, mae -0.5 > -(0.8) new stop.
        t = mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=0.2, mfe=1.0, mae=-0.5)
        r = replay_trade(t, ExitPolicy(stop_mult=0.8))
        self.assertEqual(r.bucket, "unresolved")
        self.assertAlmostEqual(r.pess_pct, 0.2)
        self.assertAlmostEqual(r.opt_pct, 0.2)

    def test_baseline_multiplier_reproduces_levels(self):
        # At stop_mult=1.0, a loser that hit exactly its stop stays at -stop.
        t = mk(stop_dist=1.2, tp1_dist=1.5, outcome_pct=-1.2, mfe=0.1, mae=-1.3)
        r = replay_trade(t, ExitPolicy(stop_mult=1.0))
        self.assertEqual(r.bucket, "loss")
        self.assertAlmostEqual(r.pess_pct, -1.2)


class CohortMathTests(unittest.TestCase):
    def test_fee_applied_once_per_trade(self):
        # One clean winner at +1.5, one clean loser at -0.6 (tighter stop).
        trades = [
            mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=1.5, mfe=2.0, mae=-0.2),
            mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=-1.0, mfe=0.3, mae=-2.0),
        ]
        st = replay_cohort(trades, ExitPolicy(stop_mult=0.6))
        # Derived from FEE_RT_PCT rather than hardcoded. This test exists to
        # pin the INVARIANT its name states — one round-trip fee per trade —
        # and that invariant does not depend on what the fee happens to be.
        # It was previously pinned at 0.37 against FEE_RT_PCT = 0.08; when the
        # constant was corrected to the measured 0.424 the test failed for a
        # reason that had nothing to do with the fee-counting logic it guards.
        self.assertGreater(FEE_RT_PCT, 0, "the guards below assume a real fee")
        gross_mean = (1.5 + -0.6) / 2
        expected = gross_mean - FEE_RT_PCT
        self.assertAlmostEqual(st.net_ev_pess, expected, places=4)
        self.assertAlmostEqual(st.net_ev_opt, expected, places=4)
        # The two ways fee counting goes wrong without changing the sign, so
        # neither would be obvious from eyeballing a results table.
        self.assertNotAlmostEqual(st.net_ev_pess, gross_mean - 2 * FEE_RT_PCT,
                                  places=4, msg="fee applied twice per trade")
        self.assertNotAlmostEqual(st.net_ev_pess, gross_mean,
                                  places=4, msg="fee not applied at all")
        self.assertEqual(st.wins, 1)
        self.assertEqual(st.losses, 1)
        self.assertEqual(st.win_rate, 50.0)
        # R:R = avg_win / |avg_loss| = 1.5 / 0.6 = 2.5
        self.assertAlmostEqual(st.rr, 2.5, places=3)

    def test_ambiguous_widens_the_band(self):
        trades = [mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=1.5, mfe=2.0, mae=-1.0)]
        st = replay_cohort(trades, ExitPolicy(stop_mult=0.6))
        self.assertEqual(st.ambiguous, 1)
        # pess = -0.6 - fee ; opt = +1.5 - fee
        self.assertAlmostEqual(st.net_ev_pess, -0.6 - FEE_RT_PCT, places=4)
        self.assertAlmostEqual(st.net_ev_opt, 1.5 - FEE_RT_PCT, places=4)
        self.assertLess(st.net_ev_pess, st.net_ev_opt)

    def test_tighter_stop_beats_baseline_on_asymmetric_set(self):
        # Winners barely dip, losers dig deep -> tightening must lift net EV.
        winners = [mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=1.5, mfe=1.8, mae=-0.3) for _ in range(5)]
        losers = [mk(stop_dist=1.0, tp1_dist=1.5, outcome_pct=-1.0, mfe=0.2, mae=-1.8) for _ in range(5)]
        trades = winners + losers
        base = baseline_net_ev(trades)                       # (5*1.5 - 5*1.0)/10 - .08
        tight = replay_cohort(trades, ExitPolicy(stop_mult=0.6))
        self.assertGreater(tight.net_ev_pess, base)          # the whole thesis

    def test_empty_cohort_is_safe(self):
        st = replay_cohort([], ExitPolicy(stop_mult=0.6))
        self.assertEqual(st.n, 0)
        self.assertEqual(st.net_ev_pess, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
