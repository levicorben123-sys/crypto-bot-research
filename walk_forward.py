"""Walk-forward parameter validation for the SMA/RSI strategy.

Grid-searches strategy parameters on a TRAIN slice of history, then re-evaluates only
the best train-set candidates on a held-out TEST slice they never saw. A parameter set
only means anything if it also works out-of-sample; anything that looks good on train
but falls apart on test is overfit noise, not an edge. Also reports buy-and-hold return
as a baseline — a strategy that can't beat holding isn't worth its complexity or fees.

Usage:
    python walk_forward.py [PAIR] [INTERVAL_MINUTES]
"""
import sys

import config
from backtest import run_backtest
from data_fetcher import fetch_ohlc

FAST_GRID = [5, 8, 10, 12, 15, 20]
SLOW_GRID = [20, 30, 40, 50, 75, 100]
RSI_OB_GRID = [65, 70, 75]
RSI_OS_GRID = [25, 30, 35]

TRAIN_FRACTION = 0.7


def evaluate(df, fast, slow, rsi_ob, rsi_os):
    config.FAST_MA_PERIOD = fast
    config.SLOW_MA_PERIOD = slow
    config.RSI_OVERBOUGHT = rsi_ob
    config.RSI_OVERSOLD = rsi_os
    _, _, _, stats = run_backtest(df, verbose=False)
    return stats


def main():
    pair = sys.argv[1] if len(sys.argv) > 1 else config.PAIR
    interval_minutes = int(sys.argv[2]) if len(sys.argv) > 2 else config.INTERVAL_MINUTES

    print(f"Fetching {pair} @ {interval_minutes}min...")
    df = fetch_ohlc(pair, interval_minutes)

    split = int(len(df) * TRAIN_FRACTION)
    train_df = df.iloc[:split].reset_index(drop=True)
    test_df = df.iloc[split:].reset_index(drop=True)

    print(f"Train: {len(train_df)} candles | Test: {len(test_df)} candles\n")

    results = []
    for fast in FAST_GRID:
        for slow in SLOW_GRID:
            if fast >= slow:
                continue
            for rsi_ob in RSI_OB_GRID:
                for rsi_os in RSI_OS_GRID:
                    train_stats = evaluate(train_df, fast, slow, rsi_ob, rsi_os)
                    results.append((fast, slow, rsi_ob, rsi_os, train_stats))

    results.sort(key=lambda r: r[4]["total_return_pct"], reverse=True)
    top = results[:10]

    print(f"{'fast':>4} {'slow':>4} {'ob':>3} {'os':>3} | {'train_ret':>9} {'train_dd':>8} {'train_wr':>8} | {'test_ret':>8} {'test_dd':>7} {'test_wr':>7} {'test_trades':>11}")
    for fast, slow, rsi_ob, rsi_os, train_stats in top:
        test_stats = evaluate(test_df, fast, slow, rsi_ob, rsi_os)
        print(
            f"{fast:>4} {slow:>4} {rsi_ob:>3} {rsi_os:>3} | "
            f"{train_stats['total_return_pct']:>8.2f}% {train_stats['max_drawdown_pct']:>7.2f}% {train_stats['win_rate_pct']:>7.2f}% | "
            f"{test_stats['total_return_pct']:>7.2f}% {test_stats['max_drawdown_pct']:>6.2f}% {test_stats['win_rate_pct']:>6.2f}% {test_stats['round_trips']:>11}"
        )

    buy_hold_test = evaluate(test_df, *top[0][:4])["buy_hold_return_pct"]
    print(f"\nBuy & hold return over test window: {buy_hold_test:.2f}%")
    print(
        "\nA config only means something if its TEST return holds up (positive, beats "
        "buy&hold, reasonable drawdown) despite being picked on TRAIN data alone. If the "
        "top train performers all collapse on test, the strategy has no real edge on this "
        "pair/interval — that's a valid, useful finding, not a failure to try harder."
    )


if __name__ == "__main__":
    main()
