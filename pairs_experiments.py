"""Regime-split validation grid for the BTC/ETH pairs mean-reversion strategy.

Same discipline as strategy.py's experiments: train (fit window/entry/exit thresholds)
on one regime, test ONLY on a later, untouched regime. A config only counts if it also
holds up on the test slice.
"""
from data_fetcher import fetch_yahoo_history
from pairs_strategy import align_pair
from pairs_backtest import run_pairs_backtest

WINDOW_GRID = [20, 30, 45, 60]
ENTRY_Z_GRID = [1.5, 2.0, 2.5]
EXIT_Z_GRID = [0.25, 0.5]
STOP_Z = 4.0

REGIME_SPLITS = [
    ("split1", "2018-01-01", "2021-12-31", "2022-01-01", "2026-09-16"),
    ("split2", "2020-01-01", "2022-12-31", "2023-01-01", "2026-09-16"),
]


def slice_df(df, start, end):
    return df[(df["time"] >= start) & (df["time"] <= end)].reset_index(drop=True)


def main():
    btc = fetch_yahoo_history("BTC-USD", range_="10y")
    eth = fetch_yahoo_history("ETH-USD", range_="10y")
    merged = align_pair(btc, eth)

    for label, train_start, train_end, test_start, test_end in REGIME_SPLITS:
        train_df = slice_df(merged, train_start, train_end)
        test_df = slice_df(merged, test_start, test_end)

        results = []
        for window in WINDOW_GRID:
            for entry_z in ENTRY_Z_GRID:
                for exit_z in EXIT_Z_GRID:
                    stats = run_pairs_backtest(train_df, window=window, entry_z=entry_z, exit_z=exit_z, stop_z=STOP_Z)
                    results.append((window, entry_z, exit_z, stats))

        results.sort(key=lambda r: r[3]["total_return_pct"], reverse=True)
        top = results[:5]

        print(f"\n=== {label}: train {train_start}..{train_end} -> test {test_start}..{test_end} ===")
        print(f"{'win':>4} {'ez':>4} {'xz':>5} | {'train_ret':>9} {'train_dd':>8} {'train_rt':>8} | {'test_ret':>8} {'test_dd':>7} {'test_rt':>7}")
        for window, entry_z, exit_z, train_stats in top:
            test_stats = run_pairs_backtest(test_df, window=window, entry_z=entry_z, exit_z=exit_z, stop_z=STOP_Z)
            print(
                f"{window:>4} {entry_z:>4} {exit_z:>5} | "
                f"{train_stats['total_return_pct']:>8.2f}% {train_stats['max_drawdown_pct']:>7.2f}% {train_stats['round_trips']:>8} | "
                f"{test_stats['total_return_pct']:>7.2f}% {test_stats['max_drawdown_pct']:>6.2f}% {test_stats['round_trips']:>7}"
            )

        bh_a = run_pairs_backtest(test_df, window=WINDOW_GRID[0])["buy_hold_a_pct"]
        bh_b = run_pairs_backtest(test_df, window=WINDOW_GRID[0])["buy_hold_b_pct"]
        print(f"(context only) BTC buy&hold over test: {bh_a:.2f}% | ETH buy&hold over test: {bh_b:.2f}%")


if __name__ == "__main__":
    main()
