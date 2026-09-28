"""Trend-following exit styles, paired with trend-following entries, validated by regime.

Prior rounds (experiments.py, experiments_regime.py) used a single exit rule everywhere:
a tight fixed take-profit/stop-loss (config.TAKE_PROFIT_PCT/STOP_LOSS_PCT, ~2%/1.5%).
That caps every winning trade at ~2%, so by construction it can never capture a
multi-year trend - it was never a fair test of whether trend-following entries have edge,
only proof that a scalp-sized exit can't out-run a historic rally.

This tests two genuine trend-following exit styles instead, paired only with entries that
make sense for them (mean-reversion entries stay on the tight TP/SL from experiments.py -
that pairing was already fairly tested):

  - Trailing stop: no profit ceiling, exit when price drops TRAILING_STOP_PCT off the
    highest high since entry. Paired with: trend-filtered SMA crossover, volatility
    breakout, momentum/ROC.
  - Reverse-signal exit: exit when the fast/slow MA crosses back down (the bot's
    original pre-TP/SL design). Paired with: trend-filtered SMA crossover only, since
    that's the only family with a natural reverse-signal.

Same validation discipline as before: grid-search on TRAIN, re-check only top candidates
on held-out TEST, split by market regime (via experiments_regime.REGIME_SPLITS), across
BTC-USD and ETH-USD. A trend-following strategy has structurally deeper drawdowns than a
tight-stop one, so the max-drawdown bar is relaxed to <40% (vs <25% for the tight-exit
families) - that's a property of the style, not the experiment cutting it slack on returns.

Usage:
    python experiments_trend.py
"""
import config
from data_fetcher import fetch_yahoo_history
from experiments import (
    FAMILIES, _empty_stats, _stats, momentum_roc, trend_filtered_crossover,
    volatility_breakout,
)
from experiments_regime import REGIME_SPLITS, SYMBOLS, slice_dates

MIN_TEST_TRADES = 2
MAX_DRAWDOWN_PCT = 40
TRAILING_STOP_GRID = [3.0, 5.0, 8.0, 12.0]


# ---------------------------------------------------------------------------
# Simulation engines
# ---------------------------------------------------------------------------

def simulate_trailing(df, entry_col, trailing_stop_pct, starting_cash=10_000.0):
    df = df.dropna().reset_index(drop=True)
    if len(df) < 2:
        return _empty_stats(starting_cash)

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position = starting_cash, 0.0, False
    running_high = None
    trades = []
    equity_curve = []

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        if not in_position:
            if bool(curr[entry_col]):
                running_high = curr["high"]
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                trades.append(("BUY", price))
        else:
            running_high = max(running_high, curr["high"])
            stop_price = running_high * (1 - trailing_stop_pct / 100)
            if curr["low"] <= stop_price:
                exit_price = stop_price
                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                running_high = None
                trades.append(("SELL", exit_price))
                price = exit_price

        equity_curve.append(cash + coins * price)

    return _stats(df, equity_curve, trades, starting_cash)


def simulate_reverse_signal(df, entry_col, exit_col, starting_cash=10_000.0):
    df = df.dropna().reset_index(drop=True)
    if len(df) < 2:
        return _empty_stats(starting_cash)

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position = starting_cash, 0.0, False
    trades = []
    equity_curve = []

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        if not in_position:
            if bool(curr[entry_col]):
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                trades.append(("BUY", price))
        else:
            if bool(curr[exit_col]):
                proceeds = coins * price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                trades.append(("SELL", price))

        equity_curve.append(cash + coins * price)

    return _stats(df, equity_curve, trades, starting_cash)


# ---------------------------------------------------------------------------
# Entry+exit builders
# ---------------------------------------------------------------------------

def trend_filtered_crossover_with_exit(df, fast, slow, trend_period):
    df = trend_filtered_crossover(df, fast, slow, trend_period)
    crossed_down = (df["sma_fast"].shift(1) >= df["sma_slow"].shift(1)) & (df["sma_fast"] < df["sma_slow"])
    df["exit"] = crossed_down
    return df


TREND_SETUPS = {
    "crossover+reverse_signal": {
        "build": trend_filtered_crossover_with_exit,
        "grid": FAMILIES["trend_filtered_crossover"]["grid"],
        "engine": "reverse_signal",
    },
    "crossover+trailing_stop": {
        "build": trend_filtered_crossover,
        "grid": FAMILIES["trend_filtered_crossover"]["grid"],
        "engine": "trailing",
    },
    "breakout+trailing_stop": {
        "build": volatility_breakout,
        "grid": FAMILIES["volatility_breakout"]["grid"],
        "engine": "trailing",
    },
    "momentum+trailing_stop": {
        "build": momentum_roc,
        "grid": FAMILIES["momentum_roc"]["grid"],
        "engine": "trailing",
    },
}


def _passed(test_stats):
    return (
        test_stats["round_trips"] >= MIN_TEST_TRADES
        and test_stats["total_return_pct"] > test_stats["buy_hold_return_pct"]
        and test_stats["total_return_pct"] > 0
        and test_stats["max_drawdown_pct"] < MAX_DRAWDOWN_PCT
    )


def run_setup(name, spec, train_df, test_df):
    build, grid, engine = spec["build"], spec["grid"], spec["engine"]

    results = []
    for params in grid:
        train_signal_df = build(train_df, **params)
        if engine == "reverse_signal":
            stats = simulate_reverse_signal(train_signal_df, "entry", "exit")
            results.append((params, None, stats))
        else:
            for stop_pct in TRAILING_STOP_GRID:
                stats = simulate_trailing(train_signal_df, "entry", stop_pct)
                results.append((params, stop_pct, stats))

    results.sort(key=lambda r: r[2]["total_return_pct"], reverse=True)
    top = results[:5]

    validated = []
    for params, stop_pct, train_stats in top:
        test_signal_df = build(test_df, **params)
        if engine == "reverse_signal":
            test_stats = simulate_reverse_signal(test_signal_df, "entry", "exit")
        else:
            test_stats = simulate_trailing(test_signal_df, "entry", stop_pct)
        validated.append((params, stop_pct, train_stats, test_stats, _passed(test_stats)))

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

            for setup_name, spec in TREND_SETUPS.items():
                validated = run_setup(setup_name, spec, train_df, test_df)
                params, stop_pct, train_stats, test_stats, passed = validated[0]
                tag = "PASS" if passed else "fail"
                stop_str = f"stop={stop_pct}" if stop_pct is not None else "exit=reverse_signal"
                print(
                    f"    [{tag}] {setup_name:28s} {stop_str} {params} | "
                    f"train_ret={train_stats['total_return_pct']:8.2f}% | "
                    f"test_ret={test_stats['total_return_pct']:8.2f}% test_bh={test_stats['buy_hold_return_pct']:8.2f}% "
                    f"test_dd={test_stats['max_drawdown_pct']:6.2f}% test_trades={test_stats['round_trips']}"
                )
                if passed:
                    all_winners.append((pair, split_idx, setup_name, params, stop_pct, test_stats))

    print("\n" + "=" * 80)
    if all_winners:
        print(f"{len(all_winners)} config(s) validated across a real regime split:")
        for w in all_winners:
            print(" ", w)
    else:
        print(
            "No trend-following setup validated across any regime split. Honest finding: "
            "even with room to let winners run (trailing stop / reverse-signal exit, no "
            "profit ceiling), these entry signals don't show a real edge over buy-and-hold."
        )

    return all_winners


if __name__ == "__main__":
    main()
