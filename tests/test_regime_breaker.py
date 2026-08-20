# -*- coding: utf-8 -*-
"""Tests for analysis.regime_breaker detector logic (Wilson + hysteresis)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.regime_breaker import (  # noqa: E402
    breaker_state, wilson_bounds, replay, OPEN, TRIPPED,
    MIN_N, TRIP_CEILING, RESET_FLOOR,
)


class WilsonTests(unittest.TestCase):
    def test_bounds_ordered(self):
        lo, hi = wilson_bounds(5, 40)
        self.assertLess(lo, hi)

    def test_empty(self):
        self.assertEqual(wilson_bounds(0, 0), (0.0, 100.0))


class TransitionTests(unittest.TestCase):
    def test_below_min_n_holds_state(self):
        ev = breaker_state(OPEN, n=MIN_N - 1, wins=0)   # 0% but too few
        self.assertEqual(ev.state, OPEN)
        self.assertFalse(ev.changed)

    def test_trips_on_clear_adverse_regime(self):
        # 5 wins / 60 -> ~8.3% WR, Wilson high well under 30% -> TRIP
        ev = breaker_state(OPEN, n=60, wins=5)
        self.assertEqual(ev.state, TRIPPED)
        self.assertTrue(ev.changed)
        self.assertLess(ev.wilson_high, TRIP_CEILING)

    def test_does_not_trip_on_healthy(self):
        # 28 wins / 60 -> 46.7% -> stays OPEN
        ev = breaker_state(OPEN, n=60, wins=28)
        self.assertEqual(ev.state, OPEN)
        self.assertFalse(ev.changed)

    def test_does_not_trip_on_noisy_losing_streak_at_small_n(self):
        # 6 wins / 26 -> ~23% but Wilson high likely >30 at this n -> hold OPEN
        ev = breaker_state(OPEN, n=26, wins=6)
        # Wilson high for 6/26 is ~41% -> above ceiling -> no trip (noise guard)
        self.assertEqual(ev.state, OPEN)

    def test_stays_tripped_in_hysteresis_band(self):
        # Already tripped; recovery to ~40% WR (Wilson low < 42) -> stay TRIPPED
        ev = breaker_state(TRIPPED, n=60, wins=24)   # 40%
        self.assertEqual(ev.state, TRIPPED)
        self.assertFalse(ev.changed)

    def test_resets_on_confirmed_recovery(self):
        # Tripped; strong recovery 40 wins / 70 -> ~57%, Wilson low > 42 -> RESET
        ev = breaker_state(TRIPPED, n=70, wins=40)
        self.assertEqual(ev.state, OPEN)
        self.assertTrue(ev.changed)
        self.assertGreater(ev.wilson_low, RESET_FLOOR)


class ReplayTests(unittest.TestCase):
    def test_replay_trips_then_resets(self):
        # Synthetic sequence: healthy -> adverse -> recovery. Space events 1h
        # apart and make each block >= the 48-event window (48h window @ 1h
        # spacing) so a trailing window can fill with a SINGLE regime — otherwise
        # the window perpetually mixes regimes and never trips (which was the
        # real-data lag, but here we want clean block separation to test logic).
        HOUR = 3600
        base = 1_780_000_000
        seq = []
        # 60 healthy at ~50% (alternate)
        for i in range(60):
            seq.append((base + i * HOUR, i % 2))
        # 60 adverse at ~10% win (1 in 10)
        off = base + 60 * HOUR
        for i in range(60):
            seq.append((off + i * HOUR, 1 if i % 10 == 0 else 0))
        # 70 recovery at ~60% win (6 in 10)
        off2 = off + 60 * HOUR
        for i in range(70):
            seq.append((off2 + i * HOUR, 1 if i % 10 < 6 else 0))
        events = replay(seq)
        states = [e[1] for e in events]
        self.assertIn(TRIPPED, states)
        self.assertIn(OPEN, states)
        self.assertEqual(states[0], TRIPPED)   # first change must be a trip
        # And it must trip before it resets (order sanity).
        self.assertLess(states.index(TRIPPED), states.index(OPEN))


if __name__ == "__main__":
    unittest.main(verbosity=2)
