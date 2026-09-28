"""Round 10: Round 8's stop-loss/take-profit grid, unchanged, re-run on Kraken's own
native daily OHLCV (data/kraken_historical/extracted/XBTUSD_1440.csv) instead of Yahoo -
higher-quality data (same exchange the live bot trades on, no cross-exchange divergence,
no resampling), and a direct extension along the stop-loss axis that Round 8 didn't
explore (Round 8 only ever tested SL=1%; this adds 3%/5%/7% at the same TP grid).

This is NOT a new search from scratch - same entry (SMA(10)/SMA(30)+RSI, unconditional
entry already ruled out in round 7), same simulate_wide_tp engine, same MIN_TRADES/
MAX_DRAWDOWN_PCT_REF pass bar, same "must pass ALL independent splits" discipline as
round 8 - only the data source and the regime-split boundaries change (extended to the
Kraken CSV's earlier start, Oct 2013, and capped at its actual end, Jun 2026).

Usage:
    python experiments_multisplit_kraken.py
"""
from data_fetcher import load_kraken_csv_1min  # schema-generic despite the name; works for any Kraken OHLCVT CSV
from experiments_multisplit import SL_GRID_PCT, TP_GRID_PCT, _passed, slice_dates
from experiments_wide_tp import simulate_wide_tp
from experiments_wide_tp import baseline_signal_entry

CSV_PATH = "data/kraken_historical/extracted/XBTUSD_1440.csv"

SPLITS = [
    ("2018-01-01", "2021-12-31", "2022-01-01", "2025-12-31"),  # round 8 split 1
    ("2017-01-01", "2019-12-31", "2020-01-01", "2021-12-31"),  # round 8 split 2
    ("2013-10-06", "2017-12-31", "2018-01-01", "2019-12-31"),  # extended: full Kraken history start -> 2018 bear
    ("2019-01-01", "2022-12-31", "2023-01-01", "2026-06-30"),  # recent, capped at data end
]


def main():
    df = load_kraken_csv_1min(CSV_PATH)
    print(f"Kraken native daily BTC/USD: {len(df)} candles, "
          f"{df['time'].min().date()}..{df['time'].max().date()}\n")

    by_combo = {}
    gap_events_by_sl = {sl: [] for sl in SL_GRID_PCT}

    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(SPLITS, 1):
        train_df = slice_dates(df, train_start, train_end)
        test_df = slice_dates(df, test_start, test_end)
        if len(train_df) < 100 or len(test_df) < 100:
            print(f"Split {split_idx}: insufficient data, skipping")
            continue

        train_signal_df = baseline_signal_entry(train_df)
        test_signal_df = baseline_signal_entry(test_df)
        test_bh = (test_df["close"].iloc[-1] / test_df["close"].iloc[0] - 1) * 100

        print(f"=== Split {split_idx}: train {train_start}..{train_end} -> test {test_start}..{test_end} "
              f"(test buy&hold {test_bh:+.2f}%) ===")
        print(
            f"{'sl':>3} {'tp':>4} | {'train_ret':>9} | {'test_ret':>8} {'dd':>6} {'trades':>6} "
            f"{'winrate':>7} {'expect':>7} {'uw':>7} {'gaps':>5}"
        )

        for sl_pct in SL_GRID_PCT:
            for tp_pct in TP_GRID_PCT:
                train_stats = simulate_wide_tp(train_signal_df, "entry", tp_pct, sl_pct)
                test_stats = simulate_wide_tp(test_signal_df, "entry", tp_pct, sl_pct)
                passed = _passed(test_stats)

                by_combo.setdefault((sl_pct, tp_pct), []).append((split_idx, test_stats, passed))
                if test_stats["gap_through_count"]:
                    gap_events_by_sl[sl_pct].append(test_stats["gap_through_worst_pct"])

                tag = "PASS" if passed else "fail"
                print(
                    f"[{tag}] {sl_pct:>2}% {tp_pct:>3}% | {train_stats['total_return_pct']:8.2f}% | "
                    f"{test_stats['total_return_pct']:7.2f}% {test_stats['max_drawdown_pct']:5.2f}% "
                    f"{test_stats['completed_trades']:6d} {test_stats['win_rate_pct']:6.1f}% "
                    f"{test_stats['expectancy_pct']:6.2f}% {test_stats['max_underwater_pct']:6.2f}% "
                    f"{test_stats['gap_through_count']:5d}"
                )
        print()

    print("=" * 100)
    print("Gap-through risk by stop level (SL triggered but candle opened below the stop price):")
    for sl_pct, events in gap_events_by_sl.items():
        if events:
            print(f"  {sl_pct}% stop: {len(events)} occurrence(s) across all splits/TPs, "
                  f"worst case {max(events):.2f} extra percentage point(s) of loss")
        else:
            print(f"  {sl_pct}% stop: 0 occurrences — never breached by a gap")

    n_splits = len(SPLITS)
    all_pass = [combo for combo, results in by_combo.items() if len(results) == n_splits and all(p for _, _, p in results)]
    majority_pass = [
        combo for combo, results in by_combo.items()
        if len(results) == n_splits and sum(p for _, _, p in results) >= n_splits - 1 and combo not in all_pass
    ]

    print(f"\nCombos passing ALL {n_splits} splits: {len(all_pass)}")
    for combo in all_pass:
        sl_pct, tp_pct = combo
        results = by_combo[combo]
        print(f"  SL={sl_pct}% TP={tp_pct}% |", [
            f"split{si}: ret={s['total_return_pct']:.1f}% wr={s['win_rate_pct']:.1f}% "
            f"exp={s['expectancy_pct']:.2f}% trades={s['completed_trades']}"
            for si, s, p in results
        ])

    print(f"\nCombos passing {n_splits - 1}/{n_splits} splits (one miss - reported for context):")
    for combo in majority_pass:
        sl_pct, tp_pct = combo
        results = by_combo[combo]
        failing = [(si, s) for si, s, p in results if not p]
        print(f"  SL={sl_pct}% TP={tp_pct}% | failed split(s): ", [
            f"split{si}: ret={s['total_return_pct']:.1f}% dd={s['max_drawdown_pct']:.1f}% trades={s['completed_trades']}"
            for si, s in failing
        ])

    if not all_pass:
        print("\nNo (stop-loss, take-profit) combination survived all 4 independent regime splits.")

    by_sl = {}
    for (sl_pct, tp_pct), results in by_combo.items():
        if len(results) == n_splits and all(p for _, _, p in results):
            by_sl.setdefault(sl_pct, []).append(tp_pct)
    print("\nAll-4-splits passes grouped by stop-loss level:")
    for sl_pct in SL_GRID_PCT:
        tps = sorted(by_sl.get(sl_pct, []))
        print(f"  SL={sl_pct}%: {len(tps)} passing TP level(s) {tps if tps else '(none)'}")

    return by_combo, all_pass, majority_pass


if __name__ == "__main__":
    main()
