"""Round 9: does Round 8's validated config actually work at the timeframe the live bot
trades on? Rounds 2-8 all validated on Yahoo DAILY candles, but main.py trades Kraken
15-minute candles (config.INTERVAL_MINUTES=15) - an unaddressed gap between what was
proven and what's live. This closes it using Kraken's own official bulk historical data
(data/kraken_historical/extracted/XBTUSD_1.csv, genuine 1-minute OHLCV, Oct 2013-Jun 2026,
~4.9M rows) resampled to 15-minute (primary - matches the live bot) and tested natively at
1-minute (secondary/exploratory).

Sharp methodological point this round surfaces regardless of the pass/fail numbers:
config.FAST_MA_PERIOD=10 / SLOW_MA_PERIOD=30 are BAR counts, not day counts. On daily
bars (rounds 2-8) that's a 10-day/30-day crossover. Applied unchanged to 15-minute bars
(what main.py actually runs), it's a 2.5-hour/7.5-hour crossover - a fundamentally
different, much faster signal that happens to reuse the same numbers. This round tests
exactly what's live: the same bar-count periods, on the live resolution.

Uses a numpy-array-based simulate loop instead of experiments_wide_tp's DataFrame.iloc
version - necessary for performance at 1-minute resolution (multi-year splits run into
the millions of rows; .iloc-per-row would not finish in reasonable time).

Usage:
    python experiments_kraken_native.py
"""
import numpy as np

import config
from data_fetcher import load_kraken_csv_1min, resample_ohlc
from experiments_wide_tp import MAX_DRAWDOWN_PCT_REF, MIN_TRADES, baseline_signal_entry

CSV_PATH = "data/kraken_historical/extracted/XBTUSD_1.csv"

# Round 8's exact shipped config
STOP_LOSS_PCT = 1.0
TAKE_PROFIT_PCT = 20.0

SPLITS = [
    ("2018-01-01", "2021-12-31", "2022-01-01", "2025-12-31"),  # round 8 split 1
    ("2017-01-01", "2019-12-31", "2020-01-01", "2021-12-31"),  # round 8 split 2
    ("2013-10-06", "2017-12-31", "2018-01-01", "2019-12-31"),  # extended: full early history -> 2018 bear
    ("2019-01-01", "2022-12-31", "2023-01-01", "2026-06-30"),  # recent, capped at data end
]


def slice_dates(df, start, end):
    mask = (df["time"] >= start) & (df["time"] <= end)
    return df.loc[mask].reset_index(drop=True)


def simulate_fast(df, entry_col, tp_pct, sl_pct, starting_cash=10_000.0):
    """Numpy-array simulate loop - same fixed TP/SL/fee mechanics as
    experiments_wide_tp.simulate_wide_tp, rewritten for speed on million-row inputs."""
    n = len(df)
    if n < 2:
        return _empty_result(starting_cash)

    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    entries = df[entry_col].to_numpy()

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = starting_cash, 0.0, False, 0.0
    trade_returns = []
    trade_sides_prices = []  # (side, price) for win-rate cross-check
    underwater_pcts = []
    current_min = 0.0
    gap_events_pct = []
    equity_curve = np.empty(n - 1)

    for i in range(1, n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        price = c

        if not in_position:
            if entries[i]:
                entry_price = price
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                current_min = 0.0
                trade_sides_prices.append(("BUY", price))
        else:
            underwater = (l / entry_price - 1) * 100
            if underwater < current_min:
                current_min = underwater

            tp_price = entry_price * (1 + tp_pct / 100)
            sl_price = entry_price * (1 - sl_pct / 100)

            exit_price, exit_kind = None, None
            if l <= sl_price:
                exit_price, exit_kind = sl_price, "SL"
            elif h >= tp_price:
                exit_price, exit_kind = tp_price, "TP"

            if exit_price is not None:
                if exit_kind == "SL" and o < sl_price:
                    gap_events_pct.append((sl_price - o) / entry_price * 100)

                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                trade_returns.append((exit_price / entry_price - 1) * 100)
                trade_sides_prices.append(("SELL", exit_price))
                underwater_pcts.append(current_min)
                price = exit_price
                entry_price = 0.0

        equity_curve[i - 1] = cash + coins * price

    still_holding = in_position
    final_unrealized_pct = None
    if still_holding:
        final_unrealized_pct = (closes[-1] / entry_price - 1) * 100
        underwater_pcts.append(current_min)

    final_equity = equity_curve[-1] if len(equity_curve) else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100

    peak = starting_cash
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    buy_hold_return_pct = (closes[-1] / closes[0] - 1) * 100

    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r <= 0]
    n_trades = len(trade_returns)
    win_rate = len(wins) / n_trades if n_trades else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy_pct = win_rate * avg_win + (1 - win_rate) * avg_loss

    return {
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_dd,
        "buy_hold_return_pct": buy_hold_return_pct,
        "completed_trades": n_trades,
        "win_rate_pct": win_rate * 100,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "expectancy_pct": expectancy_pct,
        "still_holding_at_end": still_holding,
        "final_unrealized_pct": final_unrealized_pct,
        "max_underwater_pct": min(underwater_pcts) if underwater_pcts else 0.0,
        "gap_through_count": len(gap_events_pct),
        "gap_through_worst_pct": max(gap_events_pct) if gap_events_pct else 0.0,
    }


def _empty_result(starting_cash):
    return {
        "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "buy_hold_return_pct": 0.0,
        "completed_trades": 0, "win_rate_pct": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "expectancy_pct": 0.0, "still_holding_at_end": False, "final_unrealized_pct": None,
        "max_underwater_pct": 0.0, "gap_through_count": 0, "gap_through_worst_pct": 0.0,
    }


def _passed(test_stats):
    return (
        test_stats["completed_trades"] >= MIN_TRADES
        and test_stats["total_return_pct"] > 0
        and test_stats["max_drawdown_pct"] < MAX_DRAWDOWN_PCT_REF
    )


def run_resolution(df, label):
    print(f"\n{'=' * 100}\nResolution: {label} ({len(df):,} candles, "
          f"{df['time'].min()}..{df['time'].max()})\n{'=' * 100}")

    results = []
    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(SPLITS, 1):
        train_df = slice_dates(df, train_start, train_end)
        test_df = slice_dates(df, test_start, test_end)
        if len(train_df) < 50 or len(test_df) < 50:
            print(f"Split {split_idx}: insufficient data, skipping")
            continue

        train_signal_df = baseline_signal_entry(train_df)
        test_signal_df = baseline_signal_entry(test_df)

        train_stats = simulate_fast(train_signal_df, "entry", TAKE_PROFIT_PCT, STOP_LOSS_PCT)
        test_stats = simulate_fast(test_signal_df, "entry", TAKE_PROFIT_PCT, STOP_LOSS_PCT)
        passed = _passed(test_stats)
        results.append((split_idx, train_stats, test_stats, passed))

        tag = "PASS" if passed else "fail"
        print(
            f"[{tag}] Split {split_idx} (train {train_start}..{train_end} -> "
            f"test {test_start}..{test_end}, buy&hold {test_stats['buy_hold_return_pct']:+.2f}%)\n"
            f"    train_ret={train_stats['total_return_pct']:8.2f}% | "
            f"test_ret={test_stats['total_return_pct']:8.2f}% dd={test_stats['max_drawdown_pct']:6.2f}% "
            f"trades={test_stats['completed_trades']:6d} winrate={test_stats['win_rate_pct']:5.1f}% "
            f"expectancy={test_stats['expectancy_pct']:6.3f}% underwater={test_stats['max_underwater_pct']:7.2f}% "
            f"gaps={test_stats['gap_through_count']} (worst {test_stats['gap_through_worst_pct']:.3f}pp)"
        )

    all_pass = len(results) > 0 and all(p for _, _, _, p in results)
    print(f"\n{label}: {'VALIDATED across all splits' if all_pass else 'did NOT validate on every split'}")
    return results, all_pass


def main():
    print("Loading Kraken 1-minute historical data...")
    df1min = load_kraken_csv_1min(CSV_PATH)
    print(f"Loaded {len(df1min):,} 1-minute candles, {df1min['time'].min()}..{df1min['time'].max()}")

    df15min = resample_ohlc(df1min, 15)
    print(f"Resampled to {len(df15min):,} 15-minute candles")

    results_15, valid_15 = run_resolution(df15min, "15-MINUTE (matches live bot - PRIMARY)")
    results_1, valid_1 = run_resolution(df1min, "1-MINUTE (secondary/exploratory)")

    print(f"\n{'=' * 100}")
    print(f"15-minute (primary, matches live config.INTERVAL_MINUTES=15): "
          f"{'VALIDATED' if valid_15 else 'NOT validated'} across all {len(results_15)} splits")
    print(f"1-minute (secondary): {'VALIDATED' if valid_1 else 'NOT validated'} across all {len(results_1)} splits")

    return results_15, valid_15, results_1, valid_1


if __name__ == "__main__":
    main()
