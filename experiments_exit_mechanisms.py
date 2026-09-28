"""Round 12: two new exit mechanisms compared directly against the current fixed-20%-TP
baseline (rounds 8-11), same entry (event-based SMA/RSI, unchanged - Round 11 settled
that), same 1% stop-loss, same 4 regime splits, Kraken native daily data.

Mechanism 1 - trailing profit lock: once unrealized profit reaches ACTIVATION_PCT (3%),
stop using a fixed take-profit and instead trail behind the peak price reached since
activation, exiting when price pulls back TRAIL_PCT from that peak (grid: 2/3/5/8%).
Hard ceiling at CEILING_PCT (50%) - exit immediately if ever reached, don't risk giving
back a huge win waiting for the trail. The original 1% stop-loss from entry stays active
as the downside floor at all times, before AND after activation - never removed.

Daily-bar simplification, stated plainly: activation and the first trailing-stop check
are not evaluated on the same day (we only have one high/low per day, so we can't know
which happened first intraday) - activation sets the peak from that day's high, and the
trailing exit is checked starting the following day. This mirrors the SL-checked-before-
TP-on-the-same-day convention already used everywhere else in this project.

Mechanism 2 - tiered partial exits: instead of all-or-nothing, sell fixed fractions of
the ORIGINAL position size as different profit levels are hit (default: 25% at each of
3%/10%/20%/50%). The 1% stop-loss remains active on whatever fraction of the position is
still open, closing all of it if triggered. A real partial-position engine - coins_held
tracks the actual remaining position, each tier sale and any final stop-loss all pay fees
independently. Also tests a front-loaded schedule (40/30/20/10 at the same profit levels)
to see if weighting matters, per the brief's "don't over-engineer a large grid" guidance.

A "trade" for tiered exits is the whole episode from entry to the position reaching zero
(via tiers, a stop-loss on the remainder, or still open at the test window's end) - report
its blended (proceeds-weighted) return, same shape as other mechanisms' per-trade returns.

Usage:
    python experiments_exit_mechanisms.py
"""
import config
from data_fetcher import load_kraken_csv_1min
from experiments_multisplit import slice_dates
from experiments_multisplit_kraken import SPLITS
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp

CSV_PATH = "data/kraken_historical/extracted/XBTUSD_1440.csv"
STOP_LOSS_PCT = 1.0
BASELINE_TP_PCT = 20.0

ACTIVATION_PCT = 3.0
CEILING_PCT = 50.0
TRAIL_GRID_PCT = [2.0, 3.0, 5.0, 8.0]

TIER_SCHEDULES = {
    "tiered-even (25/25/25/25 @ 3/10/20/50%)": [(3, 0.25), (10, 0.25), (20, 0.25), (50, 0.25)],
    "tiered-front-loaded (40/30/20/10 @ 3/10/20/50%)": [(3, 0.40), (10, 0.30), (20, 0.20), (50, 0.10)],
}

MIN_TRADES = 5
MAX_DRAWDOWN_PCT_REF = 40


def _base_stats(df, equity_curve, trade_returns, starting_cash):
    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100

    peak = starting_cash
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)

    buy_hold_return_pct = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100

    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r <= 0]
    n = len(trade_returns)
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy_pct = win_rate * avg_win + (1 - win_rate) * avg_loss

    return {
        "total_return_pct": total_return_pct, "max_drawdown_pct": max_dd,
        "buy_hold_return_pct": buy_hold_return_pct, "completed_trades": n,
        "win_rate_pct": win_rate * 100, "avg_win_pct": avg_win, "avg_loss_pct": avg_loss,
        "expectancy_pct": expectancy_pct,
    }


def simulate_trailing_lock(df, entry_col, trail_pct, sl_pct=STOP_LOSS_PCT,
                            activation_pct=ACTIVATION_PCT, ceiling_pct=CEILING_PCT,
                            starting_cash=10_000.0):
    df = df.reset_index(drop=True)
    if len(df) < 2:
        return _base_stats(df, [], [], starting_cash) if len(df) else {
            "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "buy_hold_return_pct": 0.0,
            "completed_trades": 0, "win_rate_pct": 0.0, "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0, "expectancy_pct": 0.0,
        }

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = starting_cash, 0.0, False, None
    activated, peak_price = False, None
    trade_returns = []
    equity_curve = []

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        if not in_position:
            if bool(curr[entry_col]):
                entry_price = price
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                activated, peak_price = False, None
        else:
            sl_price = entry_price * (1 - sl_pct / 100)
            ceiling_price = entry_price * (1 + ceiling_pct / 100)

            exit_price = None
            if curr["low"] <= sl_price:
                exit_price = sl_price
            elif curr["high"] >= ceiling_price:
                exit_price = ceiling_price
            elif activated:
                peak_price = max(peak_price, curr["high"])
                trail_stop_price = peak_price * (1 - trail_pct / 100)
                if curr["low"] <= trail_stop_price:
                    exit_price = trail_stop_price
            else:
                activation_price = entry_price * (1 + activation_pct / 100)
                if curr["high"] >= activation_price:
                    activated = True
                    peak_price = curr["high"]

            if exit_price is not None:
                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                trade_returns.append((exit_price / entry_price - 1) * 100)
                price = exit_price
                entry_price, activated, peak_price = None, False, None

        equity_curve.append(cash + coins * price)

    return _base_stats(df, equity_curve, trade_returns, starting_cash)


def simulate_tiered_exit(df, entry_col, tiers, sl_pct=STOP_LOSS_PCT, starting_cash=10_000.0):
    df = df.reset_index(drop=True)
    if len(df) < 2:
        return {
            "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "buy_hold_return_pct": 0.0,
            "completed_trades": 0, "win_rate_pct": 0.0, "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0, "expectancy_pct": 0.0,
        }

    fee_rate = config.FEE_PCT / 100
    cash, in_position, entry_price = starting_cash, False, None
    coins_held = 0.0          # actual remaining position
    original_coins = 0.0      # position size at entry, tier weights are fractions of this
    tier_hit = []
    episode_cost = 0.0        # dollars spent entering (post-fee), for blended return calc
    episode_proceeds = 0.0    # dollars received from sells so far this episode
    trade_returns = []
    equity_curve = []

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        if not in_position:
            if bool(curr[entry_col]):
                entry_price = price
                spend = cash * (1 - fee_rate)
                original_coins = spend / price
                coins_held = original_coins
                episode_cost = cash  # pre-fee cash committed, so blended return nets fees like other mechanisms
                episode_proceeds = 0.0
                cash = 0.0
                in_position = True
                tier_hit = [False] * len(tiers)
        else:
            sl_price = entry_price * (1 - sl_pct / 100)

            if curr["low"] <= sl_price:
                proceeds = coins_held * sl_price * (1 - fee_rate)
                cash += proceeds
                episode_proceeds += proceeds
                coins_held = 0.0
                in_position = False
                trade_returns.append((episode_proceeds / episode_cost - 1) * 100)
                price = sl_price
                entry_price = None
            else:
                for idx, (tier_pct, weight) in enumerate(tiers):
                    if tier_hit[idx]:
                        continue
                    tier_price = entry_price * (1 + tier_pct / 100)
                    if curr["high"] >= tier_price:
                        tier_hit[idx] = True
                        sell_qty = min(original_coins * weight, coins_held)
                        proceeds = sell_qty * tier_price * (1 - fee_rate)
                        cash += proceeds
                        episode_proceeds += proceeds
                        coins_held -= sell_qty

                if coins_held <= original_coins * 1e-6:  # fully unwound via tiers
                    coins_held = 0.0
                    in_position = False
                    trade_returns.append((episode_proceeds / episode_cost - 1) * 100)
                    entry_price = None

        equity_curve.append(cash + coins_held * price)

    if in_position and coins_held > 0:
        # still holding a partial position at the end of the test window - mark remaining
        # to market and count the episode's blended return so far (not excluded, per the
        # project's standing rule against silently dropping open losers from the stats)
        final_price = df["close"].iloc[-1]
        unrealized_value = coins_held * final_price
        trade_returns.append(((episode_proceeds + unrealized_value) / episode_cost - 1) * 100)

    return _base_stats(df, equity_curve, trade_returns, starting_cash)


def _passed(stats):
    return (
        stats["completed_trades"] >= MIN_TRADES
        and stats["total_return_pct"] > 0
        and stats["max_drawdown_pct"] < MAX_DRAWDOWN_PCT_REF
    )


def main():
    df = load_kraken_csv_1min(CSV_PATH)
    print(f"Kraken native daily BTC/USD: {len(df)} candles, "
          f"{df['time'].min().date()}..{df['time'].max().date()}\n")

    mechanisms = {"baseline (fixed 20% TP, current live default)": ("baseline", None)}
    for trail_pct in TRAIL_GRID_PCT:
        mechanisms[f"trailing-lock (activate@3%, trail={trail_pct}%, ceiling@50%)"] = ("trailing", trail_pct)
    for name, tiers in TIER_SCHEDULES.items():
        mechanisms[name] = ("tiered", tiers)

    by_mechanism = {name: [] for name in mechanisms}

    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"Split {split_idx}: insufficient data, skipping")
            continue

        signal_df = baseline_signal_entry(test_df)
        test_bh = (test_df["close"].iloc[-1] / test_df["close"].iloc[0] - 1) * 100
        print(f"=== Split {split_idx}: test {test_start}..{test_end} (buy&hold {test_bh:+.2f}%) ===")

        for name, (kind, param) in mechanisms.items():
            if kind == "baseline":
                stats = simulate_wide_tp(signal_df, "entry", BASELINE_TP_PCT, STOP_LOSS_PCT)
            elif kind == "trailing":
                stats = simulate_trailing_lock(signal_df, "entry", param)
            else:
                stats = simulate_tiered_exit(signal_df, "entry", param)

            passed = _passed(stats)
            by_mechanism[name].append((split_idx, stats, passed))

            tag = "PASS" if passed else "fail"
            print(
                f"  [{tag}] {name:52s} | ret={stats['total_return_pct']:8.2f}% "
                f"dd={stats['max_drawdown_pct']:6.2f}% trades={stats['completed_trades']:4d} "
                f"winrate={stats['win_rate_pct']:5.1f}% expect={stats['expectancy_pct']:6.2f}%"
            )
        print()

    print("=" * 100)
    n_splits = len(SPLITS)
    all_pass_names = []
    for name, results in by_mechanism.items():
        all_pass = len(results) == n_splits and all(p for _, _, p in results)
        total_trades = sum(s["completed_trades"] for _, s, _ in results)
        avg_return = sum(s["total_return_pct"] for _, s, _ in results) / len(results) if results else 0.0
        avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in results) / len(results) if results else 0.0
        tag = "VALIDATED (all splits)" if all_pass else "did not validate on every split"
        print(f"{name:52s} | {tag:32s} | total_trades={total_trades:4d} avg_ret={avg_return:8.2f}% avg_dd={avg_dd:6.2f}%")
        if all_pass:
            all_pass_names.append(name)

    print(f"\nMechanisms validated across all {n_splits} splits: {all_pass_names}")
    return by_mechanism, all_pass_names


if __name__ == "__main__":
    main()
