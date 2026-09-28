"""Round 11: event-based vs state-based entry, same already-validated exit (1% SL / 20%
TP from rounds 8-10), same 4 independent regime splits, Kraken native daily data.

Event-based (the current live default, strategy.py's generate_signal / baseline_signal_entry
in experiments_wide_tp.py): buy only on the specific day the fast SMA crosses above the
slow SMA (prev bar fast<=slow, curr bar fast>slow), gated by RSI < overbought.

State-based (new, this round): buy whenever flat AND fast > slow AND RSI < overbought -
no requirement that today is a fresh cross. Not the same as round 7's "unconditional
entry" (no signal at all, already shown to be a disaster) - this still requires the real
trend-confirmation signal, just doesn't require the specific crossing moment. The
simulate engine already only acts on the entry column while flat, so a state-based entry
column that stays True for many consecutive days still only buys once per position - but
critically, if the bot exits (TP or SL) while the trend state is still bullish, it can
re-buy on the very next bar rather than waiting for a fresh cross. This round tracks
entry/exit dates specifically to measure that re-entry behavior, not just aggregate
stats.

Usage:
    python experiments_entry_style.py
"""
import config
from data_fetcher import load_kraken_csv_1min
from experiments_multisplit_kraken import SPLITS
from experiments_multisplit import slice_dates
from experiments_wide_tp import baseline_signal_entry
from strategy import compute_rsi

CSV_PATH = "data/kraken_historical/extracted/XBTUSD_1440.csv"
STOP_LOSS_PCT = 1.0
TAKE_PROFIT_PCT = 20.0
MIN_TRADES = 5
MAX_DRAWDOWN_PCT_REF = 40


def state_based_entry(df):
    df = df.copy()
    sma_fast = df["close"].rolling(config.FAST_MA_PERIOD).mean()
    sma_slow = df["close"].rolling(config.SLOW_MA_PERIOD).mean()
    rsi = compute_rsi(df["close"], config.RSI_PERIOD)
    df["entry"] = (sma_fast > sma_slow) & (rsi < config.RSI_OVERBOUGHT)
    return df


ENTRY_STYLES = {
    "event-based (current live default)": baseline_signal_entry,
    "state-based (new, this round)": state_based_entry,
}


def simulate_with_dates(df, entry_col, tp_pct, sl_pct, starting_cash=10_000.0):
    """Same fixed TP/SL/fee mechanics as experiments_wide_tp.simulate_wide_tp, plus
    entry/exit date tracking to measure re-entry latency after a stop-out or take-profit."""
    df = df.reset_index(drop=True)
    if len(df) < 2:
        return _empty_result(starting_cash)

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = starting_cash, 0.0, False, None
    trade_returns_pct = []
    trade_log = []  # (entry_date, exit_date, days_since_prior_exit or None)
    equity_curve = []
    underwater_pcts = []
    current_trade_min_pct = 0.0
    gap_events_pct = []
    last_exit_date = None

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
                days_since_prior_exit = (
                    (curr["time"] - last_exit_date).days if last_exit_date is not None else None
                )
                trade_log.append({"entry_date": curr["time"], "days_since_prior_exit": days_since_prior_exit})
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
                trade_log[-1]["exit_date"] = curr["time"]
                trade_log[-1]["exit_kind"] = exit_kind
                underwater_pcts.append(current_trade_min_pct)
                last_exit_date = curr["time"]
                price = exit_price
                entry_price = None

        equity_curve.append(cash + coins * price)

    still_holding = in_position
    final_unrealized_pct = None
    if still_holding:
        final_unrealized_pct = (df["close"].iloc[-1] / entry_price - 1) * 100
        underwater_pcts.append(current_trade_min_pct)

    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100

    peak = starting_cash
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)

    buy_hold_return_pct = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100

    wins = [r for r in trade_returns_pct if r > 0]
    losses = [r for r in trade_returns_pct if r <= 0]
    n = len(trade_returns_pct)
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy_pct = win_rate * avg_win + (1 - win_rate) * avg_loss

    immediate_reentries = sum(
        1 for t in trade_log if t.get("days_since_prior_exit") is not None and t["days_since_prior_exit"] <= 1
    )
    reentry_gaps = [t["days_since_prior_exit"] for t in trade_log if t.get("days_since_prior_exit") is not None]
    avg_days_between_trades = sum(reentry_gaps) / len(reentry_gaps) if reentry_gaps else None

    return {
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_dd,
        "buy_hold_return_pct": buy_hold_return_pct,
        "completed_trades": n,
        "win_rate_pct": win_rate * 100,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "expectancy_pct": expectancy_pct,
        "still_holding_at_end": still_holding,
        "final_unrealized_pct": final_unrealized_pct,
        "max_underwater_pct": min(underwater_pcts) if underwater_pcts else 0.0,
        "gap_through_count": len(gap_events_pct),
        "gap_through_worst_pct": max(gap_events_pct) if gap_events_pct else 0.0,
        "immediate_reentries": immediate_reentries,
        "avg_days_between_trades": avg_days_between_trades,
    }


def _empty_result(starting_cash):
    return {
        "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "buy_hold_return_pct": 0.0,
        "completed_trades": 0, "win_rate_pct": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "expectancy_pct": 0.0, "still_holding_at_end": False, "final_unrealized_pct": None,
        "max_underwater_pct": 0.0, "gap_through_count": 0, "gap_through_worst_pct": 0.0,
        "immediate_reentries": 0, "avg_days_between_trades": None,
    }


def _passed(test_stats):
    return (
        test_stats["completed_trades"] >= MIN_TRADES
        and test_stats["total_return_pct"] > 0
        and test_stats["max_drawdown_pct"] < MAX_DRAWDOWN_PCT_REF
    )


def main():
    df = load_kraken_csv_1min(CSV_PATH)
    print(f"Kraken native daily BTC/USD: {len(df)} candles, "
          f"{df['time'].min().date()}..{df['time'].max().date()}\n")

    by_style = {name: [] for name in ENTRY_STYLES}

    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"Split {split_idx}: insufficient data, skipping")
            continue

        test_bh = (test_df["close"].iloc[-1] / test_df["close"].iloc[0] - 1) * 100
        print(f"=== Split {split_idx}: test {test_start}..{test_end} (buy&hold {test_bh:+.2f}%) ===")

        for style_name, build_fn in ENTRY_STYLES.items():
            signal_df = build_fn(test_df)
            stats = simulate_with_dates(signal_df, "entry", TAKE_PROFIT_PCT, STOP_LOSS_PCT)
            passed = _passed(stats)
            by_style[style_name].append((split_idx, stats, passed))

            avg_gap_str = f"{stats['avg_days_between_trades']:.1f}d" if stats["avg_days_between_trades"] is not None else "n/a"
            tag = "PASS" if passed else "fail"
            print(
                f"  [{tag}] {style_name:38s} | ret={stats['total_return_pct']:8.2f}% "
                f"dd={stats['max_drawdown_pct']:6.2f}% trades={stats['completed_trades']:4d} "
                f"winrate={stats['win_rate_pct']:5.1f}% expect={stats['expectancy_pct']:6.2f}% "
                f"immediate_reentries={stats['immediate_reentries']:3d} avg_gap={avg_gap_str}"
            )
        print()

    print("=" * 100)
    n_splits = len([s for s in SPLITS])
    for style_name, results in by_style.items():
        all_pass = len(results) > 0 and all(p for _, _, p in results)
        total_trades = sum(s["completed_trades"] for _, s, _ in results)
        total_reentries = sum(s["immediate_reentries"] for _, s, _ in results)
        print(f"{style_name}: {'VALIDATED across all splits' if all_pass else 'did NOT validate on every split'} "
              f"| total trades across splits: {total_trades} | immediate re-entries (<=1 day after exit): {total_reentries}")

    return by_style


if __name__ == "__main__":
    main()
