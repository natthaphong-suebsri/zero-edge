# VERDICT — `btc_regime_short_v1`

**Status: KILLED, 2026-08-07.** Adjudicated 68 days after its own deadline.

| | |
|---|---|
| Pre-registered | 2026-05-31 |
| Shipped (shadow-only, never wired to suppression) | 2026-05-31 |
| KILL deadline per protocol | **2026-06-30** (30-day mark) |
| Actually adjudicated | **2026-08-07** |
| Forward data available at adjudication | 2,973 resolved DUAL shorts, 2026-05-31 → 08-07 |

---

## The hypothesis, as pre-registered

> Fire DUAL short only when BTC trailing-12h return < −1%.

**SUCCESS** — Wilson-low net EV clears fee across **3 non-overlapping forward windows**
(n ≥ 60 each), including ≥ 1 non-downtrend BTC window.
**PROMOTE** — at n ≥ 150 across varied BTC regimes.
**KILL** — at the 30-day mark if the gate fails the first OOS window, or no cohort
clears fee, or autocorrelation worsens.

The in-sample result that motivated it: gate-pass DUAL shorts at **70.0% WR, net EV
+0.487%, n=70** — all inside a single declining-BTC window (80k → 73k).

## The forward result

Windows are consecutive non-overlapping blocks of 60 gate-pass rows. Net EV is at the
**pre-registered** 0.23% cost (FEE 0.08 + SLIPPAGE 0.15), CIs cluster-robust on fire-day.

| window | dates | n | WR | net EV | 95% CI | clears? |
|---|---|---|---|---|---|---|
| 1 | 06-01 → 06-16 | 60 | 61.7% | +0.607% | [−0.085, +1.299] | no — lower bound < 0 |
| 2 | 06-16 → 06-30 | 60 | 60.0% | +0.282% | [−0.261, +0.825] | no |
| 3 | 06-30 → 07-16 | 60 | 28.3% | **−0.888%** | [−1.746, −0.030] | no — **significantly negative** |
| 4 | 07-16 → 07-28 | 60 | 60.0% | +0.134% | [−0.489, +0.757] | no |

**0 of 4 windows cleared. The criterion required 3.**

Pooled gate-pass, n=284: gross +0.181%, **net −0.049%** at the pre-registered cost and
**−0.243%** at the measured 0.424%. Gate-reject, n=2,689: gross +0.007%.

The **in-sample 70.0% win rate came out at 50.0% forward** — a coin flip.

## Why it failed

The gate does separate — pass gross (+0.181%) beats reject gross (+0.007%). The separation
is simply **too small to pay for itself**. It is a real but sub-economic effect, the same
shape as the split-half drift result: directionally right, economically irrelevant.

Two things were visible early and were not acted on:

1. **The n=70 in-sample cohort sat entirely inside one BTC downtrend.** "BTC down → shorts
   win" is near-tautological in a downtrend. The real test was always a regime *transition*,
   and the first one (window 3) produced −0.888%.
2. **The trailing-12h construction lags the turn.** This was written into the original spec
   as "reaction not prediction", and confirmed live on 2026-05-31 when the skip cohort ran
   n=10 at 90% WR — the gate would have suppressed 9 of 10 winners at the start of a move.

## What this does not change

The kill is not evidence that the *opposite* rule works. The whole DUAL short book carries
no directional information: measured 2026-08-07, BTC-relative forward return on the notified
population is **−0.004% [−0.072, +0.064]** at 4h. There is no regime split of a zero that
produces a non-zero.

## Actions taken

- Removed from `SHADOW_RULES` in `fetchers/shadow_rules.py`. The rule function is **kept
  callable** so the 8,362 rows already in `shadow_predictions` stay interpretable and so it
  can be re-enabled if a future regime ever argues for it.
- All existing `shadow_predictions` rows retained. `analysis/rule_scoreboard.py` will now
  report it with `live=False`.
- No change to live alert behaviour: this rule was **never wired to suppression**, so nothing
  that fires today fired differently because of it.

## The process failure, which matters more than the rule

A pre-registration with a stated kill date sat 68 days past that date with the data sitting
in the database the whole time. The rule was harmless — shadow-only — but the habit is not.
A protocol that is not adjudicated on schedule provides the *appearance* of rigour while
allowing the same freedom as having no protocol at all: the hypothesis stays alive exactly
as long as nobody looks.

**Whatever replaces this should carry a scheduled adjudication, not a remembered one.**

---

*Future hypothesis verdicts belong beside this file as `VERDICT_<rule_name>.md`.
Retirement one-liners stay in the `SHADOW_RULES` registry comment block; the full record
lives here.*
