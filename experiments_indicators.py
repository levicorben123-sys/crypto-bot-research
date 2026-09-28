"""TradingView-style indicator strategies: MACD, Stochastic, SuperTrend, ADX-filtered
trend continuation, rolling-VWAP mean-reversion, and Ichimoku Cloud.

None of these were tested in rounds 1-4. Trend-following families (MACD, SuperTrend,
ADX-filtered, Ichimoku) use each indicator's own natural reverse-signal exit, reusing
experiments_trend.simulate_reverse_signal, so winners aren't capped by a fixed TP - that
was the bug that sank round 2. Mean-reversion families (Stochastic, VWAP) use the tight
TP/SL engine from experiments.py, which is the correct pairing for a reversion trade.

Same validation discipline as every prior round: grid-search on TRAIN, only re-check top
candidates on held-out TEST, split by market regime (experiments_regime.REGIME_SPLITS),
across BTC-USD and ETH-USD Yahoo daily history. A result only counts if it beats TEST
buy-and-hold with positive return, enough trades, and reasonable drawdown.

Note: "VWAP" here is a rolling volume-weighted average on daily candles, not true
session VWAP (which requires intraday data and resets daily) - a standard practical
approximation, not the real thing.

Usage:
    python experiments_indicators.py
"""
import numpy as np
import pandas as pd

import config
from data_fetcher import fetch_yahoo_history
from experiments import TP_SL_GRID, _empty_stats, _stats, simulate
from experiments_regime import REGIME_SPLITS, SYMBOLS, slice_dates
from experiments_trend import simulate_reverse_signal

MIN_TEST_TRADES_TREND = 2
MIN_TEST_TRADES_REVERSION = 3
MAX_DD_TREND = 40
MAX_DD_REVERSION = 25


# ---------------------------------------------------------------------------
# Indicator builders - each returns df with boolean 'entry' (and 'exit' for
# reverse-signal setups) columns.
# ---------------------------------------------------------------------------

def _true_range(df):
    prev_close = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def macd_crossover(df, fast, slow, signal):
    df = df.copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    df["entry"] = (macd.shift(1) <= signal_line.shift(1)) & (macd > signal_line)
    df["exit"] = (macd.shift(1) >= signal_line.shift(1)) & (macd < signal_line)
    return df


def stochastic_reversion(df, k_period, oversold):
    df = df.copy()
    low_n = df["low"].rolling(k_period).min()
    high_n = df["high"].rolling(k_period).max()
    percent_k = 100 * (df["close"] - low_n) / (high_n - low_n)
    percent_d = percent_k.rolling(3).mean()
    df["entry"] = (
        (percent_k.shift(1) <= percent_d.shift(1)) & (percent_k > percent_d) & (percent_k < oversold)
    )
    return df


def supertrend(df, atr_period, multiplier):
    df = df.copy()
    hl2 = (df["high"] + df["low"]) / 2
    atr = _true_range(df).ewm(alpha=1 / atr_period, adjust=False).mean()
    basic_upper = (hl2 + multiplier * atr).to_numpy()
    basic_lower = (hl2 - multiplier * atr).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)  # 1 = uptrend, -1 = downtrend

    for i in range(n):
        if i == 0 or np.isnan(final_upper[i - 1]):
            final_upper[i] = basic_upper[i]
            final_lower[i] = basic_lower[i]
            direction[i] = 1
            continue

        final_upper[i] = (
            basic_upper[i] if (basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            basic_lower[i] if (basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )

        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if close[i] > final_upper[i] else -1

    df["_direction"] = direction
    prev_dir = df["_direction"].shift(1)
    df["entry"] = (prev_dir == -1) & (df["_direction"] == 1)
    df["exit"] = (prev_dir == 1) & (df["_direction"] == -1)
    return df


def adx_filtered_crossover(df, fast, slow, adx_threshold, adx_period=14):
    df = df.copy()
    sma_fast = df["close"].rolling(fast).mean()
    sma_slow = df["close"].rolling(slow).mean()

    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = _true_range(df)

    atr = tr.ewm(alpha=1 / adx_period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / adx_period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / adx_period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / adx_period, adjust=False).mean()

    crossed_up = (sma_fast.shift(1) <= sma_slow.shift(1)) & (sma_fast > sma_slow)
    crossed_down = (sma_fast.shift(1) >= sma_slow.shift(1)) & (sma_fast < sma_slow)
    df["entry"] = crossed_up & (adx > adx_threshold)
    df["exit"] = crossed_down
    return df


def rolling_vwap_reversion(df, window, entry_threshold_pct):
    df = df.copy()
    vwap = (df["close"] * df["volume"]).rolling(window).sum() / df["volume"].rolling(window).sum()
    deviation_pct = (df["close"] / vwap - 1) * 100
    df["entry"] = deviation_pct < -entry_threshold_pct
    return df


def ichimoku_cloud(df, tenkan_period=9, kijun_period=26, senkou_b_period=52, shift=26):
    df = df.copy()
    tenkan = (df["high"].rolling(tenkan_period).max() + df["low"].rolling(tenkan_period).min()) / 2
    kijun = (df["high"].rolling(kijun_period).max() + df["low"].rolling(kijun_period).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(shift)
    senkou_b = ((df["high"].rolling(senkou_b_period).max() + df["low"].rolling(senkou_b_period).min()) / 2).shift(shift)

    cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)

    df["entry"] = (df["close"].shift(1) <= cloud_top.shift(1)) & (df["close"] > cloud_top)
    df["exit"] = (df["close"].shift(1) >= cloud_bottom.shift(1)) & (df["close"] < cloud_bottom)
    return df


# ---------------------------------------------------------------------------
# Setup registry: family name -> build fn, param grid, engine, and family type
# ---------------------------------------------------------------------------

SETUPS = {
    "macd_crossover": {
        "build": macd_crossover,
        "grid": [{"fast": f, "slow": s, "signal": sig}
                 for f, s, sig in [(12, 26, 9), (8, 21, 9), (5, 35, 5)]],
        "engine": "reverse_signal",
        "kind": "trend",
    },
    "supertrend": {
        "build": supertrend,
        "grid": [{"atr_period": p, "multiplier": m}
                 for p in (7, 10, 14) for m in (2.0, 3.0, 4.0)],
        "engine": "reverse_signal",
        "kind": "trend",
    },
    "adx_filtered_crossover": {
        "build": adx_filtered_crossover,
        "grid": [{"fast": f, "slow": s, "adx_threshold": a}
                 for f, s in [(10, 30), (5, 20)] for a in (20, 25, 30)],
        "engine": "reverse_signal",
        "kind": "trend",
    },
    "ichimoku_cloud": {
        "build": ichimoku_cloud,
        "grid": [{}],  # standard 9/26/52 periods only - not a parameter to overfit
        "engine": "reverse_signal",
        "kind": "trend",
    },
    "stochastic_reversion": {
        "build": stochastic_reversion,
        "grid": [{"k_period": k, "oversold": o} for k in (14, 21) for o in (20, 25, 30)],
        "engine": "tight_tpsl",
        "kind": "reversion",
    },
    "rolling_vwap_reversion": {
        "build": rolling_vwap_reversion,
        "grid": [{"window": w, "entry_threshold_pct": t}
                 for w in (20, 30, 50) for t in (3, 5, 8)],
        "engine": "tight_tpsl",
        "kind": "reversion",
    },
}


def _passed(test_stats, kind):
    max_dd = MAX_DD_TREND if kind == "trend" else MAX_DD_REVERSION
    min_trades = MIN_TEST_TRADES_TREND if kind == "trend" else MIN_TEST_TRADES_REVERSION
    return (
        test_stats["round_trips"] >= min_trades
        and test_stats["total_return_pct"] > test_stats["buy_hold_return_pct"]
        and test_stats["total_return_pct"] > 0
        and test_stats["max_drawdown_pct"] < max_dd
    )


def run_setup(name, spec, train_df, test_df):
    build, grid, engine, kind = spec["build"], spec["grid"], spec["engine"], spec["kind"]

    results = []
    for params in grid:
        train_signal_df = build(train_df, **params)
        if engine == "reverse_signal":
            stats = simulate_reverse_signal(train_signal_df, "entry", "exit")
            results.append((params, None, None, stats))
        else:
            for tp_pct, sl_pct in TP_SL_GRID:
                stats = simulate(train_signal_df, "entry", tp_pct, sl_pct)
                results.append((params, tp_pct, sl_pct, stats))

    results.sort(key=lambda r: r[3]["total_return_pct"], reverse=True)
    top = results[:5]

    validated = []
    for params, tp_pct, sl_pct, train_stats in top:
        test_signal_df = build(test_df, **params)
        if engine == "reverse_signal":
            test_stats = simulate_reverse_signal(test_signal_df, "entry", "exit")
        else:
            test_stats = simulate(test_signal_df, "entry", tp_pct, sl_pct)
        validated.append((params, tp_pct, sl_pct, train_stats, test_stats, _passed(test_stats, kind)))

    return validated


def main():
    all_winners = []

    for pair, yahoo_symbol in SYMBOLS.items():
        print(f"\n### {pair} ({yahoo_symbol}) ###")
        df = fetch_yahoo_history(yahoo_symbol, range_="10y", interval="1d")

        for split_idx, (train_start, train_end, test_start, test_end) in enumerate(REGIME_SPLITS, 1):
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
                print(
                    f"    [{tag}] {setup_name:24s} {exit_str} {params} | "
                    f"train_ret={train_stats['total_return_pct']:8.2f}% | "
                    f"test_ret={test_stats['total_return_pct']:8.2f}% test_bh={test_stats['buy_hold_return_pct']:8.2f}% "
                    f"test_dd={test_stats['max_drawdown_pct']:6.2f}% test_trades={test_stats['round_trips']}"
                )
                if passed:
                    all_winners.append((pair, split_idx, setup_name, params, tp_pct, sl_pct, test_stats))

    print("\n" + "=" * 80)
    if all_winners:
        print(f"{len(all_winners)} config(s) validated across a real regime split:")
        for w in all_winners:
            print(" ", w)
    else:
        print(
            "No TradingView-style indicator setup validated across any regime split. Honest "
            "finding: MACD, Stochastic, SuperTrend, ADX-filtered trend, rolling-VWAP "
            "reversion, and Ichimoku Cloud don't show a real edge over buy-and-hold on this "
            "data either."
        )

    return all_winners


if __name__ == "__main__":
    main()
