# -*- coding: utf-8 -*-
"""
analysis/regime_breaker.py
─────────────────────────────────────────────────────────────────────────────
Short-side regime circuit-breaker — SHADOW / detector logic + offline calibration.

See docs/SPEC_regime_circuit_breaker.md for the full design. This module is the
pure detector plus an offline replay that walks the breaker over the entire
alert_outcomes history to CALIBRATE the thresholds before anything goes live:
it must TRIP around the 2026-05-28 regime collapse and stay QUIET during the
healthy 05-14..05-26 windows. If it trips in a healthy window, the thresholds
are wrong.

It changes ZERO live behavior. Wiring TRIPPED state to notification suppression
is a separate, later step gated on this calibration passing.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config

Z = 1.96
# ── Pre-registered thresholds (named constants = the pre-registration) ────────
WINDOW_HOURS = 48      # trailing window of resolved shorts to score
MIN_N        = 25      # need this many resolved in-window to have an opinion
TRIP_CEILING = 30.0    # TRIP when Wilson UPPER bound of WR < this (% )
RESET_FLOOR  = 42.0    # RESET when Wilson LOWER bound of WR > this (%)

OPEN = "OPEN"          # shorts firing normally
TRIPPED = "TRIPPED"    # adverse regime detected — shorts would be stood down


def wilson_bounds(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% (low, high) bounds on a proportion, as percents."""
    if n == 0:
        return (0.0, 100.0)
    p = k / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    half = (Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))) / denom
    return (round(100 * (center - half), 1), round(100 * (center + half), 1))


@dataclass(frozen=True)
class BreakerEval:
    state: str
    changed: bool
    n: int
    wins: int
    wr: float
    wilson_low: float
    wilson_high: float


def breaker_state(prev_state: str, n: int, wins: int) -> BreakerEval:
    """Pure transition function with hysteresis.

    TRIP  (OPEN->TRIPPED):  n>=MIN_N AND Wilson_high(WR) < TRIP_CEILING
    RESET (TRIPPED->OPEN):  n>=MIN_N AND Wilson_low(WR)  > RESET_FLOOR
    Otherwise: hold current state. Below MIN_N we never change state (no opinion).
    """
    lo, hi = wilson_bounds(wins, n)
    wr = round(100 * wins / n, 1) if n else 0.0
    state = prev_state
    if n >= MIN_N:
        if prev_state == OPEN and hi < TRIP_CEILING:
            state = TRIPPED
        elif prev_state == TRIPPED and lo > RESET_FLOOR:
            state = OPEN
    return BreakerEval(state=state, changed=(state != prev_state),
                       n=n, wins=wins, wr=wr, wilson_low=lo, wilson_high=hi)


# ── Live window (for the running scanner) ─────────────────────────────────────
def current_window(db_path: str = None, hours: int = WINDOW_HOURS) -> tuple[int, int]:
    """(n, wins) of DUAL-short outcomes resolved in the trailing `hours`.
    Cheap indexed read; safe to call once per scan. ts_fired is epoch seconds."""
    cutoff = time.time() - hours * 3600
    db = sqlite3.connect(db_path or config.DB_PATH)
    try:
        row = db.execute(
            """
            SELECT COUNT(*), SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END)
            FROM alert_outcomes
            WHERE alert_kind='DUAL' AND direction='short'
              AND outcome IN ('win','loss') AND ts_fired >= ?
            """,
            (cutoff,),
        ).fetchone()
    finally:
        db.close()
    return (int(row[0] or 0), int(row[1] or 0))


# ── Offline calibration replay ────────────────────────────────────────────────
def _load_short_outcomes(db_path: str = None) -> list[tuple[int, int]]:
    """(ts_fired, is_win) for resolved DUAL shorts, time-ordered."""
    db = sqlite3.connect(db_path or config.DB_PATH)
    rows = db.execute(
        """
        SELECT ts_fired, CASE WHEN outcome='win' THEN 1 ELSE 0 END
        FROM alert_outcomes
        WHERE alert_kind='DUAL' AND direction='short'
          AND outcome IN ('win','loss') AND ts_fired IS NOT NULL
        ORDER BY ts_fired ASC
        """
    ).fetchall()
    db.close()
    return [(int(ts), int(w)) for ts, w in rows]


def replay(outcomes: list[tuple[int, int]], window_hours: int = WINDOW_HOURS) -> list[tuple]:
    """Walk the breaker over history. At each resolved short, score the trailing
    `window_hours` of resolved shorts and evaluate state. Returns the list of
    STATE-CHANGE events: (ts, kind, n, wins, wr, wilson_low, wilson_high)."""
    state = OPEN
    events = []
    win_s = window_hours * 3600
    for i, (ts, _) in enumerate(outcomes):
        window = [(t, w) for (t, w) in outcomes[: i + 1] if t > ts - win_s]
        n = len(window)
        wins = sum(w for _, w in window)
        ev = breaker_state(state, n, wins)
        if ev.changed:
            events.append((ts, ev.state, ev.n, ev.wins, ev.wr, ev.wilson_low, ev.wilson_high))
            state = ev.state
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description="Regime breaker offline calibration replay")
    ap.add_argument("--window", type=int, default=WINDOW_HOURS)
    args = ap.parse_args()
    window = args.window

    outcomes = _load_short_outcomes()
    if not outcomes:
        print("No resolved DUAL shorts."); return
    t0 = datetime.fromtimestamp(outcomes[0][0], tz=timezone.utc)
    t1 = datetime.fromtimestamp(outcomes[-1][0], tz=timezone.utc)
    print(f"\nREGIME BREAKER calibration | DUAL short | n={len(outcomes)} resolved")
    print(f"window={window}h  TRIP when WilsonHigh<{TRIP_CEILING}%  "
          f"RESET when WilsonLow>{RESET_FLOOR}%  MIN_N={MIN_N}")
    print(f"data span: {t0:%Y-%m-%d %H:%M} -> {t1:%Y-%m-%d %H:%M} UTC\n")
    events = replay(outcomes, window_hours=window)
    if not events:
        print("No state changes — breaker never tripped over history.")
    else:
        print(f"{'when (UTC)':<18}{'->state':>9}{'n':>5}{'wins':>5}{'WR%':>7}{'wilson[lo,hi]':>16}")
        print("-" * 60)
        for ts, st, n, wins, wr, lo, hi in events:
            when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")
            print(f"{when:<18}{st:>9}{n:>5}{wins:>5}{wr:>7.1f}{f'[{lo},{hi}]':>16}")
    print("\nCalibration check: TRIP should appear ~05-28 (regime onset) and there")
    print("should be NO trip during the healthy 05-14..05-26 span. If it trips in a")
    print("healthy window, loosen TRIP_CEILING / raise MIN_N and re-run.")


if __name__ == "__main__":
    main()
