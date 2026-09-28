"""Search for a strategy family with a genuine, validated edge over buy-and-hold.

Generic engine: any strategy is just a function that adds a boolean 'entry' column to
an OHLC dataframe. Exits are always the existing TP/SL-with-fees rule from backtest.py
(kept consistent so comparisons are apples-to-apples). Every family is grid-searched on
a TRAIN slice (first 70%) and only re-checked on a held-out TEST slice; a result only
counts if it holds up on TEST (positive return, beats TEST buy-and-hold, >=3 trades so
it isn't one lucky fill, and reasonable drawdown).

Usage:
    python experiments.py
"""
import config
from data_fetcher import fetch_ohlc
from strategy import compute_rsi

TRAIN_FRACTION = 0.7
PAIRS = ["XBTUSD", "ETHUSD"]
INTERVALS = [240, 1440]

MIN_TEST_TRADES = 3


# ---------------------------------------------------------------------------
# Generic simulation engine (entry column + TP/SL/fee exits, same rule for every family)
# ---------------------------------------------------------------------------

def simulate(df, entry_col, tp_pct, sl_pct, starting_cash=10_000.0):
    df = df.dropna().reset_index(drop=True)
    if len(df) < 2:
        return _empty_stats(starting_cash)

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = starting_cash, 0.0, False, None
    trades = []
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
                trades.append(("BUY", price))
        else:
            tp_price = entry_price * (1 + tp_pct / 100)
            sl_price = entry_price * (1 - sl_pct / 100)
            exit_price = None
            if curr["low"] <= sl_price:
                exit_price = sl_price
            elif curr["high"] >= tp_price:
                exit_price = tp_price
            if exit_price is not None:
                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                entry_price = None
                trades.append(("SELL", exit_price))
                price = exit_price

        equity_curve.append(cash + coins * price)

    return _stats(df, equity_curve, trades, starting_cash)


def _empty_stats(starting_cash):
    return {"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "round_trips": 0,
            "win_rate_pct": 0.0, "buy_hold_return_pct": 0.0}


def _stats(df, equity_curve, trades, starting_cash):
    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100

    peak = starting_cash
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)

    round_trips = len(trades) // 2
    wins = sum(1 for j in range(0, len(trades) - 1, 2) if trades[j + 1][1] > trades[j][1])
    win_rate_pct = (wins / round_trips * 100) if round_trips else 0.0
    buy_hold_return_pct = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100

    return {
        "total_return_pct": total_return_pct, "max_drawdown_pct": max_dd,
        "round_trips": round_trips, "win_rate_pct": win_rate_pct,
        "buy_hold_return_pct": buy_hold_return_pct,
    }


# ---------------------------------------------------------------------------
# Strategy families — each returns a df with an added boolean 'entry' column
# ---------------------------------------------------------------------------

def mean_reversion_rsi(df, rsi_period, oversold):
    df = df.copy()
    df["rsi"] = compute_rsi(df["close"], rsi_period)
    df["entry"] = df["rsi"] < oversold
    return df


def bollinger_reversion(df, period, num_std):
    df = df.copy()
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    lower = sma - num_std * std
    df["entry"] = df["close"] < lower
    return df


def volatility_breakout(df, donchian_period, atr_period, min_atr_pct):
    df = df.copy()
    donchian_upper = df["high"].rolling(donchian_period).max().shift(1)  # prior N bars, exclude current
    prev_close = df["close"].shift(1)
    tr = pd_concat_max(df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs())
    atr = tr.rolling(atr_period).mean()
    atr_pct = atr / df["close"] * 100
    df["entry"] = (df["close"] > donchian_upper) & (atr_pct > min_atr_pct)
    return df


def pd_concat_max(*series):
    import pandas as pd
    return pd.concat(series, axis=1).max(axis=1)


def trend_filtered_crossover(df, fast, slow, trend_period):
    df = df.copy()
    df["sma_fast"] = df["close"].rolling(fast).mean()
    df["sma_slow"] = df["close"].rolling(slow).mean()
    df["trend_ma"] = df["close"].rolling(trend_period).mean()
    crossed_up = (df["sma_fast"].shift(1) <= df["sma_slow"].shift(1)) & (df["sma_fast"] > df["sma_slow"])
    df["entry"] = crossed_up & (df["close"] > df["trend_ma"])
    return df


def momentum_roc(df, lookback, threshold_pct):
    df = df.copy()
    roc = (df["close"] / df["close"].shift(lookback) - 1) * 100
    df["entry"] = roc > threshold_pct
    return df


FAMILIES = {
    "mean_reversion_rsi": {
        "build": mean_reversion_rsi,
        "grid": [{"rsi_period": p, "oversold": o}
                 for p in (10, 14, 21) for o in (20, 25, 30)],
    },
    "bollinger_reversion": {
        "build": bollinger_reversion,
        "grid": [{"period": p, "num_std": s}
                 for p in (15, 20, 30) for s in (2.0, 2.5, 3.0)],
    },
    "volatility_breakout": {
        "build": volatility_breakout,
        "grid": [{"donchian_period": d, "atr_period": 14, "min_atr_pct": m}
                 for d in (10, 20, 30) for m in (0.5, 1.0, 1.5)],
    },
    "trend_filtered_crossover": {
        "build": trend_filtered_crossover,
        "grid": [{"fast": f, "slow": s, "trend_period": t}
                 for f in (5, 10) for s in (20, 30) for t in (50, 100, 150) if f < s],
    },
    "momentum_roc": {
        "build": momentum_roc,
        "grid": [{"lookback": l, "threshold_pct": t}
                 for l in (5, 10, 20) for t in (2, 3, 5)],
    },
}

TP_SL_GRID = [(1.5, 1.0), (2.0, 1.5), (3.0, 2.0)]


def run_family(family_name, spec, train_df, test_df):
    results = []
    for params in spec["grid"]:
        train_signal_df = spec["build"](train_df, **params)
        for tp_pct, sl_pct in TP_SL_GRID:
            stats = simulate(train_signal_df, "entry", tp_pct, sl_pct)
            results.append((params, tp_pct, sl_pct, stats))

    results.sort(key=lambda r: r[3]["total_return_pct"], reverse=True)
    top = results[:5]

    validated = []
    for params, tp_pct, sl_pct, train_stats in top:
        test_signal_df = spec["build"](test_df, **params)
        test_stats = simulate(test_signal_df, "entry", tp_pct, sl_pct)
        passed = (
            test_stats["round_trips"] >= MIN_TEST_TRADES
            and test_stats["total_return_pct"] > test_stats["buy_hold_return_pct"]
            and test_stats["total_return_pct"] > 0
            and test_stats["max_drawdown_pct"] < 25
        )
        validated.append((params, tp_pct, sl_pct, train_stats, test_stats, passed))

    return validated


def main():
    all_winners = []
    summary_rows = []

    for pair in PAIRS:
        for interval in INTERVALS:
            print(f"\n=== {pair} @ {interval}min ===")
            df = fetch_ohlc(pair, interval)
            split = int(len(df) * TRAIN_FRACTION)
            train_df, test_df = df.iloc[:split].reset_index(drop=True), df.iloc[split:].reset_index(drop=True)
            print(f"train={len(train_df)} test={len(test_df)} candles")

            for family_name, spec in FAMILIES.items():
                validated = run_family(family_name, spec, train_df, test_df)
                best = validated[0]
                params, tp_pct, sl_pct, train_stats, test_stats, passed = best
                tag = "PASS" if passed else "fail"
                print(
                    f"  [{tag}] {family_name:26s} tp={tp_pct} sl={sl_pct} {params} | "
                    f"train_ret={train_stats['total_return_pct']:7.2f}% | "
                    f"test_ret={test_stats['total_return_pct']:7.2f}% test_bh={test_stats['buy_hold_return_pct']:7.2f}% "
                    f"test_dd={test_stats['max_drawdown_pct']:6.2f}% test_trades={test_stats['round_trips']}"
                )
                summary_rows.append((pair, interval, family_name, params, tp_pct, sl_pct, train_stats, test_stats, passed))
                if passed:
                    all_winners.append((pair, interval, family_name, params, tp_pct, sl_pct, test_stats))

    print("\n" + "=" * 80)
    if all_winners:
        print(f"{len(all_winners)} config(s) validated out-of-sample:")
        for w in all_winners:
            print(" ", w)
    else:
        print("No config in any family validated out-of-sample (test beat buy&hold, "
              "positive return, >=3 trades, <25% drawdown). Honest finding: none of "
              "these signal families show a real edge on this data.")

    return summary_rows, all_winners


if __name__ == "__main__":
    main()
