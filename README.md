# Zero Edge

**I built a crypto futures scanner, ran it live for three months, and then measured whether its
alerts predicted anything. They did not — and the confidence interval is tight enough to say so
as a measurement rather than a shrug.**

| | |
|---|---|
| Alerts recorded | **11,953** |
| Days measured | **88** |
| Resolved win/loss | **5,921** |
| Pre-registered hypotheses | **1**, killed on its own criterion |
| Directional edge, BTC-relative, 4h | **−0.004%** &nbsp;[−0.072, +0.064] |
| Measured round-trip cost | **0.424%** |

The system scanned ~350 Binance USDT-M perpetuals on a loop — scoring volatility compression,
open-interest shifts, cumulative volume delta and liquidation clustering — and fired graded alerts
to Telegram. It ran as a service on a Linux VPS and recorded every alert it ever sent along with
the price path that followed.

That recording is the only reason this repository exists. The scanner's job was to find edge. Its
more valuable output turned out to be a clean dataset for proving it never had any.

---

## 1. The alerts carry no directional information

Hold every alert from fire time to a fixed horizon. No stop, no target, no path rule — just the
return. Then subtract each symbol's BTC beta over the identical window, because a book that is 80%
short in a falling market will look brilliant for reasons that have nothing to do with the signal.

What remains is the alert's own contribution:

| Horizon | n | BTC-relative return | 95% interval (cluster-robust) |
|---|---:|---:|---|
| 1h | 2,833 | +0.014% | [−0.014, +0.042] |
| **4h** (≈ median hold) | 2,791 | **−0.004%** | **[−0.072, +0.064]** |
| 12h | 2,718 | −0.023% | [−0.143, +0.097] |
| 24h | 2,781 | +0.052% | [−0.195, +0.298] |

**The interval is the finding, not the point estimate.** At four hours it is ±0.07% — three to six
times narrower than the round-trip cost. Even the most optimistic end of the range would pay for
less than a third of the cheapest possible trade.

The dataset makes this harder to explain away, not easier. Over the window Bitcoin fell **19.2%**
and the book was **80% short** (6,099 short against 1,527 long) — the most favourable backdrop a
short-biased system could ask for. The raw, unadjusted return was still −0.002%.

One cell did come up statistically significant: the US session, 13–21 UTC, at +0.130%
[+0.013, +0.247]. It changes nothing. The effect is already smaller than the cost of trading it,
so more data cannot rescue it — the size is the problem, not the significance. It was also roughly
the 81st test run against this dataset.

## 2. The win rate was hiding it the whole time

For months the scoreboard read like a coin flip that was nearly working: 44% win rate, wins bigger
than losses, always one adjustment from profitable.

**A win rate does not measure whether an alert was right.** It measures which level the price path
touched first — so it describes where you placed the stop and the target, not the signal. Move the
levels and the win rate moves, with no new information entering the system.

The clean version: the strongest short alerts were directionally correct **55% of the time** at
four hours, and still had a mean of zero. Small wins, fat losses — the shape of being short an
asset whose upside tail is longer than its downside.

## 3. The one idea good enough to pre-register, and what happened to it

One pattern looked real: fire short alerts only when Bitcoin's trailing 12-hour return is below
−1%. In-sample, 70.0% win rate over 70 trades at +0.487% each.

The success criterion was written down **before** collecting more data: the lower bound of net
expected value must clear costs across three non-overlapping forward windows of ≥60 trades,
including at least one non-downtrend window. So was the kill condition, with a date.

| Window | Dates | n | Win rate | Net EV | 95% interval | Clears? |
|---|---|---:|---:|---:|---|---|
| 1 | 06-01 → 06-16 | 60 | 61.7% | +0.607% | [−0.085, +1.299] | no |
| 2 | 06-16 → 06-30 | 60 | 60.0% | +0.282% | [−0.261, +0.825] | no |
| 3 | 06-30 → 07-16 | 60 | 28.3% | **−0.888%** | [−1.746, −0.030] | no — significantly negative |
| 4 | 07-16 → 07-28 | 60 | 60.0% | +0.134% | [−0.489, +0.757] | no |

**Zero of four cleared. The criterion required three.** The in-sample 70.0% win rate came out at
50.0% forward. Full adjudication: [`docs/VERDICT_btc_regime_short_v1.md`](docs/VERDICT_btc_regime_short_v1.md).

Two things were visible early and I ignored both. The original 70-trade sample sat entirely inside
a single Bitcoin downtrend — and "shorts win when Bitcoin falls" is nearly tautological inside a
downtrend. And a trailing 12-hour signal is by construction late to every turn; I had written
"reaction, not prediction" into the spec myself, then quietly hoped otherwise.

The most uncomfortable line in the verdict is not about markets. The kill date was 30 June. I
adjudicated on 7 August — **68 days late**. A pre-registration you don't call on time is not a
protocol, it's a preference.

## 4. A bucket that looked profitable because it survived

Alerts that never touched either level within 24 hours landed in a bucket labelled `expired` —
1,824 rows, ~15% of the sample. That bucket ran **61.8% positive**, which looks like a discovery.

It is geometry. The target sat at 1.5× the risk and the stop at 1.0× (measured 2.94% against
1.96% — exactly the intended ratio). A trade enters this bucket only by surviving that asymmetric
barrier without touching either side, and conditioning on survival through an asymmetric barrier
skews the endpoint positive **with no market effect required at all**.

Proof by simulation: zero-drift random walks through the identical barrier geometry and the
identical 60-second polling, calibrated so the same 23% of paths survive, produce **61.2%
positive** against 61.8% observed. The entire apparent edge is reproduced by noise passed through
the measurement apparatus.

The returns were recorded correctly. The sample simply was not random, and nothing in the column
names said so.

## 5. Every earlier confidence interval was too narrow

Alerts do not arrive independently — a regime turns and the whole book fires, wins and loses
together. The day-clustering design effect measured **11.1**, against the 1.9–2.5 assumed.

That turns an apparent 3,692 observations into an effective sample of about **330**. Every interval
computed on that cohort beforehand was ~3.3× too narrow — the direction that manufactures false
confidence rather than false doubt. A runs test on the same sequence returns Z = −10.68.

## 6. No take-profit level can rescue a zero-mean signal

The obvious response is to fix the exits. Tested twice — the second time after discovering the cost
constant used in the first pass was 5.3× too low.

| Stop multiple | Win rate | Reward : risk | Net EV (pessimistic) | Net EV (optimistic) |
|---|---:|---:|---:|---:|
| 0.40× | 33.5% | 3.65 | −0.156% | +0.049% |
| 0.60× | 38.2% | 2.37 | −0.215% | −0.089% |
| 1.00× | 44.1% | 1.38 | −0.365% | −0.365% |

Every pessimistic cell is negative; the single positive number is the most path-ambiguous cell
resolved in the most favourable direction. Tightening the stop improves things monotonically — from
very negative toward less negative.

The reason is structural. For a random walk, the chance of reaching a target at *k* times the risk
before the stop is 1/(1+k). The win rate needed to break even after costs, divided by that
probability, is:

```
breakeven ÷ martingale  =  1 + fee_in_R  =  1.355
```

**Identical for every target multiple.** Every take-profit level, at any distance, needs ~36% more
hit rate than a random walk delivers, and the deficit is scale-invariant — moving the target
relocates it rather than shrinking it. Measured win rate was 44.1%; the shipped 1.5× target needs
54.2% at true cost.

Two design details made it worse. Targets were placed at liquidation clusters — high-volatility
zones, so the exit was aimed at the widest-spread moment available. And a stop is inherently a
market order triggering during fast adverse moves, so tighter stops fire more often *and* pay more
each time. The one structural advantage a target has is resting as a limit order for the maker
rebate; in practice **99.44% of fills were taker**.

## 7. What is left when the edge is zero

```
P&L  =  −( turnover × cost )
```

Not a strategy — an accounting identity. It leaves exactly two levers with arithmetic behind them:
**trade less** and **pay less per trade**. Neither creates edge; both reduce bleed.

Bucketing my own trading by fill count in 15-minute windows: below 11 fills the buckets summed to
**+$482**; at 11 and above, **−$837**. The sign of profitability was set by turnover, not selection
— and 11 fills is the 75th percentile of my own behaviour, so the entire drawdown lived in my worst
quartile of self-control.

---

## If you are building one of these

1. **Record everything before you believe anything.** This report exists only because the system
   logged every alert with the price path that followed. Worth more than any feature I built.
2. **Strip the market before claiming a signal.** A short book in a falling market looks skilled.
3. **Report intervals, not point estimates — and correct them for clustering.** A tight interval
   containing zero is a finding; a point estimate is a mood.
4. **Compare against the cost floor, not against zero.** An edge smaller than spread plus fees is
   not a small edge, it is a loss.
5. **Write the kill condition down with a date, then honour the date.**
6. **Interrogate every bucket that looks profitable.** If a cohort is defined by surviving a
   filter, its returns are conditioned on that survival. Push pure noise through your own apparatus
   and see what it produces before believing what your data produced.

The system was retired rather than tuned. A profitable-looking backtest would have taken an
afternoon to produce; the measurement that mattered had already been made.

---

## What is in this repository

```
analysis/
  exit_replay.py       offline replay of alternative stop/target policies against
                       recorded MFE/MAE; reports a [pessimistic, optimistic] band
                       for trades that reached both levels (path order unknown)
  regime_breaker.py    trailing-window regime detector + offline calibration replay
  rule_scoreboard.py   standing report per shadow rule: fire vs skip win rate, lift,
                       Wilson lower bound, net EV, verdict
  conditional_cut.py   pre-registered conditional-cut evaluation; prints raw-n and
                       effective-n intervals side by side
docs/
  VERDICT_btc_regime_short_v1.md   the full kill adjudication
tests/                 28 tests, no network, no database required
```

```bash
python -m pytest tests/ -q     # 28 passed
```

**Reproducing the numbers** needs the recorded database, which is not distributed — it contains
Telegram chat ids (personal data) and runs to 12 GB. The modules read a SQLite file whose `alert_outcomes` table
carries, per alert: fire timestamp, symbol, direction, alert kind, entry, stop, target, realised
outcome, `outcome_pct`, and the MFE/MAE extremes over the holding window. Point `config.DB_PATH` at
your own recording with that shape and every figure above re-derives.

The analysis modules are reproduced here byte-identical to the versions that produced these
results. `config.py` is a trimmed shim supplying the one value they import.

## Scope and honesty

This is a claim about one system, measured carefully, that turned out not to work. It is not
trading advice and not a claim about markets in general. Any of the numbers above can be checked
against the code that produced them; where a single cost figure appears, it is the measured 0.424%
round trip rather than the more flattering 0.23% originally pre-registered.

MIT licensed. Built and measured by Natthaphong Suebsri.
