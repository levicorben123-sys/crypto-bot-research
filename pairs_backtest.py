"""Backtest the BTC/ETH pairs mean-reversion strategy.

Simplification, stated plainly: this does NOT model margin/borrow cost for the short
leg, exchange margin requirements, or borrow availability - real short-selling has
ongoing costs this ignores. Treat results as an upper bound, not a realistic P&L.

Position sizing: each open trade risks notional = starting_cash / 2 per leg (fixed,
non-compounding) so both legs are dollar-neutral. Fees (config.FEE_PCT) are charged on
both legs, on both open and close (4 fee events per round trip).
"""
import config
from pairs_strategy import compute_ratio_zscore


def run_pairs_backtest(
    merged_df,
    window: int = 45,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    stop_z: float = 4.0,
    starting_cash: float = None,
    verbose: bool = False,
):
    starting_cash = starting_cash if starting_cash is not None else config.STARTING_CASH
    fee_rate = config.FEE_PCT / 100

    df = compute_ratio_zscore(merged_df, window).dropna().reset_index(drop=True)

    if len(df) < 5:
        return {
            "final_equity": starting_cash, "total_return_pct": 0.0, "max_drawdown_pct": 0.0,
            "round_trips": 0, "win_rate_pct": 0.0, "trades": [], "equity_curve": [],
        }

    notional = starting_cash / 2
    realized_pnl = 0.0
    position = None  # None, "long_a_short_b", "short_a_long_b"
    entry_price_a = entry_price_b = None
    trades = []
    equity_curve = []

    for i in range(len(df)):
        row = df.iloc[i]
        z = row["zscore"]
        price_a, price_b = row["close_a"], row["close_b"]
        unrealized = 0.0

        if position is None:
            if z > entry_z:
                position = "short_a_long_b"
                entry_price_a, entry_price_b = price_a, price_b
                realized_pnl -= notional * fee_rate * 2
                trades.append({"side": "OPEN short_a_long_b", "time": row["time"], "z": z})
            elif z < -entry_z:
                position = "long_a_short_b"
                entry_price_a, entry_price_b = price_a, price_b
                realized_pnl -= notional * fee_rate * 2
                trades.append({"side": "OPEN long_a_short_b", "time": row["time"], "z": z})
        else:
            if position == "long_a_short_b":
                unrealized = notional * (price_a / entry_price_a - 1) + notional * (1 - price_b / entry_price_b)
            else:
                unrealized = notional * (1 - price_a / entry_price_a) + notional * (price_b / entry_price_b - 1)

            if abs(z) < exit_z or abs(z) > stop_z:
                exit_pnl = unrealized - notional * fee_rate * 2
                realized_pnl += exit_pnl
                trades.append({
                    "side": f"CLOSE {position}", "time": row["time"], "z": z, "pnl": exit_pnl,
                })
                position = None
                entry_price_a = entry_price_b = None
                unrealized = 0.0

        equity_curve.append(starting_cash + realized_pnl + unrealized)

    final_equity = equity_curve[-1]
    total_return_pct = (final_equity / starting_cash - 1) * 100

    peak = starting_cash
    max_drawdown_pct = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        max_drawdown_pct = max(max_drawdown_pct, (peak - eq) / peak * 100)

    closes = [t for t in trades if t["side"].startswith("CLOSE")]
    round_trips = len(closes)
    wins = sum(1 for t in closes if t["pnl"] > 0)
    win_rate_pct = (wins / round_trips * 100) if round_trips else 0.0

    buy_hold_a_pct = (df["close_a"].iloc[-1] / df["close_a"].iloc[0] - 1) * 100
    buy_hold_b_pct = (df["close_b"].iloc[-1] / df["close_b"].iloc[0] - 1) * 100

    stats = {
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "round_trips": round_trips,
        "win_rate_pct": win_rate_pct,
        "buy_hold_a_pct": buy_hold_a_pct,
        "buy_hold_b_pct": buy_hold_b_pct,
        "trades": trades,
        "equity_curve": equity_curve,
    }

    if verbose:
        print(f"Final equity:      {final_equity:,.2f}")
        print(f"Total return:      {total_return_pct:.2f}%")
        print(f"Max drawdown:      {max_drawdown_pct:.2f}%")
        print(f"Round trips:       {round_trips}")
        print(f"Win rate:          {win_rate_pct:.2f}%")
        print(f"Buy&hold A / B:    {buy_hold_a_pct:.2f}% / {buy_hold_b_pct:.2f}%")

    return stats
