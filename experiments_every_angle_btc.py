"""Round 21: the same "every angle" indicator-toolkit search as Round 20 (ETH/DOGE),
now run for BTC specifically - using BTC's proper 4-independent-split Kraken-native daily
data (the most rigorous validation this project has, per Round 10), not the 2-split
Yahoo data used for ETH/DOGE. Same trend-family (own reverse-signal exit) / mean-reversion
-family (small TP/SL grid) split, same validation bar, same cadence tracking.

Usage:
    python experiments_every_angle_btc.py
"""
from data_fetcher import load_kraken_csv_1min
from experiments_every_angle import (
    REVERSION_SETUPS, SMALL_TPSL_GRID, TREND_SETUPS,
    simulate_reverse_signal_with_cadence, simulate_tpsl_with_cadence,
)
from experiments_multisplit import slice_dates, _passed
from experiments_multisplit_kraken import CSV_PATH, SPLITS


def main():
    n_splits = len(SPLITS)
    df = load_kraken_csv_1min(CSV_PATH)
    print(f"BTC: {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()}, {n_splits} splits\n")

    all_pass = []

    print("-- Trend-family indicators (own reverse-signal exit) --")
    for name, spec in TREND_SETUPS.items():
        for params in spec["grid"]:
            per_split = []
            for split_idx, (ts, te, vs, ve) in enumerate(SPLITS, 1):
                test_df = slice_dates(df, vs, ve)
                if len(test_df) < 100:
                    continue
                built = spec["build"](test_df, **params)
                stats, gap = simulate_reverse_signal_with_cadence(built, "entry", "exit")
                passed = _passed(stats)
                per_split.append((stats, gap, passed))
            if not per_split:
                continue
            all_ok = len(per_split) == n_splits and all(p for _, _, p in per_split)
            avg_ret = sum(s["total_return_pct"] for s, _, _ in per_split) / len(per_split)
            avg_dd = sum(s["max_drawdown_pct"] for s, _, _ in per_split) / len(per_split)
            avg_trades = sum(s["completed_trades"] for s, _, _ in per_split) / len(per_split)
            gaps = [g for _, g, _ in per_split if g is not None]
            avg_gap = sum(gaps) / len(gaps) if gaps else None
            gap_str = f"{avg_gap:.1f}d" if avg_gap is not None else "n/a"
            tag = "PASS" if all_ok else "fail"
            print(f"  [{tag}] {name}{params} | avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% "
                  f"avg_trades={avg_trades:5.1f} avg_gap={gap_str}")
            if all_ok:
                all_pass.append(("BTC", name, params, None, avg_ret, avg_dd, avg_gap))

    print("\n-- Mean-reversion indicators (small TP/SL grid) --")
    for name, spec in REVERSION_SETUPS.items():
        for params in spec["grid"]:
            best_for_params = None
            for tp_pct, sl_pct in SMALL_TPSL_GRID:
                per_split = []
                for split_idx, (ts, te, vs, ve) in enumerate(SPLITS, 1):
                    test_df = slice_dates(df, vs, ve)
                    if len(test_df) < 100:
                        continue
                    built = spec["build"](test_df, **params)
                    stats, gap = simulate_tpsl_with_cadence(built, "entry", tp_pct, sl_pct)
                    passed = _passed(stats)
                    per_split.append((stats, gap, passed))
                if not per_split:
                    continue
                all_ok = len(per_split) == n_splits and all(p for _, _, p in per_split)
                if all_ok:
                    avg_ret = sum(s["total_return_pct"] for s, _, _ in per_split) / len(per_split)
                    avg_dd = sum(s["max_drawdown_pct"] for s, _, _ in per_split) / len(per_split)
                    gaps = [g for _, g, _ in per_split if g is not None]
                    avg_gap = sum(gaps) / len(gaps) if gaps else 9999
                    all_pass.append(("BTC", name, params, (tp_pct, sl_pct), avg_ret, avg_dd, avg_gap))
                    if best_for_params is None or avg_gap < best_for_params[-1]:
                        best_for_params = (tp_pct, sl_pct, avg_ret, avg_dd, avg_gap)
            if best_for_params:
                tp_pct, sl_pct, avg_ret, avg_dd, avg_gap = best_for_params
                print(f"  [PASS] {name}{params} best@TP={tp_pct}/SL={sl_pct} | "
                      f"avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% avg_gap={avg_gap:.1f}d")
            else:
                print(f"  [fail] {name}{params} | no TP/SL in the small grid validated on all {n_splits} splits")

    print(f"\n{'='*100}\nAll validated combos, sorted by cadence:\n{'='*100}")
    for coin, name, params, tpsl, ret, dd, gap in sorted(all_pass, key=lambda r: r[-1] if r[-1] else 9999):
        tpsl_str = f" TP/SL={tpsl}" if tpsl else ""
        gap_str = f"{gap:.1f}d" if gap else "n/a"
        print(f"  {name}{params}{tpsl_str} | ret={ret:.2f}% dd={dd:.2f}% gap={gap_str}")

    if not all_pass:
        print("  NONE validated across all 4 splits.")

    return all_pass


if __name__ == "__main__":
    main()
