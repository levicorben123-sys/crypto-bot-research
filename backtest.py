"""Backtest the SMA-crossover + RSI strategy against Kraken historical OHLC data.

No API keys needed - uses Kraken's public market data endpoint. Kraken only retains
~720 candles per pair/interval, so total history depends on interval size
(e.g. 15m -> ~7.5 days, 240m/4h -> ~120 days, 1440m/1d -> ~2 years).

Usage:
    python backtest.py [PAIR] [INTERVAL_MINUTES]
    python backtest.py XBTUSD 240
"""
import sys

import config
from data_fetcher import fetch_ohlc
from strategy import add_indicators


def run_backtest(df, starting_cash: float = None, verbose: bool = True):
    starting_cash = starting_cash if starting_cash is not None else config.STARTING_CASH
    df = add_indicators(df).dropna().reset_index(drop=True)

    if len(df) < 2:
        stats = {
            "span_days": 0.0, "starting_cash": starting_cash, "final_equity": starting_cash,
            "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "round_trips": 0,
            "win_rate_pct": 0.0, "buy_hold_return_pct": 0.0,
        }
        return df, [], [], stats

    cash = starting_cash
    coins = 0.0
    in_position = False
    entry_price = None
    trades = []
    equity_curve = []
    fee_rate = config.FEE_PCT / 100

    for i in range(1, len(df)):
        prev, curr = df.iloc[i - 1], df.iloc[i]
        price = curr["close"]

        if not in_position:
            crossed_up = prev["sma_fast"] <= prev["sma_slow"] and curr["sma_fast"] > curr["sma_slow"]
            if crossed_up and curr["rsi"] < config.RSI_OVERBOUGHT:
                entry_price = price
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                trades.append(("BUY", curr["time"], price))
        else:
            # Use the candle's high/low (not just close) to check whether the target or
            # stop would have been hit intra-candle, then exit at that trigger price.
            tp_price = entry_price * (1 + config.TAKE_PROFIT_PCT / 100)
            sl_price = entry_price * (1 - config.STOP_LOSS_PCT / 100)

            exit_price = None
            if curr["low"] <= sl_price:
                exit_price = sl_price  # stop-loss checked first: assume the worse fill if both hit same candle
            elif curr["high"] >= tp_price:
                exit_price = tp_price

            if exit_price is not None:
                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                entry_price = None
                trades.append(("SELL", curr["time"], exit_price))
                price = exit_price

        equity_curve.append(cash + coins * price)

    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100

    peak = starting_cash
    max_drawdown_pct = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        drawdown = (peak - eq) / peak * 100
        max_drawdown_pct = max(max_drawdown_pct, drawdown)

    round_trips = len(trades) // 2
    wins = 0
    for j in range(0, len(trades) - 1, 2):
        buy_price, sell_price = trades[j][2], trades[j + 1][2]
        if sell_price > buy_price:
            wins += 1
    win_rate_pct = (wins / round_trips * 100) if round_trips else 0.0

    span_days = (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds() / 86400
    buy_hold_return_pct = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100

    stats = {
        "span_days": span_days,
        "starting_cash": starting_cash,
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "round_trips": round_trips,
        "win_rate_pct": win_rate_pct,
        "buy_hold_return_pct": buy_hold_return_pct,
    }

    if verbose:
        print(f"Data span:         {span_days:.1f} days ({len(df)} candles)")
        print(f"Starting cash:     {starting_cash:,.2f}")
        print(f"Final equity:      {final_equity:,.2f}")
        print(f"Total return:      {total_return_pct:.2f}%")
        print(f"Buy & hold return: {buy_hold_return_pct:.2f}%")
        print(f"Max drawdown:      {max_drawdown_pct:.2f}%")
        print(f"Round-trip trades: {round_trips}")
        print(f"Win rate:          {win_rate_pct:.2f}%")

    return df, equity_curve, trades, stats


def plot_equity_curve(df, equity_curve, out_path="backtest_equity.png"):
    import matplotlib.pyplot as plt

    plt.figure(figsize=(10, 5))
    plt.plot(df["time"].iloc[1:], equity_curve)
    plt.title("Equity Curve")
    plt.xlabel("Time")
    plt.ylabel("Portfolio Value")
    plt.tight_layout()
    plt.savefig(out_path)
    print(f"Saved chart to {out_path}")


if __name__ == "__main__":
    pair = sys.argv[1] if len(sys.argv) > 1 else config.PAIR
    interval_minutes = int(sys.argv[2]) if len(sys.argv) > 2 else config.INTERVAL_MINUTES

    print(f"Backtesting {pair} at {interval_minutes}min candles...\n")

    hist_df = fetch_ohlc(pair, interval_minutes)
    result_df, equity_curve, trades, stats = run_backtest(hist_df)
    plot_equity_curve(result_df, equity_curve)
