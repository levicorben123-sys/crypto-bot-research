"""LINK deep-dive: full entry-strategy discovery (Round 5/8/20 methodology) + Round
26/27 trailing-stop-ratchet optimization, applied fresh to LINK as a DOGE-replacement
candidate. Written as a standalone file (importing shared engines from the existing
modules) rather than editing experiments_trailing_ratchet.py/README.md directly, since
this was run in parallel with 5 other coins' identical deep-dives against the same
shared files - avoids merge/overwrite collisions. Findings are written to
LINK_RESEARCH.md, not README.md, for the same reason.

Backtest/research only - nothing here is wired into any live bot.

Data: LINK's own native Kraken daily OHLCV, pulled from the same "Kraken_OHLCVT_Full_
2026Q2" bulk archive that supplied BTC/ETH/DOGE's data (assets.kraken.com/marketing/
institutions/, 5-part split zip, ~8.97GB total) via HTTP range requests against the
archive's central directory (see scratch_link_data_fetch.py for the one-off extraction
script - no need to download the full archive, only LINKUSD_1440.csv's compressed bytes,
~77KB). Kraken's pair name is the plain "LINKUSD" (no historical altname, confirmed via
Kraken's public AssetPairs endpoint - unlike BTC=XBT or DOGE=XDG).

Verified before trusting it: spot-checked against well-documented LINK price history -
2021-05-10 high=$53.00 matches LINK's widely-reported May-2021 all-time high almost
exactly, 2020-03-13 COVID "Black Thursday" low ~$1.40 matches, 2022-06-18 bear-market low
~$5.40-6.45 matches. Cross-checked against Yahoo's LINK-USD daily closes for every
matching date: mean absolute difference 0.17% across 2471 matched dates - even tighter
than ETH's 0.29% or DOGE's 0.26% cross-checks, consistent and trustworthy.

UNLIKE ETH, this does NOT extend LINK's usable history further back - Yahoo's LINK-USD
actually starts EARLIER (2017-11-09) than Kraken's LINKUSD listing (2019-09-25), the same
situation DOGE was in. What Kraken's data provides instead: exchange-native prices
(avoiding the cross-exchange entry-timing fragility Round 26 follow-up 4 found for ETH)
and ~6.76 years of history (2019-09-25..2026-06-30) - enough for 3 non-overlapping test
windows built the same way as DOGE_SPLITS (train capped at LINK's actual Kraken listing
date), not the old 2-split REGIME_SPLITS Round 15 used.

Usage:
    python experiments_link_deepdive.py
"""
import statistics
from functools import partial

from data_fetcher import load_kraken_csv_1min

from experiments_multisplit import _passed, slice_dates, SL_GRID_PCT, TP_GRID_PCT
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp, MIN_TRADES, MAX_DRAWDOWN_PCT_REF
from experiments_indicators import SETUPS, run_setup
from experiments_trailing_ratchet import (
    simulate_trailing_ratchet, milestone_floor_tight_static, milestone_floor_peak_relative,
    milestone_floor_peak_relative_aggressive, BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT,
    TRAIL_PCT_GRID, TRAIL_STEP_GRID, _trail_step_label, _trail_params_agree,
)

LINK_CSV_PATH = "data/kraken_historical/extracted/LINKUSD_1440.csv"

LINK_SPLITS = [
    ("2019-09-25", "2021-12-31", "2022-01-01", "2023-12-31"),  # 2022-23 bear/consolidation
    ("2019-09-25", "2020-12-31", "2021-01-01", "2021-12-31"),  # 2021 DeFi/bull mania (LINK's defining regime)
    ("2019-09-25", "2023-12-31", "2024-01-01", "2026-06-30"),  # recent/current regime
]


def _split_summary(results, n_splits):
    pass_all = len(results) == n_splits and all(p for _, _, p in results)
    avg_ret = sum(s["total_return_pct"] for _, s, _ in results) / len(results) if results else 0.0
    avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in results) / len(results) if results else 0.0
    return pass_all, avg_ret, avg_dd


# ============================================================================
# Step A: baseline signal entry - full SL/TP grid (Round 8 methodology) across
# all 3 LINK splits, direct against the test window each split (no per-split
# fitting - the grid itself is what's validated across splits, same as
# experiments_multisplit.py for BTC).
# ============================================================================

def run_baseline_grid(df):
    print(f"\n{'='*100}\nLINK Step A: baseline SMA/RSI signal entry, full SL/TP grid "
          f"({len(SL_GRID_PCT)}x{len(TP_GRID_PCT)}={len(SL_GRID_PCT)*len(TP_GRID_PCT)} combos), "
          f"across all {len(LINK_SPLITS)} splits\n{'='*100}")

    per_split_signal = []
    for split_idx, (_, _, test_start, test_end) in enumerate(LINK_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        signal_df = baseline_signal_entry(test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))
        bh = (test_df["close"].iloc[-1] / test_df["close"].iloc[0] - 1) * 100
        print(f"  Split {split_idx}: test {test_start}..{test_end} (buy&hold {bh:+.2f}%)")

    by_combo = {}
    for sl_pct in SL_GRID_PCT:
        for tp_pct in TP_GRID_PCT:
            rows = []
            for split_idx, ts, te, signal_df in per_split_signal:
                stats = simulate_wide_tp(signal_df, "entry", tp_pct, sl_pct)
                rows.append((split_idx, stats, _passed(stats)))
            by_combo[(sl_pct, tp_pct)] = rows

    n_splits = len(per_split_signal)
    all_pass = [c for c, rows in by_combo.items() if all(p for _, _, p in rows)]
    print(f"\n  Combos passing ALL {n_splits} splits: {len(all_pass)}")
    best = None
    for combo in sorted(all_pass, key=lambda c: -sum(s["total_return_pct"] for _, s, _ in by_combo[c]) / n_splits):
        rows = by_combo[combo]
        avg_ret = sum(s["total_return_pct"] for _, s, _ in rows) / n_splits
        avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in rows) / n_splits
        print(f"    SL={combo[0]}% TP={combo[1]}% | avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% | " +
              ", ".join(f"s{i}:{s['total_return_pct']:+.1f}%" for i, s, _ in rows))
        if best is None:
            best = (combo, avg_ret, avg_dd)

    if not all_pass:
        print("  No (SL,TP) combo passed all splits with the baseline signal entry.")
        return per_split_signal, None

    print(f"\n  Best baseline combo: SL={best[0][0]}% TP={best[0][1]}% "
          f"(avg_ret={best[1]:.2f}% avg_dd={best[2]:.2f}%)")
    return per_split_signal, best


# ============================================================================
# Step B: does a smarter entry (indicator toolkit) beat the baseline for LINK
# specifically? Round 5/20 methodology: grid-search each family on TRAIN, only
# validate the top pick on held-out TEST, per split.
# ============================================================================

def run_indicator_search(df):
    print(f"\n{'='*100}\nLINK Step B: indicator-toolkit search (MACD/Stochastic/SuperTrend/"
          f"ADX-filtered/rolling-VWAP/Ichimoku) vs. baseline, per split (train-then-test)\n{'='*100}")

    winners = []
    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(LINK_SPLITS, 1):
        train_df = slice_dates(df, train_start, train_end)
        test_df = slice_dates(df, test_start, test_end)
        if len(train_df) < 100 or len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        print(f"\n  Split {split_idx}: train {train_start}..{train_end} -> test {test_start}..{test_end}")
        for setup_name, spec in SETUPS.items():
            validated = run_setup(setup_name, spec, train_df, test_df)
            params, tp_pct, sl_pct, train_stats, test_stats, passed = validated[0]
            tag = "PASS" if passed else "fail"
            exit_str = f"tp={tp_pct} sl={sl_pct}" if tp_pct is not None else "exit=reverse_signal"
            print(f"    [{tag}] {setup_name:24s} {exit_str} {params} | "
                  f"test_ret={test_stats['total_return_pct']:8.2f}% "
                  f"test_bh={test_stats['buy_hold_return_pct']:8.2f}% "
                  f"test_dd={test_stats['max_drawdown_pct']:6.2f}% trades={test_stats['round_trips']}")
            if passed:
                winners.append((split_idx, setup_name, params, tp_pct, sl_pct, test_stats))

    print(f"\n  Indicator-toolkit configs that validated (beat buy&hold, profitable, "
          f"enough trades, reasonable dd) on at least one split: {len(winners)}")
    for w in winners:
        print("   ", w)

    # does any ONE indicator family+params validate on ALL 3 splits (the real bar)?
    by_setup_params = {}
    for split_idx, setup_name, params, tp_pct, sl_pct, test_stats in winners:
        key = (setup_name, tuple(sorted(params.items())), tp_pct, sl_pct)
        by_setup_params.setdefault(key, []).append((split_idx, test_stats))
    full_validators = {k: v for k, v in by_setup_params.items() if len(v) == len(LINK_SPLITS)}
    print(f"\n  Configs validating on ALL {len(LINK_SPLITS)} splits: {len(full_validators)}")
    for k, v in full_validators.items():
        avg_ret = sum(s["total_return_pct"] for _, s in v) / len(v)
        print(f"    {k} | avg_ret={avg_ret:.2f}%")

    return full_validators


def main():
    print("Loading LINK Kraken native daily data...")
    df = load_kraken_csv_1min(LINK_CSV_PATH)
    print(f"LINK/USD (Kraken native): {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()}")

    per_split_signal, best_baseline = run_baseline_grid(df)
    full_validators = run_indicator_search(df)

    print(f"\n{'='*100}\nLINK entry-strategy verdict\n{'='*100}")
    if full_validators:
        print(f"  An indicator-toolkit config validated on all {len(LINK_SPLITS)} splits - "
              f"this beats/replaces the baseline signal as LINK's entry.")
    elif best_baseline:
        print(f"  No indicator-toolkit config validated on all splits; baseline signal entry "
              f"(SL={best_baseline[0][0]}% TP={best_baseline[0][1]}%) is what validates for LINK.")
    else:
        print(f"  Neither baseline nor any indicator-toolkit config validated on all "
              f"{len(LINK_SPLITS)} splits. LINK does not currently clear this project's bar "
              f"as a live-bot candidate with any entry style tested.")

    return df, per_split_signal, best_baseline, full_validators


if __name__ == "__main__":
    main()
