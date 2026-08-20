# -*- coding: utf-8 -*-
"""
analysis/rule_scoreboard.py
─────────────────────────────────────────────────────────────────────────────
Standing scoreboard for every shadow rule. Replaces the hand-computed prose
verdicts that lived in shadow_rules.py comments with one queryable report.

For each rule it joins shadow_predictions -> alert_outcomes (resolved win/loss)
and reports, for the would_fire=1 (KEEP) cohort vs the would_fire=0 (SKIP)
cohort:
    n, win rate, lift (fire WR - skip WR), Wilson 95% lower bound on fire WR,
    net EV of the fired cohort after fees, and a screening status.

RIGOR GUARD (the whole reason this exists, per the 2026-05-29 review):
  The runs test on the DUAL-short sequence was Z=-10.68 — outcomes are NOT
  independent, they cluster by regime. Wilson intervals and lift therefore
  ASSUME something false and OVERSTATE precision. So a SCREEN-PASS here is
  necessary-but-NOT-sufficient: it means "worth taking to the full promotion
  protocol" (pre-registration, time-split OOS, walk-forward, runs test), NOT
  "promote it." Nothing is promoted from this board alone.

USAGE:
    python -m analysis.rule_scoreboard
"""
from __future__ import annotations

import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config

# All-in taker round-trip, measured 2026-07-26 from 7,109 real fills: commission
# 0.099% + execution-vs-mid 0.325% = 0.424%. Was 0.08 -- below commission alone.
# See analysis/exit_replay.py for the derivation. Raising the cost can only demote
# rules, never promote them, so the pre-registered screening thresholds below stay
# valid: nothing that failed can now pass.
FEE_RT_PCT = 0.424
FEE_RT_PCT_LEGACY = 0.08       # pre-2026-07-26, kept for reconciling older output
Z = 1.96
# Pre-registered screening thresholds (named constants = the pre-registration).
MIN_N_PROMOTE = 50
MIN_LIFT_PP = 8.0


def wilson_low(k: int, n: int) -> Optional[float]:
    """Wilson 95% lower bound on a proportion, as a percent. None if n==0."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    half = (Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))) / denom
    return round(100 * (center - half), 1)


@dataclass(frozen=True)
class RuleRow:
    rule: str
    active: bool
    n_fire: int
    wr_fire: Optional[float]
    n_skip: int
    wr_skip: Optional[float]
    lift_pp: Optional[float]
    fire_wilson_low: Optional[float]
    fire_net_ev: Optional[float]
    status: str


def classify(n_fire: int, wr_fire: Optional[float], lift: Optional[float],
             net_ev: Optional[float]) -> str:
    if n_fire == 0:
        return "NO FIRES"
    if net_ev is not None and net_ev < 0:
        return "REJECT (netEV<0)"
    if lift is not None and lift <= -MIN_LIFT_PP:
        return "REJECT (lift<0)"
    if n_fire < MIN_N_PROMOTE:
        return f"WATCH (n={n_fire})"
    # n>=50, netEV>=0, lift not strongly negative:
    if net_ev is not None and net_ev > 0 and (lift is None or lift >= MIN_LIFT_PP):
        return "SCREEN-PASS*"
    return "NEUTRAL"


def build(db_path: str = None) -> list[RuleRow]:
    db = sqlite3.connect(db_path or config.DB_PATH)
    cur = db.cursor()
    cur.execute(
        """
        SELECT s.rule_name, s.would_fire,
               COUNT(*) AS n,
               SUM(CASE WHEN o.outcome='win'  THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN o.outcome='loss' THEN 1 ELSE 0 END) AS losses,
               SUM(o.outcome_pct) AS sum_pct
        FROM shadow_predictions s
        JOIN alert_outcomes o ON o.diag_alert_id = s.diag_alert_id
        WHERE o.outcome IN ('win','loss') AND o.outcome_pct IS NOT NULL
        GROUP BY s.rule_name, s.would_fire
        """
    )
    agg: dict[str, dict] = {}
    for rule, fire, n, wins, losses, sum_pct in cur.fetchall():
        agg.setdefault(rule, {})[int(fire)] = {
            "n": n, "wins": wins or 0, "losses": losses or 0, "sum_pct": sum_pct or 0.0,
        }
    db.close()

    # Which rules are still wired live?
    try:
        from fetchers.shadow_rules import SHADOW_RULES
        active = {name for name, _ in SHADOW_RULES}
    except Exception:
        active = set()

    rows = []
    for rule in sorted(agg):
        fire = agg[rule].get(1, {"n": 0, "wins": 0, "losses": 0, "sum_pct": 0.0})
        skip = agg[rule].get(0, {"n": 0, "wins": 0, "losses": 0, "sum_pct": 0.0})
        nf, ns = fire["n"], skip["n"]
        wr_fire = round(100 * fire["wins"] / nf, 1) if nf else None
        wr_skip = round(100 * skip["wins"] / ns, 1) if ns else None
        lift = round(wr_fire - wr_skip, 1) if (wr_fire is not None and wr_skip is not None) else None
        fire_low = wilson_low(fire["wins"], nf) if nf else None
        net_ev = round(fire["sum_pct"] / nf - FEE_RT_PCT, 4) if nf else None
        rows.append(RuleRow(
            rule=rule, active=(rule in active),
            n_fire=nf, wr_fire=wr_fire, n_skip=ns, wr_skip=wr_skip,
            lift_pp=lift, fire_wilson_low=fire_low, fire_net_ev=net_ev,
            status=classify(nf, wr_fire, lift, net_ev),
        ))
    return rows


def main() -> None:
    rows = build()
    print("\nSHADOW RULE SCOREBOARD  (fired = would_fire=1 cohort)\n")
    print(f"{'rule':<22}{'live':>5}{'n_fire':>7}{'WR_f':>6}{'WR_skip':>8}"
          f"{'lift':>7}{'wilsonLB':>9}{'netEV':>9}  status")
    print("-" * 92)
    for r in rows:
        def s(v, suf=""):
            return (f"{v}{suf}" if v is not None else "-")
        print(f"{r.rule:<22}{('yes' if r.active else 'no'):>5}{r.n_fire:>7}"
              f"{s(r.wr_fire):>6}{s(r.wr_skip):>8}{s(r.lift_pp):>7}"
              f"{s(r.fire_wilson_low):>9}{s(r.fire_net_ev):>9}  {r.status}")
    print("\n* SCREEN-PASS = passes the screening gate only (n>=%d, netEV>0, lift>=%gpp)."
          % (MIN_N_PROMOTE, MIN_LIFT_PP))
    print("  It is NECESSARY-NOT-SUFFICIENT. Lift/Wilson assume independent outcomes,")
    print("  which the runs test (Z=-10.68) disproved. Live promotion still requires the")
    print("  full protocol: pre-registration, time-split OOS, walk-forward, runs test.")


if __name__ == "__main__":
    main()
