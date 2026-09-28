"""Tight stop (1%), wide take-profit (3%-50%) - asymmetric risk/reward instead of round
2's symmetric ~2%/1.5% TP/SL. Directly addresses round 6's finding: removing the
stop-loss entirely produced 57-94% underwater drawdowns, the worst risk profile tried.
This keeps a genuinely tight stop (caps every loss near 1%) while giving the winner much
more room to run than the original design.

Tests two entry variants to isolate whether the entry signal matters at all under this
exit regime:
  - "signal": the project's baseline SMA-crossover + RSI entry (strategy.py's default
    params, not re-optimized here - this is the defensible baseline, not a new search).
  - "unconditional": buy immediately whenever flat, no filter - isolates the exit design's
    contribution from the entry signal's contribution.

Reports win rate AND per-trade expectancy (win_rate*avg_win - loss_rate*avg_loss), since
an asymmetric TP/SL is explicitly a bet that expectancy can be positive even at a low win
rate. Also checks whether the 1% stop actually holds on daily candles - a gap-down candle
can open below the stop price, filling worse than intended; this is measured directly
rather than assumed away.

Usage:
    python experiments_wide_tp.py
"""
import config
from data_fetcher import fetch_yahoo_history
from experiments import _stats
from experiments_regime import REGIME_SPLITS, slice_dates
from strategy import compute_rsi

SYMBOLS = {"ETH-USD": "ETH-USD", "BTC-USD": "BTC-USD"}  # ETH primary, BTC secondary
TP_GRID_PCT = [3, 5, 7, 10, 15, 20, 25, 30, 40, 50]
STOP_LOSS_PCT = 1.0
MIN_TRADES = 5
MAX_DRAWDOWN_PCT_REF = 40


def baseline_signal_entry(df):
    df = df.copy()
    sma_fast = df["close"].rolling(config.FAST_MA_PERIOD).mean()
    sma_slow = df["close"].rolling(config.SLOW_MA_PERIOD).mean()
    rsi = compute_rsi(df["close"], config.RSI_PERIOD)
    crossed_up = (sma_fast.shift(1) <= sma_slow.shift(1)) & (sma_fast > sma_slow)
    df["entry"] = crossed_up & (rsi < config.RSI_OVERBOUGHT)
    return df


def unconditional_entry(df):
    df = df.copy()
    df["entry"] = True
    return df


ENTRY_VARIANTS = {
    "signal": baseline_signal_entry,
    "unconditional": unconditional_entry,
}


def simulate_wide_tp(df, entry_col, tp_pct, sl_pct=STOP_LOSS_PCT, starting_cash=10_000.0):
    df = df.reset_index(drop=True)
    if len(df) < 2:
        return _empty_result(starting_cash)

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = starting_cash, 0.0, False, None
    trades = []
    trade_returns_pct = []
    equity_curve = []
    underwater_pcts = []
    current_trade_min_pct = 0.0
    gap_events_pct = []  # each entry: how many extra points below the stop the open gapped to

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        if not in_position:
            if bool(curr[entry_col]):
                entry_price = price
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                current_trade_min_pct = 0.0
                trades.append(("BUY", price))
        else:
            underwater_pct = (curr["low"] / entry_price - 1) * 100
            current_trade_min_pct = min(current_trade_min_pct, underwater_pct)

            tp_price = entry_price * (1 + tp_pct / 100)
            sl_price = entry_price * (1 - sl_pct / 100)

            exit_price, exit_kind = None, None
            if curr["low"] <= sl_price:
                exit_price, exit_kind = sl_price, "SL"
            elif curr["high"] >= tp_price:
                exit_price, exit_kind = tp_price, "TP"

            if exit_price is not None:
                if exit_kind == "SL" and curr["open"] < sl_price:
                    gap_events_pct.append((sl_price - curr["open"]) / entry_price * 100)

                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                trade_returns_pct.append((exit_price / entry_price - 1) * 100)
                trades.append(("SELL", exit_price))
                underwater_pcts.append(current_trade_min_pct)
                price = exit_price
                entry_price = None

        equity_curve.append(cash + coins * price)

    still_holding = in_position
    final_unrealized_pct = None
    if still_holding:
        final_unrealized_pct = (df["close"].iloc[-1] / entry_price - 1) * 100
        underwater_pcts.append(current_trade_min_pct)

    base = _stats(df, equity_curve, trades, starting_cash)

    wins = [r for r in trade_returns_pct if r > 0]
    losses = [r for r in trade_returns_pct if r <= 0]
    n = len(trade_returns_pct)
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0  # negative
    expectancy_pct = win_rate * avg_win + (1 - win_rate) * avg_loss

    base.update(
        completed_trades=n,
        win_rate_pct=win_rate * 100,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        expectancy_pct=expectancy_pct,
        still_holding_at_end=still_holding,
        final_unrealized_pct=final_unrealized_pct,
        max_underwater_pct=min(underwater_pcts) if underwater_pcts else 0.0,
        gap_through_count=len(gap_events_pct),
        gap_through_worst_pct=max(gap_events_pct) if gap_events_pct else 0.0,
    )
    return base


def _empty_result(starting_cash):
    return {
        "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "round_trips": 0,
        "buy_hold_return_pct": 0.0, "completed_trades": 0, "win_rate_pct": 0.0,
        "avg_win_pct": 0.0, "avg_loss_pct": 0.0, "expectancy_pct": 0.0,
        "still_holding_at_end": False, "final_unrealized_pct": None,
        "max_underwater_pct": 0.0, "gap_through_count": 0, "gap_through_worst_pct": 0.0,
    }


def _passed(test_stats):
    return (
        test_stats["completed_trades"] >= MIN_TRADES
        and test_stats["total_return_pct"] > 0
        and test_stats["max_drawdown_pct"] < MAX_DRAWDOWN_PCT_REF
    )


def main():
    all_gap_events = 0
    all_gap_worst = 0.0
    # (entry_name, symbol, tp_pct) -> list of (split_idx, passed, beat_bh)
    by_key = {}

    for entry_name, entry_fn in ENTRY_VARIANTS.items():
        for symbol in SYMBOLS:
            df = fetch_yahoo_history(symbol, range_="10y", interval="1d")
            print(f"\n### entry={entry_name} | {symbol} ({len(df)} candles, "
                  f"{df['time'].min().date()}..{df['time'].max().date()}) ###")

            for split_idx, (train_start, train_end, test_start, test_end) in enumerate(REGIME_SPLITS, 1):
                train_df = slice_dates(df, train_start, train_end)
                test_df = slice_dates(df, test_start, test_end)
                if len(train_df) < 100 or len(test_df) < 100:
                    print(f"  Split {split_idx}: insufficient data, skipping")
                    continue

                train_signal_df = entry_fn(train_df)
                test_signal_df = entry_fn(test_df)

                print(f"\n  Split {split_idx}: train {train_start}..{train_end} -> test {test_start}..{test_end}")
                print(
                    f"  {'tp':>4} | {'train_ret':>9} | {'test_ret':>8} {'test_bh':>8} "
                    f"{'dd':>6} {'trades':>6} {'winrate':>7} {'expect':>7} {'uw':>7} {'gaps':>5}"
                )

                for tp_pct in TP_GRID_PCT:
                    train_stats = simulate_wide_tp(train_signal_df, "entry", tp_pct)
                    test_stats = simulate_wide_tp(test_signal_df, "entry", tp_pct)
                    passed = _passed(test_stats)
                    beat_bh = test_stats["total_return_pct"] > test_stats["buy_hold_return_pct"]

                    key = (entry_name, symbol, tp_pct)
                    by_key.setdefault(key, []).append((split_idx, passed, beat_bh))

                    all_gap_events += test_stats["gap_through_count"]
                    all_gap_worst = max(all_gap_worst, test_stats["gap_through_worst_pct"])

                    tag = "PASS" if passed else "fail"
                    print(
                        f"  [{tag}] {tp_pct:>3}% | {train_stats['total_return_pct']:8.2f}% | "
                        f"{test_stats['total_return_pct']:7.2f}% {test_stats['buy_hold_return_pct']:7.2f}% "
                        f"{test_stats['max_drawdown_pct']:5.2f}% {test_stats['completed_trades']:6d} "
                        f"{test_stats['win_rate_pct']:6.1f}% {test_stats['expectancy_pct']:6.2f}% "
                        f"{test_stats['max_underwater_pct']:6.2f}% {test_stats['gap_through_count']:5d}"
                    )

    print("\n" + "=" * 100)
    print(f"Stop-loss gap-through check (SL triggered but candle opened below the stop "
          f"price): {all_gap_events} occurrence(s) across all runs, worst case "
          f"{all_gap_worst:.2f} extra percentage point(s) of loss beyond the intended 1% "
          f"stop.")

    validated = [key for key, results in by_key.items() if results and all(p for _, p, _ in results)]
    if validated:
        print(f"\n{len(validated)} (entry, symbol, tp%) combo(s) passed on every regime split:")
        for k in validated:
            print(" ", k)
    else:
        print(
            "\nNo (entry, symbol, take-profit%) combination passed the bar on every regime "
            "split. See the win-rate/expectancy columns above for why."
        )

    return by_key, validated


if __name__ == "__main__":
    main()
