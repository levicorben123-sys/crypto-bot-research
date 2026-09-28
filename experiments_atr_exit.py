"""Round 22: ATR-based (volatility-scaled) exits instead of fixed-percentage TP/SL -
every exit in this project's 21 prior rounds has been a fixed percentage regardless of
how volatile the coin is at the moment of entry. An ATR-based stop/target widens
automatically in volatile periods and tightens in calm ones - a genuinely different
mechanism, not a parameter tweak. Found via web research into open-source trading bot
repos (freqtrade, walk-forward backtesters) that pair ATR-based exits with MA-crossover
entries specifically.

Each coin keeps its OWN currently-live entry mechanism unchanged (one variable at a time,
per this project's standing discipline) - only the exit becomes ATR-scaled:
  - BTC: SMA(10,30)+RSI (baseline_signal_entry)
  - ETH: Stochastic(21, oversold=30) mean-reversion (Round 20's live entry)
  - DOGE: SMA(5,10)+RSI (fast_entry)

At entry, ATR is computed over the trailing `atr_period` bars; stop = entry - sl_mult*ATR,
target = entry + tp_mult*ATR. Same validation bar as always (profitable, >=5 trades,
<40% drawdown, passes every independent split for that coin).

Usage:
    python experiments_atr_exit.py
"""
import numpy as np

import config
from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_fast_entry import fast_entry
from experiments_indicators import _true_range, stochastic_reversion
from experiments_multisplit import slice_dates, _passed
from experiments_multisplit_kraken import CSV_PATH as BTC_CSV_PATH, SPLITS as BTC_SPLITS
from experiments_regime import REGIME_SPLITS
from experiments_wide_tp import baseline_signal_entry

ATR_PERIOD_GRID = [14, 21]
SL_MULT_GRID = [0.5, 1.0, 1.5, 2.0, 3.0]
TP_MULT_GRID = [1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0]


def btc_entry(df):
    return baseline_signal_entry(df)


def eth_entry(df):
    return stochastic_reversion(df, k_period=21, oversold=30)


def doge_entry(df):
    return fast_entry(df, 5, 10)


COINS = {
    "BTC": {"kind": "kraken_csv", "path": BTC_CSV_PATH, "splits": BTC_SPLITS, "entry_fn": btc_entry},
    "ETH": {"kind": "yahoo", "symbol": "ETH-USD", "splits": REGIME_SPLITS, "entry_fn": eth_entry},
    "DOGE": {"kind": "yahoo", "symbol": "DOGE-USD", "splits": REGIME_SPLITS, "entry_fn": doge_entry},
}


def simulate_atr_exit(df, atr_period, sl_mult, tp_mult, starting_cash=10_000.0):
    df = df.copy().reset_index(drop=True)
    df["_atr"] = _true_range(df).ewm(alpha=1 / atr_period, adjust=False).mean()
    df = df.dropna(subset=["_atr"]).reset_index(drop=True)

    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = starting_cash, 0.0, False, None
    stop_price = target_price = None
    entry_times, trade_returns_pct = [], []
    equity_curve = []
    peak, max_dd = starting_cash, 0.0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]

        if not in_position:
            if bool(curr["entry"]):
                entry_price = price
                atr_at_entry = curr["_atr"]
                stop_price = entry_price - sl_mult * atr_at_entry
                target_price = entry_price + tp_mult * atr_at_entry
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                entry_times.append(curr["time"])
        else:
            exit_price = None
            if curr["low"] <= stop_price:
                exit_price = stop_price
            elif curr["high"] >= target_price:
                exit_price = target_price
            if exit_price is not None:
                proceeds = coins * exit_price
                cash = proceeds * (1 - fee_rate)
                coins = 0.0
                in_position = False
                trade_returns_pct.append((exit_price / entry_price - 1) * 100)

        equity = cash + coins * price
        equity_curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)

    final_equity = equity_curve[-1] if equity_curve else starting_cash
    total_return_pct = (final_equity / starting_cash - 1) * 100
    n = len(trade_returns_pct)
    wins = [r for r in trade_returns_pct if r > 0]
    win_rate = len(wins) / n * 100 if n else 0.0

    median_gap = None
    if len(entry_times) >= 2:
        gaps = np.diff(np.array(entry_times, dtype="datetime64[D]")).astype(int)
        median_gap = float(np.median(gaps))

    return {
        "total_return_pct": total_return_pct, "max_drawdown_pct": max_dd,
        "completed_trades": n, "win_rate_pct": win_rate,
    }, median_gap


def load_coin_df(cfg):
    if cfg["kind"] == "kraken_csv":
        return load_kraken_csv_1min(cfg["path"])
    return fetch_yahoo_history(cfg["symbol"], range_="10y", interval="1d")


def main():
    all_pass = []
    for coin, cfg in COINS.items():
        df = load_coin_df(cfg)
        n_splits = len(cfg["splits"])
        print(f"\n{'='*90}\n{coin} ({n_splits} splits) - entry unchanged, ATR-scaled exit\n{'='*90}")

        for atr_period in ATR_PERIOD_GRID:
            for sl_mult in SL_MULT_GRID:
                for tp_mult in TP_MULT_GRID:
                    per_split = []
                    for split_idx, (ts, te, vs, ve) in enumerate(cfg["splits"], 1):
                        test_df = slice_dates(df, vs, ve)
                        if len(test_df) < 100:
                            continue
                        signal_df = cfg["entry_fn"](test_df)
                        stats, gap = simulate_atr_exit(signal_df, atr_period, sl_mult, tp_mult)
                        passed = _passed(stats)
                        per_split.append((stats, gap, passed))
                    if not per_split:
                        continue
                    all_ok = len(per_split) == n_splits and all(p for _, _, p in per_split)
                    if all_ok:
                        avg_ret = sum(s["total_return_pct"] for s, _, _ in per_split) / len(per_split)
                        avg_dd = sum(s["max_drawdown_pct"] for s, _, _ in per_split) / len(per_split)
                        avg_trades = sum(s["completed_trades"] for s, _, _ in per_split) / len(per_split)
                        gaps = [g for _, g, _ in per_split if g is not None]
                        avg_gap = sum(gaps) / len(gaps) if gaps else 9999
                        all_pass.append((coin, atr_period, sl_mult, tp_mult, avg_ret, avg_dd, avg_trades, avg_gap))

        coin_pass = [r for r in all_pass if r[0] == coin]
        print(f"  {len(coin_pass)} ATR-exit combos pass all {n_splits} splits")
        for r in sorted(coin_pass, key=lambda r: r[-1])[:8]:
            _, atr_p, sl_m, tp_m, ret, dd, trades, gap = r
            print(f"    ATR({atr_p}) SL={sl_m}x TP={tp_m}x | ret={ret:.2f}% dd={dd:.2f}% "
                  f"trades={trades:.1f} gap={gap:.1f}d")
        if not coin_pass:
            print("    NONE validated")

    return all_pass


if __name__ == "__main__":
    main()
