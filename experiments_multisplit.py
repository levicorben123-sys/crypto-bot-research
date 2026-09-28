"""Round 8: chase round 7's most interesting result (BTC, 1% stop, wide take-profit,
baseline signal entry - positive expectancy that replicated on 2 regime splits) with a
wider SL/TP grid AND, critically, more independent regime splits - not just a bigger
search on the same 2 splits.

Why more splits, not just more parameters: round 7 tested ~10 take-profit values on 2
splits. This round tests 4 stop-losses x 13 take-profits = 52 combos - on only the same 2
splits, spurious passes are expected by pure chance even under a true null. Adding 2 more
genuinely independent regime splits (using BTC-USD history back to 2014) means a survivor
has to repeat across 4 differently-composed markets, not just clear a wider net cast over
the same 2 fish.

Splits used (BTC-USD, `fetch_yahoo_history` back to 2014-09):
  1. train 2018-2021 -> test 2022-2025   (round 7's split 1: chop/COVID/bull -> bear+recovery)
  2. train 2017-2019 -> test 2020-2021   (round 7's split 2: pre-COVID -> COVID crash+monster bull)
  3. train 2014-2017 -> test 2018-2019   (NEW: early chop+2017 mania -> 2018 crypto winter bear + partial recovery)
  4. train 2019-2022 -> test 2023-2026   (NEW: COVID crash+2021 bull+2022 bear -> most recent recovery/bull)

Entry is fixed to the baseline SMA/RSI signal (round 7 already showed unconditional entry
is a disaster with a tight stop - not retested). Reuses simulate_wide_tp/
baseline_signal_entry from experiments_wide_tp.py unchanged.

A combo only counts as validated if it passes (profitable, >=5 trades, <40% drawdown) on
ALL 4 splits - not a majority vote. Gap-through risk is re-checked at every stop level
(round 7 only confirmed it at 1%; a tighter 3%/5% stop is more exposed to being breached
by a gap on daily candles).

Usage:
    python experiments_multisplit.py
"""
from data_fetcher import fetch_yahoo_history
from experiments_wide_tp import MAX_DRAWDOWN_PCT_REF, MIN_TRADES, baseline_signal_entry, simulate_wide_tp

SYMBOL = "BTC-USD"
SL_GRID_PCT = [1, 3, 5, 7]
TP_GRID_PCT = [3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100]

SPLITS = [
    ("2018-01-01", "2021-12-31", "2022-01-01", "2025-12-31"),  # round 7 split 1
    ("2017-01-01", "2019-12-31", "2020-01-01", "2021-12-31"),  # round 7 split 2
    ("2014-09-17", "2017-12-31", "2018-01-01", "2019-12-31"),  # NEW: early chop+mania -> 2018 bear
    ("2019-01-01", "2022-12-31", "2023-01-01", "2026-09-16"),  # NEW: COVID+2021 bull+2022 bear -> recovery
]


def slice_dates(df, start, end):
    mask = (df["time"] >= start) & (df["time"] <= end)
    return df.loc[mask].reset_index(drop=True)


def _passed(test_stats):
    return (
        test_stats["completed_trades"] >= MIN_TRADES
        and test_stats["total_return_pct"] > 0
        and test_stats["max_drawdown_pct"] < MAX_DRAWDOWN_PCT_REF
    )


def main():
    df = fetch_yahoo_history(SYMBOL, range_="15y", interval="1d")
    print(f"{SYMBOL}: {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()}\n")

    # (sl_pct, tp_pct) -> list of (split_idx, test_stats, passed)
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
        print(
            "\nNo (stop-loss, take-profit) combination survived all 4 independent regime "
            "splits. Honest read: round 7's result likely didn't generalize once tested "
            "against genuinely different market history, not just a wider parameter net "
            "on the same 2 splits."
        )

    return by_combo, all_pass, majority_pass


if __name__ == "__main__":
    main()
