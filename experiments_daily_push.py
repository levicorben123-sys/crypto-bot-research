"""Round 18: how close to literal DAILY trading can ETH/DOGE get, using their actual
current live exit configs (ETH: SL=2%/TP=50%, DOGE: SL=2%/TP=100%, both from Round 17),
before it stops being "safe" by this project's own validation bar? Motivated directly by
Round 17's finding that ETH tolerates a faster entry (SMA(5,10)) far better than BTC ever
did - worth actually testing how far that tolerance goes, rather than assuming Round
9/11/13's "faster always hurts" verdict (which was BTC-specific) applies here too.

Three ways to push toward daily action, same exit held fixed throughout (one variable at
a time, per this project's standing discipline):

1. Even faster event-based entry: SMA pairs faster than Round 17's winning (5,10) -
   (3,5), (2,4), (2,3), plus (2,5) again as a bridge point already known to validate for
   neither coin's OLD exit config (worth re-checking against the NEW exit).
2. State-based entry (Round 11's mechanism: buy whenever flat AND fast>slow, no fresh-
   cross requirement) at the current live MA pair (5,10) - already failed decisively for
   BTC in Round 11; re-tested here because Round 17 showed BTC's rules don't
   automatically transfer to these coins.
3. Unconditional entry (Round 7's mechanism: buy immediately whenever flat, no signal at
   all) - the most literal way to force daily-or-near-daily action; already a disaster
   for BTC/ETH/DOGE under DIFFERENT exit configs in Round 7 and Round 14, re-tested here
   against the current live exits specifically.

Usage:
    python experiments_daily_push.py
"""
import config
from data_fetcher import fetch_yahoo_history
from experiments_fast_entry import fast_entry, simulate_with_cadence
from experiments_multisplit import slice_dates, _passed
from experiments_regime import REGIME_SPLITS
from experiments_wide_tp import unconditional_entry
from strategy import compute_rsi

FAST_MA_GRID = [(5, 10), (3, 5), (2, 4), (2, 3), (2, 5)]

LIVE_EXITS = {
    "ETH": {"symbol": "ETH-USD", "sl": 2.0, "tp": 50.0},
    "DOGE": {"symbol": "DOGE-USD", "sl": 2.0, "tp": 100.0},
}


def state_based_entry(df, fast_period, slow_period):
    df = df.copy()
    sma_fast = df["close"].rolling(fast_period).mean()
    sma_slow = df["close"].rolling(slow_period).mean()
    rsi = compute_rsi(df["close"], config.RSI_PERIOD)
    df["entry"] = (sma_fast > sma_slow) & (rsi < config.RSI_OVERBOUGHT)
    return df


def run_variant(df, entry_fn, sl, tp, label):
    per_split = []
    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(REGIME_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            continue
        signal_df = entry_fn(test_df)
        stats, gap, n_signals = simulate_with_cadence(signal_df, tp, sl)
        passed = _passed(stats)
        per_split.append((split_idx, stats, gap, n_signals, passed))

    all_pass = len(per_split) == len(REGIME_SPLITS) and all(p for *_, p in per_split)
    avg_ret = sum(s["total_return_pct"] for _, s, *_ in per_split) / len(per_split)
    avg_dd = sum(s["max_drawdown_pct"] for _, s, *_ in per_split) / len(per_split)
    avg_trades = sum(s["completed_trades"] for _, s, *_ in per_split) / len(per_split)
    gaps = [g for _, _, g, _, _ in per_split if g is not None]
    avg_gap = sum(gaps) / len(gaps) if gaps else None
    gap_str = f"{avg_gap:.1f}d" if avg_gap is not None else "n/a (< 2 trades)"

    tag = "PASS" if all_pass else "fail"
    print(f"  [{tag}] {label:>28} | avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% "
          f"avg_trades={avg_trades:5.1f} avg_gap={gap_str}")
    return all_pass, avg_ret, avg_gap


def main():
    for coin, cfg in LIVE_EXITS.items():
        df = fetch_yahoo_history(cfg["symbol"], range_="10y", interval="1d")
        sl, tp = cfg["sl"], cfg["tp"]
        print(f"\n{'='*90}\n{coin}: live exit fixed at SL={sl}% TP={tp}%\n{'='*90}")

        print("\n-- 1. Even faster event-based entry --")
        for fast, slow in FAST_MA_GRID:
            run_variant(df, lambda d, f=fast, s=slow: fast_entry(d, f, s), sl, tp, f"event SMA({fast},{slow})")

        print("\n-- 2. State-based entry (buy whenever flat AND fast>slow) --")
        for fast, slow in [(5, 10), (10, 30)]:
            run_variant(df, lambda d, f=fast, s=slow: state_based_entry(d, f, s), sl, tp, f"state SMA({fast},{slow})")

        print("\n-- 3. Unconditional entry (buy immediately whenever flat) --")
        run_variant(df, unconditional_entry, sl, tp, "unconditional")

    print(f"\nFor reference: literal daily action needs an average gap of ~1 day between trades.")


if __name__ == "__main__":
    main()
