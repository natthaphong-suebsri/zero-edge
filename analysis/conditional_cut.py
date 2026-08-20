# -*- coding: utf-8 -*-
"""
Conditional cut — "is DUAL-short an edge, or just beta?" (the forced-liquidation test).

Pre-registered in docs/CONDITIONAL_CUT_PREREGISTER.md. Buckets DUAL-short outcomes by
**trapped-long density** (OI / L-S context at fire time) and asks: does the edge CONCENTRATE
where crowded longs would get force-liquidated, or is EV flat across buckets (= beta)?

Data sources:
  - alert_outcomes   : the DUAL-short alerts + realized outcome_pct (DB)
  - framework_signals: OI / L-S context per scan — oi_delta_pct, ls_ratio (DB)
  - Binance fapi     : BTCUSDT daily klines for the 20-day-SMA regime tag, fetched at
                       read time. Replaces the sparse framework_signals close (multi-day
                       gaps made its trailing-window SMA unreliable — audit 2026-06-10).
                       The regime filter therefore needs network access; --no-regime-filter
                       skips it. Read-side only — the live scanner is unchanged.

The verdict belongs to FORWARD clean data (default --since 2026-06-01). Run on old data only as a
code smoke-test, NOT for inference (that would be the p-hacking the pre-registration guards against).

Usage:
  python -m analysis.conditional_cut                 # since 2026-06-01, downtrend-filtered
  python -m analysis.conditional_cut --since 2026-01-01 --no-regime-filter
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config  # noqa: E402
import sqlite3  # noqa: E402

FEE, SLIPPAGE, Z = 0.08, 0.15, 1.96

# MEASURED all-in round-trip cost -- 2026-07-26, from 7,109 real taker fills priced
# against Binance 1m OHLC. Realised commission was 0.099% (the actual rate, not the
# book rate) and execution-vs-mid 0.325%: 0.424% all in, against the 0.23% that
# FEE + SLIPPAGE assumes. Execution cost also scales with volatility -- +0.18% per
# 1% of the fill minute's range, t=29.5 -- so alerts firing into fast moves pay
# MORE than this average, and DUAL-shorts fire into fast moves by construction.
#
# FEE and SLIPPAGE are deliberately NOT changed. They are pre-registered, and the
# Sept-1 verdict has to stay computable exactly as locked. The measured figure
# rides alongside instead, the same way honest_pctl rides alongside the raw
# liquidation score -- so nothing silently moves the bar the verdict is judged on.
COST_MEASURED_RT = 0.424
SMA_WINDOW_DAYS = 20           # 20-day BTC SMA defines the downtrend regime (locked)
BTC_KLINES_URL = ("https://fapi.binance.com/fapi/v1/klines"
                  "?symbol=BTCUSDT&interval=1d&limit=1500")
CTX_TOLERANCE_S = 180          # match an alert to its framework_signals row within 3 min
REALIZED = ("win", "loss", "horizon")


def wilson(k: int, n: int) -> tuple:
    if not n:
        return (None, None)
    p = k / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return (round(100 * (c - h), 1), round(100 * (c + h), 1))


def cell(rows: list[tuple]) -> dict | None:
    """rows: (day, pct). Pooled + correlation-adjusted (design-effect) stats."""
    if not rows:
        return None
    pcts = [p for _, p in rows]
    n = len(pcts)
    wins = sum(1 for p in pcts if p > 0)
    p_bar = wins / n
    ev = sum(pcts) / n - FEE
    lo, hi = wilson(wins, n)
    byday = defaultdict(list)
    for d, p in rows:
        byday[d].append(p)
    daily_wr = [sum(1 for p in ps if p > 0) / len(ps) for ps in byday.values()]
    navg = statistics.mean(len(ps) for ps in byday.values())
    if len(byday) >= 2 and 0 < p_bar < 1:
        binom = p_bar * (1 - p_bar) / navg
        deff = statistics.pvariance(daily_wr) / binom if binom else float("nan")
        eff_n = n / deff if deff and deff > 0 else float("nan")
    else:
        deff = eff_n = float("nan")
    # The pre-registered PASS criterion (printed at the end of main) asks for the
    # EFF-N CI floor. `ci` above is computed on RAW n, and with deff measured at
    # 1.9-2.5 that interval is ~1.4x too NARROW -- the direction that manufactures
    # a false PASS. So compute the interval the criterion actually names, and print
    # both, clearly labelled, rather than quietly swapping one for the other.
    if eff_n == eff_n and eff_n > 0:
        e = min(eff_n, n)   # deff<1 would otherwise claim more information than was collected
        ci_eff = wilson(p_bar * e, e)
    else:
        ci_eff = (None, None)
    return {"n": n, "wr": 100 * p_bar, "ev": ev, "ev_slip": ev - SLIPPAGE,
            "ci": (lo, hi), "ci_eff": ci_eff,
            "ev_measured": ev + FEE - COST_MEASURED_RT,
            "deff": deff, "eff_n": eff_n}


def fmt(label: str, c: dict | None) -> str:
    if not c:
        return f"  {label:<26} n=0"
    effn = f"{c['eff_n']:.0f}" if c['eff_n'] == c['eff_n'] else "n/a"
    deff = f"{c['deff']:.1f}" if c['deff'] == c['deff'] else "n/a"
    cie = (f"[{c['ci_eff'][0]},{c['ci_eff'][1]}]"
           if c['ci_eff'][0] is not None else "[n/a]")
    return (f"  {label:<26} n={c['n']:<4} effn={effn:<5} deff={deff:<5} "
            f"WR={c['wr']:5.1f}% netEV={c['ev']:+.3f}% EVslip={c['ev_slip']:+.3f}% "
            f"EVmeas={c['ev_measured']:+.3f}% "
            f"CIraw[{c['ci'][0]},{c['ci'][1]}] CIeff{cie}")


def _btc_downtrend_by_date() -> dict:
    """Map each UTC date -> True if BTC closed below its trailing 20-day SMA that day.

    Faithful regime source: BTCUSDT daily candles from Binance USDT-M (fapi) — the same
    feed and locked definition tools/btc_regime_killtest.py uses (downtrend = daily close
    < 20-day SMA, window includes the fire date). Keeping it identical to the kill-test
    means the conditional cut and its naive baseline tag regime the same way.
    """
    req = urllib.request.Request(BTC_KLINES_URL, headers={"User-Agent": "conditional-cut/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    # kline: [openTime(ms), open, high, low, close, vol, closeTime, ...]
    closes = sorted(
        (datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date(), float(k[4]))
        for k in data
    )
    vals = [px for _, px in closes]
    reg: dict = {}
    for i, (d, px) in enumerate(closes):
        if i < SMA_WINDOW_DAYS - 1:
            continue  # not enough history for a 20-day SMA yet
        sma = sum(vals[i - SMA_WINDOW_DAYS + 1: i + 1]) / SMA_WINDOW_DAYS
        reg[d] = px < sma
    return reg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-01", help="YYYY-MM-DD (clean-data window start)")
    ap.add_argument("--no-regime-filter", action="store_true",
                    help="skip the BTC-downtrend filter (use all DUAL-shorts)")
    ap.add_argument("--db", default=config.DB_PATH)
    args = ap.parse_args()
    since = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

    c = sqlite3.connect(args.db)

    # --- DUAL-short realized outcomes -------------------------------------------------
    outs = c.execute(
        "SELECT symbol, ts_fired, outcome_pct FROM alert_outcomes "
        "WHERE alert_kind='DUAL' AND direction='short' AND outcome IN ('win','loss','horizon') "
        "AND outcome_pct IS NOT NULL AND ts_fired >= ?", (since,)).fetchall()

    # --- OI/L-S context per symbol from framework_signals (nearest scan) --------------
    ctx: dict = defaultdict(list)   # symbol -> sorted [(ts, oi_delta_pct, ls_ratio)]
    for sym, ts, oid, ls in c.execute(
            "SELECT symbol, ts, oi_delta_pct, ls_ratio FROM framework_signals "
            "WHERE ts >= ? ORDER BY ts", (since - 3600,)):
        ctx[sym].append((ts, oid, ls))
    ctx_ts = {s: [r[0] for r in v] for s, v in ctx.items()}

    def context_at(sym: str, ts: int):
        arr, times = ctx.get(sym), ctx_ts.get(sym)
        if not arr:
            return None, None
        i = bisect.bisect_left(times, ts)
        best = min([j for j in (i - 1, i) if 0 <= j < len(arr)],
                   key=lambda j: abs(arr[j][0] - ts), default=None)
        if best is None or abs(arr[best][0] - ts) > CTX_TOLERANCE_S:
            return None, None
        return arr[best][1], arr[best][2]   # oi_delta_pct, ls_ratio

    c.close()

    # --- BTC 20-day SMA regime (faithful daily klines, not the sparse DB close) -------
    # framework_signals' BTCUSDT close had multi-day gaps (audit 2026-06-10), so an SMA
    # built from it was unreliable. Pull BTC daily candles from Binance instead and tag
    # each alert by its fire-date regime. Same locked definition as the kill-test.
    btc_down: dict = {}
    if not args.no_regime_filter:
        try:
            btc_down = _btc_downtrend_by_date()
        except Exception as e:
            print(f"[!] Could not fetch BTC daily klines for the regime filter: {e}")
            print("    Re-run with network access, or pass --no-regime-filter to skip it.")
            return

    def is_downtrend(ts: int) -> bool | None:
        """True/False if we have a 20-day SMA for the alert's UTC fire-date, else None."""
        return btc_down.get(datetime.fromtimestamp(ts, timezone.utc).date())

    # --- assemble enriched rows -------------------------------------------------------
    rows, no_ctx, no_regime = [], 0, 0
    for sym, ts, pct in outs:
        oid, ls = context_at(sym, ts)
        if oid is None or ls is None:
            no_ctx += 1
            continue
        if not args.no_regime_filter:
            dt = is_downtrend(ts)
            if dt is None:
                no_regime += 1
                continue
            if not dt:
                continue
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        rows.append({"day": day, "pct": pct, "oi_delta": oid, "ls": ls})

    regime_note = "ALL regimes" if args.no_regime_filter else "BTC downtrend only"
    print(f"=== Conditional cut: DUAL-short, {regime_note}, since {args.since} ===")
    print(f"realized DUAL-shorts: {len(outs)} | usable (had OI/LS context): {len(rows)} | "
          f"dropped: no-context={no_ctx} no-regime={no_regime}")
    if len(rows) < 30:
        print("\n[!] Sample too small for inference — this is a CODE SMOKE-TEST only.")
        print("    The real verdict awaits forward clean data (see CONDITIONAL_CUT_PREREGISTER.md).")
    print()

    def stats_for(pred, label):
        print(fmt(label, cell([(r["day"], r["pct"]) for r in rows if pred(r)])))

    # Bucket 1: ls_ratio terciles
    ls_vals = sorted(r["ls"] for r in rows)
    if len(ls_vals) >= 3:
        t1 = ls_vals[len(ls_vals) // 3]
        t2 = ls_vals[2 * len(ls_vals) // 3]
        med = statistics.median(ls_vals)
        print("-- by L/S ratio tercile (crowded longs = more liquidation fuel) --")
        stats_for(lambda r: r["ls"] <= t1, "ls LOW (fewest longs)")
        stats_for(lambda r: t1 < r["ls"] <= t2, "ls MID")
        stats_for(lambda r: r["ls"] > t2, "ls HIGH (most longs)")
        print("\n-- by OI delta sign --")
        stats_for(lambda r: r["oi_delta"] is not None and r["oi_delta"] > 0, "OI rising (new longs?)")
        stats_for(lambda r: r["oi_delta"] is not None and r["oi_delta"] <= 0, "OI flat/falling")
        print("\n-- COMBINED trapped-long density (the pre-registered test) --")
        stats_for(lambda r: r["ls"] >= med and (r["oi_delta"] or 0) > 0, "TRAPPED (ls>=med & OI up)")
        stats_for(lambda r: not (r["ls"] >= med and (r["oi_delta"] or 0) > 0), "NOT trapped")
        print("\nPASS (preregister): TRAPPED EVslip > 0 with eff-n CI floor > 0 AND materially > NOT.")
        print("KILL: EV flat across buckets (no interaction) -> it's beta, retire the alpha claim.")
        print("\nREADING THE COLUMNS -- two ways to get this wrong:")
        print("  * Apply the PASS criterion to CIeff, NOT CIraw. CIraw is the raw-n interval;")
        print("    at the measured deff of 1.9-2.5 it is ~1.4x too narrow, which is the")
        print("    direction that manufactures a false PASS.")
        print("  * EVslip (pre-registered, 0.23% assumed cost) is what the verdict uses.")
        print("    EVmeas restates the same EV at the MEASURED 0.424% all-in cost. If the two")
        print("    disagree about the sign, the verdict still follows EVslip -- but say so.")


if __name__ == "__main__":
    main()
