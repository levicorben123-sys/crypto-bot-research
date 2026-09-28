"""Round 20: "search every angle" for ETH/DOGE daily-frequency trading - the full
indicator toolkit from Round 5 (MACD, Stochastic, SuperTrend, ADX-filtered trend,
rolling-VWAP, Ichimoku), never before evaluated for trade CADENCE, re-run here
specifically to check whether any of them offers a genuinely frequent, safe alternative
to the SMA-crossover family this project has used everywhere else.

Trend-family indicators (MACD, SuperTrend, ADX-filtered, Ichimoku) keep their natural
reverse-signal exit - forcing a tight TP/SL onto a trend-following entry was explicitly
the bug that sank Round 2, not repeated here. Mean-reversion indicators (Stochastic,
VWAP) pair with a small TP/SL grid (Round 19's small-win grid plus the original tight
grid from experiments.py), the correct pairing for a reversion trade, matching Round 5's
own design choice.

Same validation bar as always: profitable, >=5 trades, <40% drawdown, passes BOTH
independent regime splits. Cadence (median days between trades) reported for every
result, not just the passing ones, since a mechanism that's safe-but-slow is still useful
context even if it doesn't answer "daily" specifically.

Usage:
    python experiments_every_angle.py
"""
import numpy as np

import config
from data_fetcher import fetch_yahoo_history
from experiments_indicators import (
    macd_crossover, supertrend, adx_filtered_crossover, ichimoku_cloud,
    stochastic_reversion, rolling_vwap_reversion, SETUPS,
)
from experiments_multisplit import slice_dates, _passed
from experiments_regime import REGIME_SPLITS

COINS = {"ETH": "ETH-USD", "DOGE": "DOGE-USD"}

TREND_SETUPS = {k: v for k, v in SETUPS.items() if v["kind"] == "trend"}
REVERSION_SETUPS = {k: v for k, v in SETUPS.items() if v["kind"] == "reversion"}

SMALL_TPSL_GRID = [
    (tp, sl)
    for tp in [1, 1.5, 2, 3, 5, 7, 10]
    for sl in [0.5, 1, 1.5, 2, 3, 5]
]


def simulate_reverse_signal_with_cadence(df, entry_col, exit_col, starting_cash=10_000.0):
    df = df.dropna().reset_index(drop=True)
    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position = starting_cash, 0.0, False
    entry_times, trade_returns_pct = [], []
    entry_price = None
    equity_curve = []
    peak, max_dd = starting_cash, 0.0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]
        if not in_position:
            if bool(curr[entry_col]):
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                entry_price = price
                entry_times.append(curr["time"])
        else:
            if bool(curr[exit_col]):
                proceeds = coins * price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                trade_returns_pct.append((price / entry_price - 1) * 100)
        equity = cash + coins * price
        equity_curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)

    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100
    n = len(trade_returns_pct)
    wins = [r for r in trade_returns_pct if r > 0]
    win_rate = len(wins) / n * 100 if n else 0.0

    median_gap = None
    if len(entry_times) >= 2:
        gaps = np.diff(np.array(entry_times, dtype="datetime64[D]")).astype(int)
        median_gap = float(np.median(gaps))

    return {
        "total_return_pct": total_return_pct, "max_drawdown_pct": max_dd,
        "completed_trades": n, "win_rate_pct": win_rate,
    }, median_gap


def simulate_tpsl_with_cadence(df, entry_col, tp_pct, sl_pct, starting_cash=10_000.0):
    df = df.dropna().reset_index(drop=True)
    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = starting_cash, 0.0, False, None
    entry_times, trade_returns_pct = [], []
    equity_curve = []
    peak, max_dd = starting_cash, 0.0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]
        if not in_position:
            if bool(curr[entry_col]):
                entry_price = price
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                entry_times.append(curr["time"])
        else:
            tp_price = entry_price * (1 + tp_pct / 100)
            sl_price = entry_price * (1 - sl_pct / 100)
            exit_price = None
            if curr["low"] <= sl_price:
                exit_price = sl_price
            elif curr["high"] >= tp_price:
                exit_price = tp_price
            if exit_price is not None:
                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                trade_returns_pct.append((exit_price / entry_price - 1) * 100)
        equity = cash + coins * price
        equity_curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)

    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100
    n = len(trade_returns_pct)
    wins = [r for r in trade_returns_pct if r > 0]
    win_rate = len(wins) / n * 100 if n else 0.0

    median_gap = None
    if len(entry_times) >= 2:
        gaps = np.diff(np.array(entry_times, dtype="datetime64[D]")).astype(int)
        median_gap = float(np.median(gaps))

    return {
        "total_return_pct": total_return_pct, "max_drawdown_pct": max_dd,
        "completed_trades": n, "win_rate_pct": win_rate,
    }, median_gap


def main():
    n_splits = len(REGIME_SPLITS)
    all_pass = []

    for coin, symbol in COINS.items():
        df = fetch_yahoo_history(symbol, range_="10y", interval="1d")
        print(f"\n{'='*100}\n{coin}\n{'='*100}")

        print("\n-- Trend-family indicators (own reverse-signal exit) --")
        for name, spec in TREND_SETUPS.items():
            for params in spec["grid"]:
                per_split = []
                for split_idx, (ts, te, vs, ve) in enumerate(REGIME_SPLITS, 1):
                    test_df = slice_dates(df, vs, ve)
                    if len(test_df) < 100:
                        continue
                    built = spec["build"](test_df, **params)
                    stats, gap = simulate_reverse_signal_with_cadence(built, "entry", "exit")
                    passed = _passed(stats)
                    per_split.append((stats, gap, passed))
                if not per_split:
                    continue
                all_ok = len(per_split) == n_splits and all(p for _, _, p in per_split)
                avg_ret = sum(s["total_return_pct"] for s, _, _ in per_split) / len(per_split)
                avg_dd = sum(s["max_drawdown_pct"] for s, _, _ in per_split) / len(per_split)
                avg_trades = sum(s["completed_trades"] for s, _, _ in per_split) / len(per_split)
                gaps = [g for _, g, _ in per_split if g is not None]
                avg_gap = sum(gaps) / len(gaps) if gaps else None
                gap_str = f"{avg_gap:.1f}d" if avg_gap is not None else "n/a"
                tag = "PASS" if all_ok else "fail"
                print(f"  [{tag}] {name}{params} | avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% "
                      f"avg_trades={avg_trades:5.1f} avg_gap={gap_str}")
                if all_ok:
                    all_pass.append((coin, name, params, None, avg_ret, avg_dd, avg_gap))

        print("\n-- Mean-reversion indicators (small TP/SL grid) --")
        for name, spec in REVERSION_SETUPS.items():
            for params in spec["grid"]:
                best_for_params = None
                for tp_pct, sl_pct in SMALL_TPSL_GRID:
                    per_split = []
                    for split_idx, (ts, te, vs, ve) in enumerate(REGIME_SPLITS, 1):
                        test_df = slice_dates(df, vs, ve)
                        if len(test_df) < 100:
                            continue
                        built = spec["build"](test_df, **params)
                        stats, gap = simulate_tpsl_with_cadence(built, "entry", tp_pct, sl_pct)
                        passed = _passed(stats)
                        per_split.append((stats, gap, passed))
                    if not per_split:
                        continue
                    all_ok = len(per_split) == n_splits and all(p for _, _, p in per_split)
                    if all_ok:
                        avg_ret = sum(s["total_return_pct"] for s, _, _ in per_split) / len(per_split)
                        avg_dd = sum(s["max_drawdown_pct"] for s, _, _ in per_split) / len(per_split)
                        gaps = [g for _, g, _ in per_split if g is not None]
                        avg_gap = sum(gaps) / len(gaps) if gaps else 9999
                        all_pass.append((coin, name, params, (tp_pct, sl_pct), avg_ret, avg_dd, avg_gap))
                        if best_for_params is None or avg_gap < best_for_params[-1]:
                            best_for_params = (tp_pct, sl_pct, avg_ret, avg_dd, avg_gap)
                if best_for_params:
                    tp_pct, sl_pct, avg_ret, avg_dd, avg_gap = best_for_params
                    print(f"  [PASS] {name}{params} best@TP={tp_pct}/SL={sl_pct} | "
                          f"avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% avg_gap={avg_gap:.1f}d")
                else:
                    print(f"  [fail] {name}{params} | no TP/SL in the small grid validated on both splits")

    print(f"\n{'='*100}\nAll validated (coin, indicator, params) combos, sorted by cadence:\n{'='*100}")
    for coin, name, params, tpsl, ret, dd, gap in sorted(all_pass, key=lambda r: r[-1] if r[-1] else 9999):
        tpsl_str = f" TP/SL={tpsl}" if tpsl else ""
        gap_str = f"{gap:.1f}d" if gap else "n/a"
        print(f"  {coin} {name}{params}{tpsl_str} | ret={ret:.2f}% dd={dd:.2f}% gap={gap_str}")

    return all_pass


if __name__ == "__main__":
    main()
