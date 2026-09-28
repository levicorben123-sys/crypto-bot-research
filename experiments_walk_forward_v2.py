"""Round 24: proper walk-forward optimization, as a stronger check than this project's
usual fixed 2-4 regime splits. Rolls a train/test window forward repeatedly across each
coin's full available history: optimize SL/TP on a 2-year train window, evaluate that
exact choice on the following 6-month test window (never touched during optimization),
then slide forward 6 months and repeat. This generates many more independent
out-of-sample folds than a fixed split, and is a standard defense against overfitting
found in open-source backtesting frameworks (see README Round 24 web-research note).

Each coin's entry mechanism is held fixed (already validated in prior rounds) - only the
SL/TP exit is walk-forward re-optimized per fold:
  - BTC: SMA(10,30)+RSI entry
  - ETH: Stochastic(21, oversold=30) entry
  - DOGE: SMA(5,10)+RSI entry

Two things are reported and compared directly:
  1. Walk-forward re-optimized: pick the best (by train return, >=3 train trades) SL/TP
     per fold, evaluate out-of-sample on that fold's test window.
  2. Static baseline: just run each coin's ALREADY-VALIDATED fixed live config across the
     exact same test windows, no re-optimization at all.
If (1) doesn't beat (2), re-optimizing isn't adding anything - the existing validated
config is already doing as well as continuous re-tuning would.

Usage:
    python experiments_walk_forward_v2.py
"""
from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_adx_filter import COINS as LIVE_COINS
from experiments_multisplit import _passed
from experiments_wide_tp import simulate_wide_tp

TRAIN_DAYS = 730
TEST_DAYS = 182
STEP_DAYS = 182

GRIDS = {
    "BTC": {"sl": [1, 3, 5, 7], "tp": [10, 20, 30, 40, 50, 60, 75, 100]},
    "ETH": {"sl": [0.5, 1, 1.5, 2, 3, 5], "tp": [5, 7, 10, 15, 20, 30, 40, 50]},
    "DOGE": {"sl": [1, 2, 3, 5, 7, 10], "tp": [10, 15, 20, 30, 50, 75, 100]},
}


def load_coin_df(cfg):
    if cfg["kind"] == "kraken_csv":
        return load_kraken_csv_1min(cfg["path"])
    return fetch_yahoo_history(cfg["symbol"], range_="10y", interval="1d")


def make_folds(df, train_days, test_days, step_days):
    start = df["time"].min()
    end = df["time"].max()
    folds = []
    cursor = start
    import pandas as pd
    while True:
        train_start = cursor
        train_end = train_start + pd.Timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + pd.Timedelta(days=test_days)
        if test_end > end:
            break
        folds.append((train_start, train_end, test_start, test_end))
        cursor = cursor + pd.Timedelta(days=step_days)
    return folds


def slice_between(df, start, end):
    mask = (df["time"] >= start) & (df["time"] < end)
    return df.loc[mask].reset_index(drop=True)


def main():
    for coin, cfg in LIVE_COINS.items():
        df = load_coin_df(cfg)
        folds = make_folds(df, TRAIN_DAYS, TEST_DAYS, STEP_DAYS)
        grid = GRIDS[coin]
        print(f"\n{'='*90}\n{coin}: {len(folds)} walk-forward folds "
              f"(train={TRAIN_DAYS}d, test={TEST_DAYS}d, step={STEP_DAYS}d)\n{'='*90}")

        wf_test_returns, wf_test_dds, wf_profitable = [], [], 0
        static_test_returns, static_test_dds, static_profitable = [], [], 0

        for train_start, train_end, test_start, test_end in folds:
            train_df = slice_between(df, train_start, train_end)
            test_df = slice_between(df, test_start, test_end)
            if len(train_df) < 100 or len(test_df) < 30:
                continue

            train_signal_df = cfg["entry_fn"](train_df)
            best = None
            for sl in grid["sl"]:
                for tp in grid["tp"]:
                    stats = simulate_wide_tp(train_signal_df, "entry", tp, sl)
                    if stats["completed_trades"] >= 3:
                        if best is None or stats["total_return_pct"] > best[2]["total_return_pct"]:
                            best = (sl, tp, stats)
            if best is None:
                continue
            sl, tp, _ = best

            test_signal_df = cfg["entry_fn"](test_df)
            wf_test_stats = simulate_wide_tp(test_signal_df, "entry", tp, sl)
            wf_test_returns.append(wf_test_stats["total_return_pct"])
            wf_test_dds.append(wf_test_stats["max_drawdown_pct"])
            if wf_test_stats["total_return_pct"] > 0:
                wf_profitable += 1

            static_test_stats = simulate_wide_tp(test_signal_df, "entry", cfg["tp"], cfg["sl"])
            static_test_returns.append(static_test_stats["total_return_pct"])
            static_test_dds.append(static_test_stats["max_drawdown_pct"])
            if static_test_stats["total_return_pct"] > 0:
                static_profitable += 1

        n = len(wf_test_returns)
        if n == 0:
            print("  insufficient data for any complete fold")
            continue

        print(f"  {n} usable folds")
        print(f"  [walk-forward re-optimized] avg test ret/fold={sum(wf_test_returns)/n:6.2f}% "
              f"avg test dd/fold={sum(wf_test_dds)/n:5.2f}% profitable folds={wf_profitable}/{n} "
              f"({wf_profitable/n*100:.0f}%)")
        print(f"  [static live config, no re-opt] avg test ret/fold={sum(static_test_returns)/n:6.2f}% "
              f"avg test dd/fold={sum(static_test_dds)/n:5.2f}% profitable folds={static_profitable}/{n} "
              f"({static_profitable/n*100:.0f}%)")


if __name__ == "__main__":
    main()
