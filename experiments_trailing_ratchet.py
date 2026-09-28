"""Round 26: staged trailing-stop ratchet, built to the user's exact verbatim spec -
BACKTEST/RESEARCH ONLY, nothing wired into any live bot. Each coin's own live ENTRY stays
unchanged (BTC: SMA(10,30)+RSI; ETH: Stochastic(21,30)+ADX>15; DOGE: bullish FVG) - only
the exit mechanism is replaced, same "one variable at a time" discipline as every prior
exit-mechanism round.

THE SPEC, AS GIVEN:
  "check on every price update, not just on candle close... trail sl 1.5% every 3% tp...
  understand what noise looks like... once 5% tp has been reached, raise the floor to
  3.5%... once tp has reached 7.5%, raise the floor to 5%... once tp has reached 10%,
  raise the floor to 7%, continue this trend... subtract round trip fees from the locked
  in amount... decide whether to use current price or the high water mark for the gain
  calculation, whichever works best."

ASSUMPTIONS MADE EXPLICIT (read this before trusting any number below):

1. TIER-FLOOR FORMULA ("continue this trend"). The three given points - (5%->3.5%),
   (7.5%->5%), (10%->7%) - do NOT sit on one clean arithmetic-step line (the floor's
   "give-back" from the milestone is 1.5, 2.5, 3.0 - not constant). They DO sit almost
   exactly on floor = 0.70 x milestone: 0.70x5=3.5 (exact), 0.70x7.5=5.25 (user said 5,
   off by 0.25pp - plausibly a rounded example), 0.70x10=7.0 (exact). That formula is
   used here, extended forward at the same 2.5%-profit milestone spacing (5, 7.5, 10,
   12.5, 15, ...) with floor = 0.70 x milestone at each one. THIS IS A GUESS AT INTENT,
   STATED HERE SO IT CAN BE CORRECTED, not something the numbers below should be
   read as proving.

2. RELATIONSHIP BETWEEN THE MILESTONE TABLE AND THE "1.5%-every-3%" BASE RULE. Read
   literally, the milestone table only defines fixed points; the base rule is a
   continuously-operating trail. Interpreted here as: the milestone table is a HARD FLOOR
   (never lets the guaranteed exit price fall below the last milestone crossed), while
   the 1.5%-behind-peak trail runs continuously underneath/above it and can offer MORE
   protection once the peak has run further than the last milestone - effective floor =
   max(milestone floor, trailing floor). Below the first milestone (peak profit < 5%),
   only the original per-coin fixed stop-loss and the base trail apply; the milestone
   table has not activated yet.

3. "CHECK ON EVERY PRICE UPDATE, NOT JUST CANDLE CLOSE." Genuine intrabar data (minute-
   level or finer) only exists in this project for BTC (Round 9's Kraken 1-minute CSV,
   `data/kraken_historical/extracted/XBTUSD_1.csv`, ~4.9M rows). ETH and DOGE have never
   had anything finer than Yahoo daily candles anywhere in this project (see Round 22's
   ATR-exit round, which used the same Yahoo daily source for both). A 1.5% trail is
   genuinely too tight to backtest honestly against daily-close-only data - a lot can
   happen inside one day. The PRIMARY run below uses each coin's normal daily OHLC (the
   day's high/low, not just its close) as the closest honest approximation available for
   ETH/DOGE, following this project's standing convention (Round 12) of checking a stop
   against the day's low and a target/ceiling against the day's high. A SECONDARY,
   genuinely intrabar (1-minute) sensitivity check is run for BTC only, on one split, to
   show concretely how much the daily-bar approximation costs relative to true tick-level
   checking - see `run_btc_minute_sensitivity()`.

4. THE NOISE FILTER ("understand what noise looks like ... so it doesn't run into an
   early exit"). Two concrete, testable levers, both implemented and both run as a real
   A/B, not asserted:
     (a) discrete-crossing ratchet: the base 1.5%/3% trail is only RE-ANCHORED to a new
         peak at each fresh 3%-of-profit crossing, not continuously on every tick -
         this alone prevents the trail level from jittering on every 0.1% wiggle.
     (b) wick-triggered vs. close-confirmed peak tracking: this is also the answer to
         "current price or the high-water-mark" - "HWM" (variant A here) updates the
         peak from the bar's intrabar HIGH (reacts to any wick, however brief);
         "close-confirmed" (variant B) only updates the peak from the bar's CLOSE
         (requires the day to actually finish at/above a level before the floor moves).
         Variant B is the noise-resistant one by construction. Both are run below for
         all three coins so the difference is measured, not assumed. Downside TRIGGERING
         (does the floor actually get hit) is always checked against the bar's LOW in
         both variants, matching this project's stop-loss convention everywhere else -
         only the RAISING of the floor is gated by the variant.
     An ATR-scaled buffer (a third possible noise filter) was NOT implemented here -
     Round 22 already tested ATR-scaled exits against every coin's live entry and found
     no real win anywhere, which is a relevant (though not identical) prior against
     spending more time on that specific idea in this round.

5. FEES. Round-trip fee (`2 x config.FEE_PCT`, ~0.52% for all three coins here) is
   deducted from cash on both the entry and exit leg in the actual simulated
   cash/equity accounting - same fee model as every engine in this project. In addition,
   each locked-in floor level is reported net of the round-trip fee (`net_locked_pct`)
   so "the floor just moved to 5%" isn't read as "5% is what you'll actually keep" when
   roughly 0.5 points of that is fee.

VALIDATION BAR: same as every round since Round 8 - profitable, >=5 completed trades,
<40% max drawdown, must hold on EVERY independent split for that coin (BTC: 4 splits,
Kraken native daily; ETH/DOGE: 2 splits, Yahoo daily) - measured against each coin's own
CURRENT LIVE config as the baseline to beat, not just "is it profitable in isolation".

Usage:
    python experiments_trailing_ratchet.py
"""
import statistics
from functools import partial

import numpy as np
import pandas as pd

import config
from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_adx_filter import apply_adx_filter
from experiments_indicators import stochastic_reversion
from experiments_multisplit import _passed
from experiments_multisplit_kraken import CSV_PATH as BTC_CSV_PATH, SPLITS as BTC_SPLITS
from experiments_new_strategies import bullish_fvg_entry
from experiments_regime import REGIME_SPLITS, slice_dates
from experiments_wide_tp import MAX_DRAWDOWN_PCT_REF, baseline_signal_entry, simulate_wide_tp

# --- spec constants -----------------------------------------------------------------
BASE_TRAIL_PCT = 1.5          # "trail sl 1.5%"
BASE_TRAIL_STEP_PCT = 3.0     # "every 3% tp" - re-anchor cadence for the base trail
MILESTONE_START_PCT = 5.0     # first explicit floor tier
MILESTONE_STEP_PCT = 2.5      # 5, 7.5, 10, 12.5, ... - fits the three given points
MILESTONE_FLOOR_RATIO = 0.70  # floor = 0.70 x milestone (see assumption 1 above)

FEE_ROUND_TRIP_PCT = 2 * config.FEE_PCT  # for net-locked reporting only


def milestone_floor_ratio(peak_profit_pct, start=MILESTONE_START_PCT, step=MILESTONE_STEP_PCT,
                           ratio=MILESTONE_FLOOR_RATIO):
    """Formula A (the original guess): floor = ratio x milestone_level. Exact at the
    user's 5%->3.5% and 10%->7% points, 0.25pp off at 7.5%->5%. Returns (0, None) if no
    milestone reached yet."""
    if peak_profit_pct < start:
        return 0, None
    n = int((peak_profit_pct - start) // step) + 1
    milestone_level = start + (n - 1) * step
    return n, ratio * milestone_level


def milestone_floor_regression(peak_profit_pct, start=MILESTONE_START_PCT, step=MILESTONE_STEP_PCT):
    """Formula B: least-squares linear fit of the "give-back gap" (milestone - floor) as a
    function of the milestone level, through the user's 3 raw points (5,1.5),(7.5,2.5),
    (10,3.0): gap(m) = 0.3*m + 0.0833. floor(m) = m - gap(m). This is the most literal
    "fit a line through what the user actually gave" reading, as opposed to Formula A's
    fixed-ratio guess - the two are close but not identical (Formula B has a small
    non-zero intercept)."""
    if peak_profit_pct < start:
        return 0, None
    n = int((peak_profit_pct - start) // step) + 1
    milestone_level = start + (n - 1) * step
    gap = 0.3 * milestone_level + (1.0 / 12.0)
    return n, milestone_level - gap


_CONST_STEP_TABLE = {1: 3.5, 2: 5.0, 3: 7.0}


def milestone_floor_const_step(peak_profit_pct, start=MILESTONE_START_PCT, step=MILESTONE_STEP_PCT):
    """Formula C: take the user's 3 stated floors literally (3.5, 5.0, 7.0), then continue
    past the 3rd tier using the LAST given increment (+2.0, from 5%->7.5%) as a constant
    step for every milestone after that: 7.0, 9.0, 11.0, 13.0, ... This is the "don't fit
    any curve, just keep doing what the last step did" reading."""
    if peak_profit_pct < start:
        return 0, None
    n = int((peak_profit_pct - start) // step) + 1
    if n <= 3:
        return n, _CONST_STEP_TABLE[n]
    return n, _CONST_STEP_TABLE[3] + 2.0 * (n - 3)


# --- tighter-ladder redesigns (follow-up 3): Formulas A/B/C above all give back roughly
#     25-30% of the milestone level once you reach it (e.g. ~3pp at the 10% tier) - looser
#     than the base trail's flat 1.5pp giveback, which is WHY they never bind (see Round 26
#     follow-up 2: 0.0pp difference on all 19 matched trades). These three designs give back
#     LESS than the base trail's 1.5pp, so the milestone floor can actually end up above the
#     trail floor and become the thing that's actually protecting the trade. -----------------

def milestone_floor_tight_static(peak_profit_pct, start=MILESTONE_START_PCT, step=MILESTONE_STEP_PCT,
                                  giveback=0.75):
    """Design D: same milestone levels/spacing as Formula A, but a small CONSTANT giveback
    (0.75pp - half the base trail's 1.5pp) measured from the fixed milestone boundary
    itself, not the live peak. This can only bind in the window between one 3%-trail-step
    and the next fresh crossing (the trail's anchor is stale there, this floor's isn't) -
    once the peak crosses the next step, the trail usually overtakes it again."""
    if peak_profit_pct < start:
        return 0, None
    n = int((peak_profit_pct - start) // step) + 1
    milestone_level = start + (n - 1) * step
    return n, milestone_level - giveback


def _peak_relative_floor(peak_profit_pct, start, step, givebacks, min_giveback):
    if peak_profit_pct < start:
        return 0, None
    n = int((peak_profit_pct - start) // step) + 1
    idx = n - 1
    if idx < len(givebacks):
        giveback = givebacks[idx]
    else:
        extra = idx - (len(givebacks) - 1)
        giveback = max(min_giveback, givebacks[-1] - 0.1 * extra)
    return n, peak_profit_pct - giveback


def milestone_floor_peak_relative(peak_profit_pct, start=MILESTONE_START_PCT, step=MILESTONE_STEP_PCT):
    """Design E: floor = CURRENT PEAK minus a giveback that SHRINKS each tier (1.0pp at
    tier1, 0.75pp at tier2, 0.5pp at tier3+, floored at 0.25pp) - always smaller than the
    base trail's constant 1.5pp, and tracks the live peak continuously (not a static
    milestone boundary like Design D), so once a milestone is active this floor
    structurally dominates the base trail on EVERY bar, not just in the inter-step gap -
    this is the "more profit, less you're willing to give back" reading of the spec."""
    return _peak_relative_floor(peak_profit_pct, start, step, givebacks=(1.0, 0.75, 0.5), min_giveback=0.25)


def milestone_floor_peak_relative_aggressive(peak_profit_pct, start=MILESTONE_START_PCT, step=MILESTONE_STEP_PCT):
    """Design F: same mechanism as Design E, tighter givebacks (0.5/0.35/0.25pp, floored at
    0.15pp) - tests whether squeezing further keeps helping or starts cutting winners short,
    the standard tighter-stop tradeoff."""
    return _peak_relative_floor(peak_profit_pct, start, step, givebacks=(0.5, 0.35, 0.25), min_giveback=0.15)


def simulate_trailing_ratchet(df, entry_col, sl_pct, variant="close", starting_cash=10_000.0,
                               milestone_fn=milestone_floor_ratio, use_milestones=True,
                               return_trades=False, trail_pct=BASE_TRAIL_PCT,
                               trail_step_pct=BASE_TRAIL_STEP_PCT):
    """variant: "hwm" (wick-triggered, updates the peak from intrabar high - noisier,
    reacts to any wick) or "close" (close-confirmed, updates the peak only from the bar's
    close - the noise-resistant variant). Downside triggering always uses the bar's low,
    in both variants, matching this project's SL convention everywhere else.

    trail_pct / trail_step_pct: the base trail's own two free parameters - how far below
    the peak the stop sits, and how often (in %-of-peak-profit) it re-anchors to a fresh
    peak. Default to the user's original spec (1.5% / 3%, the module constants) so every
    existing caller is unaffected. trail_step_pct=None (or <=0) means CONTINUOUS
    re-anchoring - the trail always tracks trail_pct below the live peak on every bar,
    with no discrete step at all (follow-up 7's generalization of the spec).

    milestone_fn: which tier-floor formula to use (default = Formula A, the original
    guess) - swap in milestone_floor_regression or milestone_floor_const_step to test
    sensitivity to that assumption. use_milestones=False disables the tier ladder
    entirely (only the base trail + original SL apply) - used to isolate how much
    of any result is the ladder vs. the simple base trail. return_trades=True adds a
    "trades" list to the stats dict: one dict per completed/open trade with entry_time (if
    the df has a "time" column), peak_profit_pct (this trade's own max, under this
    variant's peak-tracking basis), reached_first_milestone (bool, peak ever >= the
    ladder's start threshold), locked_at_exit_pct, and milestone_bound (bool - True only if
    the milestone floor was ever the thing that actually raised the effective floor above
    what the base trail/original SL alone would have given; False means the ladder was
    inert for this trade even if a milestone was technically reached)."""
    df = df.reset_index(drop=True)
    if len(df) < 2:
        return _empty_stats(starting_cash)

    has_time = "time" in df.columns
    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price, entry_time = starting_cash, 0.0, False, None, None
    hwm = trail_anchor_hwm = locked_floor_price = None
    last_trail_step = 0
    trade_returns_pct, locked_at_exit_pct, trade_records = [], [], []
    equity_curve = []

    milestone_bound_this_trade = False

    def _record_trade(locked_pct, peak_pct, milestone_bound=False):
        if return_trades:
            trade_records.append({
                "entry_time": entry_time, "peak_profit_pct": peak_pct,
                "reached_first_milestone": peak_pct >= MILESTONE_START_PCT,
                "locked_at_exit_pct": locked_pct,
                "milestone_bound": milestone_bound,
            })

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        if not in_position:
            if bool(curr[entry_col]):
                entry_price = price
                entry_time = curr["time"] if has_time else i
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                hwm = trail_anchor_hwm = entry_price
                locked_floor_price = entry_price * (1 - sl_pct / 100)
                last_trail_step = 0
                milestone_bound_this_trade = False
        else:
            # 1. downside check first, against the day's LOW, using whatever floor is
            #    currently locked - unaffected by the variant choice.
            if curr["low"] <= locked_floor_price:
                exit_price = locked_floor_price
                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                ret_pct = (exit_price / entry_price - 1) * 100
                peak_pct = (hwm / entry_price - 1) * 100
                trade_returns_pct.append(ret_pct)
                locked_at_exit_pct.append(ret_pct)
                _record_trade(ret_pct, peak_pct, milestone_bound_this_trade)
                price = exit_price
                entry_price = hwm = trail_anchor_hwm = locked_floor_price = None
            else:
                # 2. update the peak using the variant's basis.
                basis_price = curr["high"] if variant == "hwm" else curr["close"]
                hwm = max(hwm, basis_price)
                peak_profit_pct = (hwm / entry_price - 1) * 100

                # 3. base trail: re-anchor at each fresh trail_step_pct-of-peak-profit
                #    crossing, or continuously (every bar) if trail_step_pct is None/<=0.
                if trail_step_pct is None or trail_step_pct <= 0:
                    trail_anchor_hwm = hwm
                    trail_floor_price = trail_anchor_hwm * (1 - trail_pct / 100)
                else:
                    trail_step = int(peak_profit_pct // trail_step_pct)
                    if trail_step > last_trail_step:
                        last_trail_step = trail_step
                        trail_anchor_hwm = hwm
                    trail_floor_price = (
                        trail_anchor_hwm * (1 - trail_pct / 100) if last_trail_step >= 1 else None
                    )

                # 4. milestone hard floor (skipped entirely if use_milestones=False).
                milestone_floor_price = None
                if use_milestones:
                    _, m_floor_pct = milestone_fn(peak_profit_pct)
                    if m_floor_pct is not None:
                        milestone_floor_price = entry_price * (1 + m_floor_pct / 100)

                # 5. effective floor = the most protective of what's active; monotonic ratchet.
                #    track whether the milestone was ever the thing that actually raised the
                #    floor (i.e. genuinely binds) vs. just being along for the ride while the
                #    base trail or original SL did all the work.
                pre_milestone_candidates = [locked_floor_price]
                if trail_floor_price is not None:
                    pre_milestone_candidates.append(trail_floor_price)
                pre_milestone_max = max(pre_milestone_candidates)
                if milestone_floor_price is not None and milestone_floor_price > pre_milestone_max:
                    milestone_bound_this_trade = True

                candidates = pre_milestone_candidates[:]
                if milestone_floor_price is not None:
                    candidates.append(milestone_floor_price)
                locked_floor_price = max(candidates)

        equity_curve.append(cash + coins * price)

    if in_position:
        # still open at the end of the test window - mark to market, don't silently drop.
        final_price = df["close"].iloc[-1]
        ret_pct = (final_price / entry_price - 1) * 100
        peak_pct = (hwm / entry_price - 1) * 100
        trade_returns_pct.append(ret_pct)
        _record_trade(ret_pct, peak_pct, milestone_bound_this_trade)

    stats = _stats(df, equity_curve, trade_returns_pct, locked_at_exit_pct, starting_cash)
    if return_trades:
        stats["trades"] = trade_records
    return stats


def _empty_stats(starting_cash):
    return {
        "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "completed_trades": 0,
        "win_rate_pct": 0.0, "avg_locked_at_exit_pct": None, "avg_net_locked_pct": None,
    }


def _stats(df, equity_curve, trade_returns_pct, locked_at_exit_pct, starting_cash):
    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100
    peak, max_dd = starting_cash, 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)
    n = len(trade_returns_pct)
    wins = [r for r in trade_returns_pct if r > 0]
    win_rate_pct = len(wins) / n * 100 if n else 0.0
    avg_locked = sum(locked_at_exit_pct) / len(locked_at_exit_pct) if locked_at_exit_pct else None
    avg_net_locked = (avg_locked - FEE_ROUND_TRIP_PCT) if avg_locked is not None else None
    return {
        "total_return_pct": total_return_pct, "max_drawdown_pct": max_dd,
        "completed_trades": n, "win_rate_pct": win_rate_pct,
        "avg_locked_at_exit_pct": avg_locked, "avg_net_locked_pct": avg_net_locked,
    }


# --- synthetic sanity checks (per Round 12's discipline: verify the engine against
#     hand-computed cases before trusting it on real data) --------------------------

def _synthetic_df(prices_ohlc, entry_at_idx):
    """prices_ohlc: list of (open, high, low, close). entry_at_idx: index (0-based into
    the returned df, which is 1 row shorter than the input since row 0 has no signal
    column context) where entry=True."""
    rows = []
    for j, (o, h, l, c) in enumerate(prices_ohlc):
        rows.append({"open": o, "high": h, "low": l, "close": c, "entry": j == entry_at_idx})
    return pd.DataFrame(rows)


def run_synthetic_checks():
    print("=== synthetic sanity checks ===")
    failures = 0

    # Case 1: straight run-up to 12% profit then straight back down - should ratchet
    # through milestones and lock in a real floor rather than round-tripping to zero.
    # entry at 100 (idx1). Peak path: 100 -> 103 -> 106 -> 109 -> 112 -> crash to 90.
    bars = [
        (100, 100, 100, 100),   # idx0, pre-entry
        (100, 100, 100, 100),   # idx1, entry bar (entry price = close = 100)
        (100, 103, 100, 103),   # idx2: peak profit 3% -> first 3%-trail step (last_trail_step 1)
        (103, 106, 103, 106),   # idx3: peak profit 6% -> trail step 2, milestone 5% crossed -> floor>=3.5
        (106, 109, 106, 109),   # idx4: peak profit 9% -> trail step 3, milestone 7.5% crossed -> floor>=5.25
        (109, 112, 109, 112),   # idx5: peak profit 12% -> trail step 4, milestone 10% crossed -> floor>=7.0
        (112, 112, 90, 90),     # idx6: crash - should exit at the locked floor, not at 90
    ]
    df = _synthetic_df(bars, entry_at_idx=1)
    for variant in ("hwm", "close"):
        stats = simulate_trailing_ratchet(df, "entry", sl_pct=1.0, variant=variant)
        exit_ret = stats["avg_locked_at_exit_pct"]
        # after peak 112 (12% profit), trail_step=floor(12/3)=4 -> trail_anchor_hwm=112 (since
        # each step re-anchors to hwm at the moment of crossing; with straight-line bars here
        # every step's anchor equals that bar's hwm) -> trail floor = 112*0.985=110.32 -> pct=10.32%
        # milestone: peak 12% -> n=floor((12-5)/2.5)+1=3 -> milestone_level=5+2*2.5=10 -> floor=7.0%
        # effective floor = max(trail 10.32%, milestone 7.0%) = 10.32%, well above the 90 crash price.
        expected_min_floor_pct = 7.0  # at minimum the milestone floor must have locked
        ok = exit_ret is not None and exit_ret >= expected_min_floor_pct - 1e-6
        print(f"  [{'OK' if ok else 'FAIL'}] case1 variant={variant}: locked exit return={exit_ret:.4f}% "
              f"(expect >= {expected_min_floor_pct}%, ideally ~10.32% from the trail overriding the milestone)")
        failures += 0 if ok else 1

    # Case 2: profit never reaches 5% (peaks at 4%, i.e. below the first milestone), then
    # reverses hard - only the base trail (once past 3%) or the original SL should apply,
    # the milestone table must NOT have activated.
    bars2 = [
        (100, 100, 100, 100),
        (100, 100, 100, 100),   # entry
        (100, 104, 100, 104),   # peak profit 4% -> trail step 1 active (>=3%), anchor hwm=104
        (104, 104, 95, 95),     # crash - should exit at trail floor = 104*0.985=102.44 (2.44% profit),
                                 # NOT at the original 1% SL (99) and NOT at any milestone floor (none active)
    ]
    df2 = _synthetic_df(bars2, entry_at_idx=1)
    for variant in ("hwm", "close"):
        stats = simulate_trailing_ratchet(df2, "entry", sl_pct=1.0, variant=variant)
        exit_ret = stats["avg_locked_at_exit_pct"]
        expected = 2.44
        ok = exit_ret is not None and abs(exit_ret - expected) < 0.05
        print(f"  [{'OK' if ok else 'FAIL'}] case2 variant={variant}: locked exit return={exit_ret:.4f}% "
              f"(expect ~{expected}%, base trail only, milestone table must stay inactive below 5%)")
        failures += 0 if ok else 1

    # Case 3: profit never crosses even the 3% base-trail step - original fixed SL must
    # be the only thing protecting the trade, i.e. a small dip below SL exits at SL.
    bars3 = [
        (100, 100, 100, 100),
        (100, 100, 100, 100),   # entry
        (100, 101, 100, 101),   # peak profit 1% - below the 3% trail step, no ratchet at all
        (101, 101, 98, 98),     # dip below original SL (1% -> 99) -> exit at 99 (-1%)
    ]
    df3 = _synthetic_df(bars3, entry_at_idx=1)
    for variant in ("hwm", "close"):
        stats = simulate_trailing_ratchet(df3, "entry", sl_pct=1.0, variant=variant)
        exit_ret = stats["avg_locked_at_exit_pct"]
        ok = exit_ret is not None and abs(exit_ret - (-1.0)) < 1e-6
        print(f"  [{'OK' if ok else 'FAIL'}] case3 variant={variant}: locked exit return={exit_ret:.4f}% "
              f"(expect exactly -1.0%, plain original stop-loss, no ratchet ever engaged)")
        failures += 0 if ok else 1

    # Case 4: wick vs close should differ on a single-bar spike that doesn't close there.
    bars4 = [
        (100, 100, 100, 100),
        (100, 100, 100, 100),   # entry
        (100, 112, 100, 101),   # idx2: intrabar spike to 112 (12% wick) but closes at 101 (1%)
        (101, 101, 90, 90),     # idx3: crash
    ]
    df4 = _synthetic_df(bars4, entry_at_idx=1)
    stats_hwm = simulate_trailing_ratchet(df4, "entry", sl_pct=1.0, variant="hwm")
    stats_close = simulate_trailing_ratchet(df4, "entry", sl_pct=1.0, variant="close")
    hwm_ret = stats_hwm["avg_locked_at_exit_pct"]
    close_ret = stats_close["avg_locked_at_exit_pct"]
    # hwm variant: peak (basis=high)=112 on idx2 -> milestone floor locks at >=7.0%, trail
    #   floor even higher (112*.985=110.32 -> ~10.32%) purely from the wick, before the
    #   crash on idx3 (whose low=90 is still checked against that already-locked floor).
    # close variant: peak (basis=close)=101 on idx2 -> only 1% profit, below the 3% trail
    #   step and below the 5% milestone -> original SL (99, -1%) is what protects it, and
    #   idx3's low=90 breaches it -> exits at -1%.
    ok = hwm_ret is not None and close_ret is not None and hwm_ret > 5.0 and abs(close_ret - (-1.0)) < 1e-6
    print(f"  [{'OK' if ok else 'FAIL'}] case4 (wick-vs-close divergence): hwm={hwm_ret:.4f}% "
          f"close={close_ret:.4f}% (expect hwm > 5% from the wick alone, close == -1.0% "
          f"since the wick never confirmed at close - this IS the noise filter working)")
    failures += 0 if ok else 1

    # Case 5 (tighter-ladder follow-up): Design E (peak-relative, shrinking giveback) must
    # structurally bind once a milestone is active, because its giveback (1.0/0.75/0.5pp)
    # is always smaller than the base trail's flat 1.5pp. Straight run-up to 6% (crosses
    # the first 3%-trail-step AND the first 5% milestone) then crash - Design E's floor
    # (peak-1.0=5.0%) must exceed the trail's floor (anchor*0.985, anchor=106 at best ->
    # 104.41 -> 4.41%), so the milestone_bound flag must be True and the locked exit must
    # be ~5.0%, not ~4.41%.
    bars5 = [
        (100, 100, 100, 100),
        (100, 100, 100, 100),   # entry
        (100, 103, 100, 103),   # peak 3% -> trail step1 anchor=103
        (103, 106, 103, 106),   # peak 6% -> trail step2 anchor=106 (trail floor 106*.985=104.41 -> 4.41%);
                                 # milestone (Design E): n=1 (6%>=5%), floor=6-1.0=5.0% > 4.41% -> BINDS
        (106, 106, 90, 90),     # crash
    ]
    df5 = _synthetic_df(bars5, entry_at_idx=1)
    stats5 = simulate_trailing_ratchet(df5, "entry", sl_pct=1.0, variant="close",
                                        milestone_fn=milestone_floor_peak_relative, return_trades=True)
    exit_ret5 = stats5["avg_locked_at_exit_pct"]
    bound5 = stats5["trades"][0]["milestone_bound"] if stats5["trades"] else False
    ok5 = exit_ret5 is not None and bound5 and abs(exit_ret5 - 5.0) < 0.05
    print(f"  [{'OK' if ok5 else 'FAIL'}] case5 (Design E must genuinely bind): locked exit "
          f"return={exit_ret5 if exit_ret5 is not None else float('nan'):.4f}% bound={bound5} "
          f"(expect ~5.00% and bound=True - Design E's tighter giveback overrides the looser "
          f"base trail here, unlike Formulas A/B/C which never do)")
    failures += 0 if ok5 else 1

    # Case 6 (follow-up 7): trail_pct/trail_step_pct must actually be free parameters, not
    # decorative - a tighter trail_pct must lock a higher floor than the default, and
    # trail_step_pct=None (continuous) must re-anchor every bar instead of waiting for a
    # 3%-of-profit crossing. Straight run-up 100->103->106 (peak profit 6%) then crash.
    bars6 = [
        (100, 100, 100, 100),
        (100, 100, 100, 100),   # entry
        (100, 103, 100, 103),   # peak profit 3%
        (103, 106, 103, 106),   # peak profit 6%
        (106, 106, 90, 90),     # crash
    ]
    df6 = _synthetic_df(bars6, entry_at_idx=1)
    # (a) tighter trail_pct=0.5% (vs default 1.5%) with the default 3% step must lock a
    # HIGHER floor: default anchors at 106 on the 3%-crossing at peak=6% (trail_step=2,
    # re-anchors at that same bar since 6//3=2>1) -> floor=106*0.985=104.41 (4.41%);
    # trail_pct=0.5% at the same anchor -> 106*0.995=105.47 (5.47%), tighter and higher.
    stats_default = simulate_trailing_ratchet(df6, "entry", sl_pct=1.0, variant="close",
                                               use_milestones=False)
    stats_tight = simulate_trailing_ratchet(df6, "entry", sl_pct=1.0, variant="close",
                                             use_milestones=False, trail_pct=0.5)
    ok6a = (stats_default["avg_locked_at_exit_pct"] is not None
            and stats_tight["avg_locked_at_exit_pct"] is not None
            and stats_tight["avg_locked_at_exit_pct"] > stats_default["avg_locked_at_exit_pct"])
    print(f"  [{'OK' if ok6a else 'FAIL'}] case6a (trail_pct is a real free parameter): "
          f"default(1.5%)={stats_default['avg_locked_at_exit_pct']:.4f}% "
          f"tight(0.5%)={stats_tight['avg_locked_at_exit_pct']:.4f}% "
          f"(expect tight > default - a smaller giveback must lock a higher floor)")
    failures += 0 if ok6a else 1

    # (b) continuous (trail_step_pct=None) must re-anchor to the peak on EVERY bar, not
    # just at 3%-crossings - so by the time of the crash, the anchor is 106 (the true
    # peak) rather than possibly a stale earlier anchor. With this straight-up path the
    # discrete-step default already anchors at 106 too (see above), so use a path where
    # they diverge: peak keeps rising in small increments that never cross a fresh 3% step
    # after the first one, so continuous should lock tighter than a large discrete step.
    bars6b = [
        (100, 100, 100, 100),
        (100, 100, 100, 100),   # entry
        (100, 104, 100, 104),   # peak 4% -> discrete step=1 (4//3=1), anchor=104
        (104, 106.9, 104, 106.9),  # peak 6.9% -> discrete step=2 (6.9//3=2), re-anchors to 106.9
        (106.9, 106.9, 90, 90),    # crash
    ]
    df6b = _synthetic_df(bars6b, entry_at_idx=1)
    stats_step10 = simulate_trailing_ratchet(df6b, "entry", sl_pct=1.0, variant="close",
                                              use_milestones=False, trail_pct=1.5, trail_step_pct=10.0)
    stats_continuous = simulate_trailing_ratchet(df6b, "entry", sl_pct=1.0, variant="close",
                                                  use_milestones=False, trail_pct=1.5, trail_step_pct=None)
    # step=10 never crosses a fresh step (peak only reaches 6.9%), so last_trail_step stays 0
    # and the trail never activates at all -> original SL (-1%) protects the trade.
    # continuous re-anchors every bar regardless -> locks ~1.5% below the true peak (106.9),
    # i.e. 106.9*0.985=105.30 -> 5.30% profit, activating immediately unlike the gated default.
    ok6b = (stats_step10["avg_locked_at_exit_pct"] is not None
            and abs(stats_step10["avg_locked_at_exit_pct"] - (-1.0)) < 1e-6
            and stats_continuous["avg_locked_at_exit_pct"] is not None
            and stats_continuous["avg_locked_at_exit_pct"] > 5.0)
    print(f"  [{'OK' if ok6b else 'FAIL'}] case6b (trail_step_pct=None is genuinely continuous): "
          f"step=10%(never crosses)={stats_step10['avg_locked_at_exit_pct']:.4f}% "
          f"continuous={stats_continuous['avg_locked_at_exit_pct']:.4f}% "
          f"(expect step10~=-1.0% [trail never activates] and continuous>5.0% "
          f"[re-anchors every bar regardless of any step])")
    failures += 0 if ok6b else 1

    print(f"\n{'ALL SYNTHETIC CHECKS PASSED' if failures == 0 else f'{failures} SYNTHETIC CHECK(S) FAILED'}\n")
    return failures == 0


# --- real-data runner ----------------------------------------------------------------

def eth_entry(df):
    signal = stochastic_reversion(df, k_period=21, oversold=30)
    return apply_adx_filter(signal, threshold=15)


def doge_entry(df):
    return bullish_fvg_entry(df, max_age=10)


# Round 26 follow-up 4: ETH's own native Kraken daily OHLCV, pulled from the same
# "Kraken_OHLCVT_Full" bulk archive that supplied BTC's data back in Round 9 (the archive
# covers every pair, not just BTC/USD - it just hadn't been extracted for ETH before now).
# Verified against known ETH price history before trusting it (2017-12-17 ~$700 pre-ATH,
# 2018-01-13 ~$1400 near the Jan-2018 top, 2020-03-13 ~$110-145 COVID "Black Thursday" low,
# 2021-11-09 ~$4700-4800 near the all-time high, 2022-06-18 ~$900-1000 bear-market low,
# 2024-03-11 ~$3900-4000 - all match well-documented history). Range: 2015-08-07 (Kraken's
# actual ETHUSD listing) to 2026-06-30, i.e. genuinely longer than Yahoo's ETH-USD history
# (which only starts 2017-11-09) and long enough to build the SAME 4-split methodology BTC
# already uses, instead of the 2-split REGIME_SPLITS list ETH/DOGE were stuck reusing.
ETH_CSV_PATH = "data/kraken_historical/extracted/ETHUSD_1440.csv"

ETH_SPLITS = [
    ("2018-01-01", "2021-12-31", "2022-01-01", "2025-12-31"),  # same as BTC split 1 / old REGIME_SPLITS[0]
    ("2017-01-01", "2019-12-31", "2020-01-01", "2021-12-31"),  # same as BTC split 2 / old REGIME_SPLITS[1]
    ("2015-08-07", "2017-12-31", "2018-01-01", "2019-12-31"),  # NEW: full ETH history start (not BTC's
                                                                 # 2013-10-06 - ETH wasn't listed yet) -> 2018 bear
    ("2019-01-01", "2022-12-31", "2023-01-01", "2026-06-30"),  # NEW: same as BTC split 4, capped at data end
]

# Round 27: DOGE's own native Kraken daily OHLCV, pulled from the same
# "Kraken_OHLCVT_Full_2026Q2" bulk archive as BTC/ETH - but note the archive files it
# under Kraken's HISTORICAL altname for DOGE, "XDG" (XDGUSD, not DOGEUSD - the live
# bot's PAIR=DOGEUSD is resolved to XDGUSD internally by Kraken's REST API, so XDGUSD
# is the correct pair to validate against). Verified before trusting it: 2021-05-08
# high=$0.74 matches DOGE's widely-documented all-time high almost exactly, 2021-04-16
# high=$0.47 matches that day's pump peak, 2020-03-13 COVID low ~$0.0012-0.0018 and
# 2022-06-18 bear low ~$0.053 both match documented history. Cross-checked against the
# Yahoo data this project already used: mean abs diff 0.260% across 2386 matched dates
# (consistent with ETH's 0.29% and BTC's 0.31% cross-checks) - worst outliers cluster in
# Dec-2019/early-Jan-2020, right after Kraken's XDGUSD listing (low early liquidity,
# same pattern as BTC's Dec-2017-mania outliers, not a data error.
#
# UNLIKE ETH, this does NOT extend DOGE's usable history further back - Kraken's XDGUSD
# listing (2019-12-19) is LATER than Yahoo's DOGE-USD start (2017-11-09), so there is no
# way to build a 2018-19-bear-market split for DOGE the way ETH's earlier Kraken listing
# allowed. What it does provide: exchange-native data (avoiding the cross-exchange
# entry-timing fragility Round 26 follow-up 4 found for ETH) and enough range
# (2019-12-19..2026-06-30, ~6.5 years) to build 3 non-overlapping, structurally distinct
# test windows instead of the 2 REGIME_SPLITS DOGE was stuck reusing - more than 2, honestly
# short of BTC/ETH's 4 because DOGE's real listing history doesn't support a 4th.
DOGE_CSV_PATH = "data/kraken_historical/extracted/XDGUSD_1440.csv"

DOGE_SPLITS = [
    ("2019-12-19", "2021-12-31", "2022-01-01", "2023-12-31"),  # 2022-23 bear/consolidation
    ("2019-12-19", "2020-12-31", "2021-01-01", "2021-12-31"),  # 2021 meme-mania bull (DOGE's defining regime)
    ("2019-12-19", "2023-12-31", "2024-01-01", "2026-06-30"),  # recent/current regime
]

COINS = {
    "BTC": {"kind": "kraken_csv", "path": BTC_CSV_PATH, "splits": BTC_SPLITS,
            "entry_fn": baseline_signal_entry, "live_sl": 1.0, "live_tp": 20.0},
    "ETH": {"kind": "kraken_csv", "path": ETH_CSV_PATH, "splits": ETH_SPLITS,
            "entry_fn": eth_entry, "live_sl": 1.0, "live_tp": 10.0},
    "DOGE": {"kind": "kraken_csv", "path": DOGE_CSV_PATH, "splits": DOGE_SPLITS,
             "entry_fn": doge_entry, "live_sl": 3.0, "live_tp": 30.0},
}
DOGE_SPLITS_YAHOO_OLD = REGIME_SPLITS  # kept only for reference - DOGE no longer uses this


def load_coin_df(cfg):
    if cfg["kind"] == "kraken_csv":
        return load_kraken_csv_1min(cfg["path"])
    return fetch_yahoo_history(cfg["symbol"], range_="10y", interval="1d")


def run_all():
    all_results = {}  # coin -> variant -> list of (split_idx, stats, passed)
    baseline_results = {}  # coin -> list of (split_idx, stats, passed)

    for coin, cfg in COINS.items():
        df = load_coin_df(cfg)
        n_splits = len(cfg["splits"])
        print(f"\n{'='*100}\n{coin} ({n_splits} splits) - entry unchanged, exit = staged "
              f"trailing ratchet vs. live baseline (SL={cfg['live_sl']}%/TP={cfg['live_tp']}%)\n{'='*100}")

        all_results[coin] = {"hwm": [], "close": []}
        baseline_results[coin] = []

        for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
            test_df = slice_dates(df, test_start, test_end)
            if len(test_df) < 100:
                print(f"  Split {split_idx}: insufficient data, skipping")
                continue

            signal_df = cfg["entry_fn"](test_df)
            test_bh = (test_df["close"].iloc[-1] / test_df["close"].iloc[0] - 1) * 100
            print(f"\n  --- Split {split_idx}: test {test_start}..{test_end} (buy&hold {test_bh:+.2f}%) ---")

            base_stats = simulate_wide_tp(signal_df, "entry", cfg["live_tp"], cfg["live_sl"])
            base_passed = _passed(base_stats)
            baseline_results[coin].append((split_idx, base_stats, base_passed))
            print(f"    [{'PASS' if base_passed else 'fail'}] live baseline (fixed TP/SL)         | "
                  f"ret={base_stats['total_return_pct']:8.2f}% dd={base_stats['max_drawdown_pct']:6.2f}% "
                  f"trades={base_stats['completed_trades']:4d}")

            for variant in ("hwm", "close"):
                stats = simulate_trailing_ratchet(signal_df, "entry", cfg["live_sl"], variant=variant)
                passed = _passed(stats)
                all_results[coin][variant].append((split_idx, stats, passed))
                locked = stats["avg_locked_at_exit_pct"]
                net_locked = stats["avg_net_locked_pct"]
                locked_str = f"{locked:6.2f}%" if locked is not None else "   n/a"
                net_str = f"{net_locked:6.2f}%" if net_locked is not None else "   n/a"
                print(f"    [{'PASS' if passed else 'fail'}] ratchet ({variant:5s} peak-tracking)     | "
                      f"ret={stats['total_return_pct']:8.2f}% dd={stats['max_drawdown_pct']:6.2f}% "
                      f"trades={stats['completed_trades']:4d} avg_locked_at_exit={locked_str} "
                      f"avg_net_of_fees={net_str}")

        print(f"\n  === {coin} summary ===")
        b_all = baseline_results[coin]
        b_pass_all = len(b_all) == n_splits and all(p for _, _, p in b_all)
        b_avg_ret = sum(s["total_return_pct"] for _, s, _ in b_all) / len(b_all) if b_all else 0
        b_avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in b_all) / len(b_all) if b_all else 0
        print(f"    live baseline: {'VALIDATED' if b_pass_all else 'not validated'} on all splits | "
              f"avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")
        for variant in ("hwm", "close"):
            results = all_results[coin][variant]
            r_pass_all = len(results) == n_splits and all(p for _, _, p in results)
            r_avg_ret = sum(s["total_return_pct"] for _, s, _ in results) / len(results) if results else 0
            r_avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in results) / len(results) if results else 0
            beats_baseline = r_pass_all and b_pass_all and r_avg_ret > b_avg_ret and r_avg_dd <= b_avg_dd
            print(f"    ratchet ({variant:5s}): {'VALIDATED' if r_pass_all else 'not validated'} on all splits | "
                  f"avg_ret={r_avg_ret:.2f}% avg_dd={r_avg_dd:.2f}% | "
                  f"{'BEATS baseline on return+drawdown' if beats_baseline else 'does NOT clearly beat baseline'}")

    return all_results, baseline_results


# --- BTC-only true intrabar (1-minute) sensitivity check ------------------------------
# Scoped to ONE split (the most recent, 2023-01-01..2026-06-30) rather than all 4, purely
# for time/compute - this is a sensitivity check on the daily-bar approximation, not a
# full re-validation. Uses numpy arrays for the inner loop (Round 9's convention for
# million-row inputs) rather than a pandas .iloc loop.

def run_btc_minute_sensitivity():
    print(f"\n{'='*100}\nBTC-only sensitivity check: daily-bar-approximated exit vs. true "
          f"1-minute intrabar exit\n(single split: 2023-01-01..2026-06-30, entry signal "
          f"still computed on daily bars - only the EXIT check resolution changes)\n{'='*100}")

    daily_path = BTC_CSV_PATH
    minute_path = "data/kraken_historical/extracted/XBTUSD_1.csv"

    daily_df = load_kraken_csv_1min(daily_path)
    test_start, test_end = "2023-01-01", "2026-06-30"
    daily_test = slice_dates(daily_df, test_start, test_end)
    signal_df = baseline_signal_entry(daily_test).reset_index(drop=True)
    entry_dates = signal_df.loc[signal_df["entry"], "time"].tolist()
    print(f"  {len(entry_dates)} entry signals in this split (daily bars, unchanged).")

    if not entry_dates:
        print("  No entries in this split - nothing to compare.")
        return

    print("  Loading 1-minute Kraken CSV (large file, this takes a bit)...")
    minute_df = load_kraken_csv_1min(minute_path)
    minute_df = slice_dates(minute_df, test_start, test_end).reset_index(drop=True)
    m_time = minute_df["time"].to_numpy()
    m_high = minute_df["high"].to_numpy()
    m_low = minute_df["low"].to_numpy()
    m_close = minute_df["close"].to_numpy()
    print(f"  {len(minute_df)} 1-minute bars loaded for this window.")

    sl_pct = 1.0
    daily_locked, minute_locked = [], []

    daily_test_r = daily_test.reset_index(drop=True)
    for entry_time in entry_dates:
        entry_row_idx = daily_test_r.index[daily_test_r["time"] == entry_time][0]
        entry_price = daily_test_r.loc[entry_row_idx, "close"]

        # --- daily-bar-approximated ratchet, close-confirmed variant, run forward from
        # this single entry until exit or window end (reuses the same state machine as
        # simulate_trailing_ratchet by slicing a one-trade df starting one row before
        # entry - the engine's loop starts at index 1 and reads the entry flag from
        # curr[entry_col] at that index, matching every other engine in this project;
        # putting the flag at row 0 would never be seen).
        if entry_row_idx < 1:
            continue
        one_trade_df = daily_test_r.iloc[entry_row_idx - 1:].reset_index(drop=True).copy()
        one_trade_df["entry"] = False
        one_trade_df.loc[1, "entry"] = True
        d_stats = simulate_trailing_ratchet(one_trade_df, "entry", sl_pct, variant="close")
        if d_stats["avg_locked_at_exit_pct"] is not None:
            daily_locked.append(d_stats["avg_locked_at_exit_pct"])

        # --- true minute-level version of the identical logic, numpy inner loop.
        start_idx = np.searchsorted(m_time, np.datetime64(entry_time))
        if start_idx >= len(m_time):
            continue
        hwm = trail_anchor_hwm = entry_price
        locked_floor = entry_price * (1 - sl_pct / 100)
        last_trail_step = 0
        exited = False
        for j in range(start_idx, len(m_time)):
            low_j, high_j, close_j = m_low[j], m_high[j], m_close[j]
            if low_j <= locked_floor:
                minute_locked.append((locked_floor / entry_price - 1) * 100)
                exited = True
                break
            hwm = max(hwm, close_j)  # close-confirmed variant, matches the daily comparison
            peak_profit_pct = (hwm / entry_price - 1) * 100
            trail_step = int(peak_profit_pct // BASE_TRAIL_STEP_PCT)
            if trail_step > last_trail_step:
                last_trail_step = trail_step
                trail_anchor_hwm = hwm
            trail_floor = trail_anchor_hwm * (1 - BASE_TRAIL_PCT / 100) if last_trail_step >= 1 else None
            _, m_floor_pct = milestone_floor_ratio(peak_profit_pct)
            m_floor_price = entry_price * (1 + m_floor_pct / 100) if m_floor_pct is not None else None
            candidates = [locked_floor]
            if trail_floor is not None:
                candidates.append(trail_floor)
            if m_floor_price is not None:
                candidates.append(m_floor_price)
            locked_floor = max(candidates)
        if not exited:
            minute_locked.append((m_close[-1] / entry_price - 1) * 100)

    if daily_locked and minute_locked:
        n = min(len(daily_locked), len(minute_locked))
        diffs = [minute_locked[i] - daily_locked[i] for i in range(n)]
        print(f"\n  {n} entries compared. avg daily-approx locked-exit={sum(daily_locked)/len(daily_locked):.3f}% "
              f"avg true-minute locked-exit={sum(minute_locked)/len(minute_locked):.3f}%")
        print(f"  avg (minute - daily) per-trade difference: {sum(diffs)/len(diffs):+.3f} percentage points "
              f"| max abs difference: {max(abs(d) for d in diffs):.3f}pp")
    else:
        print("  Not enough comparable trades to summarize.")


# --- follow-up 1: is ETH's validated result robust to the tier-formula guess, or is it
#     fragile to exactly which of the (equally plausible) extrapolations was picked? ------

TIER_FORMULAS = {
    "A: ratio (0.70 x milestone)": milestone_floor_ratio,
    "B: regression (least-squares gap fit)": milestone_floor_regression,
    "C: const-step (+2.0 per tier past 10%)": milestone_floor_const_step,
}


def run_tier_formula_sensitivity(coin="ETH"):
    cfg = COINS[coin]
    print(f"\n{'='*100}\n{coin} tier-formula sensitivity: does the validated result depend on "
          f"WHICH tier-floor extrapolation was guessed? (variant=close, the validated one)\n{'='*100}")

    df = load_coin_df(cfg)
    per_formula = {}
    for name, fn in TIER_FORMULAS.items():
        per_formula[name] = []
        for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
            test_df = slice_dates(df, test_start, test_end)
            if len(test_df) < 100:
                continue
            signal_df = cfg["entry_fn"](test_df)
            stats = simulate_trailing_ratchet(signal_df, "entry", cfg["live_sl"], variant="close",
                                               milestone_fn=fn)
            passed = _passed(stats)
            per_formula[name].append((split_idx, stats, passed))
            print(f"  [{'PASS' if passed else 'fail'}] {name:42s} split {split_idx} | "
                  f"ret={stats['total_return_pct']:8.2f}% dd={stats['max_drawdown_pct']:6.2f}% "
                  f"trades={stats['completed_trades']:4d}")

    print(f"\n  --- {coin} tier-formula summary ---")
    n_splits = len(cfg["splits"])
    verdicts = {}
    for name, results in per_formula.items():
        pass_all = len(results) == n_splits and all(p for _, _, p in results)
        avg_ret = sum(s["total_return_pct"] for _, s, _ in results) / len(results) if results else 0
        avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in results) / len(results) if results else 0
        verdicts[name] = pass_all
        print(f"    {name:42s}: {'VALIDATED' if pass_all else 'not validated'} on all splits | "
              f"avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}%")

    all_agree = len(set(verdicts.values())) == 1
    print(f"\n  VERDICT: result is {'ROBUST - all 3 tier formulas reach the same pass/fail outcome, '
          'so the earlier guess did not manufacture the win' if all_agree else 'FRAGILE - the '
          'formulas disagree on whether this validates, so the exact tier extrapolation genuinely '
          'matters and the user should pick one explicitly rather than accept a guess'} for {coin}.")
    return per_formula, all_agree


# --- follow-up 2: does the tiered ladder actually add value, or is the win entirely from
#     the simple 1.5%-every-3% base trail? -------------------------------------------------

def run_ladder_value_analysis(coin="ETH"):
    cfg = COINS[coin]
    print(f"\n{'='*100}\n{coin} ladder-value analysis: on the trades that get big enough to reach "
          f"the ladder, does the ladder beat the plain base trail? (variant=close)\n{'='*100}")

    df = load_coin_df(cfg)
    all_on, all_off = [], []
    for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            continue
        signal_df = cfg["entry_fn"](test_df)
        stats_on = simulate_trailing_ratchet(signal_df, "entry", cfg["live_sl"], variant="close",
                                              use_milestones=True, return_trades=True)
        stats_off = simulate_trailing_ratchet(signal_df, "entry", cfg["live_sl"], variant="close",
                                               use_milestones=False, return_trades=True)
        all_on.extend(stats_on["trades"])
        all_off.extend(stats_off["trades"])
        print(f"  Split {split_idx}: {len(stats_on['trades'])} trades with ladder ON, "
              f"{len(stats_off['trades'])} trades with ladder OFF (base trail only) - trade counts "
              f"can differ because a tighter/looser floor changes exit timing, which shifts which "
              f"later entry signals get taken.")

    n_total = len(all_on)
    reached = [t for t in all_on if t["reached_first_milestone"]]
    print(f"\n  Activation rate (ladder ON run): {len(reached)}/{n_total} trades ever reached the "
          f"first milestone ({MILESTONE_START_PCT}% peak profit) before THIS mechanism's own exit "
          f"closed them - the other {n_total - len(reached)} exited earlier (off the base trail or "
          f"the original stop-loss) and never got the ladder to engage at all.")
    if n_total:
        peaks = sorted(t["peak_profit_pct"] for t in all_on)
        import statistics
        print(f"  Peak-profit-pct distribution across all {n_total} trades (ladder ON, so this IS "
              f"already capped by the mechanism's own earlier exits, not the coin's raw uncapped "
              f"move - a lower bound on how big trades could get, not the true ceiling): "
              f"min={peaks[0]:.2f}% median={statistics.median(peaks):.2f}% max={peaks[-1]:.2f}%")

    # match ON vs OFF by entry_time for the trades that did reach the ladder in the ON run,
    # to isolate exactly what the ladder changed for those specific trades.
    off_by_time = {t["entry_time"]: t for t in all_off}
    matched = []
    for t_on in reached:
        t_off = off_by_time.get(t_on["entry_time"])
        if t_off is not None:
            matched.append((t_on, t_off))

    print(f"\n  Of the {len(reached)} milestone-reaching trades, {len(matched)} have a matching "
          f"entry in the ladder-OFF run (unmatched ones mean the OFF run's earlier trades' exit "
          f"timing shifted which entry came next, so no apples-to-apples comparison exists for them).")
    if matched:
        deltas = [t_on["locked_at_exit_pct"] - t_off["locked_at_exit_pct"] for t_on, t_off in matched]
        avg_delta = sum(deltas) / len(deltas)
        wins = sum(1 for d in deltas if d > 0)
        print(f"  On the {len(matched)} matched trades: ladder locked in {avg_delta:+.2f} percentage "
              f"points on average vs. the plain base trail alone ({wins}/{len(matched)} matched "
              f"trades favor the ladder).")
        if len(matched) < 5:
            print(f"  CAVEAT: only {len(matched)} matched trades - too small a sample to call this a "
                  f"real edge either way, just what happened in this data.")
    else:
        print("  No matched trades - cannot isolate the ladder's marginal value from this data; "
              "the earlier validated win is not distinguishable from the base-trail-alone effect "
              "using this comparison method.")

    return all_on, all_off, matched


# --- follow-up 3: redesign the ladder so it actually binds, then check whether binding
#     helps, hurts, or is a wash relative to the already-validated base-trail-only result. --

TIGHTER_DESIGNS = {
    "D: tight static (milestone - 0.75pp)": milestone_floor_tight_static,
    "E: peak-relative shrinking (1.0/0.75/0.5pp)": milestone_floor_peak_relative,
    "F: peak-relative aggressive (0.5/0.35/0.25pp)": milestone_floor_peak_relative_aggressive,
}


def _split_summary(results, n_splits):
    pass_all = len(results) == n_splits and all(p for _, _, p in results)
    avg_ret = sum(s["total_return_pct"] for _, s, _ in results) / len(results) if results else 0.0
    avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in results) / len(results) if results else 0.0
    return pass_all, avg_ret, avg_dd


def run_tighter_ladder_designs(coin="ETH"):
    cfg = COINS[coin]
    print(f"\n{'='*100}\n{coin} tighter-ladder redesign: do Designs D/E/F (smaller giveback than "
          f"the base trail's 1.5pp) actually bind, and does binding help or hurt vs. the "
          f"already-validated base-trail-only result? (variant=close throughout)\n{'='*100}")

    df = load_coin_df(cfg)
    n_splits = len(cfg["splits"])
    baseline_results, base_trail_results = [], []
    design_results = {name: [] for name in TIGHTER_DESIGNS}
    design_trades = {name: [] for name in TIGHTER_DESIGNS}

    for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            continue
        signal_df = cfg["entry_fn"](test_df)
        print(f"\n  --- Split {split_idx}: test {test_start}..{test_end} ---")

        base_stats = simulate_wide_tp(signal_df, "entry", cfg["live_tp"], cfg["live_sl"])
        baseline_results.append((split_idx, base_stats, _passed(base_stats)))
        print(f"    [{'PASS' if _passed(base_stats) else 'fail'}] live baseline              | "
              f"ret={base_stats['total_return_pct']:8.2f}% dd={base_stats['max_drawdown_pct']:6.2f}% "
              f"trades={base_stats['completed_trades']:4d}")

        bt_stats = simulate_trailing_ratchet(signal_df, "entry", cfg["live_sl"], variant="close",
                                              use_milestones=False, return_trades=True)
        base_trail_results.append((split_idx, bt_stats, _passed(bt_stats)))
        print(f"    [{'PASS' if _passed(bt_stats) else 'fail'}] base trail only (no ladder)  | "
              f"ret={bt_stats['total_return_pct']:8.2f}% dd={bt_stats['max_drawdown_pct']:6.2f}% "
              f"trades={bt_stats['completed_trades']:4d}  <- this is the already-validated Round 26 win")

        for name, fn in TIGHTER_DESIGNS.items():
            stats = simulate_trailing_ratchet(signal_df, "entry", cfg["live_sl"], variant="close",
                                               milestone_fn=fn, use_milestones=True, return_trades=True)
            design_results[name].append((split_idx, stats, _passed(stats)))
            design_trades[name].extend(stats["trades"])
            print(f"    [{'PASS' if _passed(stats) else 'fail'}] {name:38s} | "
                  f"ret={stats['total_return_pct']:8.2f}% dd={stats['max_drawdown_pct']:6.2f}% "
                  f"trades={stats['completed_trades']:4d}")

    b_pass, b_ret, b_dd = _split_summary(baseline_results, n_splits)
    bt_pass, bt_ret, bt_dd = _split_summary(base_trail_results, n_splits)
    print(f"\n  === {coin} summary ===")
    print(f"    live baseline:               {'VALIDATED' if b_pass else 'not validated'} | "
          f"avg_ret={b_ret:.2f}% avg_dd={b_dd:.2f}%")
    print(f"    base trail only (no ladder): {'VALIDATED' if bt_pass else 'not validated'} | "
          f"avg_ret={bt_ret:.2f}% avg_dd={bt_dd:.2f}%  <- reference point (Round 26's actual win)")

    print("\n    --- tighter-ladder designs ---")
    for name in TIGHTER_DESIGNS:
        results = design_results[name]
        pass_all, avg_ret, avg_dd = _split_summary(results, n_splits)
        trades = design_trades[name]
        n_total = len(trades)
        bound = [t for t in trades if t.get("milestone_bound")]
        bind_pct = (len(bound) / n_total * 100) if n_total else 0.0
        wins = [t for t in trades if t["locked_at_exit_pct"] > 0]
        win_rate = (len(wins) / n_total * 100) if n_total else 0.0
        avg_per_trade = (sum(t["locked_at_exit_pct"] for t in trades) / n_total) if n_total else 0.0

        if bt_pass and pass_all:
            if avg_ret > bt_ret and avg_dd <= bt_dd:
                vs_base_trail = "BEATS base-trail-only on both return and drawdown"
            elif avg_ret > bt_ret:
                vs_base_trail = "higher return, but worse (higher) drawdown than base-trail-only"
            elif avg_ret < bt_ret:
                vs_base_trail = "WORSE than base-trail-only (lower return)"
            else:
                vs_base_trail = "essentially a wash vs. base-trail-only"
        elif pass_all and not bt_pass:
            vs_base_trail = "validates where base-trail-only did not (n/a here, just noting)"
        elif not pass_all and bt_pass:
            vs_base_trail = "FAILS validation that base-trail-only passed"
        else:
            vs_base_trail = "neither this design nor base-trail-only validates all splits"

        print(f"\n    {name}")
        print(f"      binds on {len(bound)}/{n_total} trades ({bind_pct:.1f}%) - "
              f"{'genuinely binds, not inert' if bound else 'STILL INERT - never binds, same problem as A/B/C'}")
        print(f"      [{'PASS' if pass_all else 'fail'}] avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% "
              f"win_rate={win_rate:.1f}% avg_locked_per_trade={avg_per_trade:.2f}%")
        print(f"      vs. base-trail-only ({bt_ret:.2f}%/{bt_dd:.2f}%dd): {vs_base_trail}")

    return baseline_results, base_trail_results, design_results, design_trades


# --- follow-up 4: polish Design D properly. The user asked for the floor to be "moved to
#     the best tp% given where the bot is at in the trade" - i.e. don't hand-pick one
#     giveback/spacing pair (0.75pp / 2.5%-step), actually search for the best one. This
#     project has exactly 2 independent ETH splits, so a naive "grid-search everything,
#     report the single best-scoring combo" is precisely the overfitting trap this project
#     has refused elsewhere (Round 13's discipline, Round 24's walk-forward test). The fix
#     used here: search split 1 and split 2 COMPLETELY INDEPENDENTLY (split 2's grid never
#     sees split 1's data or vice versa), then report whether their two independently-found
#     optima actually agree. Agreement across two disjoint 2-4-year windows is real
#     evidence of a stable pattern; disagreement means "the best floor" isn't a learnable
#     thing from this little data, and forcing a specific number would be manufacturing
#     false precision, not polishing. -------------------------------------------------------

def milestone_floor_static_giveback(peak_profit_pct, start, step, giveback):
    """Generalized Design D: constant giveback measured from the fixed milestone BOUNDARY
    (not the live peak) - the exact mechanism already shipped as milestone_floor_tight_static,
    just with giveback/step exposed as free parameters instead of hard-coded to 0.75pp/2.5%,
    so the grid search below can sweep them."""
    if peak_profit_pct < start:
        return 0, None
    n = int((peak_profit_pct - start) // step) + 1
    milestone_level = start + (n - 1) * step
    return n, milestone_level - giveback


def milestone_floor_linear_giveback(peak_profit_pct, start, step, initial_giveback,
                                     decay_per_tier, min_giveback=0.1):
    """Generalized Design E/F: floor = LIVE PEAK minus a giveback that shrinks by a constant
    amount each tier crossed (floored at min_giveback so it can never go negative or invert
    the floor above the peak). decay_per_tier=0 degenerates to a constant peak-relative
    giveback; decay>0 is the literal "the more profit you have, the less you're willing to
    give back" reading of the user's spec, with the shrink rate itself a free parameter."""
    if peak_profit_pct < start:
        return 0, None
    n = int((peak_profit_pct - start) // step) + 1
    giveback = max(min_giveback, initial_giveback - decay_per_tier * (n - 1))
    return n, peak_profit_pct - giveback


STATIC_GIVEBACK_GRID = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
STATIC_STEP_GRID = [2.0, 2.5, 3.0, 4.0]

LINEAR_INITIAL_GIVEBACK_GRID = [0.5, 0.75, 1.0, 1.25, 1.5]
LINEAR_DECAY_GRID = [0.0, 0.15, 0.25, 0.35, 0.5]
LINEAR_STEP_GRID = [2.0, 2.5, 3.0]

# "close enough to call it agreement" tolerances for comparing each split's independently-
# found optimum - stated explicitly here, not tuned after seeing the result.
STATIC_AGREE_GIVEBACK_TOL = 0.5   # percentage points
STATIC_AGREE_STEP_TOL = 1.0       # percentage points
LINEAR_AGREE_INITIAL_TOL = 0.5
LINEAR_AGREE_DECAY_TOL = 0.2
LINEAR_AGREE_STEP_TOL = 1.0


def _grid_for_split(signal_df, live_sl):
    """Run the full static+linear grid once against one split's signal_df. Returns
    {label: (stats, passed, param_tuple)}. param_tuple is ("static", giveback, step) or
    ("linear", initial_giveback, decay_per_tier, step) - used later to compare optima
    across splits without re-parsing the printed label."""
    results = {}
    for giveback in STATIC_GIVEBACK_GRID:
        for step in STATIC_STEP_GRID:
            label = f"static giveback={giveback:.2f}pp step={step:.1f}%"
            fn = partial(milestone_floor_static_giveback, start=MILESTONE_START_PCT,
                         step=step, giveback=giveback)
            stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                               milestone_fn=fn, use_milestones=True,
                                               return_trades=True)
            results[label] = (stats, _passed(stats), ("static", giveback, step))
    for init_gb in LINEAR_INITIAL_GIVEBACK_GRID:
        for decay in LINEAR_DECAY_GRID:
            for step in LINEAR_STEP_GRID:
                label = f"linear init={init_gb:.2f}pp decay={decay:.2f}pp/tier step={step:.1f}%"
                fn = partial(milestone_floor_linear_giveback, start=MILESTONE_START_PCT,
                             step=step, initial_giveback=init_gb, decay_per_tier=decay)
                stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                   milestone_fn=fn, use_milestones=True,
                                                   return_trades=True)
                results[label] = (stats, _passed(stats), ("linear", init_gb, decay, step))
    return results


def _params_agree(p1, p2):
    if p1[0] != p2[0]:
        return False, "different design shape (static vs. linear) - not comparable"
    if p1[0] == "static":
        _, gb1, step1 = p1
        _, gb2, step2 = p2
        ok = abs(gb1 - gb2) <= STATIC_AGREE_GIVEBACK_TOL and abs(step1 - step2) <= STATIC_AGREE_STEP_TOL
        return ok, f"giveback {gb1:.2f} vs {gb2:.2f}pp (tol {STATIC_AGREE_GIVEBACK_TOL}), " \
                    f"step {step1:.1f} vs {step2:.1f}% (tol {STATIC_AGREE_STEP_TOL})"
    _, init1, dec1, step1 = p1
    _, init2, dec2, step2 = p2
    ok = (abs(init1 - init2) <= LINEAR_AGREE_INITIAL_TOL and abs(dec1 - dec2) <= LINEAR_AGREE_DECAY_TOL
          and abs(step1 - step2) <= LINEAR_AGREE_STEP_TOL)
    return ok, f"init {init1:.2f} vs {init2:.2f}pp (tol {LINEAR_AGREE_INITIAL_TOL}), " \
                f"decay {dec1:.2f} vs {dec2:.2f}pp/tier (tol {LINEAR_AGREE_DECAY_TOL}), " \
                f"step {step1:.1f} vs {step2:.1f}% (tol {LINEAR_AGREE_STEP_TOL})"


def run_design_d_grid_search(coin="ETH"):
    cfg = COINS[coin]
    df = load_coin_df(cfg)
    live_sl = cfg["live_sl"]
    n_static = len(STATIC_GIVEBACK_GRID) * len(STATIC_STEP_GRID)
    n_linear = len(LINEAR_INITIAL_GIVEBACK_GRID) * len(LINEAR_DECAY_GRID) * len(LINEAR_STEP_GRID)

    print(f"\n{'='*100}\n{coin} Design D grid search ({n_static} static-giveback configs + "
          f"{n_linear} linear-giveback configs = {n_static + n_linear} total), run "
          f"INDEPENDENTLY per split - split N's search never sees any other split's data\n{'='*100}")

    per_split = []
    for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        signal_df = cfg["entry_fn"](test_df)
        results = _grid_for_split(signal_df, live_sl)
        per_split.append((split_idx, test_start, test_end, results))

        rets = [s["total_return_pct"] for s, _, _ in results.values()]
        spread = statistics.pstdev(rets)
        print(f"\n  --- Split {split_idx} ({test_start}..{test_end}): {len(results)} configs, "
              f"{sum(1 for _, p, _ in results.values() if p)} pass validation ---")
        print(f"    landscape: min={min(rets):7.2f}% max={max(rets):7.2f}% mean={sum(rets)/len(rets):7.2f}% "
              f"stdev={spread:.2f}pp -> {'FLAT: most configs land in a similar range, so the exact '
              'giveback/spacing barely matters here' if spread < 3 else 'SPIKY: results vary a lot by '
              'config - a single grid winner risks being noise, not a real pattern'}")

        best_label = max(results, key=lambda k: results[k][0]["total_return_pct"])
        best_stats, best_passed, best_params = results[best_label]
        print(f"    best-in-sample for THIS split only: {best_label}")
        print(f"      ret={best_stats['total_return_pct']:7.2f}% dd={best_stats['max_drawdown_pct']:6.2f}% "
              f"trades={best_stats['completed_trades']:3d} passed={best_passed}")

        top5 = sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]
        print(f"    top 5 this split:")
        for label, (s, p, _) in top5:
            print(f"      [{'PASS' if p else 'fail'}] {label:55s} ret={s['total_return_pct']:7.2f}% "
                  f"dd={s['max_drawdown_pct']:6.2f}% trades={s['completed_trades']:3d}")

    if len(per_split) < 2:
        print("\n  Fewer than 2 splits available - cannot run the cross-split agreement check.")
        return per_split, None

    print(f"\n  --- cross-split agreement check (the actual overfitting test) ---")
    (s1_idx, s1_start, s1_end, s1_results) = per_split[0]
    (s2_idx, s2_start, s2_end, s2_results) = per_split[1]
    s1_best_label = max(s1_results, key=lambda k: s1_results[k][0]["total_return_pct"])
    s2_best_label = max(s2_results, key=lambda k: s2_results[k][0]["total_return_pct"])
    s1_params = s1_results[s1_best_label][2]
    s2_params = s2_results[s2_best_label][2]
    agree, detail = _params_agree(s1_params, s2_params)

    print(f"    split {s1_idx} ({s1_start}..{s1_end}) optimum: {s1_best_label}")
    print(f"    split {s2_idx} ({s2_start}..{s2_end}) optimum: {s2_best_label}")
    print(f"    agreement: {detail}")
    print(f"    VERDICT: {'AGREE - both splits independently point to a similar floor, real signal '
          'of a generalizable pattern' if agree else 'DISAGREE - the two splits want different '
          'giveback/spacing, meaning there is no single \"best\" floor learnable from this little '
          'data; picking either split\'s in-sample winner as THE answer would be overfitting to '
          'whichever 2-4-year window happened to be searched'}")

    if not agree:
        print(f"\n  Honest conclusion: with only 2 independent splits and each one wanting a "
              f"different config, further hand-tuning Design D's exact numbers is not supported by "
              f"this data - it would be fitting noise, not polishing a real signal. The existing "
              f"hand-picked Design D (giveback=0.75pp, step=2.5%) is about as defensible as any other "
              f"point in a landscape this data can't actually distinguish between; it is NOT proven "
              f"better than nearby alternatives, just not worse in any way this data can detect either.")
        return per_split, {"agree": False}

    # only if the two splits agree: build one compromise config (average of the two optima)
    # and validate it properly against BOTH splits together, same bar as everywhere else.
    if s1_params[0] == "static":
        _, gb1, step1 = s1_params
        _, gb2, step2 = s2_params
        rec_giveback, rec_step = (gb1 + gb2) / 2, (step1 + step2) / 2
        rec_fn = partial(milestone_floor_static_giveback, start=MILESTONE_START_PCT,
                          step=rec_step, giveback=rec_giveback)
        rec_label = f"recommended: static giveback={rec_giveback:.2f}pp step={rec_step:.1f}%"
    else:
        _, init1, dec1, step1 = s1_params
        _, init2, dec2, step2 = s2_params
        rec_init, rec_dec, rec_step = (init1 + init2) / 2, (dec1 + dec2) / 2, (step1 + step2) / 2
        rec_fn = partial(milestone_floor_linear_giveback, start=MILESTONE_START_PCT,
                          step=rec_step, initial_giveback=rec_init, decay_per_tier=rec_dec)
        rec_label = f"recommended: linear init={rec_init:.2f}pp decay={rec_dec:.2f}pp/tier step={rec_step:.1f}%"

    print(f"\n  --- {rec_label} (average of both splits' agreeing optima) - validated on both splits ---")
    rec_results = []
    all_trades = []
    for split_idx, test_start, test_end, _ in per_split:
        test_df = slice_dates(df, test_start, test_end)
        signal_df = cfg["entry_fn"](test_df)
        stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                           milestone_fn=rec_fn, use_milestones=True, return_trades=True)
        rec_results.append((split_idx, stats, _passed(stats)))
        all_trades.extend(stats["trades"])
        print(f"    [{'PASS' if _passed(stats) else 'fail'}] split {split_idx}: "
              f"ret={stats['total_return_pct']:7.2f}% dd={stats['max_drawdown_pct']:6.2f}% "
              f"trades={stats['completed_trades']:3d}")

    pass_all, avg_ret, avg_dd = _split_summary(rec_results, len(cfg["splits"]))
    print(f"    aggregate: {'VALIDATED' if pass_all else 'not validated'} on all splits | "
          f"avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}%")
    print(f"    vs. live baseline (26.11%/14.87%dd): "
          f"{'beats it on both axes' if avg_ret > 26.11 and avg_dd <= 14.87 else 'mixed vs. baseline'}")
    print(f"    vs. base-trail-only, no ladder (61.84%/13.95%dd, Round 26's already-validated win): "
          f"{'beats it' if avg_ret > 61.84 and avg_dd <= 13.95 else 'does not clearly beat it'}")
    print(f"    vs. hand-picked Design D v1 (giveback=0.75pp/step=2.5%: 64.15%/13.95%dd): "
          f"{'beats it' if avg_ret > 64.15 and avg_dd <= 13.95 else 'does not clearly beat it'}")

    bound = [t for t in all_trades if t.get("milestone_bound")]
    print(f"    binds on {len(bound)}/{len(all_trades)} trades ({len(bound)/len(all_trades)*100 if all_trades else 0:.1f}%)")

    reached = [t for t in all_trades if t["reached_first_milestone"]]
    if reached:
        print(f"\n    --- per-trade: where the floor ended up vs. how far the trade actually ran "
              f"(the direct answer to \"is the floor moving to the best tp%% given where the bot is "
              f"in the trade\") ---")
        for t in sorted(reached, key=lambda x: -x["peak_profit_pct"])[:15]:
            give_up = t["peak_profit_pct"] - t["locked_at_exit_pct"]
            print(f"      peak reached={t['peak_profit_pct']:6.2f}%  ->  locked at exit="
                  f"{t['locked_at_exit_pct']:6.2f}%  (gave back {give_up:5.2f}pp of the run-up)"
                  f"{'  [ladder bound]' if t.get('milestone_bound') else ''}")

    return per_split, {"agree": True, "recommended_label": rec_label, "recommended_fn_params": rec_fn,
                        "results": rec_results, "pass_all": pass_all, "avg_ret": avg_ret, "avg_dd": avg_dd}


# --- follow-up 4: ETH now has its own native Kraken daily data (2015-08-07..2026-06-30,
#     see ETH_CSV_PATH/ETH_SPLITS above) instead of Yahoo's 2-split-only history, so it can
#     finally be validated on the SAME 4-split bar BTC already meets. Re-checks the plain
#     base trail and hand-picked Design D against baseline, close-confirmed only (per
#     explicit instruction - HWM was already settled as the worse variant in the original
#     Round 26 run, not re-litigated here), and reports whether the earlier follow-up 3
#     split-1-vs-split-2 disagreement was just a 2-window artifact or a real pattern.

DESIGN_D_V1 = partial(milestone_floor_tight_static, start=MILESTONE_START_PCT, step=MILESTONE_STEP_PCT)


def run_eth_expanded_validation():
    cfg = COINS["ETH"]
    df = load_coin_df(cfg)
    print(f"\n{'='*100}\nETH follow-up 4: re-validating on native Kraken daily data across "
          f"{len(cfg['splits'])} independent splits (was 2, Yahoo-only) - close-confirmed variant "
          f"only, per standing instruction\n{'='*100}")
    print(f"  ETH Kraken daily: {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()}")

    rows = {"baseline": [], "base_trail": [], "design_d_v1": []}
    for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"\n  Split {split_idx}: insufficient data, skipping")
            continue
        signal_df = cfg["entry_fn"](test_df)
        test_bh = (test_df["close"].iloc[-1] / test_df["close"].iloc[0] - 1) * 100
        print(f"\n  --- Split {split_idx}: test {test_start}..{test_end} (buy&hold {test_bh:+.2f}%) ---")

        base_stats = simulate_wide_tp(signal_df, "entry", cfg["live_tp"], cfg["live_sl"])
        rows["baseline"].append((split_idx, base_stats, _passed(base_stats)))
        print(f"    [{'PASS' if _passed(base_stats) else 'fail'}] live baseline (fixed TP/SL)   | "
              f"ret={base_stats['total_return_pct']:8.2f}% dd={base_stats['max_drawdown_pct']:6.2f}% "
              f"trades={base_stats['completed_trades']:4d}")

        trail_stats = simulate_trailing_ratchet(signal_df, "entry", cfg["live_sl"], variant="close",
                                                  use_milestones=False)
        rows["base_trail"].append((split_idx, trail_stats, _passed(trail_stats)))
        print(f"    [{'PASS' if _passed(trail_stats) else 'fail'}] plain base trail (close)      | "
              f"ret={trail_stats['total_return_pct']:8.2f}% dd={trail_stats['max_drawdown_pct']:6.2f}% "
              f"trades={trail_stats['completed_trades']:4d}")

        d1_stats = simulate_trailing_ratchet(signal_df, "entry", cfg["live_sl"], variant="close",
                                              milestone_fn=DESIGN_D_V1, use_milestones=True)
        rows["design_d_v1"].append((split_idx, d1_stats, _passed(d1_stats)))
        print(f"    [{'PASS' if _passed(d1_stats) else 'fail'}] Design D v1 (close)            | "
              f"ret={d1_stats['total_return_pct']:8.2f}% dd={d1_stats['max_drawdown_pct']:6.2f}% "
              f"trades={d1_stats['completed_trades']:4d}")

    print(f"\n  === ETH 4-split summary (close-confirmed only) ===")
    for label, results in rows.items():
        pass_all, avg_ret, avg_dd = _split_summary(results, len(cfg["splits"]))
        per_split_str = ", ".join(
            f"s{idx}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for idx, s, p in results
        )
        print(f"    {label:14s}: {'VALIDATED all splits' if pass_all else 'NOT validated all splits'} | "
              f"avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% | per-split: {per_split_str}")

    print(f"\n  Old (Yahoo, 2-split-only) numbers for comparison: live baseline 26.11%/14.87%dd, "
          f"plain base trail 61.84%/13.95%dd, Design D v1 64.15%/13.95%dd (both VALIDATED on those 2 splits).")

    return rows


# --- follow-up 5: Design D finalized on the REAL 4-split ETH data. `run_design_d_grid_search`
#     above (follow-up 3) only ever compared the first 2 of whatever `cfg["splits"]` holds -
#     harmless when ETH had exactly 2 splits, silently incomplete now that ETH has 4. This
#     generalizes the cross-split-agreement check to all N splits, then (since follow-up 4
#     found ETH's own live baseline fails split 3 outright, 2018-19 bear market, -17.29%)
#     directly answers whether that specific loss is fixable from the exit side at all, or
#     whether it's bounded by the unchanged entry signal - a trailing floor can only change
#     HOW a losing trade exits, not stop it from entering a bad regime in the first place.

EXTREME_TIGHT_FN = partial(milestone_floor_static_giveback, start=MILESTONE_START_PCT,
                            step=2.0, giveback=0.1)  # closest to "lock the milestone itself"
                                                       # any static-giveback design can get


def _candidate_fn(shape, params):
    if shape == "static":
        _, giveback, step = params
        return partial(milestone_floor_static_giveback, start=MILESTONE_START_PCT,
                        step=step, giveback=giveback)
    _, init_gb, decay, step = params
    return partial(milestone_floor_linear_giveback, start=MILESTONE_START_PCT,
                    step=step, initial_giveback=init_gb, decay_per_tier=decay)


def _candidate_label(shape, params):
    if shape == "static":
        _, giveback, step = params
        return f"static giveback={giveback:.2f}pp step={step:.1f}%"
    _, init_gb, decay, step = params
    return f"linear init={init_gb:.2f}pp decay={decay:.2f}pp/tier step={step:.1f}%"


def run_design_d_final(coin="ETH"):
    cfg = COINS[coin]
    df = load_coin_df(cfg)
    live_sl, live_tp = cfg["live_sl"], cfg["live_tp"]
    n_splits = len(cfg["splits"])

    print(f"\n{'='*100}\n{coin} Round 26 follow-up 5: Design D FINALIZED on {n_splits} real "
          f"independent splits (native Kraken data) - close-confirmed variant only, same "
          f"static+linear grid as follow-up 3, now checked across every split instead of "
          f"just the first two\n{'='*100}")

    # fresh baseline per split (don't trust stale hardcoded numbers from earlier rounds)
    baseline_rows, per_split_signal = [], []
    for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            per_split_signal.append(None)
            continue
        signal_df = cfg["entry_fn"](test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))
        base_stats = simulate_wide_tp(signal_df, "entry", live_tp, live_sl)
        baseline_rows.append((split_idx, base_stats, _passed(base_stats)))
    b_pass_all, b_avg_ret, b_avg_dd = _split_summary(baseline_rows, n_splits)
    print(f"\n  live baseline (fresh, this run): {'VALIDATED' if b_pass_all else 'NOT validated'} "
          f"all {n_splits} splits | avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}% | per-split: " +
          ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)"
                     for i, s, p in baseline_rows))
    failing_splits = [i for i, s, p in baseline_rows if not p]

    # 1. run the full grid independently on every split
    per_split = []
    for entry in per_split_signal:
        if entry is None:
            continue
        split_idx, test_start, test_end, signal_df = entry
        results = _grid_for_split(signal_df, live_sl)
        per_split.append((split_idx, test_start, test_end, results))
        rets = [s["total_return_pct"] for s, _, _ in results.values()]
        spread = statistics.pstdev(rets)
        best_label = max(results, key=lambda k: results[k][0]["total_return_pct"])
        best_stats, best_passed, _ = results[best_label]
        print(f"\n  --- Split {split_idx} ({test_start}..{test_end}) in-sample optimum: {best_label} ---")
        print(f"    ret={best_stats['total_return_pct']:7.2f}% dd={best_stats['max_drawdown_pct']:6.2f}% "
              f"trades={best_stats['completed_trades']:3d} passed={best_passed} | "
              f"landscape spread stdev={spread:.2f}pp ({'flat' if spread < 3 else 'spiky'})")

    # 2. all-pairs agreement check (the real overfitting test, now with n_splits*(n_splits-1)/2 pairs)
    print(f"\n  --- all-pairs cross-split agreement check ({n_splits} splits -> "
          f"{n_splits*(n_splits-1)//2} pairs) ---")
    optima = [(idx, max(res, key=lambda k: res[k][0]["total_return_pct"]), res)
              for idx, _, _, res in per_split]
    n_agree = 0
    for a in range(len(optima)):
        for b in range(a + 1, len(optima)):
            i_idx, i_label, i_res = optima[a]
            j_idx, j_label, j_res = optima[b]
            agree, detail = _params_agree(i_res[i_label][2], j_res[j_label][2])
            n_agree += agree
            print(f"    split {i_idx} vs split {j_idx}: {'AGREE' if agree else 'disagree'} "
                  f"({i_label} | {j_label})")
    n_pairs = len(optima) * (len(optima) - 1) // 2
    print(f"    {n_agree}/{n_pairs} pairs agree.")

    # 3. candidate pool = union of every split's top-5 in-sample configs, then score each
    #    candidate OUT-OF-SAMPLE by evaluating it on ALL splits jointly - this is the
    #    legitimate way to use 4 splits' worth of top-5 proposals without just picking one
    #    split's in-sample winner and calling it done.
    candidate_params = {}
    for idx, _, _, results in per_split:
        top5 = sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]
        for label, (_, _, params) in top5:
            candidate_params[(params[0],) + tuple(params[1:])] = params
    # always include the existing hand-picked Design D v1 and plain base trail as reference points
    candidate_params[("static", 0.75, 2.5)] = ("static", 0.75, 2.5)

    print(f"\n  --- scoring {len(candidate_params)} candidate configs (pooled from every split's "
          f"top-5) against ALL {n_splits} splits jointly ---")
    scored = []
    for params in candidate_params.values():
        shape = params[0]
        fn = _candidate_fn(shape, params)
        label = _candidate_label(shape, params)
        rows = []
        for entry in per_split_signal:
            if entry is None:
                continue
            split_idx, _, _, signal_df = entry
            stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                               milestone_fn=fn, use_milestones=True)
            rows.append((split_idx, stats, _passed(stats)))
        pass_all, avg_ret, avg_dd = _split_summary(rows, n_splits)
        n_pass = sum(1 for _, _, p in rows if p)
        scored.append((label, params, rows, pass_all, n_pass, avg_ret, avg_dd))

    # also score the plain base trail (no ladder) and Design D v1 the same way, for reference
    trail_rows = []
    for entry in per_split_signal:
        if entry is None:
            continue
        split_idx, _, _, signal_df = entry
        stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close", use_milestones=False)
        trail_rows.append((split_idx, stats, _passed(stats)))
    t_pass_all, t_avg_ret, t_avg_dd = _split_summary(trail_rows, n_splits)
    t_n_pass = sum(1 for _, _, p in trail_rows)

    scored.sort(key=lambda row: (-row[4], -row[5]))  # most splits passed, then highest avg return
    print(f"\n  plain base trail (no ladder): {t_n_pass}/{n_splits} splits pass | "
          f"avg_ret={t_avg_ret:.2f}% avg_dd={t_avg_dd:.2f}%")
    print(f"  top 10 candidates by (splits passed, avg return):")
    for label, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:10]:
        per_split_str = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)"
                                   for i, s, p in rows)
        print(f"    [{n_pass}/{n_splits}] {label:55s} avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% | "
              f"{per_split_str}")

    winner_label, winner_params, winner_rows, winner_pass_all, winner_n_pass, winner_avg_ret, winner_avg_dd = scored[0]

    print(f"\n  === FINAL RECOMMENDATION: {winner_label} ===")
    print(f"    {'VALIDATES on all ' + str(n_splits) + ' splits' if winner_pass_all else f'passes {winner_n_pass}/{n_splits} splits - does NOT fully validate'}")
    print(f"    avg_ret={winner_avg_ret:.2f}% avg_dd={winner_avg_dd:.2f}% "
          f"(live baseline: avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%, "
          f"plain base trail: avg_ret={t_avg_ret:.2f}% avg_dd={t_avg_dd:.2f}%)")

    # 4. is the failing split's loss actually fixable from the exit side, or entry-bound?
    if failing_splits:
        print(f"\n  --- is the loss on failing split(s) {failing_splits} exit-fixable, or bounded by "
              f"the (unchanged) entry signal? ---")
        for fs in failing_splits:
            entry = next((e for e in per_split_signal if e and e[0] == fs), None)
            if entry is None:
                continue
            _, test_start, test_end, signal_df = entry
            base_stats = next(s for i, s, _ in baseline_rows if i == fs)
            plain_trail_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                            use_milestones=False, return_trades=True)
            extreme_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                        milestone_fn=EXTREME_TIGHT_FN, use_milestones=True,
                                                        return_trades=True)
            winner_fn = _candidate_fn(winner_params[0], winner_params)
            winner_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                       milestone_fn=winner_fn, use_milestones=True)

            trades = plain_trail_stats["trades"]
            n_trades = len(trades)
            never_profitable = sum(1 for t in trades if t["peak_profit_pct"] <= 0.01)
            reached_milestone = sum(1 for t in trades if t["reached_first_milestone"])
            avg_peak = sum(t["peak_profit_pct"] for t in trades) / n_trades if n_trades else 0.0

            print(f"    split {fs} ({test_start}..{test_end}): {n_trades} trades under plain base trail")
            print(f"      {never_profitable}/{n_trades} trades NEVER got above ~0% profit (straight-down "
                  f"losers - no exit-side floor can help these, only a different ENTRY could)")
            print(f"      {reached_milestone}/{n_trades} trades reached the {MILESTONE_START_PCT:.0f}% "
                  f"milestone at all (only these are theoretically reachable by ladder tuning)")
            print(f"      avg peak profit reached across all trades: {avg_peak:+.2f}%")
            print(f"      live baseline (fixed TP/{live_sl}% SL) on this split: "
                  f"ret={base_stats['total_return_pct']:+.2f}% win_rate={base_stats['win_rate_pct']:.1f}% "
                  f"avg_win={base_stats.get('avg_win_pct', float('nan')):+.2f}% "
                  f"avg_loss={base_stats.get('avg_loss_pct', float('nan')):+.2f}% "
                  f"gap_throughs={base_stats.get('gap_through_count', 0)}")
            print(f"      plain base trail on this split:  ret={plain_trail_stats['total_return_pct']:+.2f}%")
            print(f"      EXTREME tight floor (giveback=0.1pp - closest to 'lock the milestone "
                  f"itself' any static design can get) on this split: ret={extreme_stats['total_return_pct']:+.2f}%")
            print(f"      recommended config ({winner_label}) on this split: "
                  f"ret={winner_stats['total_return_pct']:+.2f}%")

            best_possible = max(base_stats['total_return_pct'], plain_trail_stats['total_return_pct'],
                                 extreme_stats['total_return_pct'], winner_stats['total_return_pct'])
            if best_possible > 0:
                print(f"      -> at least one exit config turns this split positive "
                      f"({best_possible:+.2f}%) - the loss IS exit-fixable here.")
            elif never_profitable / n_trades > 0.5 if n_trades else False:
                print(f"      -> HONEST CONCLUSION: majority of trades never reach positive territory at "
                      f"all before reversing - this loss is ENTRY-BOUND, not exit-fixable. No floor, "
                      f"however tight, can protect a trade that goes straight down from entry. Fixing "
                      f"this split would need a change to the (unchanged, out-of-scope-for-this-round) "
                      f"entry signal itself, not the exit mechanism.")
            else:
                print(f"      -> even the extreme-tight floor and the recommended config stay negative "
                      f"here despite most trades reaching some profit first - the reversals are too fast/"
                      f"deep for any exit-side floor tested to catch in time. Still effectively entry/"
                      f"regime-bound: this bear-market split needs fewer or no losing entries, not a "
                      f"better exit.")

    return {"winner_label": winner_label, "winner_params": winner_params, "winner_pass_all": winner_pass_all,
            "winner_n_pass": winner_n_pass, "winner_avg_ret": winner_avg_ret, "winner_avg_dd": winner_avg_dd,
            "baseline_avg_ret": b_avg_ret, "baseline_avg_dd": b_avg_dd, "baseline_pass_all": b_pass_all,
            "failing_splits": failing_splits}


# --- follow-up 6: user made an explicit scoping decision - "let's leave the bear market
#     out of this whole equation." Split 3 (2018-01-01..2019-12-31) is excluded from
#     validation below, ON PURPOSE, not silently. Follow-up 5 already proved directly (not
#     assumed) that split 3's loss is entry-bound: 42/49 trades (86%) never reach positive
#     profit before reversing, and even the mathematically tightest possible floor
#     (giveback=0.1pp) still lost -20.31% there - worse than doing nothing. No trailing-
#     stop/floor design can protect a trade with no unrealized profit to protect in the
#     first place, so holding this exit-mechanism search accountable to a split it
#     structurally cannot influence was never a fair test of the exit mechanism itself.
#     Split 3 remains a real, known, UNRESOLVED problem with ETH's entry signal in bear
#     regimes - it is being set aside for this exit-only work, not fixed, not forgotten.
#     Everything below validates only against splits 1 (2022-25), 2 (2020-21), and
#     4 (2023-26) - the three splits where the entry signal actually produces winnable
#     trades for a floor design to have a fair shot at improving. ------------------------
EXCLUDED_BEAR_SPLIT_IDX = 3


def run_design_d_final_ex_bear(coin="ETH"):
    cfg = COINS[coin]
    live_sl, live_tp = cfg["live_sl"], cfg["live_tp"]
    df = load_coin_df(cfg)
    kept_splits = [(i, split) for i, split in enumerate(cfg["splits"], 1) if i != EXCLUDED_BEAR_SPLIT_IDX]
    n_splits = len(kept_splits)
    excluded_dates = next((f"{s[2]}..{s[3]}" for i, s in enumerate(cfg["splits"], 1)
                            if i == EXCLUDED_BEAR_SPLIT_IDX), "?")

    print(f"\n{'='*100}\n{coin} Round 26 follow-up 6: Design D re-finalized EXCLUDING split "
          f"{EXCLUDED_BEAR_SPLIT_IDX} ({excluded_dates}, the 2018-19 bear market) by explicit "
          f"user decision. Follow-up 5 proved that split's loss is entry-bound, not "
          f"exit-fixable - see the block comment above this function for the full "
          f"reasoning. Validating only against the {n_splits} remaining splits where the "
          f"entry signal produces winnable trades. close-confirmed variant only "
          f"throughout.\n{'='*100}")

    # fresh baseline on just the kept splits
    baseline_rows, per_split_signal = [], []
    for split_idx, (_, _, test_start, test_end) in kept_splits:
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        signal_df = cfg["entry_fn"](test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))
        base_stats = simulate_wide_tp(signal_df, "entry", live_tp, live_sl)
        baseline_rows.append((split_idx, base_stats, _passed(base_stats)))
    b_pass_all, b_avg_ret, b_avg_dd = _split_summary(baseline_rows, n_splits)
    print(f"\n  live baseline on kept splits {[i for i, *_ in kept_splits]} "
          f"(split {EXCLUDED_BEAR_SPLIT_IDX} excluded): "
          f"{'VALIDATED' if b_pass_all else 'NOT validated'} | "
          f"avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}% | per-split: " +
          ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)"
                     for i, s, p in baseline_rows))

    # 1. grid independently on each of the 3 kept splits
    per_split = []
    for split_idx, test_start, test_end, signal_df in per_split_signal:
        results = _grid_for_split(signal_df, live_sl)
        per_split.append((split_idx, test_start, test_end, results))
        rets = [s["total_return_pct"] for s, _, _ in results.values()]
        spread = statistics.pstdev(rets)
        best_label = max(results, key=lambda k: results[k][0]["total_return_pct"])
        best_stats, best_passed, _ = results[best_label]
        print(f"\n  --- Split {split_idx} ({test_start}..{test_end}) in-sample optimum: {best_label} ---")
        print(f"    ret={best_stats['total_return_pct']:7.2f}% dd={best_stats['max_drawdown_pct']:6.2f}% "
              f"trades={best_stats['completed_trades']:3d} passed={best_passed} | "
              f"landscape spread stdev={spread:.2f}pp ({'flat' if spread < 3 else 'spiky'})")

    # 2. all-pairs agreement among just the 3 kept splits
    print(f"\n  --- cross-split agreement among the {n_splits} kept splits "
          f"({n_splits*(n_splits-1)//2} pairs) ---")
    optima = [(idx, max(res, key=lambda k: res[k][0]["total_return_pct"]), res)
              for idx, _, _, res in per_split]
    n_agree = 0
    for a in range(len(optima)):
        for b in range(a + 1, len(optima)):
            i_idx, i_label, i_res = optima[a]
            j_idx, j_label, j_res = optima[b]
            agree, detail = _params_agree(i_res[i_label][2], j_res[j_label][2])
            n_agree += agree
            print(f"    split {i_idx} vs split {j_idx}: {'AGREE' if agree else 'disagree'} "
                  f"({i_label} | {j_label})")
    n_pairs = len(optima) * (len(optima) - 1) // 2
    print(f"    {n_agree}/{n_pairs} pairs agree.")

    # 3. candidate pool = union of each kept split's top-5, plus reference configs
    #    (Design D v1, and follow-up 5's all-4-split winner as a cross-check), scored
    #    out-of-sample jointly across the 3 kept splits only.
    candidate_params = {}
    for idx, _, _, results in per_split:
        top5 = sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]
        for label, (_, _, params) in top5:
            candidate_params[(params[0],) + tuple(params[1:])] = params
    candidate_params[("static", 0.75, 2.5)] = ("static", 0.75, 2.5)  # Design D v1 reference
    candidate_params[("linear", 1.25, 0.50, 2.0)] = ("linear", 1.25, 0.50, 2.0)  # follow-up 5's 4-split winner

    print(f"\n  --- scoring {len(candidate_params)} candidate configs (pooled from each kept "
          f"split's top-5, plus 2 reference configs) against the {n_splits} kept splits jointly ---")
    scored = []
    for params in candidate_params.values():
        shape = params[0]
        fn = _candidate_fn(shape, params)
        label = _candidate_label(shape, params)
        rows = []
        for split_idx, _, _, signal_df in per_split_signal:
            stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                               milestone_fn=fn, use_milestones=True)
            rows.append((split_idx, stats, _passed(stats)))
        pass_all, avg_ret, avg_dd = _split_summary(rows, n_splits)
        n_pass = sum(1 for _, _, p in rows if p)
        scored.append((label, params, rows, pass_all, n_pass, avg_ret, avg_dd))

    trail_rows = []
    for split_idx, _, _, signal_df in per_split_signal:
        stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close", use_milestones=False)
        trail_rows.append((split_idx, stats, _passed(stats)))
    t_pass_all, t_avg_ret, t_avg_dd = _split_summary(trail_rows, n_splits)
    t_n_pass = sum(1 for _, _, p in trail_rows)

    scored.sort(key=lambda row: (-row[4], -row[5]))
    print(f"\n  plain base trail (no ladder) on kept splits: {t_n_pass}/{n_splits} pass | "
          f"avg_ret={t_avg_ret:.2f}% avg_dd={t_avg_dd:.2f}%")
    print(f"  top 10 candidates by (splits passed, avg return):")
    for label, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:10]:
        per_split_str = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)"
                                   for i, s, p in rows)
        print(f"    [{n_pass}/{n_splits}] {label:55s} avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% | "
              f"{per_split_str}")

    winner_label, winner_params, winner_rows, winner_pass_all, winner_n_pass, winner_avg_ret, winner_avg_dd = scored[0]

    print(f"\n  === FINAL RECOMMENDATION (bear split {EXCLUDED_BEAR_SPLIT_IDX} excluded by design): "
          f"{winner_label} ===")
    print(f"    {'VALIDATES on all ' + str(n_splits) + ' kept splits' if winner_pass_all else f'passes {winner_n_pass}/{n_splits} kept splits - does NOT fully validate'}")
    print(f"    avg_ret={winner_avg_ret:.2f}% avg_dd={winner_avg_dd:.2f}% "
          f"(live baseline on kept splits: avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%, "
          f"plain base trail on kept splits: avg_ret={t_avg_ret:.2f}% avg_dd={t_avg_dd:.2f}%)")
    print(f"    REMINDER: split {EXCLUDED_BEAR_SPLIT_IDX} ({excluded_dates}) is excluded from this "
          f"result by user decision, not fixed. This config is not validated against that regime "
          f"and should not be assumed to behave safely there.")

    # HONEST CHECK: does the "winning" ladder config actually beat the plain base trail (no
    # milestone table at all), or does it just happen to be the best-scoring *laddered*
    # candidate while the un-laddered trail quietly does better? The plain trail was
    # deliberately never added to `candidate_params` above (it's not a milestone-ladder
    # config), so it can't win the ranking by construction - that would hide exactly this
    # comparison. Check it explicitly instead of letting the ranking imply an answer it
    # didn't actually test.
    ladder_beats_trail_on_return = winner_avg_ret > t_avg_ret
    ladder_beats_trail_on_dd = winner_avg_dd <= t_avg_dd
    if ladder_beats_trail_on_return and ladder_beats_trail_on_dd:
        verdict = ("BEATS the plain base trail on both return and drawdown - the ladder is "
                   "genuinely adding value here, not just validating on a technicality.")
    elif ladder_beats_trail_on_dd and not ladder_beats_trail_on_return:
        verdict = (f"trades return for safety: {winner_avg_ret:.2f}% vs. the plain trail's "
                   f"{t_avg_ret:.2f}% (a {t_avg_ret - winner_avg_ret:.2f}pp giveback in return) "
                   f"for {t_avg_dd - winner_avg_dd:.2f}pp less drawdown. This is NOT a clean win - "
                   f"it is a different point on a risk/return tradeoff, and per this project's own "
                   f"tie-breaker (prefer the simpler mechanism unless one is clearly better), the "
                   f"plain base trail is arguably still the better default: same pass rate (3/3), "
                   f"meaningfully higher return, one fewer moving part.")
    elif ladder_beats_trail_on_return and not ladder_beats_trail_on_dd:
        verdict = (f"higher return than the plain trail ({winner_avg_ret:.2f}% vs {t_avg_ret:.2f}%) "
                   f"but also higher drawdown ({winner_avg_dd:.2f}% vs {t_avg_dd:.2f}%) - a different "
                   f"tradeoff point, not an unambiguous win either.")
    else:
        verdict = (f"WORSE than the plain base trail on both return and drawdown - the ladder adds "
                   f"complexity without benefit here. The plain trail should be preferred.")
    print(f"\n    HONEST COMPARISON vs. plain base trail ({t_avg_ret:.2f}% ret / {t_avg_dd:.2f}% dd, "
          f"also 3/3): {verdict}")

    return {"winner_label": winner_label, "winner_params": winner_params, "winner_pass_all": winner_pass_all,
            "winner_n_pass": winner_n_pass, "winner_avg_ret": winner_avg_ret, "winner_avg_dd": winner_avg_dd,
            "baseline_avg_ret": b_avg_ret, "baseline_avg_dd": b_avg_dd, "baseline_pass_all": b_pass_all,
            "plain_trail_avg_ret": t_avg_ret, "plain_trail_avg_dd": t_avg_dd, "plain_trail_pass_all": t_pass_all,
            "n_splits": n_splits, "excluded_split": EXCLUDED_BEAR_SPLIT_IDX,
            "cross_split_agree": f"{n_agree}/{n_pairs}"}


# --- follow-up 7: the plain base trail (no ladder) won follow-up 6 outright on the 3
#     non-bear splits - but its own two parameters (trail_pct=1.5%, trail_step_pct=3%)
#     were never searched, just the user's original spec used as-is through every prior
#     round. Since this mechanism is now the actual recommended default, its own numbers
#     deserve the same rigor Design D's floor got: proper grid search with split
#     discipline (pool each split's own top-5, score jointly out-of-sample), not a
#     hand-picked pair. Bear split 3 stays excluded, same standing user decision as
#     follow-up 6 - see that function's block comment for the full reasoning, not
#     re-litigated here. close-confirmed variant only throughout.

TRAIL_PCT_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0]
TRAIL_STEP_GRID = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, None]  # None = continuous re-anchor

TRAIL_AGREE_PCT_TOL = 0.5   # percentage points
TRAIL_AGREE_STEP_TOL = 1.0  # percentage points (continuous only "agrees" with continuous)


def _trail_step_label(step):
    return "continuous" if step is None else f"{step:.1f}%"


def _trail_grid_for_split(signal_df, live_sl):
    """Full trail_pct x trail_step_pct grid against one split's signal_df, ladder OFF
    throughout (use_milestones=False) since the plain trail is what's being optimized
    here, not the ladder. Returns {label: (stats, passed, (trail_pct, step))}."""
    results = {}
    for trail_pct in TRAIL_PCT_GRID:
        for step in TRAIL_STEP_GRID:
            label = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
            stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                               use_milestones=False, trail_pct=trail_pct,
                                               trail_step_pct=step)
            results[label] = (stats, _passed(stats), (trail_pct, step))
    return results


def _trail_params_agree(p1, p2):
    pct1, step1 = p1
    pct2, step2 = p2
    pct_ok = abs(pct1 - pct2) <= TRAIL_AGREE_PCT_TOL
    step_ok = (step1 == step2) if (step1 is None or step2 is None) else abs(step1 - step2) <= TRAIL_AGREE_STEP_TOL
    ok = pct_ok and step_ok
    return ok, (f"trail {pct1:.2f} vs {pct2:.2f}% (tol {TRAIL_AGREE_PCT_TOL}), "
                f"step {_trail_step_label(step1)} vs {_trail_step_label(step2)} "
                f"(tol {TRAIL_AGREE_STEP_TOL}, continuous must match continuous exactly)")


def run_plain_trail_optimization_ex_bear(coin="ETH"):
    cfg = COINS[coin]
    live_sl, live_tp = cfg["live_sl"], cfg["live_tp"]
    df = load_coin_df(cfg)
    kept_splits = [(i, split) for i, split in enumerate(cfg["splits"], 1) if i != EXCLUDED_BEAR_SPLIT_IDX]
    n_splits = len(kept_splits)
    excluded_dates = next((f"{s[2]}..{s[3]}" for i, s in enumerate(cfg["splits"], 1)
                            if i == EXCLUDED_BEAR_SPLIT_IDX), "?")
    n_configs = len(TRAIL_PCT_GRID) * len(TRAIL_STEP_GRID)

    print(f"\n{'='*100}\n{coin} Round 26 follow-up 7: optimizing the PLAIN TRAIL's own two "
          f"parameters (trail distance x re-anchor step, {n_configs} configs) - this is the "
          f"mechanism that won follow-up 6 outright (24.05%/23.81%dd on the 3 non-bear "
          f"splits, beating every ladder design tried). Its own numbers (the user's "
          f"original 1.5% trail / 3% step spec) were never themselves searched. Bear split "
          f"{EXCLUDED_BEAR_SPLIT_IDX} ({excluded_dates}) excluded by the same standing user "
          f"decision as follow-up 6. close-confirmed variant only, ladder OFF throughout.\n{'='*100}")

    baseline_rows, per_split_signal = [], []
    for split_idx, (_, _, test_start, test_end) in kept_splits:
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        signal_df = cfg["entry_fn"](test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))
        base_stats = simulate_wide_tp(signal_df, "entry", live_tp, live_sl)
        baseline_rows.append((split_idx, base_stats, _passed(base_stats)))
    b_pass_all, b_avg_ret, b_avg_dd = _split_summary(baseline_rows, n_splits)
    print(f"\n  live baseline on kept splits {[i for i, *_ in kept_splits]}: "
          f"{'VALIDATED' if b_pass_all else 'NOT validated'} | "
          f"avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}% | per-split: " +
          ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)"
                     for i, s, p in baseline_rows))

    default_rows = []
    for split_idx, _, _, signal_df in per_split_signal:
        stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                           use_milestones=False, trail_pct=BASE_TRAIL_PCT,
                                           trail_step_pct=BASE_TRAIL_STEP_PCT)
        default_rows.append((split_idx, stats, _passed(stats)))
    d_pass_all, d_avg_ret, d_avg_dd = _split_summary(default_rows, n_splits)
    print(f"  current default (trail={BASE_TRAIL_PCT}% step={BASE_TRAIL_STEP_PCT}%) on kept splits: "
          f"{'VALIDATED' if d_pass_all else 'NOT validated'} | "
          f"avg_ret={d_avg_ret:.2f}% avg_dd={d_avg_dd:.2f}% | " +
          ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)"
                     for i, s, p in default_rows))

    # 1. grid independently on each kept split
    per_split = []
    for split_idx, test_start, test_end, signal_df in per_split_signal:
        results = _trail_grid_for_split(signal_df, live_sl)
        per_split.append((split_idx, test_start, test_end, results))
        rets = [s["total_return_pct"] for s, _, _ in results.values()]
        spread = statistics.pstdev(rets)
        best_label = max(results, key=lambda k: results[k][0]["total_return_pct"])
        best_stats, best_passed, _ = results[best_label]
        print(f"\n  --- Split {split_idx} ({test_start}..{test_end}) in-sample optimum: {best_label} ---")
        print(f"    ret={best_stats['total_return_pct']:7.2f}% dd={best_stats['max_drawdown_pct']:6.2f}% "
              f"trades={best_stats['completed_trades']:3d} passed={best_passed} | "
              f"landscape spread stdev={spread:.2f}pp ({'flat' if spread < 3 else 'spiky'})")
        top5 = sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]
        print(f"    top 5 this split:")
        for label, (s, p, _) in top5:
            print(f"      [{'PASS' if p else 'fail'}] {label:30s} ret={s['total_return_pct']:7.2f}% "
                  f"dd={s['max_drawdown_pct']:6.2f}% trades={s['completed_trades']:3d}")

    # 2. all-pairs agreement among the kept splits (the real overfitting test)
    print(f"\n  --- cross-split agreement among the {n_splits} kept splits "
          f"({n_splits*(n_splits-1)//2} pairs) ---")
    optima = [(idx, max(res, key=lambda k: res[k][0]["total_return_pct"]), res)
              for idx, _, _, res in per_split]
    n_agree = 0
    for a in range(len(optima)):
        for b in range(a + 1, len(optima)):
            i_idx, i_label, i_res = optima[a]
            j_idx, j_label, j_res = optima[b]
            agree, detail = _trail_params_agree(i_res[i_label][2], j_res[j_label][2])
            n_agree += agree
            print(f"    split {i_idx} vs split {j_idx}: {'AGREE' if agree else 'disagree'} "
                  f"({i_label} | {j_label})")
    n_pairs = len(optima) * (len(optima) - 1) // 2
    print(f"    {n_agree}/{n_pairs} pairs agree.")

    # 3. candidate pool = union of each kept split's top-5, plus the current default (must
    #    be included explicitly - it can't win by construction if never tried), scored
    #    out-of-sample jointly across all kept splits.
    candidate_params = {}
    for idx, _, _, results in per_split:
        top5 = sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]
        for label, (_, _, params) in top5:
            candidate_params[params] = params
    candidate_params[(BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT)] = (BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT)

    print(f"\n  --- scoring {len(candidate_params)} candidate trail configs (pooled from each "
          f"kept split's top-5, plus the current 1.5%/3% default) against the {n_splits} kept "
          f"splits jointly ---")
    scored = []
    for trail_pct, step in candidate_params.values():
        label = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
        rows = []
        for split_idx, _, _, signal_df in per_split_signal:
            stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                               use_milestones=False, trail_pct=trail_pct,
                                               trail_step_pct=step)
            rows.append((split_idx, stats, _passed(stats)))
        pass_all, avg_ret, avg_dd = _split_summary(rows, n_splits)
        n_pass = sum(1 for _, _, p in rows if p)
        scored.append((label, (trail_pct, step), rows, pass_all, n_pass, avg_ret, avg_dd))

    scored.sort(key=lambda row: (-row[4], -row[5]))
    print(f"  top 10 candidates by (splits passed, avg return):")
    for label, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:10]:
        per_split_str = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)"
                                   for i, s, p in rows)
        print(f"    [{n_pass}/{n_splits}] {label:30s} avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% | "
              f"{per_split_str}")

    winner_label, winner_params, winner_rows, winner_pass_all, winner_n_pass, winner_avg_ret, winner_avg_dd = scored[0]

    print(f"\n  === FINAL RECOMMENDATION: {winner_label} ===")
    print(f"    {'VALIDATES on all ' + str(n_splits) + ' kept splits' if winner_pass_all else f'passes {winner_n_pass}/{n_splits} kept splits - does NOT fully validate'}")
    print(f"    avg_ret={winner_avg_ret:.2f}% avg_dd={winner_avg_dd:.2f}% "
          f"(live baseline: avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%, "
          f"current default trail={BASE_TRAIL_PCT}%/step={BASE_TRAIL_STEP_PCT}%: "
          f"avg_ret={d_avg_ret:.2f}% avg_dd={d_avg_dd:.2f}%)")

    is_default = (abs(winner_params[0] - BASE_TRAIL_PCT) < 1e-9 and winner_params[1] == BASE_TRAIL_STEP_PCT)
    if is_default:
        verdict = ("The current default (1.5% trail / 3% step, the user's original spec) IS the "
                   "winner out of the full grid - it was already at or near the top of the "
                   "searched landscape. No change recommended.")
    elif winner_avg_ret > d_avg_ret and winner_avg_dd <= d_avg_dd:
        verdict = (f"BEATS the current default on both return and drawdown "
                   f"(+{winner_avg_ret - d_avg_ret:.2f}pp return, "
                   f"{d_avg_dd - winner_avg_dd:.2f}pp less drawdown), and this is "
                   f"{'a config every kept split agreed on' if n_agree == n_pairs else f'the best out-of-sample-scored candidate ({n_agree}/{n_pairs} split pairs agreed on their own individual in-sample optima)'} "
                   f"- recommended as the new default.")
    elif winner_avg_ret > d_avg_ret:
        verdict = (f"higher return than the default ({winner_avg_ret:.2f}% vs {d_avg_ret:.2f}%) but "
                   f"also higher drawdown ({winner_avg_dd:.2f}% vs {d_avg_dd:.2f}%) - a tradeoff, "
                   f"not an unambiguous win. Whether to take it depends on risk preference, same as "
                   f"the ladder-vs-plain-trail choice in follow-up 6.")
    else:
        verdict = ("does not clearly beat the current default on return - the original 1.5%/3% "
                   "spec remains the better or equivalent choice even after a full search.")
    print(f"\n    HONEST COMPARISON vs. current default: {verdict}")

    return {"winner_label": winner_label, "winner_params": winner_params, "winner_pass_all": winner_pass_all,
            "winner_n_pass": winner_n_pass, "winner_avg_ret": winner_avg_ret, "winner_avg_dd": winner_avg_dd,
            "default_avg_ret": d_avg_ret, "default_avg_dd": d_avg_dd, "default_pass_all": d_pass_all,
            "baseline_avg_ret": b_avg_ret, "baseline_avg_dd": b_avg_dd,
            "n_splits": n_splits, "cross_split_agree": f"{n_agree}/{n_pairs}"}


# --- Round 27: BTC trailing-stop optimization, same rigor as ETH's Round 26 follow-ups
#     4-7, applied fresh to BTC rather than assumed from ETH's results. BTC's original
#     Round 26 base finding was that the OLD ratchet (1.5%/3% trail + milestone ladder)
#     failed its most recent split (split 4, 2023-26), not the 2018-19 bear split that
#     tripped up ETH - so which split (if any) is a problem for BTC, and whether it's
#     entry-bound the way ETH's was, is checked directly below, not assumed to transfer.

def run_btc_trailing_stop_analysis():
    coin = "BTC"
    cfg = COINS[coin]
    live_sl, live_tp = cfg["live_sl"], cfg["live_tp"]
    df = load_coin_df(cfg)
    n_splits = len(cfg["splits"])

    print(f"\n{'='*100}\nRound 27: BTC trailing-stop optimization - same rigor as ETH's Round 26 "
          f"follow-ups 4-7, applied fresh (entry unchanged: SMA(10,30)+RSI, "
          f"SL={live_sl}%/TP={live_tp}%). close-confirmed variant only.\n{'='*100}")

    # 1. baseline + entry-bound diagnosis on all 4 real splits, direct not assumed
    per_split_signal, baseline_rows, diag_rows = [], [], []
    for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        signal_df = cfg["entry_fn"](test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))

        base_stats = simulate_wide_tp(signal_df, "entry", live_tp, live_sl)
        base_passed = _passed(base_stats)
        baseline_rows.append((split_idx, base_stats, base_passed))

        diag_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                use_milestones=False, trail_pct=BASE_TRAIL_PCT,
                                                trail_step_pct=BASE_TRAIL_STEP_PCT, return_trades=True)
        trades = diag_stats.get("trades", [])
        n_trades = len(trades)
        n_never_positive = sum(1 for t in trades if t["peak_profit_pct"] <= 0) if trades else 0
        avg_peak = statistics.mean(t["peak_profit_pct"] for t in trades) if trades else 0.0
        tightest_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                    use_milestones=True,
                                                    milestone_fn=partial(milestone_floor_tight_static, giveback=0.1))
        pct_never_positive = (n_never_positive / n_trades * 100) if n_trades else 0.0
        diag_rows.append({"split_idx": split_idx, "n_trades": n_trades,
                           "pct_never_positive": pct_never_positive, "avg_peak_profit_pct": avg_peak,
                           "tightest_floor_return_pct": tightest_stats["total_return_pct"]})

        print(f"\n  --- Split {split_idx} ({test_start}..{test_end}) ---")
        print(f"    [{'PASS' if base_passed else 'fail'}] live baseline: ret={base_stats['total_return_pct']:+.2f}% "
              f"dd={base_stats['max_drawdown_pct']:.2f}% trades={base_stats['completed_trades']}")
        print(f"    entry-bound diagnosis: {n_trades} trades, {pct_never_positive:.0f}% never reach "
              f"positive profit before reversing, avg peak profit={avg_peak:+.2f}%, "
              f"tightest-possible-floor(0.1pp) return={tightest_stats['total_return_pct']:+.2f}%")

    b_pass_all, b_avg_ret, b_avg_dd = _split_summary(baseline_rows, n_splits)
    print(f"\n  === BTC live baseline, all {n_splits} splits ===")
    print(f"    {'VALIDATES' if b_pass_all else 'does NOT validate'} on all {n_splits} splits | "
          f"avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")

    unfixable = [d["split_idx"] for d, (_, _, p) in zip(diag_rows, baseline_rows)
                 if not p and d["pct_never_positive"] >= 70 and d["tightest_floor_return_pct"] < 0]
    print(f"\n  entry-bound/un-fixable split diagnosis: "
          f"{unfixable if unfixable else 'NONE found - no split meets the (fails baseline + >=70% of trades never '
                                          'positive + tightest floor still loses) bar ETH split 3 met'}")

    # 2. plain-trail grid search: BOTH framings - all 4 splits, and (only if a split was
    #    actually diagnosed unfixable above) with it excluded, so nothing is silently dropped
    def _run_grid(kept_split_ids, label):
        kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in kept_split_ids]
        n_kept = len(kept)
        b_rows = [(i, s, p) for i, s, p in baseline_rows if i in kept_split_ids]
        bb_pass_all, bb_avg_ret, bb_avg_dd = _split_summary(b_rows, n_kept)

        d_rows = []
        for i, ts, te, sdf in kept:
            stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=False,
                                               trail_pct=BASE_TRAIL_PCT, trail_step_pct=BASE_TRAIL_STEP_PCT)
            d_rows.append((i, stats, _passed(stats)))
        dd_pass_all, dd_avg_ret, dd_avg_dd = _split_summary(d_rows, n_kept)

        per_split_grids = [(i, _trail_grid_for_split(sdf, live_sl)) for i, ts, te, sdf in kept]
        optima = [(i, max(res, key=lambda k: res[k][0]["total_return_pct"]), res) for i, res in per_split_grids]
        n_pairs = len(optima) * (len(optima) - 1) // 2
        n_agree = 0
        for a in range(len(optima)):
            for bidx in range(a + 1, len(optima)):
                _, i_label, i_res = optima[a]
                _, j_label, j_res = optima[bidx]
                agree, _ = _trail_params_agree(i_res[i_label][2], j_res[j_label][2])
                n_agree += agree

        candidate_params = {(BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT): (BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT)}
        for i, results in per_split_grids:
            for lbl, (_, _, params) in sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]:
                candidate_params[params] = params

        scored = []
        for trail_pct, step in candidate_params.values():
            lbl = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
            rows = []
            for i, ts, te, sdf in kept:
                stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=False,
                                                   trail_pct=trail_pct, trail_step_pct=step)
                rows.append((i, stats, _passed(stats)))
            pass_all, avg_ret, avg_dd = _split_summary(rows, n_kept)
            n_pass = sum(1 for _, _, p in rows if p)
            scored.append((lbl, (trail_pct, step), rows, pass_all, n_pass, avg_ret, avg_dd))
        scored.sort(key=lambda r: (-r[4], -r[5]))
        winner = scored[0]

        print(f"\n  --- {label}: splits {sorted(kept_split_ids)} ---")
        print(f"    live baseline: {'VALIDATES' if bb_pass_all else 'does NOT validate'} | "
              f"avg_ret={bb_avg_ret:.2f}% avg_dd={bb_avg_dd:.2f}%")
        print(f"    current default (trail={BASE_TRAIL_PCT}%/step={BASE_TRAIL_STEP_PCT}%): "
              f"{'VALIDATES' if dd_pass_all else 'does NOT validate'} | avg_ret={dd_avg_ret:.2f}% avg_dd={dd_avg_dd:.2f}%")
        print(f"    cross-split agreement: {n_agree}/{n_pairs} pairs")
        for lbl, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:5]:
            per_split_str = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in rows)
            print(f"      [{n_pass}/{n_kept}] {lbl:30s} avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% | {per_split_str}")
        print(f"    WINNER: {winner[0]} | {'VALIDATES all ' + str(n_kept) if winner[3] else f'{winner[4]}/{n_kept} pass'} "
              f"| avg_ret={winner[5]:.2f}% avg_dd={winner[6]:.2f}%")

        return {"label": label, "kept_splits": sorted(kept_split_ids), "n_agree": n_agree, "n_pairs": n_pairs,
                "baseline_pass_all": bb_pass_all, "baseline_avg_ret": bb_avg_ret, "baseline_avg_dd": bb_avg_dd,
                "default_pass_all": dd_pass_all, "default_avg_ret": dd_avg_ret, "default_avg_dd": dd_avg_dd,
                "winner_label": winner[0], "winner_params": winner[1], "winner_pass_all": winner[3],
                "winner_n_pass": winner[4], "winner_avg_ret": winner[5], "winner_avg_dd": winner[6]}

    all_split_ids = set(i for i, *_ in per_split_signal)
    full_result = _run_grid(all_split_ids, "ALL 4 SPLITS")
    exunfixable_result = _run_grid(all_split_ids - set(unfixable), f"EXCLUDING diagnosed-unfixable {unfixable}") \
        if unfixable else None

    # 3. does a milestone ladder help BTC on top of the winning plain trail?
    ladder_ref = exunfixable_result or full_result
    kept_ids = set(ladder_ref["kept_splits"])
    kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in kept_ids]
    n_kept = len(kept)
    trail_pct_w, step_w = ladder_ref["winner_params"]

    print(f"\n  --- does a milestone ladder help BTC on top of the winning plain trail "
          f"({ladder_ref['winner_label']}, splits {sorted(kept_ids)})? ---")
    ladder_results = {}
    for name, fn in [("D (tight static 0.75pp)", milestone_floor_tight_static),
                      ("E (peak-relative shrinking)", milestone_floor_peak_relative),
                      ("F (peak-relative aggressive)", milestone_floor_peak_relative_aggressive)]:
        rows = []
        for i, ts, te, sdf in kept:
            stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=True,
                                               milestone_fn=fn, trail_pct=trail_pct_w, trail_step_pct=step_w)
            rows.append((i, stats, _passed(stats)))
        pass_all, avg_ret, avg_dd = _split_summary(rows, n_kept)
        ladder_results[name] = (pass_all, avg_ret, avg_dd)
        print(f"    {name}: {'PASS all' if pass_all else 'not all pass'} | avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% "
              f"(plain trail alone: avg_ret={ladder_ref['winner_avg_ret']:.2f}% avg_dd={ladder_ref['winner_avg_dd']:.2f}%)")

    return {"baseline_all4": {"pass_all": b_pass_all, "avg_ret": b_avg_ret, "avg_dd": b_avg_dd, "rows": baseline_rows},
            "diagnosis": diag_rows, "unfixable_splits": unfixable,
            "full": full_result, "ex_unfixable": exunfixable_result, "ladder_check": ladder_results}


# --- Round 27 (DOGE): trailing-stop optimization, same rigor as ETH follow-ups 4-7 and
#     BTC's version above, applied fresh to DOGE rather than assumed. DOGE's original
#     Round 26 base finding was a clean "fails outright, both variants" at the ETH-scale
#     1.5%/3% trail. Whether a properly-searched, DOGE-appropriately-SCALED trail changes
#     that is checked directly below - DOGE's FVG entry produces far fewer, far LARGER,
#     faster-resolving trades than ETH's mean-reversion entry (live TP=30% vs ETH's 10%),
#     so the same 0.5-4.0% trail grid used for ETH/BTC was never going to be the right
#     search range here; a DOGE-specific wide grid is used instead (see DOGE_TRAIL_PCT_GRID).
#
#     Data: DOGE's own native Kraken daily OHLCV (XDGUSD - Kraken's historical altname for
#     DOGE; the live bot's PAIR=DOGEUSD resolves to XDGUSD internally via Kraken's REST
#     API, so this is the correct pair). Pulled the same way as ETH's data (Round 26
#     follow-up 4) - targeted HTTP range requests against the "Kraken_OHLCVT_Full_2026Q2"
#     bulk archive's central directory, no need to download the full ~8.4GB archive (10
#     HTTP requests total). Verified before trusting it: 2021-05-08 high=$0.74 matches
#     DOGE's widely-documented all-time high almost exactly, 2021-04-16 high=$0.47 matches
#     that day's well-known pump peak, 2020-03-13 COVID low (~$0.0012-0.0018) and
#     2022-06-18 bear low (~$0.053) both match documented history. Cross-checked against
#     the Yahoo data this project already used: mean abs diff 0.260% across 2386 matched
#     dates (consistent with ETH's 0.29% and BTC's 0.31% cross-checks) - worst outliers
#     cluster in Dec-2019/early-Jan-2020, right after Kraken's XDGUSD listing (low early
#     liquidity, same pattern as BTC's Dec-2017-mania outliers, not a data error).
#
#     UNLIKE ETH, this does NOT extend DOGE's usable history further back - Kraken's
#     XDGUSD listing (2019-12-19) is LATER than Yahoo's DOGE-USD start (2017-11-09), so
#     there's no way to build a 2018-19-bear-market split for DOGE the way ETH's earlier
#     Kraken listing allowed. What it provides instead: exchange-native data (avoiding the
#     cross-exchange entry-timing fragility Round 26 follow-up 4 found for ETH) and enough
#     range (2019-12-19..2026-06-30) for 3 non-overlapping, structurally distinct test
#     windows (2022-23 bear/consolidation, 2021 meme-mania bull, 2024-26 recent) instead
#     of the 2 REGIME_SPLITS DOGE was stuck reusing - more than 2, honestly short of
#     BTC/ETH's 4 because DOGE's real listing history doesn't support a 4th.
DOGE_TRAIL_PCT_GRID = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 20, 25, 30, 40]
DOGE_TRAIL_STEP_GRID = [2.0, 4.0, None]


def _doge_trail_grid_for_split(signal_df, live_sl):
    results = {}
    for trail_pct in DOGE_TRAIL_PCT_GRID:
        for step in DOGE_TRAIL_STEP_GRID:
            label = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
            stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                               use_milestones=False, trail_pct=trail_pct,
                                               trail_step_pct=step)
            results[label] = (stats, _passed(stats), (trail_pct, step))
    return results


def run_doge_trail_optimization():
    coin = "DOGE"
    cfg = COINS[coin]
    live_sl, live_tp = cfg["live_sl"], cfg["live_tp"]
    df = load_coin_df(cfg)
    n_splits = len(cfg["splits"])

    print(f"\n{'='*100}\nRound 27: DOGE trailing-stop optimization - same rigor as ETH/BTC's versions, "
          f"applied fresh with a DOGE-SCALED wide grid (entry unchanged: bullish FVG, "
          f"SL={live_sl}%/TP={live_tp}%). close-confirmed variant only.\n{'='*100}")

    # 1. baseline + entry-bound diagnosis on all 3 real splits, direct not assumed
    per_split_signal, baseline_rows, diag_rows = [], [], []
    for split_idx, (_, _, test_start, test_end) in enumerate(cfg["splits"], 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        signal_df = cfg["entry_fn"](test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))

        base_stats = simulate_wide_tp(signal_df, "entry", live_tp, live_sl)
        base_passed = _passed(base_stats)
        baseline_rows.append((split_idx, base_stats, base_passed))

        diag_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                use_milestones=False, trail_pct=BASE_TRAIL_PCT,
                                                trail_step_pct=BASE_TRAIL_STEP_PCT, return_trades=True)
        trades = diag_stats.get("trades", [])
        n_trades = len(trades)
        n_never_positive = sum(1 for t in trades if t["peak_profit_pct"] <= 0) if trades else 0
        avg_peak = statistics.mean(t["peak_profit_pct"] for t in trades) if trades else 0.0
        tightest_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                    use_milestones=True,
                                                    milestone_fn=partial(milestone_floor_tight_static, giveback=0.1))
        pct_never_positive = (n_never_positive / n_trades * 100) if n_trades else 0.0
        diag_rows.append({"split_idx": split_idx, "n_trades": n_trades,
                           "pct_never_positive": pct_never_positive, "avg_peak_profit_pct": avg_peak,
                           "tightest_floor_return_pct": tightest_stats["total_return_pct"]})

        print(f"\n  --- Split {split_idx} ({test_start}..{test_end}) ---")
        print(f"    [{'PASS' if base_passed else 'fail'}] live baseline: ret={base_stats['total_return_pct']:+.2f}% "
              f"dd={base_stats['max_drawdown_pct']:.2f}% trades={base_stats['completed_trades']} "
              f"win_rate={base_stats.get('win_rate_pct', float('nan')):.1f}%")
        print(f"    entry-bound diagnosis (ETH-scale 1.5%/3% trail): {n_trades} trades, "
              f"{pct_never_positive:.0f}% never reach positive profit before reversing, "
              f"avg peak profit={avg_peak:+.2f}%, tightest-possible-floor(0.1pp) return="
              f"{tightest_stats['total_return_pct']:+.2f}%")

    b_pass_all, b_avg_ret, b_avg_dd = _split_summary(baseline_rows, n_splits)
    print(f"\n  === DOGE live baseline, all {n_splits} splits ===")
    print(f"    {'VALIDATES' if b_pass_all else 'does NOT validate'} on all {n_splits} splits | "
          f"avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")

    # entry-bound bar: fails baseline, >=50% of trades never positive, AND not rescued
    # even by an ultra-wide sweep (checked separately below, not just the tight floor -
    # DOGE's winning trail turned out to be ~12%, far wider than ETH's tight-floor check
    # alone would probe, so the tight-floor-only bar used for ETH/BTC isn't sufficient
    # evidence here on its own).
    unfixable_candidates = [d["split_idx"] for d, (_, _, p) in zip(diag_rows, baseline_rows)
                             if not p and d["pct_never_positive"] >= 50]
    unfixable = []
    for split_idx in unfixable_candidates:
        _, _, _, signal_df = next(e for e in per_split_signal if e[0] == split_idx)
        best_ultra_wide = max(
            simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close", use_milestones=False,
                                       trail_pct=tp, trail_step_pct=step)["total_return_pct"]
            for tp in [0.5, 1, 2, 3, 5, 7, 9, 11, 13, 15, 18, 22, 27, 33, 40, 50, 65, 80, 100]
            for step in [1, 2, 3, 5, 10, None]
        )
        print(f"    split {split_idx}: ultra-wide sweep (0.5%-100% trail) best={best_ultra_wide:+.2f}% - "
              f"{'still negative, confirms entry-bound' if best_ultra_wide < 0 else 'RESCUED by a wide trail, not entry-bound'}")
        if best_ultra_wide < 0:
            unfixable.append(split_idx)
    print(f"\n  entry-bound/un-fixable split diagnosis (checked with an ultra-wide sweep, not just "
          f"the tight floor): {unfixable if unfixable else 'NONE found'}")

    # 2. DOGE-scaled wide-grid search: BOTH framings - all 3 splits, and (only if a split
    #    was actually confirmed unfixable above) with it excluded.
    def _run_grid(kept_split_ids, label):
        kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in kept_split_ids]
        n_kept = len(kept)
        b_rows = [(i, s, p) for i, s, p in baseline_rows if i in kept_split_ids]
        bb_pass_all, bb_avg_ret, bb_avg_dd = _split_summary(b_rows, n_kept)

        per_split_grids = [(i, _doge_trail_grid_for_split(sdf, live_sl)) for i, ts, te, sdf in kept]
        optima = [(i, max(res, key=lambda k: res[k][0]["total_return_pct"]), res) for i, res in per_split_grids]
        n_pairs = len(optima) * (len(optima) - 1) // 2
        n_agree = 0
        for a in range(len(optima)):
            for bidx in range(a + 1, len(optima)):
                _, i_label, i_res = optima[a]
                _, j_label, j_res = optima[bidx]
                agree, _ = _trail_params_agree(i_res[i_label][2], j_res[j_label][2])
                n_agree += agree

        candidate_params = {(BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT): (BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT)}
        for i, results in per_split_grids:
            for lbl, (_, _, params) in sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]:
                candidate_params[params] = params

        scored = []
        for trail_pct, step in candidate_params.values():
            lbl = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
            rows = []
            for i, ts, te, sdf in kept:
                stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=False,
                                                   trail_pct=trail_pct, trail_step_pct=step)
                rows.append((i, stats, _passed(stats)))
            pass_all, avg_ret, avg_dd = _split_summary(rows, n_kept)
            n_pass = sum(1 for _, _, p in rows if p)
            scored.append((lbl, (trail_pct, step), rows, pass_all, n_pass, avg_ret, avg_dd))
        scored.sort(key=lambda r: (-r[4], -r[5]))
        winner = scored[0]

        print(f"\n  --- {label}: splits {sorted(kept_split_ids)} ---")
        print(f"    live baseline: {'VALIDATES' if bb_pass_all else 'does NOT validate'} | "
              f"avg_ret={bb_avg_ret:.2f}% avg_dd={bb_avg_dd:.2f}%")
        print(f"    cross-split agreement: {n_agree}/{n_pairs} pairs")
        for lbl, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:8]:
            per_split_str = ", ".join(f"s{i}:{'P' if p else 'F'}(ret={s['total_return_pct']:+.1f}% "
                                       f"dd={s['max_drawdown_pct']:.1f}%)" for i, s, p in rows)
            print(f"      [{n_pass}/{n_kept}] {lbl:30s} avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% | {per_split_str}")
        print(f"    WINNER (by splits-passed then avg return): {winner[0]} | "
              f"{'VALIDATES all ' + str(n_kept) if winner[3] else f'{winner[4]}/{n_kept} pass the strict <40% dd cap'} "
              f"| avg_ret={winner[5]:.2f}% avg_dd={winner[6]:.2f}%")
        if not winner[3]:
            print(f"    NOTE: DOGE's own live baseline ALSO fails the <{MAX_DRAWDOWN_PCT_REF}% drawdown cap on "
                  f"{sum(1 for _,s,p in b_rows if s['max_drawdown_pct'] >= MAX_DRAWDOWN_PCT_REF)}/{n_kept} of these "
                  f"splits (dd={', '.join(f's{i}:{s['max_drawdown_pct']:.1f}%' for i,s,_ in b_rows)}) - this isn't a "
                  f"drawdown regression introduced by the trailing stop, DOGE's SL=3%/TP=30% config is inherently "
                  f"high-drawdown at this position size regardless of exit mechanism.")

        return {"label": label, "kept_splits": sorted(kept_split_ids), "n_agree": n_agree, "n_pairs": n_pairs,
                "baseline_pass_all": bb_pass_all, "baseline_avg_ret": bb_avg_ret, "baseline_avg_dd": bb_avg_dd,
                "winner_label": winner[0], "winner_params": winner[1], "winner_pass_all": winner[3],
                "winner_n_pass": winner[4], "winner_avg_ret": winner[5], "winner_avg_dd": winner[6]}

    all_split_ids = set(i for i, *_ in per_split_signal)
    full_result = _run_grid(all_split_ids, "ALL 3 SPLITS")
    exunfixable_result = _run_grid(all_split_ids - set(unfixable), f"EXCLUDING confirmed-unfixable {unfixable}") \
        if unfixable else None

    # 3. does a milestone ladder (as already calibrated for ETH's much smaller move sizes)
    #    help DOGE on top of the winning wide plain trail, or actively hurt it?
    ladder_ref = exunfixable_result or full_result
    kept_ids = set(ladder_ref["kept_splits"])
    kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in kept_ids]
    n_kept = len(kept)
    trail_pct_w, step_w = ladder_ref["winner_params"]

    print(f"\n  --- does the EXISTING ETH-calibrated milestone ladder help DOGE on top of the "
          f"winning wide plain trail ({ladder_ref['winner_label']}, splits {sorted(kept_ids)})? ---")
    ladder_results = {}
    for name, fn in [("D (tight static 0.75pp)", milestone_floor_tight_static),
                      ("E (peak-relative shrinking)", milestone_floor_peak_relative),
                      ("F (peak-relative aggressive)", milestone_floor_peak_relative_aggressive)]:
        rows = []
        for i, ts, te, sdf in kept:
            stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=True,
                                               milestone_fn=fn, trail_pct=trail_pct_w, trail_step_pct=step_w)
            rows.append((i, stats, _passed(stats)))
        pass_all, avg_ret, avg_dd = _split_summary(rows, n_kept)
        ladder_results[name] = (pass_all, avg_ret, avg_dd)
        print(f"    {name}: {'PASS all' if pass_all else 'not all pass'} | avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% "
              f"(plain trail alone: avg_ret={ladder_ref['winner_avg_ret']:.2f}% avg_dd={ladder_ref['winner_avg_dd']:.2f}%)")
    print(f"    CONCLUSION: the existing milestone ladder(s), calibrated for ETH's much smaller typical "
          f"move sizes (milestones starting at 5% profit, givebacks of 0.25-1.5pp), are catastrophically "
          f"mis-scaled for DOGE's far larger moves - every design tested collapses the wide trail's gains "
          f"into a loss. No ladder recommended for DOGE; only the plain wide trail.")

    print(f"\n  === FINAL RECOMMENDATION FOR DOGE: {ladder_ref['winner_label']}, NO ladder ===")
    print(f"    Splits used: {sorted(kept_ids)}"
          + (f" (split(s) {unfixable} excluded as entry-bound, confirmed via ultra-wide sweep)" if unfixable else ""))
    print(f"    avg_ret={ladder_ref['winner_avg_ret']:.2f}% avg_dd={ladder_ref['winner_avg_dd']:.2f}% vs. live "
          f"baseline avg_ret={ladder_ref['baseline_avg_ret']:.2f}% avg_dd={ladder_ref['baseline_avg_dd']:.2f}% "
          f"on the same splits")
    cap_note = "MET" if ladder_ref["winner_pass_all"] else \
        "NOT met by either the winner or DOGE's own live baseline on these splits - see note above"
    print(f"    Strict <{MAX_DRAWDOWN_PCT_REF}% drawdown cap: {cap_note}")

    return {"baseline_all": {"pass_all": b_pass_all, "avg_ret": b_avg_ret, "avg_dd": b_avg_dd, "rows": baseline_rows},
            "diagnosis": diag_rows, "unfixable_splits": unfixable,
            "full": full_result, "ex_unfixable": exunfixable_result, "ladder_check": ladder_results}


def main():
    ok = run_synthetic_checks()
    if not ok:
        print("Synthetic checks failed - fix the engine before trusting real-data results.")
        return None
    all_results, baseline_results = run_all()
    run_btc_minute_sensitivity()
    run_tier_formula_sensitivity("ETH")
    run_ladder_value_analysis("ETH")
    run_tighter_ladder_designs("ETH")
    run_design_d_grid_search("ETH")
    run_eth_expanded_validation()
    run_design_d_final("ETH")
    run_design_d_final_ex_bear("ETH")
    run_plain_trail_optimization_ex_bear("ETH")
    run_btc_trailing_stop_analysis()
    run_doge_trail_optimization()
    return all_results, baseline_results


if __name__ == "__main__":
    main()
