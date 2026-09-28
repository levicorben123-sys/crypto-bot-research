"""Round 23: ADX regime filter - require ADX above a threshold (real trending conditions
present) as an ADDITIONAL gate on top of each coin's already-validated live entry+exit,
instead of using ADX to build a new entry signal (already tried and failed in Round 5/20's
adx_filtered_crossover). The idea: sit out choppy/non-trending periods entirely, keeping
everything else unchanged - one variable at a time.

Each coin's live entry+exit held fixed:
  - BTC: SMA(10,30)+RSI entry, SL=1%/TP=20%
  - ETH: Stochastic(21, oversold=30) entry, SL=1%/TP=10%
  - DOGE: SMA(5,10)+RSI entry, SL=2%/TP=100%

Usage:
    python experiments_adx_filter.py
"""
import numpy as np
import pandas as pd

import config
from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_fast_entry import fast_entry
from experiments_indicators import _true_range, stochastic_reversion
from experiments_multisplit import slice_dates, _passed
from experiments_multisplit_kraken import CSV_PATH as BTC_CSV_PATH, SPLITS as BTC_SPLITS
from experiments_regime import REGIME_SPLITS
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp

ADX_THRESHOLD_GRID = [15, 20, 25, 30]
ADX_PERIOD = 14


def compute_adx(df, period=ADX_PERIOD):
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = _true_range(df)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def apply_adx_filter(signal_df, threshold):
    df = signal_df.copy()
    adx = compute_adx(df)
    df["entry"] = df["entry"] & (adx > threshold)
    return df


COINS = {
    "BTC": {"kind": "kraken_csv", "path": BTC_CSV_PATH, "splits": BTC_SPLITS,
            "entry_fn": baseline_signal_entry, "sl": 1.0, "tp": 20.0},
    "ETH": {"kind": "yahoo", "symbol": "ETH-USD", "splits": REGIME_SPLITS,
            "entry_fn": lambda d: stochastic_reversion(d, k_period=21, oversold=30), "sl": 1.0, "tp": 10.0},
    "DOGE": {"kind": "yahoo", "symbol": "DOGE-USD", "splits": REGIME_SPLITS,
             "entry_fn": lambda d: fast_entry(d, 5, 10), "sl": 2.0, "tp": 100.0},
}


def load_coin_df(cfg):
    if cfg["kind"] == "kraken_csv":
        return load_kraken_csv_1min(cfg["path"])
    return fetch_yahoo_history(cfg["symbol"], range_="10y", interval="1d")


def main():
    for coin, cfg in COINS.items():
        df = load_coin_df(cfg)
        n_splits = len(cfg["splits"])
        print(f"\n{'='*90}\n{coin} ({n_splits} splits) - live entry+exit (SL={cfg['sl']}% TP={cfg['tp']}%), "
              f"ADX regime filter added\n{'='*90}")

        # baseline (no filter) for direct comparison
        per_split = []
        for split_idx, (ts, te, vs, ve) in enumerate(cfg["splits"], 1):
            test_df = slice_dates(df, vs, ve)
            if len(test_df) < 100:
                continue
            signal_df = cfg["entry_fn"](test_df)
            stats = simulate_wide_tp(signal_df, "entry", cfg["tp"], cfg["sl"])
            per_split.append(stats)
        avg_ret = sum(s["total_return_pct"] for s in per_split) / len(per_split)
        avg_dd = sum(s["max_drawdown_pct"] for s in per_split) / len(per_split)
        avg_trades = sum(s["completed_trades"] for s in per_split) / len(per_split)
        print(f"  [baseline, no ADX filter] avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% avg_trades={avg_trades:.1f}")

        for threshold in ADX_THRESHOLD_GRID:
            per_split = []
            for split_idx, (ts, te, vs, ve) in enumerate(cfg["splits"], 1):
                test_df = slice_dates(df, vs, ve)
                if len(test_df) < 100:
                    continue
                signal_df = cfg["entry_fn"](test_df)
                filtered_df = apply_adx_filter(signal_df, threshold)
                stats = simulate_wide_tp(filtered_df, "entry", cfg["tp"], cfg["sl"])
                passed = _passed(stats)
                per_split.append((stats, passed))
            all_ok = len(per_split) == n_splits and all(p for _, p in per_split)
            avg_ret = sum(s["total_return_pct"] for s, _ in per_split) / len(per_split)
            avg_dd = sum(s["max_drawdown_pct"] for s, _ in per_split) / len(per_split)
            avg_trades = sum(s["completed_trades"] for s, _ in per_split) / len(per_split)
            tag = "PASS" if all_ok else "fail"
            print(f"  [{tag}] ADX>{threshold} | avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% avg_trades={avg_trades:.1f}")


if __name__ == "__main__":
    main()
