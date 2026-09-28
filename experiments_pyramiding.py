"""Round 16: pyramiding (multiple concurrent positions in the same coin) instead of this
project's standing "one position at a time" rule. Two questions, answered in order:

1. DIAGNOSTIC (cheap, non-invasive): how often does a BUY signal actually get dropped
   today because the bot is already in a position? If this is rare, pyramiding can't help
   much regardless of how well-built it is - worth knowing before building the engine.

2. If signals are dropped often enough to matter: does allowing 2/3/5 concurrent
   positions (equal-split capital across open slots) improve on each coin's LIVE config
   (BTC SL=1/TP=20, ETH SL=10/TP=60, DOGE SL=15/TP=30), tested across the same regime
   splits this project always uses? MAX_POSITIONS=1 is included as a sanity-check row -
   it must reproduce the already-known live numbers exactly, or the new engine has a bug.

This does NOT re-search stop-loss/take-profit - it isolates ONE new variable
(concurrency) against each coin's already-validated exit, per this project's standing
discipline (one variable at a time, see Round 12/13).

Usage:
    python experiments_pyramiding.py
"""
import config
from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_multisplit import slice_dates, _passed
from experiments_multisplit_kraken import CSV_PATH as BTC_CSV_PATH
from experiments_multisplit_kraken import SPLITS as BTC_SPLITS
from experiments_regime import REGIME_SPLITS as ETH_DOGE_SPLITS
from experiments_wide_tp import baseline_signal_entry

MAX_POSITIONS_GRID = [1, 2, 3, 5]

LIVE_CONFIGS = {
    "BTC": {"kind": "kraken_csv", "path": BTC_CSV_PATH, "splits": BTC_SPLITS, "sl": 1.0, "tp": 20.0},
    "ETH": {"kind": "yahoo", "symbol": "ETH-USD", "splits": ETH_DOGE_SPLITS, "sl": 10.0, "tp": 60.0},
    "DOGE": {"kind": "yahoo", "symbol": "DOGE-USD", "splits": ETH_DOGE_SPLITS, "sl": 15.0, "tp": 30.0},
}


def load_coin_df(cfg):
    if cfg["kind"] == "kraken_csv":
        return load_kraken_csv_1min(cfg["path"])
    return fetch_yahoo_history(cfg["symbol"], range_="10y", interval="1d")


def simulate_pyramiding(df, tp_pct, sl_pct, max_positions, starting_cash=10_000.0):
    """Up to max_positions concurrent positions. Each new entry takes an equal share of
    currently-uninvested cash (cash / free_slots), so capital converges toward equal-sized
    positions without ever leaving cash stranded. Each position tracks its own TP/SL and
    exits independently. At max_positions=1 this must reduce exactly to the existing
    single-position engine (sanity-checked in main())."""
    df = df.reset_index(drop=True)
    fee_rate = config.FEE_PCT / 100
    cash = starting_cash
    positions = []  # each: {"entry_price": x, "coins": y}
    trade_returns_pct = []
    dropped_signals = 0
    equity_curve = []
    peak = starting_cash
    max_dd = 0.0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        # Gate today's entry on capacity as of the START of this bar, before today's
        # exits are applied - matches simulate_wide_tp's semantics exactly (a position
        # that exits today cannot be immediately replaced on the same bar; the freed
        # slot only becomes available starting the next bar). Without this, a same-day
        # close-then-reopen sneaks in an extra trade that the single-position engine
        # (max_positions=1) would never produce - caught via a direct A/B mismatch
        # against simulate_wide_tp on ETH SL=40/TP=5 split1 (12 trades vs. 11, and a
        # sign-flipped return) before this fix.
        free_slots_at_start = max_positions - len(positions)

        still_open = []
        for pos in positions:
            tp_price = pos["entry_price"] * (1 + tp_pct / 100)
            sl_price = pos["entry_price"] * (1 - sl_pct / 100)
            exit_price = None
            if curr["low"] <= sl_price:
                exit_price = sl_price
            elif curr["high"] >= tp_price:
                exit_price = tp_price
            if exit_price is not None:
                proceeds = pos["coins"] * exit_price
                cash += proceeds * (1 - fee_rate)
                trade_returns_pct.append((exit_price / pos["entry_price"] - 1) * 100)
            else:
                still_open.append(pos)
        positions = still_open

        if bool(curr["entry"]):
            free_slots = free_slots_at_start
            if free_slots <= 0:
                dropped_signals += 1
            elif cash > 0:
                slice_cash = cash / free_slots
                coins = (slice_cash * (1 - fee_rate)) / price
                cash -= slice_cash
                positions.append({"entry_price": price, "coins": coins})

        equity = cash + sum(pos["coins"] * price for pos in positions)
        equity_curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)

    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100
    n = len(trade_returns_pct)
    wins = [r for r in trade_returns_pct if r > 0]
    win_rate = len(wins) / n * 100 if n else 0.0

    return {
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_dd,
        "completed_trades": n,
        "win_rate_pct": win_rate,
        "dropped_signals": dropped_signals,
        "still_open_at_end": len(positions),
    }


def main():
    print("### Part 1: diagnostic - how often is a BUY signal dropped today (MAX_POSITIONS=1)? ###\n")
    for coin, cfg in LIVE_CONFIGS.items():
        df = load_coin_df(cfg)
        for split_idx, (train_start, train_end, test_start, test_end) in enumerate(cfg["splits"], 1):
            test_df = slice_dates(df, test_start, test_end)
            if len(test_df) < 100:
                continue
            signal_df = baseline_signal_entry(test_df)
            stats = simulate_pyramiding(signal_df, cfg["tp"], cfg["sl"], max_positions=1)
            total_signals = int(signal_df["entry"].sum())
            print(f"  {coin} split{split_idx} ({test_start}..{test_end}) | "
                  f"total BUY signals={total_signals} | executed={stats['completed_trades']} | "
                  f"dropped (already in position)={stats['dropped_signals']} "
                  f"({stats['dropped_signals']/total_signals*100 if total_signals else 0:.0f}% of signals)")
    print()

    print("### Part 2: does allowing 2/3/5 concurrent positions improve on the live config? ###\n")
    for coin, cfg in LIVE_CONFIGS.items():
        df = load_coin_df(cfg)
        n_splits = len(cfg["splits"])
        print(f"--- {coin}: SL={cfg['sl']}% TP={cfg['tp']}% (live config), {n_splits} split(s) ---")
        print(f"{'max_pos':>8} | {'all-pass':>8} | {'avg_ret':>9} | {'avg_dd':>8} | {'avg_trades':>10} | {'avg_winrate':>11}")

        for max_pos in MAX_POSITIONS_GRID:
            per_split = []
            for split_idx, (train_start, train_end, test_start, test_end) in enumerate(cfg["splits"], 1):
                test_df = slice_dates(df, test_start, test_end)
                if len(test_df) < 100:
                    continue
                signal_df = baseline_signal_entry(test_df)
                stats = simulate_pyramiding(signal_df, cfg["tp"], cfg["sl"], max_positions=max_pos)
                passed = _passed(stats)
                per_split.append((stats, passed))

            all_pass = len(per_split) == n_splits and all(p for _, p in per_split)
            avg_ret = sum(s["total_return_pct"] for s, _ in per_split) / len(per_split)
            avg_dd = sum(s["max_drawdown_pct"] for s, _ in per_split) / len(per_split)
            avg_trades = sum(s["completed_trades"] for s, _ in per_split) / len(per_split)
            avg_wr = sum(s["win_rate_pct"] for s, _ in per_split) / len(per_split)
            print(f"{max_pos:>8} | {'YES' if all_pass else 'no':>8} | {avg_ret:8.2f}% | {avg_dd:7.2f}% | "
                  f"{avg_trades:10.1f} | {avg_wr:10.1f}%")
        print()


if __name__ == "__main__":
    main()
