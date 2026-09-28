"""Round 14: an ultra-tight take-profit (close as soon as the trade nets +0.05% AFTER
fees) paired with a relaxed/wide stop-loss ("give the bot wiggle room"), tested across
multiple coins (BTC, ETH, SOL), not just BTC - directly answering the user's follow-up
ask after Round 13 showed entry-speed changes don't work. Same baseline SMA/RSI entry as
every prior round is tested as the primary case; the fastest entry from Round 13
(SMA(2,5)) is tested alongside it, since a tighter exit only closes positions faster - it
doesn't by itself make the event-based entry re-fire more often, and that interaction
needs to be checked, not assumed.

Fee-adjusted take-profit math: this bot's fee model deducts FEE_PCT on both legs, so the
realized net multiplier on a round trip is (1-fee)^2 * (exit/entry). To net +0.05% after
paying that twice, solve for the GROSS price move needed:
    gross_tp_pct = ((1 + net_tp_pct/100) / (1-fee_rate)**2 - 1) * 100
At this project's FEE_PCT=0.26% (Kraken taker, both sides), that works out to roughly
0.57% gross just to clear 0.05% net - i.e. fees eat the large majority of the gross move
by construction, because the target margin is so thin. This is computed exactly below,
not assumed.

Stop-loss: relaxed/wide grid (3% to 30%), explicitly the opposite of Round 8's validated
tight-stop/wide-target asymmetry - this design is tight-target/wide-stop, so a single
stop-out could erase dozens of the tiny wins this generates. That's a real risk to
measure, not wave off, so win rate AND expectancy are reported for every combo, same
discipline as every prior round.

Data: BTC uses Kraken's native daily OHLCV and the same 4 independent regime splits as
Round 10 (the most rigorous validation available in this project). ETH uses Yahoo Finance
and the 2 regime splits from Round 2 onward. SOL only has ~6.4 years of history
(2020-04-10 onward) - not enough for 4 or even 2 genuinely independent multi-year splits,
so it gets ONE train/test split, flagged explicitly as less rigorous than BTC/ETH's
validation. Reporting this honestly rather than dressing up a single split as equivalent
evidence.

Usage:
    python experiments_scalp_exit.py
"""
import numpy as np

import config
from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_fast_entry import fast_entry
from experiments_multisplit import slice_dates, _passed
from experiments_multisplit_kraken import CSV_PATH as BTC_CSV_PATH
from experiments_multisplit_kraken import SPLITS as BTC_SPLITS
from experiments_regime import REGIME_SPLITS as ETH_SPLITS
from experiments_wide_tp import simulate_wide_tp

NET_TP_PCT = 0.05
FEE_RATE = config.FEE_PCT / 100
GROSS_TP_PCT = ((1 + NET_TP_PCT / 100) / (1 - FEE_RATE) ** 2 - 1) * 100

SL_GRID_PCT = [3, 5, 7, 10, 15, 20, 25, 30]

ENTRY_STYLES = {
    "baseline(10,30)": (10, 30),
    "fast(2,5)": (2, 5),
}

SOL_SPLITS = [
    ("2020-04-10", "2023-06-30", "2023-07-01", "2026-09-17"),  # only split SOL's ~6.4y history supports
]

COINS = {
    "BTC": {"kind": "kraken_csv", "path": BTC_CSV_PATH, "splits": BTC_SPLITS},
    "ETH": {"kind": "yahoo", "symbol": "ETH-USD", "splits": ETH_SPLITS},
    "SOL": {"kind": "yahoo", "symbol": "SOL-USD", "splits": SOL_SPLITS},
}


def simulate_with_durations(df, tp_pct, sl_pct):
    """Same mechanics as simulate_wide_tp, but also tracks entry/exit timestamps so we
    can report actual trade-holding duration (does this design really resolve in ~1
    day?) and the gap between consecutive entries (does a fast exit alone increase
    overall trading frequency, or is that still gated by the entry signal?)."""
    df = df.reset_index(drop=True)
    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price, entry_time = 10_000.0, 0.0, False, None, None
    entry_times, durations_days = [], []

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]
        if not in_position:
            if bool(curr["entry"]):
                entry_price = price
                entry_time = curr["time"]
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                entry_times.append(entry_time)
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
                durations_days.append((curr["time"] - entry_time).days)
                entry_price, entry_time = None, None

    stats = simulate_wide_tp(df, "entry", tp_pct, sl_pct)

    median_gap = None
    if len(entry_times) >= 2:
        gaps = np.diff(np.array(entry_times, dtype="datetime64[D]")).astype(int)
        median_gap = float(np.median(gaps))
    median_duration = float(np.median(durations_days)) if durations_days else None

    return stats, median_gap, median_duration, len(entry_times)


def load_coin_df(coin_cfg):
    if coin_cfg["kind"] == "kraken_csv":
        return load_kraken_csv_1min(coin_cfg["path"])
    return fetch_yahoo_history(coin_cfg["symbol"], range_="10y", interval="1d")


def main():
    print(f"Net target: +{NET_TP_PCT}% after fees. FEE_PCT={config.FEE_PCT}% per side (Kraken taker, both legs).")
    print(f"=> Required GROSS take-profit to net that after round-trip fees: {GROSS_TP_PCT:.4f}%\n")
    print(f"Stop-loss grid (relaxed/wide, per the ask): {SL_GRID_PCT}\n")

    results = {}  # (coin, entry_style, sl_pct) -> list of (split_idx, stats, gap, dur, n_signals, passed)

    for coin, coin_cfg in COINS.items():
        df = load_coin_df(coin_cfg)
        splits = coin_cfg["splits"]
        n_splits = len(splits)
        note = " (SINGLE split only - insufficient history for independent multi-split validation)" if n_splits == 1 else ""
        print(f"### {coin}: {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()}, "
              f"{n_splits} split(s){note} ###")

        for style_name, (fast, slow) in ENTRY_STYLES.items():
            for split_idx, (train_start, train_end, test_start, test_end) in enumerate(splits, 1):
                test_df = slice_dates(df, test_start, test_end)
                if len(test_df) < 100:
                    continue
                test_signal_df = fast_entry(test_df, fast, slow)

                for sl_pct in SL_GRID_PCT:
                    stats, gap, dur, n_signals = simulate_with_durations(test_signal_df, GROSS_TP_PCT, sl_pct)
                    passed = _passed(stats)
                    key = (coin, style_name, sl_pct)
                    results.setdefault(key, []).append((split_idx, stats, gap, dur, n_signals, passed))

    print("\n" + "=" * 120)
    print(f"{'coin':>4} {'entry':>16} {'SL%':>4} | {'all-pass':>8} | {'avg trades/split':>16} | "
          f"{'med gap':>8} | {'med hold':>9} | {'avg test ret':>12} | {'avg win rate':>12}")

    all_pass_rows = []
    for (coin, style_name, sl_pct), rows in results.items():
        n_splits = len(COINS[coin]["splits"])
        all_pass = len(rows) == n_splits and all(p for *_, p in rows)
        avg_trades = sum(s["completed_trades"] for _, s, *_ in rows) / len(rows)
        avg_ret = sum(s["total_return_pct"] for _, s, *_ in rows) / len(rows)
        avg_wr = sum(s["win_rate_pct"] for _, s, *_ in rows) / len(rows)
        gaps = [g for _, _, g, _, _, _ in rows if g is not None]
        durs = [d for _, _, _, d, _, _ in rows if d is not None]
        gap_str = f"{sum(gaps)/len(gaps):.1f}d" if gaps else "n/a"
        dur_str = f"{sum(durs)/len(durs):.1f}d" if durs else "n/a"

        print(f"{coin:>4} {style_name:>16} {sl_pct:>3}% | {'YES' if all_pass else 'no':>8} | "
              f"{avg_trades:16.1f} | {gap_str:>8} | {dur_str:>9} | {avg_ret:11.2f}% | {avg_wr:11.1f}%")
        if all_pass:
            all_pass_rows.append((coin, style_name, sl_pct, avg_ret, avg_wr))

    print("\n" + "=" * 120)
    if all_pass_rows:
        print(f"{len(all_pass_rows)} combo(s) pass validation on every split available for that coin:")
        for coin, style_name, sl_pct, avg_ret, avg_wr in sorted(all_pass_rows, key=lambda r: -r[3]):
            print(f"  {coin} {style_name} SL={sl_pct}% | avg return {avg_ret:.2f}% | avg win rate {avg_wr:.1f}%")
    else:
        print("0 combos pass validation on every split available for their coin.")

    return results, all_pass_rows


if __name__ == "__main__":
    main()
