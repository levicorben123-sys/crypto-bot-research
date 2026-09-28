"""XRP deep-dive: entry-strategy discovery (Round 5/8/10/20-style) + Round 26/27
trailing-stop-ratchet optimization, applied fresh to a brand-new coin candidate meant to
potentially replace the DOGE live bot. Kept as its own file (rather than folding into
experiments_trailing_ratchet.py) to avoid merge collisions with parallel forks doing the
identical deep-dive for SOL/ADA/LTC/LINK/AVAX at the same time - imports shared
infrastructure from experiments_trailing_ratchet.py and experiments_indicators.py rather
than duplicating it.

DATA: XRP's own native Kraken daily OHLCV, sourced the same way as ETH/DOGE (Round 26
follow-up 4 / Round 27) - targeted HTTP range requests against the "Kraken_OHLCVT_Full_
2026Q2" bulk archive's central directory (assets.kraken.com/marketing/institutions/),
pulling only the XRPUSD_1440.csv entry (~218KB), not the full ~8.4GB archive. Kraken's
altname for XRP is XRPUSD (unlike DOGE's XDGUSD surprise) - confirmed directly via
https://api.kraken.com/0/public/AssetPairs?pair=XRPUSD before trusting the archive
filename. Range: 2017-05-18 to 2026-06-30 (XRP's actual Kraken listing date). Verified
before trusting it: 2018-01-07 high=$2.78 (Jan-2018 mania peak, consistent with
documented ~$2.5-3.3 range that week), 2020-03-13 low=$0.1165 (COVID "Black Thursday"
low, matches documented ~$0.11-0.12), 2021-04-14 high=$1.97 (April-2021 rally peak,
matches documented ~$1.84-1.96), 2023-07-13 high=$0.95 (the SEC-ruling pump day, matches
documented ~$0.90-0.95 spike), 2022-06-18 close=$0.31 (2022 bear low, matches documented
~$0.30) - all match well-documented history. Cross-checked against Yahoo's XRP-USD data
(this project's prior XRP source): mean abs diff 0.41% across 3,155 matched dates -
consistent with BTC's 0.31%, ETH's 0.29%, DOGE's 0.26% cross-checks; worst outliers
(13-19%) cluster in Dec-2017/Jan-2018, the same bubble-mania period flagged as the worst
divergence for BTC/ETH in Rounds 9/26-followup-4, not a data error.

SPLITS: XRP's Kraken history (2017-05-18 onward) supports the SAME 4-split template
BTC/ETH already use, following the exact same pattern (splits 1/2/4 identical date
ranges to BTC/ETH; split 3's train start clipped to XRP's actual listing date, same as
ETH's split 3 was clipped to ETH's 2015-08-07 listing instead of BTC's 2013-10-06).

Usage:
    python experiments_xrp_deepdive.py
"""
import statistics

import config
from data_fetcher import load_kraken_csv_1min
from experiments_indicators import (
    SETUPS, adx_filtered_crossover, ichimoku_cloud, macd_crossover,
    rolling_vwap_reversion, stochastic_reversion, supertrend,
)
from experiments_multisplit import slice_dates
from experiments_multisplit_kraken import SPLITS as BTC_SPLITS
from experiments_trend import simulate_reverse_signal
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp
from experiments_trailing_ratchet import (
    milestone_floor_peak_relative, milestone_floor_peak_relative_aggressive,
    milestone_floor_tight_static, simulate_trailing_ratchet,
)

XRP_CSV_PATH = "data/kraken_historical/extracted/XRPUSD_1440.csv"

XRP_SPLITS = [
    ("2018-01-01", "2021-12-31", "2022-01-01", "2025-12-31"),  # same as BTC/ETH split 1
    ("2017-01-01", "2019-12-31", "2020-01-01", "2021-12-31"),  # same as BTC/ETH split 2
    ("2017-05-18", "2017-12-31", "2018-01-01", "2019-12-31"),  # NEW: full XRP Kraken history start -> 2018 bear
    ("2019-01-01", "2022-12-31", "2023-01-01", "2026-06-30"),  # same as BTC/ETH split 4, capped at data end
]

MIN_TRADES = 5
MAX_DD = 40.0

# Live XRP config assumption for the "beat this" baseline comparison, matching the same
# SL=1%/TP=20% starting point every coin in this project's primary "baseline signal
# entry" search uses (Round 8's config, the one thing Round 15 confirmed also validates
# for XRP on the old 2-split Yahoo data) - re-derived fresh here on real 4-split data,
# not assumed to still hold.
XRP_LIVE_SL, XRP_LIVE_TP = 1.0, 20.0

SL_GRID = [1, 3, 5, 7]
TP_GRID = [3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100]


def _passed_wide_tp(stats):
    return (
        stats["completed_trades"] >= MIN_TRADES
        and stats["total_return_pct"] > 0
        and stats["max_drawdown_pct"] < MAX_DD
    )


def _passed_reverse(stats):
    return (
        stats["round_trips"] >= MIN_TRADES
        and stats["total_return_pct"] > 0
        and stats["max_drawdown_pct"] < MAX_DD
    )


def load_xrp():
    return load_kraken_csv_1min(XRP_CSV_PATH)


# --- Step A: baseline signal entry, full SL/TP grid, 4 real splits (Round 8/10 style) ---

def run_baseline_grid():
    print(f"\n{'='*100}\nSTEP A: XRP baseline signal entry (SMA(10,30)+RSI), SL/TP grid, "
          f"4 real Kraken splits\n{'='*100}")
    df = load_xrp()
    print(f"XRP native Kraken daily: {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()}")

    passing = []
    for sl in SL_GRID:
        for tp in TP_GRID:
            rows = []
            for split_idx, (_, _, test_start, test_end) in enumerate(XRP_SPLITS, 1):
                test_df = slice_dates(df, test_start, test_end)
                if len(test_df) < 100:
                    continue
                signal_df = baseline_signal_entry(test_df)
                stats = simulate_wide_tp(signal_df, "entry", tp, sl)
                rows.append((split_idx, stats, _passed_wide_tp(stats)))
            pass_all = len(rows) == len(XRP_SPLITS) and all(p for *_, p in rows)
            if pass_all:
                avg_ret = statistics.mean(s["total_return_pct"] for _, s, _ in rows)
                avg_dd = statistics.mean(s["max_drawdown_pct"] for _, s, _ in rows)
                avg_trades = statistics.mean(s["completed_trades"] for _, s, _ in rows)
                passing.append((sl, tp, avg_ret, avg_dd, avg_trades, rows))

    print(f"\n{len(passing)} of {len(SL_GRID) * len(TP_GRID)} (SL, TP) combos pass on ALL "
          f"{len(XRP_SPLITS)} splits:")
    passing.sort(key=lambda r: -r[2])
    for sl, tp, avg_ret, avg_dd, avg_trades, rows in passing:
        per_split = ", ".join(f"s{i}:{s['total_return_pct']:+.1f}%" for i, s, _ in rows)
        print(f"  SL={sl}% TP={tp:3d}% | avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:5.2f}% "
              f"avg_trades={avg_trades:.1f} | {per_split}")

    # confirm/derive the specific live-style default (SL=1/TP=20) result, pass or fail
    live_rows = []
    for split_idx, (_, _, test_start, test_end) in enumerate(XRP_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        signal_df = baseline_signal_entry(test_df)
        stats = simulate_wide_tp(signal_df, "entry", XRP_LIVE_TP, XRP_LIVE_SL)
        live_rows.append((split_idx, stats, _passed_wide_tp(stats)))
    live_pass_all = all(p for *_, p in live_rows)
    print(f"\nSL=1%/TP=20% (the 'default BTC-style' starting point) on XRP: "
          f"{'VALIDATES' if live_pass_all else 'does NOT validate'} all 4 splits | " +
          ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in live_rows))

    return df, passing, (XRP_LIVE_SL, XRP_LIVE_TP, live_pass_all, live_rows)


# --- Step B: indicator toolkit, same 4 splits, standalone bar (not the harder "beat
#     buy&hold" bar Round 5 originally used - matching how BTC/ETH/DOGE are actually
#     judged post-Round-8) -------------------------------------------------------------

REVERSION_TP_SL_GRID = [(tp, sl) for sl in (1, 3, 5) for tp in (5, 10, 15, 20, 30)]


def run_indicator_toolkit(df):
    print(f"\n{'='*100}\nSTEP B: indicator toolkit (Round 5/20 style) vs. baseline, "
          f"standalone bar, 4 real splits\n{'='*100}")

    winners = []
    for name, spec in SETUPS.items():
        build, grid, engine, kind = spec["build"], spec["grid"], spec["engine"], spec["kind"]
        best_for_setup = None
        for params in grid:
            tp_sl_options = [(None, None)] if engine == "reverse_signal" else REVERSION_TP_SL_GRID
            for tp, sl in tp_sl_options:
                rows = []
                ok = True
                for split_idx, (_, _, test_start, test_end) in enumerate(XRP_SPLITS, 1):
                    test_df = slice_dates(df, test_start, test_end)
                    if len(test_df) < 100:
                        ok = False
                        break
                    built = build(test_df, **params)
                    if engine == "reverse_signal":
                        stats = simulate_reverse_signal(built, "entry", "exit")
                        passed = _passed_reverse(stats)
                        trades_key = "round_trips"
                    else:
                        stats = simulate_wide_tp(built, "entry", tp, sl)
                        passed = _passed_wide_tp(stats)
                        trades_key = "completed_trades"
                    rows.append((split_idx, stats, passed, trades_key))
                if not ok or len(rows) != len(XRP_SPLITS):
                    continue
                pass_all = all(p for _, _, p, _ in rows)
                if pass_all:
                    avg_ret = statistics.mean(s["total_return_pct"] for _, s, _, _ in rows)
                    avg_dd = statistics.mean(s["max_drawdown_pct"] for _, s, _, _ in rows)
                    if best_for_setup is None or avg_ret > best_for_setup[0]:
                        best_for_setup = (avg_ret, avg_dd, params, tp, sl, rows)
        if best_for_setup:
            avg_ret, avg_dd, params, tp, sl, rows = best_for_setup
            winners.append((name, avg_ret, avg_dd, params, tp, sl, rows))
            per_split = ", ".join(f"s{i}:{s['total_return_pct']:+.1f}%" for i, s, _, _ in rows)
            print(f"  [VALIDATES ALL 4] {name:26s} params={params} tp={tp} sl={sl} "
                  f"avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:5.2f}% | {per_split}")
        else:
            print(f"  [no config validates all 4 splits] {name}")

    return winners


def main():
    df, baseline_passing, live_default = run_baseline_grid()
    indicator_winners = run_indicator_toolkit(df)

    print(f"\n{'='*100}\nSTEP A+B SUMMARY\n{'='*100}")
    live_sl, live_tp, live_pass, live_rows = live_default
    if baseline_passing:
        best_baseline = max(baseline_passing, key=lambda r: r[2])
        print(f"Best validated baseline-signal config: SL={best_baseline[0]}% TP={best_baseline[1]}% "
              f"avg_ret={best_baseline[2]:.2f}% avg_dd={best_baseline[3]:.2f}%")
    else:
        best_baseline = None
        print("No baseline-signal (SL, TP) combo validates on all 4 splits.")

    if indicator_winners:
        best_indicator = max(indicator_winners, key=lambda r: r[1])
        print(f"Best validated indicator-toolkit config: {best_indicator[0]} "
              f"avg_ret={best_indicator[1]:.2f}% avg_dd={best_indicator[2]:.2f}%")
    else:
        best_indicator = None
        print("No indicator-toolkit setup validates on all 4 splits.")

    # decide the winner entry to carry into the trailing-stop-ratchet phase - restricted
    # to tight-SL/wide-TP-style entries (baseline or reversion-kind indicator setups),
    # since the trailing-stop-ratchet mechanism fundamentally replaces a fixed TP on top
    # of an SL floor; a reverse-signal trend-following winner (no natural SL) doesn't fit
    # that framework and is reported as informational only, not carried forward.
    candidates = []
    if best_baseline:
        candidates.append(("baseline", best_baseline[2], best_baseline[0], best_baseline[1]))  # label, ret, sl, tp
    if best_indicator and SETUPS[best_indicator[0]]["kind"] == "reversion":
        candidates.append(("indicator:" + best_indicator[0], best_indicator[1], best_indicator[5], best_indicator[4]))
    elif best_indicator:
        print(f"\nNote: best indicator winner ({best_indicator[0]}) is a trend-following/"
              f"reverse-signal setup with no natural SL - reported above as informational, "
              f"not eligible for the trailing-stop-ratchet phase (which needs an SL-based "
              f"entry structure like baseline or a reversion-kind indicator).")
    if not candidates:
        print("\nNEITHER baseline nor indicator toolkit validates XRP on all 4 real splits. "
              "Stopping here - no defensible entry to carry into trailing-stop optimization.")
        return None, None, None

    winner_label, winner_ret, winner_sl, winner_tp = max(candidates, key=lambda c: c[1])
    print(f"\nWINNING ENTRY FOR XRP: {winner_label} (avg_ret={winner_ret:.2f}%, "
          f"sl={winner_sl}%, tp={winner_tp}%) - carried into Step C.")

    return df, winner_label, (winner_sl, winner_tp)


if __name__ == "__main__":
    main()
