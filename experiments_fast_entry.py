"""Round 13: does shrinking the SMA crossover periods make the bot trade meaningfully
more often (toward daily action), while still clearing this project's standing
validation bar (profitable, >=5 trades, <40% drawdown, passes on ALL 4 independent
regime splits)? Extended to also sweep the stop-loss axis (user's follow-up ask,
targeting 2-5% *daily* profit): for each SMA pair, test a wide stop-loss grid to see
whether any (entry speed, stop) combination gets closer to that target without failing
validation.

Context for this round: the user asked to configure the strategy for daily trading
while targeting 5-10% (later revised to 2-5%) profit *per day*. That target is not
solvable by parameter search regardless of the numbers below - even 2%/day compounded is
`(1.02)^252 ≈ 151x` per year, a return no real strategy sustains; see the README write-up
for that math. What IS a fair, testable question is whether a faster entry signal
(shorter SMA periods) combined with a re-tuned stop-loss gets meaningfully closer to
daily action while still clearing this project's standard bar. TP is held fixed at the
live default (20%) throughout - Round 10 already swept the TP axis exhaustively at
SL=1/3/5/7% and found no benefit to widening it; this round's new axis is SL x entry
speed, not TP.

Same data, splits, and pass bar as Round 10 (experiments_multisplit_kraken.py): Kraken's
own native daily OHLCV, 4 independent multi-year regime splits. RSI period/thresholds
held fixed at config.py defaults throughout.

Beyond pass/fail, this also reports trade cadence directly (median days between
completed trades) and an implied per-day compounding rate (from expectancy per trade and
median days between trades) so "how close to 2-5%/day" has a concrete number attached,
not just a qualitative no.

Usage:
    python experiments_fast_entry.py
"""
import numpy as np

import config
from data_fetcher import load_kraken_csv_1min
from experiments_multisplit import slice_dates, _passed
from experiments_multisplit_kraken import CSV_PATH, SPLITS
from experiments_wide_tp import simulate_wide_tp
from strategy import compute_rsi

LIVE_SL_PCT = 1.0
LIVE_TP_PCT = 20.0

# (fast, slow) SMA period pairs, ordered from the live default down to very fast.
# Below ~(2,5) a "slow" SMA is barely a moving average at all; included anyway to see
# where/if the mechanism breaks down entirely, not to suggest it as a real candidate.
MA_GRID = [
    (10, 30),  # live baseline, included as the reference row
    (5, 15),
    (5, 10),
    (3, 8),
    (2, 5),
]

# Wide stop-loss grid per the user's follow-up ask ("test as many stop loss percentages
# as possible"). Extends below Round 10's 1% floor (tighter stops = faster exits = more
# frequent re-entries, the mechanism being chased here) and above its 7% ceiling for
# completeness, even though Round 10 already found wider-than-1% strictly worse at the
# baseline entry speed - re-checked here since a faster entry could in principle change
# that interaction.
SL_GRID_PCT = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 7, 10, 15, 20]


def fast_entry(df, fast_period, slow_period):
    df = df.copy()
    sma_fast = df["close"].rolling(fast_period).mean()
    sma_slow = df["close"].rolling(slow_period).mean()
    rsi = compute_rsi(df["close"], config.RSI_PERIOD)
    crossed_up = (sma_fast.shift(1) <= sma_slow.shift(1)) & (sma_fast > sma_slow)
    df["entry"] = crossed_up & (rsi < config.RSI_OVERBOUGHT)
    return df


def simulate_with_cadence(df, tp_pct, sl_pct):
    """Same mechanics as simulate_wide_tp, but also returns entry/exit timestamps so we
    can report the actual gap between trades, not just a total trade count."""
    df = df.reset_index(drop=True)
    fee_rate = config.FEE_PCT / 100
    cash, coins, in_position, entry_price = 10_000.0, 0.0, False, None
    entry_times = []

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = curr["close"]
        if not in_position:
            if bool(curr["entry"]):
                entry_price = price
                coins = (cash * (1 - fee_rate)) / price
                cash = 0.0
                in_position = True
                entry_times.append(curr["time"])
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

    stats = simulate_wide_tp(df, "entry", tp_pct, sl_pct)

    if len(entry_times) >= 2:
        gaps_days = np.diff(np.array(entry_times, dtype="datetime64[D]")).astype(int)
        median_days_between_trades = float(np.median(gaps_days))
    else:
        median_days_between_trades = None

    return stats, median_days_between_trades, len(entry_times)


def implied_daily_pct(expectancy_pct, median_days):
    """Compounded per-day rate implied by 'expectancy_pct every median_days days,
    forever' - a concrete number to compare against a X%/day target, not a proxy for
    a real forecast (expectancy from a finite backtest sample, not a guarantee)."""
    if median_days is None or median_days <= 0:
        return None
    return ((1 + expectancy_pct / 100) ** (1 / median_days) - 1) * 100


def main():
    df = load_kraken_csv_1min(CSV_PATH)
    print(f"Kraken native daily BTC/USD: {len(df)} candles, "
          f"{df['time'].min().date()}..{df['time'].max().date()}\n")
    print(f"TP fixed at live default {LIVE_TP_PCT}% (Round 10 already ruled out widening "
          f"it). RSI period/thresholds fixed at config.py defaults "
          f"(period={config.RSI_PERIOD}, overbought={config.RSI_OVERBOUGHT}). "
          f"Sweeping SL x entry speed: SL in {SL_GRID_PCT}.\n")

    n_splits = len(SPLITS)
    # (fast, slow, sl_pct) -> list of (split_idx, stats, median_days, n_signals, passed)
    by_combo = {}

    for fast, slow in MA_GRID:
        for split_idx, (train_start, train_end, test_start, test_end) in enumerate(SPLITS, 1):
            test_df = slice_dates(df, test_start, test_end)
            if len(test_df) < 100:
                continue
            test_signal_df = fast_entry(test_df, fast, slow)

            for sl_pct in SL_GRID_PCT:
                stats, median_days, n_signals = simulate_with_cadence(test_signal_df, LIVE_TP_PCT, sl_pct)
                passed = _passed(stats)
                by_combo.setdefault((fast, slow, sl_pct), []).append(
                    (split_idx, stats, median_days, n_signals, passed)
                )

    print("=" * 110)
    print(f"{'SMA pair':>10} {'SL%':>5} | {'all-4-pass':>10} | {'avg gap':>9} | "
          f"{'avg trades/split':>16} | {'avg test ret':>12} | {'implied %/day (avg)':>19} | {'gap-throughs':>12}")

    all_pass_rows = []
    for (fast, slow, sl_pct), results in by_combo.items():
        all_pass = len(results) == n_splits and all(p for *_, p in results)
        gaps = [g for _, _, g, _, _ in results if g is not None]
        avg_gap = sum(gaps) / len(gaps) if gaps else None
        avg_trades = sum(s["completed_trades"] for _, s, *_ in results) / len(results)
        avg_ret = sum(s["total_return_pct"] for _, s, *_ in results) / len(results)
        gap_throughs = sum(s["gap_through_count"] for _, s, *_ in results)

        implied_rates = [
            implied_daily_pct(s["expectancy_pct"], g)
            for _, s, g, _, _ in results
            if g is not None and s["completed_trades"] >= 3
        ]
        avg_implied = sum(implied_rates) / len(implied_rates) if implied_rates else None

        gap_str = f"{avg_gap:.1f}d" if avg_gap is not None else "n/a"
        implied_str = f"{avg_implied:+.3f}%" if avg_implied is not None else "n/a"

        print(
            f"SMA({fast:>2},{slow:>2}) {sl_pct:>4}% | {'YES' if all_pass else 'no':>10} | "
            f"{gap_str:>9} | {avg_trades:16.1f} | {avg_ret:11.2f}% | {implied_str:>19} | {gap_throughs:12d}"
        )
        if all_pass:
            all_pass_rows.append((fast, slow, sl_pct, avg_ret, avg_gap, avg_implied))

    print("\n" + "=" * 110)
    if all_pass_rows:
        print(f"{len(all_pass_rows)} (entry speed, SL) combo(s) pass all {n_splits} splits:")
        for fast, slow, sl_pct, avg_ret, avg_gap, avg_implied in sorted(all_pass_rows, key=lambda r: -r[3]):
            implied_str = f"{avg_implied:+.3f}%/day" if avg_implied is not None else "n/a"
            print(f"  SMA({fast},{slow}) SL={sl_pct}% | avg test return {avg_ret:.2f}% | "
                  f"avg gap {avg_gap:.1f}d | implied {implied_str}")
    else:
        print(f"0 (entry speed, SL) combo(s) pass all {n_splits} splits.")

    best_by_avg_ret = max(by_combo.items(), key=lambda kv: sum(s["total_return_pct"] for _, s, *_ in kv[1]) / len(kv[1]))
    (fast, slow, sl_pct), results = best_by_avg_ret
    avg_ret = sum(s["total_return_pct"] for _, s, *_ in results) / len(results)
    all_pass = len(results) == n_splits and all(p for *_, p in results)
    print(
        f"\nSingle best combo by raw average test return (context only, NOT a "
        f"recommendation - see caveat below): SMA({fast},{slow}) SL={sl_pct}% at "
        f"{avg_ret:.2f}% avg return, all-4-splits pass = {'YES' if all_pass else 'NO'}."
    )
    print(
        "Caveat: picking 'best average across splits' without requiring it to pass on "
        "every split is exactly the mistake Round 8 was designed to avoid (see README) "
        "- a config can look great on average while collapsing on the split it happens "
        "to fail. Only the all-4-pass list above meets this project's actual bar."
    )

    print(
        "\nFor reference, literal daily action would require a median gap of ~1 day "
        "between trades, and the user's 2-5%/day target requires an implied per-day "
        "rate of +2.000% to +5.000% sustained - compare both to the figures above."
    )

    return by_combo, all_pass_rows


if __name__ == "__main__":
    main()
