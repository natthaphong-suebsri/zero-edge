# -*- coding: utf-8 -*-
"""
analysis/exit_replay.py
─────────────────────────────────────────────────────────────────────────────
Offline EXIT-POLICY backtest harness.

Replays alternative stop/target policies against the MFE/MAE already recorded on
every resolved alert, and reports the net-EV delta vs the policy that actually
fired. This is how the "tighten the stop" hypothesis (3-lens strategy review,
2026-05-29) gets validated against history instead of guessed.

WHY THIS IS SOUND (and where it is NOT):
  alert_outcomes records, per trade:
    max_favorable_pct (MFE)  >= 0   how far price ran in our favour
    max_adverse_pct   (MAE)  <= 0   how far price ran against us
  These are the PATH EXTREMES over the trade's tracked life. So:
    - "would a stop at -S have triggered?"  <=>  MAE <= -S      (path-independent)
    - "would a target at +T have filled?"   <=>  MFE >= +T      (path-independent)
  The ONLY ambiguity is a trade that reached BOTH extremes (hit the new stop AND
  the target) — we cannot know from a bounding box which came first. Those trades
  are reported as a BAND: pessimistic (stop hit first) vs optimistic (target
  first). The width of that band IS the path-dependence uncertainty. A policy
  whose band is tight is trustworthy; a wide band must be shadow-confirmed.

  This harness intentionally only models STOP/TARGET LEVEL changes (path-extreme
  comparisons). It does NOT model trailing stops, break-even moves, or
  partial-then-runner — those are sequence-dependent and a bounding box cannot
  rank them properly. Use the shadow loop for those.

USAGE:
    python -m analysis.exit_replay                       # DUAL short, default grid
    python -m analysis.exit_replay --kind DUAL --dir short --grid 0.5,0.6,0.7,0.8,1.0
    python -m analysis.exit_replay --kind DUAL --dir short --exclude-session eu
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config

# Binance USDT-M taker round-trip, ALL-IN. Measured 2026-07-26 from 7,109 real
# taker fills priced against Binance 1m OHLC:
#     realised commission  0.099%   (the actual rate; the book rate is 0.10%)
#   + execution vs mid     0.325%   (spread crossed, momentum- and footprint-controlled)
#   = all-in               0.424%
#
# Was 0.08, which is below even commission alone and understated the true cost by
# 5.3x. That mattered: this module's entire job is "is the cohort +EV AFTER costs",
# and a fee constant 5x too low answers a different question. Raising it can only
# make cohorts look worse, never better, so nothing previously killed can revive.
#
# Execution cost scales with volatility (+0.18% per 1% of the fill minute's range,
# t=29.5), so cohorts that fire into fast moves pay more than this average.
FEE_RT_PCT = 0.424
FEE_RT_PCT_LEGACY = 0.08       # the pre-2026-07-26 value, kept so old output can be reconciled


@dataclass(frozen=True)
class Trade:
    """One resolved alert, reduced to what the replay needs."""
    direction: str
    stop_dist: float      # original stop distance from entry, % (>0)
    tp1_dist: float       # original tp1 distance from entry, % (>0)
    outcome_pct: float    # realized % under the ORIGINAL policy
    mfe: float            # max favorable excursion %, >= 0
    mae: float            # max adverse excursion %, <= 0
    hour_utc: int         # fire hour (UTC), for session filtering


@dataclass(frozen=True)
class ExitPolicy:
    """Stop/target levels expressed as multiples of the original distances."""
    stop_mult: float = 1.0    # new stop = stop_mult * original stop distance
    tp1_mult: float = 1.0     # new tp1  = tp1_mult  * original tp1 distance


@dataclass(frozen=True)
class ReplayResult:
    pess_pct: float          # realized % under pessimistic ordering
    opt_pct: float           # realized % under optimistic ordering
    bucket: str              # 'win' | 'loss' | 'ambiguous' | 'unresolved'


def replay_trade(t: Trade, p: ExitPolicy) -> ReplayResult:
    """Replay one trade under an alternative exit policy using path extremes.

    Returns pessimistic and optimistic realized % (identical unless the trade is
    'ambiguous' — reached both the new stop and the new target). Fees are NOT
    applied here; the cohort aggregator applies them once per trade.
    """
    new_stop = p.stop_mult * t.stop_dist        # positive %
    new_tp1 = p.tp1_mult * t.tp1_dist           # positive %

    reached_stop = t.mae <= -new_stop           # MAE is negative
    reached_tp1 = t.mfe >= new_tp1

    if reached_stop and reached_tp1:
        # Both extremes reached — order unknown.
        return ReplayResult(pess_pct=-new_stop, opt_pct=+new_tp1, bucket="ambiguous")
    if reached_tp1:
        return ReplayResult(pess_pct=+new_tp1, opt_pct=+new_tp1, bucket="win")
    if reached_stop:
        return ReplayResult(pess_pct=-new_stop, opt_pct=-new_stop, bucket="loss")
    # Neither level reached over the tracked life — the trade closed inside the
    # band. Best available estimate of that close is the originally recorded
    # outcome_pct (it is the only realized number we have for this path).
    return ReplayResult(pess_pct=t.outcome_pct, opt_pct=t.outcome_pct, bucket="unresolved")


@dataclass(frozen=True)
class ExitStats:
    policy: ExitPolicy
    n: int
    wins: int
    losses: int
    ambiguous: int
    unresolved: int
    win_rate: float
    avg_win: float
    avg_loss: float
    rr: Optional[float]
    net_ev_pess: float       # mean net % after fee, pessimistic ordering
    net_ev_opt: float        # mean net % after fee, optimistic ordering


def replay_cohort(trades: list[Trade], policy: ExitPolicy) -> ExitStats:
    """Aggregate a policy over a cohort. Net EV is reported as a [pess, opt]
    band; the two collapse to one number when there are no ambiguous trades."""
    n = len(trades)
    pess_results, opt_results = [], []
    buckets = {"win": 0, "loss": 0, "ambiguous": 0, "unresolved": 0}
    wins_pct, losses_pct = [], []
    for t in trades:
        r = replay_trade(t, policy)
        buckets[r.bucket] += 1
        pess_results.append(r.pess_pct)
        opt_results.append(r.opt_pct)
        # Win/loss accounting uses the pessimistic (conservative) realization.
        if r.pess_pct > 0:
            wins_pct.append(r.pess_pct)
        elif r.pess_pct < 0:
            losses_pct.append(r.pess_pct)

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    nw, nl = len(wins_pct), len(losses_pct)
    avg_win = _mean(wins_pct)
    avg_loss = _mean(losses_pct)        # negative
    rr = (avg_win / abs(avg_loss)) if (nw and nl and avg_loss) else None
    net_ev_pess = _mean(pess_results) - FEE_RT_PCT if n else 0.0
    net_ev_opt = _mean(opt_results) - FEE_RT_PCT if n else 0.0
    win_rate = (nw / n) if n else 0.0
    return ExitStats(
        policy=policy, n=n, wins=nw, losses=nl,
        ambiguous=buckets["ambiguous"], unresolved=buckets["unresolved"],
        win_rate=round(100 * win_rate, 1),
        avg_win=round(avg_win, 4), avg_loss=round(avg_loss, 4),
        rr=round(rr, 3) if rr is not None else None,
        net_ev_pess=round(net_ev_pess, 4), net_ev_opt=round(net_ev_opt, 4),
    )


# ── Data loading ──────────────────────────────────────────────────────────────
_SESSIONS = {"asia": range(0, 8), "eu": range(8, 16), "us": range(16, 24)}


def load_trades(kind: str, direction: str, db_path: str = None) -> list[Trade]:
    """Pull resolved trades with a usable trade card (valid entry/stop/tp1 and
    recorded MFE/MAE). ts_fired is epoch seconds."""
    db = sqlite3.connect(db_path or config.DB_PATH)
    cur = db.cursor()
    cur.execute(
        """
        SELECT direction, entry_price, stop_loss, tp1, outcome, outcome_pct,
               max_favorable_pct, max_adverse_pct, ts_fired
        FROM alert_outcomes
        WHERE alert_kind = ? AND direction = ?
          AND outcome IN ('win','loss')
          AND entry_price > 0 AND stop_loss > 0 AND tp1 > 0
          AND max_favorable_pct IS NOT NULL AND max_adverse_pct IS NOT NULL
        """,
        (kind, direction),
    )
    trades = []
    for (d, entry, stop, tp1, outcome, opct, mfe, mae, ts) in cur.fetchall():
        stop_dist = abs(entry - stop) / entry * 100
        tp1_dist = abs(tp1 - entry) / entry * 100
        if stop_dist <= 0 or tp1_dist <= 0:
            continue
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour if ts else -1
        trades.append(Trade(
            direction=d, stop_dist=stop_dist, tp1_dist=tp1_dist,
            outcome_pct=opct if opct is not None else 0.0,
            mfe=mfe, mae=mae, hour_utc=hour,
        ))
    db.close()
    return trades


def baseline_net_ev(trades: list[Trade]) -> float:
    """Net EV of the policy that ACTUALLY fired (recorded outcome_pct)."""
    if not trades:
        return 0.0
    return round(sum(t.outcome_pct for t in trades) / len(trades) - FEE_RT_PCT, 4)


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="Exit-policy backtest over recorded MFE/MAE")
    ap.add_argument("--kind", default="DUAL")
    ap.add_argument("--dir", dest="direction", default="short", choices=["short", "long"])
    ap.add_argument("--grid", default="0.4,0.5,0.6,0.7,0.8,0.9,1.0",
                    help="comma-separated stop multipliers to sweep")
    ap.add_argument("--tp1-mult", type=float, default=1.0, help="tp1 distance multiplier (held fixed by default)")
    ap.add_argument("--exclude-session", choices=["asia", "eu", "us"], default=None,
                    help="drop trades fired in this UTC session")
    args = ap.parse_args()

    trades = load_trades(args.kind, args.direction)
    if args.exclude_session:
        hrs = set(_SESSIONS[args.exclude_session])
        trades = [t for t in trades if t.hour_utc not in hrs]

    if not trades:
        print("No trades match the filter.")
        return

    base = baseline_net_ev(trades)
    sess = f" (excl {args.exclude_session.upper()})" if args.exclude_session else ""
    print(f"\nEXIT REPLAY -- {args.kind} {args.direction}{sess} | n={len(trades)} | fee={FEE_RT_PCT}%")
    print(f"baseline net EV (as fired): {base:+.4f}%/trade\n")
    print(f"{'stopx':>6} {'win%':>6} {'R:R':>6} {'netEV(pess)':>12} {'netEV(opt)':>11} "
          f"{'d vs base':>10} {'ambig':>6} {'unres':>6}")
    print("-" * 76)

    grid = [float(x) for x in args.grid.split(",")]
    for m in grid:
        st = replay_cohort(trades, ExitPolicy(stop_mult=m, tp1_mult=args.tp1_mult))
        mid = (st.net_ev_pess + st.net_ev_opt) / 2
        delta = mid - base
        rr = f"{st.rr:.2f}" if st.rr is not None else "  -  "
        mark = "  <- baseline" if abs(m - 1.0) < 1e-9 else ""
        print(f"{m:>6.2f} {st.win_rate:>6.1f} {rr:>6} {st.net_ev_pess:>+12.4f} "
              f"{st.net_ev_opt:>+11.4f} {delta:>+10.4f} {st.ambiguous:>6} {st.unresolved:>6}{mark}")

    print("\nnetEV is a [pessimistic, optimistic] band; width = path-dependence")
    print("uncertainty from trades that reached both the new stop and the target.")
    print("Path-LEVEL changes only -- trailing/partial policies need the shadow loop.")


if __name__ == "__main__":
    main()
