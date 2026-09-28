""""Just wait for the target" — unconditional entry, no stop-loss, exit only on profit
target. Single-asset ETH only. A fundamentally different risk profile than every prior
round: rounds 1-5 all paired an entry signal with SOME loss-realizing exit (tight SL,
trailing stop, reverse-signal). This has none — a losing position is never closed, only
held until it either recovers to the target or the test window ends.

Mechanics: whenever flat, buy immediately (no signal, no filter). Hold with no stop-loss
and no time limit. Exit the instant unrealized profit reaches TARGET_PCT. Immediately
re-buy. Repeat.

Because there's no loss-realizing exit, the normal equity-curve drawdown stat isn't the
real risk here — a single open position can be deeply underwater for a long time while
the equity curve (marked to market) just looks like a dip, not a loss. This tracks
per-trade "max underwater %" (using each candle's low vs entry price) separately, and
reports it as the headline risk number, not a footnote.

Validation: same regime-split discipline as every round (train/test on structurally
different multi-year ETH-USD windows from fetch_yahoo_history, sanity-check on a second
split). Because there's only one real parameter (the target %), this reports the full
range of targets on both train and test rather than reducing to a "top 5" - the point is
to show whether the picture changes a lot across targets, not to pick a headline number.

Usage:
    python experiments_patient.py
"""
import config
from data_fetcher import fetch_yahoo_history
from experiments import _stats
from experiments_regime import REGIME_SPLITS, slice_dates

TARGET_GRID_PCT = [5, 10, 15, 20, 25, 30, 40, 50]
MIN_COMPLETED_TRADES = 5
MAX_UNDERWATER_PCT_REF = 40  # same reference cap used throughout this project


def simulate_patient_target(df, target_pct, starting_cash=10_000.0):
    df = df.dropna().reset_index(drop=True)
    if len(df) < 2:
        base = _stats(df, [], [], starting_cash) if len(df) else {
            "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "round_trips": 0,
            "win_rate_pct": 0.0, "buy_hold_return_pct": 0.0,
        }
        base.update(completed_trades=0, still_holding_at_end=False,
                     final_unrealized_pct=None, max_underwater_pct=0.0, avg_underwater_pct=0.0)
        return base

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = starting_cash, 0.0, False, None
    trades = []
    equity_curve = []
    underwater_pcts = []  # one entry per trade (completed or still-open): its worst drawdown while held
    current_trade_min_pct = 0.0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        if not in_position:
            entry_price = price
            coins = (cash * (1 - fee_rate)) / price
            cash = 0.0
            in_position = True
            current_trade_min_pct = 0.0
            trades.append(("BUY", price))
        else:
            underwater_pct = (curr["low"] / entry_price - 1) * 100
            current_trade_min_pct = min(current_trade_min_pct, underwater_pct)

            target_price = entry_price * (1 + target_pct / 100)
            if curr["high"] >= target_price:
                exit_price = target_price
                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                trades.append(("SELL", exit_price))
                underwater_pcts.append(current_trade_min_pct)
                price = exit_price
                entry_price = None

        equity_curve.append(cash + coins * price)

    still_holding_at_end = in_position
    final_unrealized_pct = None
    if still_holding_at_end:
        final_unrealized_pct = (df["close"].iloc[-1] / entry_price - 1) * 100
        underwater_pcts.append(current_trade_min_pct)

    stats = _stats(df, equity_curve, trades, starting_cash)
    stats["completed_trades"] = len(trades) // 2
    stats["still_holding_at_end"] = still_holding_at_end
    stats["final_unrealized_pct"] = final_unrealized_pct
    stats["max_underwater_pct"] = min(underwater_pcts) if underwater_pcts else 0.0
    stats["avg_underwater_pct"] = (
        sum(underwater_pcts) / len(underwater_pcts) if underwater_pcts else 0.0
    )
    return stats


def _passed(train_stats, test_stats):
    return (
        test_stats["completed_trades"] >= MIN_COMPLETED_TRADES
        and test_stats["total_return_pct"] > test_stats["buy_hold_return_pct"]
        and test_stats["total_return_pct"] > 0
        and test_stats["max_underwater_pct"] > -MAX_UNDERWATER_PCT_REF  # e.g. -40 means 40% underwater cap
    )


def main():
    yahoo_symbol = "ETH-USD"
    df = fetch_yahoo_history(yahoo_symbol, range_="10y", interval="1d")
    print(f"ETH-USD history: {len(df)} candles, {df['time'].min().date()} to {df['time'].max().date()}")

    # target_pct -> list of (split_idx, train_stats, test_stats, passed)
    by_target = {t: [] for t in TARGET_GRID_PCT}

    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(REGIME_SPLITS, 1):
        train_df = slice_dates(df, train_start, train_end)
        test_df = slice_dates(df, test_start, test_end)
        if len(train_df) < 100 or len(test_df) < 100:
            print(f"Split {split_idx}: insufficient data, skipping")
            continue

        print(f"\nSplit {split_idx}: train {train_start}..{train_end} -> test {test_start}..{test_end}")
        print(
            f"{'target':>7} | {'train_ret':>10} {'train_uw':>9} | "
            f"{'test_ret':>9} {'test_bh':>9} {'test_uw':>9} {'test_trades':>11} "
            f"{'holding@end':>11} {'final_unreal':>12}"
        )

        for target_pct in TARGET_GRID_PCT:
            train_stats = simulate_patient_target(train_df, target_pct)
            test_stats = simulate_patient_target(test_df, target_pct)
            passed = _passed(train_stats, test_stats)
            by_target[target_pct].append((split_idx, train_stats, test_stats, passed))

            holding_str = "yes" if test_stats["still_holding_at_end"] else "no"
            final_unreal = test_stats["final_unrealized_pct"]
            final_unreal_str = f"{final_unreal:+.2f}%" if final_unreal is not None else "-"
            tag = "PASS" if passed else "fail"
            print(
                f"[{tag}] {target_pct:>4}% | "
                f"{train_stats['total_return_pct']:9.2f}% {train_stats['max_underwater_pct']:8.2f}% | "
                f"{test_stats['total_return_pct']:8.2f}% {test_stats['buy_hold_return_pct']:8.2f}% "
                f"{test_stats['max_underwater_pct']:8.2f}% {test_stats['completed_trades']:11d} "
                f"{holding_str:>11} {final_unreal_str:>12}"
            )

    print("\n" + "=" * 100)
    validated_targets = [
        t for t, results in by_target.items()
        if results and all(passed for _, _, _, passed in results)
    ]
    if validated_targets:
        print(f"Target(s) that passed on EVERY regime split: {validated_targets}")
    else:
        print(
            "No target percentage passed on every regime split. Honest finding: unconditional "
            "entry + no-stop-loss + patient profit target does not show a validated edge over "
            "ETH buy-and-hold under this project's bar, and/or carries underwater drawdown "
            "beyond the reference cap. See the max_underwater_pct column above for the real "
            "risk number, independent of whether the average-case return looked good."
        )

    return by_target, validated_targets


if __name__ == "__main__":
    main()
