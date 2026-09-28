"""Round 17: exhaustive search for ETH and DOGE specifically - entry speed (5 SMA period
pairs) x stop-loss (12 values, 1%-50%) x take-profit (15 values, 1%-100%) = 900 combos per
coin, x2 independent regime splits each = 1,800 backtests per coin, 3,600 total. Goal is
two separate "best validated" picks per coin: the best-return combo among genuinely
frequent traders, and the best-return combo overall (which, per every prior round, trends
toward slower/wider setups - reported honestly either way, not steered toward what the
user asked for).

Validation bar is this project's standard one (profitable, >=5 trades, <40% drawdown,
passes BOTH independent splits) - a combo that only looks good on one split does not
qualify as "best," regardless of how the number looks, per Round 8's whole reason for
existing multi-split validation in the first place.

Usage:
    python experiments_eth_doge_full_search.py
"""
from data_fetcher import fetch_yahoo_history
from experiments_fast_entry import fast_entry, simulate_with_cadence
from experiments_multisplit import slice_dates, _passed
from experiments_regime import REGIME_SPLITS

MA_GRID = [(10, 30), (5, 15), (5, 10), (3, 8), (2, 5)]
SL_GRID_PCT = [1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50]
TP_GRID_PCT = [1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100]

COINS = {"ETH": "ETH-USD", "DOGE": "DOGE-USD"}


def main():
    all_results = {}  # coin -> {(fast,slow,sl,tp): [(split_idx, stats, gap, n_signals, passed), ...]}

    for coin, symbol in COINS.items():
        df = fetch_yahoo_history(symbol, range_="10y", interval="1d")
        print(f"### {coin}: {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()} ###")
        by_combo = {}

        for fast, slow in MA_GRID:
            for split_idx, (train_start, train_end, test_start, test_end) in enumerate(REGIME_SPLITS, 1):
                test_df = slice_dates(df, test_start, test_end)
                if len(test_df) < 100:
                    continue
                signal_df = fast_entry(test_df, fast, slow)

                for sl_pct in SL_GRID_PCT:
                    for tp_pct in TP_GRID_PCT:
                        stats, median_gap, n_signals = simulate_with_cadence(signal_df, tp_pct, sl_pct)
                        passed = _passed(stats)
                        key = (fast, slow, sl_pct, tp_pct)
                        by_combo.setdefault(key, []).append((split_idx, stats, median_gap, n_signals, passed))

        print(f"  {len(by_combo)} combos tested for {coin}")
        all_results[coin] = by_combo

    n_splits = len(REGIME_SPLITS)
    for coin, by_combo in all_results.items():
        all_pass = []
        for (fast, slow, sl, tp), rows in by_combo.items():
            if len(rows) == n_splits and all(p for *_, p in rows):
                avg_ret = sum(s["total_return_pct"] for _, s, *_ in rows) / len(rows)
                avg_dd = sum(s["max_drawdown_pct"] for _, s, *_ in rows) / len(rows)
                avg_trades = sum(s["completed_trades"] for _, s, *_ in rows) / len(rows)
                avg_wr = sum(s["win_rate_pct"] for _, s, *_ in rows) / len(rows)
                gaps = [g for _, _, g, _, _ in rows if g is not None]
                avg_gap = sum(gaps) / len(gaps) if gaps else None
                all_pass.append({
                    "fast": fast, "slow": slow, "sl": sl, "tp": tp,
                    "avg_ret": avg_ret, "avg_dd": avg_dd, "avg_trades": avg_trades,
                    "avg_wr": avg_wr, "avg_gap": avg_gap,
                })

        print(f"\n{'='*100}\n{coin}: {len(all_pass)} combos pass BOTH splits\n{'='*100}")

        with_gap = [c for c in all_pass if c["avg_gap"] is not None]
        frequent = sorted([c for c in with_gap if c["avg_gap"] <= 30], key=lambda c: -c["avg_ret"])
        longterm_all = sorted(all_pass, key=lambda c: -c["avg_ret"])

        print(f"\n-- Best validated FREQUENT-trade combo(s) for {coin} (avg gap <=30 days) --")
        if frequent:
            for c in frequent[:5]:
                print(f"  SMA({c['fast']},{c['slow']}) SL={c['sl']}% TP={c['tp']}% | avg_ret={c['avg_ret']:.2f}% "
                      f"avg_dd={c['avg_dd']:.2f}% avg_trades={c['avg_trades']:.1f} avg_wr={c['avg_wr']:.1f}% "
                      f"avg_gap={c['avg_gap']:.1f}d")
        else:
            print("  NONE - no combo trading at <=30-day average cadence survives validation on both splits.")
            fastest_any = sorted(with_gap, key=lambda c: c["avg_gap"])[:5]
            print("  Fastest-cadence validated combos found at all (any frequency):")
            for c in fastest_any:
                print(f"  SMA({c['fast']},{c['slow']}) SL={c['sl']}% TP={c['tp']}% | avg_ret={c['avg_ret']:.2f}% "
                      f"avg_dd={c['avg_dd']:.2f}% avg_trades={c['avg_trades']:.1f} avg_wr={c['avg_wr']:.1f}% "
                      f"avg_gap={c['avg_gap']:.1f}d")

        print(f"\n-- Best validated combo(s) overall (highest return, any cadence) for {coin} --")
        for c in longterm_all[:5]:
            gap_str = f"{c['avg_gap']:.1f}d" if c["avg_gap"] is not None else "n/a"
            print(f"  SMA({c['fast']},{c['slow']}) SL={c['sl']}% TP={c['tp']}% | avg_ret={c['avg_ret']:.2f}% "
                  f"avg_dd={c['avg_dd']:.2f}% avg_trades={c['avg_trades']:.1f} avg_wr={c['avg_wr']:.1f}% "
                  f"avg_gap={gap_str}")

    return all_results


if __name__ == "__main__":
    main()
