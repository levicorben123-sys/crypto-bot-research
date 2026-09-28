"""Round 25: two strategy families sourced from web research into "winning" crypto
trading strategies, neither tested in this project's prior 24 rounds:

1. Bollinger Band Squeeze Breakout: a volatility-contraction-then-expansion mechanism,
   genuinely different from Round 20's Bollinger/VWAP mean-reversion tests - this trades
   the breakout AFTER a period of unusually low volatility (a "squeeze"), betting the
   contraction resolves into a directional move, not that price reverts to the mean.

2. Simplified Smart Money Concepts (bullish Fair Value Gap mitigation + continuation):
   a 3-candle imbalance pattern popular in retail crypto trading circles. Outside
   research already found a rigorous independent backtest of the broader ICT/SMC family
   got 0 of 648 tests beating buy-and-hold on index ETFs - tested here anyway rather than
   taking that at face value, since crypto's behavior could differ from equity indices.

Both evaluated with this project's standard wide SL/TP grid (reused from
experiments_recent_window.py) and standard validation bar (profitable, >=5 trades, <40%
drawdown, passes every independent split for that coin), across all three coins on their
proper splits (BTC: 4-split Kraken-native; ETH/DOGE: 2-split Yahoo).

Usage:
    python experiments_new_strategies.py
"""
import numpy as np
import pandas as pd

from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_multisplit import slice_dates, _passed
from experiments_multisplit_kraken import CSV_PATH as BTC_CSV_PATH, SPLITS as BTC_SPLITS
from experiments_recent_window import SL_GRID_PCT, TP_GRID_PCT
from experiments_regime import REGIME_SPLITS
from experiments_wide_tp import simulate_wide_tp

COINS = {
    "BTC": {"kind": "kraken_csv", "path": BTC_CSV_PATH, "splits": BTC_SPLITS},
    "ETH": {"kind": "yahoo", "symbol": "ETH-USD", "splits": REGIME_SPLITS},
    "DOGE": {"kind": "yahoo", "symbol": "DOGE-USD", "splits": REGIME_SPLITS},
}


def bollinger_squeeze_breakout(df, period=20, num_std=2.0, squeeze_lookback=120, squeeze_pctile=20):
    df = df.copy()
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    bandwidth = (upper - lower) / sma

    def in_squeeze(x):
        return x[-1] <= np.percentile(x, squeeze_pctile)

    was_squeezed = bandwidth.rolling(squeeze_lookback).apply(in_squeeze, raw=True)
    broke_out = (df["close"] > upper) & (df["close"].shift(1) <= upper.shift(1))
    df["entry"] = (was_squeezed.shift(1) > 0) & broke_out
    return df


def bullish_fvg_entry(df, max_age=10):
    """Bullish FVG: candle i-2's high < candle i's low (a gap the market left behind).
    Entry: within `max_age` bars, price dips back INTO the gap zone (mitigation) and
    then closes back above the gap's midpoint on a later bar (continuation) - the
    standard simplified SMC entry rule."""
    df = df.copy().reset_index(drop=True)
    gap_high = df["low"]  # candle i's low = top of the gap looking back
    gap_low = df["high"].shift(2)  # candle i-2's high = bottom of the gap
    has_gap = gap_low < gap_high
    gap_mid = (gap_high + gap_low) / 2

    entry = np.zeros(len(df), dtype=bool)
    gap_events = [(i, gap_low.iloc[i], gap_high.iloc[i], gap_mid.iloc[i]) for i in range(len(df)) if has_gap.iloc[i]]

    for gap_i, g_low, g_high, g_mid in gap_events:
        mitigated = False
        for j in range(gap_i + 1, min(gap_i + 1 + max_age, len(df))):
            low_j = df["low"].iloc[j]
            close_j = df["close"].iloc[j]
            if not mitigated and low_j <= g_high:
                mitigated = True
            if mitigated and close_j > g_mid and low_j <= g_high:
                entry[j] = True
                break

    df["entry"] = entry
    return df


STRATEGIES = {
    "bollinger_squeeze_breakout": bollinger_squeeze_breakout,
    "bullish_fvg": bullish_fvg_entry,
}


def load_coin_df(cfg):
    if cfg["kind"] == "kraken_csv":
        return load_kraken_csv_1min(cfg["path"])
    return fetch_yahoo_history(cfg["symbol"], range_="10y", interval="1d")


def main():
    all_pass = []
    for coin, cfg in COINS.items():
        df = load_coin_df(cfg)
        n_splits = len(cfg["splits"])
        print(f"\n{'='*90}\n{coin} ({n_splits} splits)\n{'='*90}")

        for strat_name, build_fn in STRATEGIES.items():
            best = None
            n_pass = 0
            for sl_pct in SL_GRID_PCT:
                for tp_pct in TP_GRID_PCT:
                    per_split = []
                    for split_idx, (ts, te, vs, ve) in enumerate(cfg["splits"], 1):
                        test_df = slice_dates(df, vs, ve)
                        if len(test_df) < 150:
                            continue
                        signal_df = build_fn(test_df)
                        stats = simulate_wide_tp(signal_df, "entry", tp_pct, sl_pct)
                        passed = _passed(stats)
                        per_split.append((stats, passed))
                    if not per_split:
                        continue
                    all_ok = len(per_split) == n_splits and all(p for _, p in per_split)
                    if all_ok:
                        n_pass += 1
                        avg_ret = sum(s["total_return_pct"] for s, _ in per_split) / len(per_split)
                        avg_dd = sum(s["max_drawdown_pct"] for s, _ in per_split) / len(per_split)
                        avg_trades = sum(s["completed_trades"] for s, _ in per_split) / len(per_split)
                        all_pass.append((coin, strat_name, sl_pct, tp_pct, avg_ret, avg_dd, avg_trades))
                        if best is None or avg_ret > best[4]:
                            best = (coin, strat_name, sl_pct, tp_pct, avg_ret, avg_dd, avg_trades)

            print(f"  {strat_name}: {n_pass} combos pass all {n_splits} splits", end="")
            if best:
                print(f" | best: SL={best[2]}% TP={best[3]}% avg_ret={best[4]:.2f}% "
                      f"avg_dd={best[5]:.2f}% avg_trades={best[6]:.1f}")
            else:
                print(" | NONE validated")

    return all_pass


if __name__ == "__main__":
    main()
