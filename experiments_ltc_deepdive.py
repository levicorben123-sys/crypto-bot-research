"""LTC deep-dive: full entry-strategy discovery (Round 5/8/10/20 methodology) followed by
the Round 26/27 trailing-stop-ratchet optimization, applied fresh to LTC as a candidate to
replace the DOGE live bot. Backtest-only - nothing wired into any live bot.

Data: LTC's own native Kraken daily/minute OHLCV, pulled from the same
"Kraken_OHLCVT_Full_2026Q2" bulk archive as BTC/ETH/DOGE (LTC's pair name in the archive
is plain "LTCUSD", no legacy prefix). Range 2013-10-24..2026-06-30 - long enough to build
the SAME 4-split methodology as BTC (Round 8/10), not just ETH/DOGE's 2-3.

Usage:
    python experiments_ltc_deepdive.py
"""
import numpy as np
import pandas as pd

import config
from data_fetcher import load_kraken_csv_1min
from experiments_multisplit import SL_GRID_PCT, TP_GRID_PCT, _passed as _passed_wide, slice_dates
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp
from experiments_indicators import SETUPS, run_setup
from experiments_trailing_ratchet import (
    simulate_trailing_ratchet, milestone_floor_tight_static, milestone_floor_peak_relative,
    milestone_floor_peak_relative_aggressive, _passed as _passed_trail, _trail_step_label,
    _trail_params_agree,
)

LTC_CSV_PATH = "data/kraken_historical/extracted/LTCUSD_1440.csv"

# Same boundaries as BTC's 4 splits (experiments_multisplit_kraken.SPLITS), split 3's
# train start adjusted to LTC's actual Kraken data start (2013-10-24, vs BTC's
# 2013-10-06) - LTC has been on Kraken essentially as long as BTC, unlike ETH/DOGE.
LTC_SPLITS = [
    ("2018-01-01", "2021-12-31", "2022-01-01", "2025-12-31"),
    ("2017-01-01", "2019-12-31", "2020-01-01", "2021-12-31"),
    ("2013-10-24", "2017-12-31", "2018-01-01", "2019-12-31"),
    ("2019-01-01", "2022-12-31", "2023-01-01", "2026-06-30"),
]

LIVE_SL_BASELINE, LIVE_TP_BASELINE = 1.0, 20.0  # BTC's own live config, as a same-family reference point


def step3a_baseline_grid(df):
    print("\n" + "=" * 100)
    print("STEP 3a: baseline SMA/RSI signal entry, Round 8/10's SL/TP grid, LTC's 4 real splits")
    print("=" * 100)
    by_combo = {}
    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(LTC_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        test_signal_df = baseline_signal_entry(test_df)
        test_bh = (test_df["close"].iloc[-1] / test_df["close"].iloc[0] - 1) * 100
        print(f"\n  Split {split_idx}: test {test_start}..{test_end} (buy&hold {test_bh:+.2f}%)")
        for sl_pct in SL_GRID_PCT:
            for tp_pct in TP_GRID_PCT:
                stats = simulate_wide_tp(test_signal_df, "entry", tp_pct, sl_pct)
                passed = _passed_wide(stats)
                by_combo.setdefault((sl_pct, tp_pct), []).append((split_idx, stats, passed))

    n_splits = len(LTC_SPLITS)
    all_pass = [c for c, r in by_combo.items() if len(r) == n_splits and all(p for _, _, p in r)]
    print(f"\n  Combos passing ALL {n_splits} splits: {len(all_pass)}")
    best = None
    for combo in sorted(all_pass, key=lambda c: -sum(s["total_return_pct"] for _, s, _ in by_combo[c])):
        sl_pct, tp_pct = combo
        results = by_combo[combo]
        avg_ret = sum(s["total_return_pct"] for _, s, _ in results) / len(results)
        avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in results) / len(results)
        print(f"    SL={sl_pct}% TP={tp_pct}% | avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% | " +
              ", ".join(f"s{i}:{s['total_return_pct']:+.1f}%" for i, s, _ in results))
        if best is None:
            best = (combo, avg_ret, avg_dd, results)
    if not all_pass:
        print("    None.")
    return by_combo, all_pass, best


def step3b_indicator_toolkit(df):
    print("\n" + "=" * 100)
    print("STEP 3b: indicator toolkit (MACD/Stochastic/SuperTrend/ADX/VWAP/Ichimoku), "
          "Round 5/20 methodology, LTC's 4 splits (each split's own train->test)")
    print("=" * 100)
    setup_results = {}  # name -> list of (split_idx, validated[0])
    for split_idx, (train_start, train_end, test_start, test_end) in enumerate(LTC_SPLITS, 1):
        train_df = slice_dates(df, train_start, train_end)
        test_df = slice_dates(df, test_start, test_end)
        if len(train_df) < 100 or len(test_df) < 100:
            continue
        print(f"\n  Split {split_idx}: train {train_start}..{train_end} -> test {test_start}..{test_end}")
        for name, spec in SETUPS.items():
            validated = run_setup(name, spec, train_df, test_df)
            params, tp_pct, sl_pct, train_stats, test_stats, passed = validated[0]
            setup_results.setdefault(name, []).append((split_idx, test_stats, passed, params, tp_pct, sl_pct))
            tag = "PASS" if passed else "fail"
            print(f"    [{tag}] {name:26s} ret={test_stats['total_return_pct']:8.2f}% "
                  f"bh={test_stats['buy_hold_return_pct']:8.2f}% dd={test_stats['max_drawdown_pct']:6.2f}% "
                  f"trades={test_stats['round_trips']:3d} params={params}")

    n_splits = len(LTC_SPLITS)
    print(f"\n  Setups passing on EVERY split tested (of {n_splits}):")
    any_all_pass = False
    for name, results in setup_results.items():
        if len(results) == n_splits and all(p for _, _, p, *_ in results):
            any_all_pass = True
            avg_ret = sum(s["total_return_pct"] for _, s, *_ in results) / len(results)
            print(f"    {name}: avg_ret={avg_ret:.2f}% | " +
                  ", ".join(f"s{i}:{s['total_return_pct']:+.1f}%" for i, s, *_ in results))
    if not any_all_pass:
        print("    None - no indicator-toolkit setup validates on every split.")
    return setup_results


def diagnose_entry_bound(signal_df, live_sl, split_label):
    """Direct trade-level check (Round 26 follow-up 6/Round 27's method, not assumed):
    does the entry ever produce trades that reach positive profit, and does even the
    mathematically tightest floor still lose?"""
    stats_normal = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                              use_milestones=False, return_trades=True)
    trades = stats_normal.get("trades", [])
    if not trades:
        print(f"    {split_label}: no completed/open trades - cannot diagnose")
        return False
    never_positive = sum(1 for t in trades if t["peak_profit_pct"] <= 0)
    pct_never_positive = never_positive / len(trades) * 100
    tightest = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                          use_milestones=False, trail_pct=0.1, trail_step_pct=0.1)
    baseline_stats = simulate_wide_tp(signal_df, "entry", LIVE_TP_BASELINE, live_sl)
    baseline_passed = _passed_wide(baseline_stats)
    entry_bound = (not baseline_passed) and pct_never_positive >= 50 and tightest["total_return_pct"] < 0
    print(f"    {split_label}: {never_positive}/{len(trades)} trades ({pct_never_positive:.0f}%) never positive | "
          f"tightest-floor(0.1%) ret={tightest['total_return_pct']:+.2f}% | "
          f"baseline(SL={live_sl}%/TP={LIVE_TP_BASELINE}%) {'PASS' if baseline_passed else 'fail'} "
          f"({baseline_stats['total_return_pct']:+.2f}%) -> {'ENTRY-BOUND' if entry_bound else 'not entry-bound'}")
    return entry_bound


def step4_trailing_stop(df, entry_fn, live_sl, entry_label):
    print("\n" + "=" * 100)
    print(f"STEP 4: Round 26/27 trailing-stop-ratchet optimization on LTC's winning entry "
          f"({entry_label}, live_sl={live_sl}%), close-confirmed variant only")
    print("=" * 100)

    per_split_signal = []
    for split_idx, (_, _, test_start, test_end) in enumerate(LTC_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            continue
        signal_df = entry_fn(test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))

    print("\n  --- entry-bound diagnosis per split (direct trade-level check) ---")
    entry_bound_splits = []
    for split_idx, test_start, test_end, signal_df in per_split_signal:
        eb = diagnose_entry_bound(signal_df, live_sl, f"split {split_idx} ({test_start}..{test_end})")
        if eb:
            entry_bound_splits.append(split_idx)

    # wide grid - LTC's optimum, like DOGE's, might be much wider than BTC/ETH's 0.5-4%,
    # so don't assume the narrow grid is enough.
    trail_pct_grid = [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0]
    trail_step_grid = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, None]

    def run_scope(scope_label, kept_split_idxs):
        kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in kept_split_idxs]
        n = len(kept)
        if n == 0:
            print(f"\n  {scope_label}: no splits in scope, skipping")
            return None
        print(f"\n  --- {scope_label} ({n} splits: {[i for i, *_ in kept]}) ---")

        baseline_rows = []
        for i, ts, te, sdf in kept:
            bstats = simulate_wide_tp(sdf, "entry", LIVE_TP_BASELINE, live_sl)
            baseline_rows.append((i, bstats, _passed_wide(bstats)))
        b_avg_ret = sum(s["total_return_pct"] for _, s, _ in baseline_rows) / n
        b_avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in baseline_rows) / n
        b_pass_all = all(p for _, _, p in baseline_rows)
        print(f"    baseline(SL={live_sl}%/TP={LIVE_TP_BASELINE}%): "
              f"{'VALIDATES' if b_pass_all else 'does not validate'} | "
              f"avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}% | " +
              ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in baseline_rows))

        plain_rows = []
        for i, ts, te, sdf in kept:
            stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=False)
            plain_rows.append((i, stats, _passed_trail(stats)))
        p_avg_ret = sum(s["total_return_pct"] for _, s, _ in plain_rows) / n
        p_avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in plain_rows) / n
        p_pass_all = all(p for _, _, p in plain_rows)
        print(f"    default trail(1.5%/3%):  {'VALIDATES' if p_pass_all else 'does not validate'} | "
              f"avg_ret={p_avg_ret:.2f}% avg_dd={p_avg_dd:.2f}% | " +
              ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in plain_rows))

        # per-split in-sample grid, then joint out-of-sample scoring (pool top-5/split)
        per_split_grid = []
        for i, ts, te, sdf in kept:
            results = {}
            for trail_pct in trail_pct_grid:
                for step in trail_step_grid:
                    stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close",
                                                        use_milestones=False, trail_pct=trail_pct,
                                                        trail_step_pct=step)
                    results[(trail_pct, step)] = stats
            per_split_grid.append((i, results))
            best_key = max(results, key=lambda k: results[k]["total_return_pct"])
            print(f"    split {i} in-sample optimum: trail={best_key[0]:.2f}% "
                  f"step={_trail_step_label(best_key[1])} ret={results[best_key]['total_return_pct']:+.2f}%")

        candidate_params = {(1.5, 3.0)}  # always include the "default" spec baseline
        for i, results in per_split_grid:
            top5 = sorted(results.items(), key=lambda kv: -kv[1]["total_return_pct"])[:5]
            for key, _ in top5:
                candidate_params.add(key)

        # cross-split agreement (pairwise, on each split's own in-sample optimum)
        optima = [(i, max(results, key=lambda k: results[k]["total_return_pct"])) for i, results in per_split_grid]
        n_agree, n_pairs = 0, 0
        for a in range(len(optima)):
            for b in range(a + 1, len(optima)):
                n_pairs += 1
                agree, _ = _trail_params_agree(optima[a][1], optima[b][1])
                n_agree += agree
        print(f"    cross-split agreement: {n_agree}/{n_pairs} pairs")

        scored = []
        for trail_pct, step in candidate_params:
            rows = []
            for i, ts, te, sdf in kept:
                stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close",
                                                    use_milestones=False, trail_pct=trail_pct,
                                                    trail_step_pct=step)
                rows.append((i, stats, _passed_trail(stats)))
            n_pass = sum(1 for _, _, p in rows if p)
            avg_ret = sum(s["total_return_pct"] for _, s, _ in rows) / n
            avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in rows) / n
            scored.append((trail_pct, step, rows, n_pass == n, n_pass, avg_ret, avg_dd))
        scored.sort(key=lambda r: (-r[4], -r[5]))

        print(f"    top 8 pooled candidates ({len(candidate_params)} scored) by (splits passed, avg return):")
        for trail_pct, step, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:8]:
            print(f"      [{n_pass}/{n}] trail={trail_pct:.2f}% step={_trail_step_label(step):>10s} "
                  f"avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% | " +
                  ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in rows))

        winner = scored[0]
        return {"n": n, "baseline": (b_pass_all, b_avg_ret, b_avg_dd), "winner": winner}

    all_idxs = [i for i, *_ in per_split_signal]
    result_all = run_scope("ALL splits", all_idxs)
    result_ex = None
    if entry_bound_splits:
        kept_idxs = [i for i in all_idxs if i not in entry_bound_splits]
        result_ex = run_scope(f"EXCLUDING entry-bound split(s) {entry_bound_splits}", kept_idxs)

    # ladder check on the winning trail config from whichever scope has a winner
    base_result = result_ex if result_ex else result_all
    if base_result:
        w_trail, w_step, w_rows, w_pass_all, w_n_pass, w_avg_ret, w_avg_dd = base_result["winner"]
        scope_idxs = [i for i, s, p in w_rows]
        kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in scope_idxs]
        print(f"\n  --- milestone ladder check on top of the winning trail "
              f"(trail={w_trail:.2f}% step={_trail_step_label(w_step)}) ---")
        for ladder_name, ladder_fn in [("Design D (tight static)", milestone_floor_tight_static),
                                        ("Design E (peak-relative)", milestone_floor_peak_relative),
                                        ("Design F (peak-relative aggressive)", milestone_floor_peak_relative_aggressive)]:
            rows = []
            for i, ts, te, sdf in kept:
                stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close",
                                                    use_milestones=True, milestone_fn=ladder_fn,
                                                    trail_pct=w_trail, trail_step_pct=w_step)
                rows.append((i, stats, _passed_trail(stats)))
            avg_ret = sum(s["total_return_pct"] for _, s, _ in rows) / len(rows)
            avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in rows) / len(rows)
            delta = avg_ret - w_avg_ret
            print(f"    {ladder_name}: avg_ret={avg_ret:7.2f}% ({delta:+.2f}pp vs plain trail) avg_dd={avg_dd:6.2f}%")

    return result_all, result_ex, entry_bound_splits


def main():
    df = load_kraken_csv_1min(LTC_CSV_PATH)
    print(f"LTC native Kraken daily data: {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()}")

    by_combo, all_pass, best_baseline = step3a_baseline_grid(df)
    setup_results = step3b_indicator_toolkit(df)

    # decide winning entry: does any indicator setup beat baseline's all-4-splits result?
    winner_name = "baseline_signal_entry"
    winner_fn = baseline_signal_entry
    winner_live_sl = best_baseline[0][0] if best_baseline else LIVE_SL_BASELINE
    winner_live_tp = best_baseline[0][1] if best_baseline else LIVE_TP_BASELINE
    print("\n" + "=" * 100)
    print("ENTRY DECISION")
    print("=" * 100)
    if best_baseline:
        print(f"  baseline_signal_entry SL={winner_live_sl}%/TP={winner_live_tp}% validates all "
              f"{len(LTC_SPLITS)} splits, avg_ret={best_baseline[1]:.2f}% avg_dd={best_baseline[2]:.2f}%")
    else:
        print("  baseline_signal_entry: no (SL,TP) combo validates all splits")
    any_indicator_all_pass = any(
        len(r) == len(LTC_SPLITS) and all(p for _, _, p, *_ in r) for r in setup_results.values()
    )
    if not any_indicator_all_pass:
        print("  No indicator-toolkit setup beats/matches this -> STICKING WITH baseline_signal_entry.")
    else:
        print("  An indicator setup validated on every split - see step 3b output above for details; "
              "manual review recommended before overriding baseline.")
    print(f"  WINNING ENTRY: {winner_name} (SL={winner_live_sl}%, TP={winner_live_tp}% pre-trailing-stop)")

    result_all, result_ex, entry_bound_splits = step4_trailing_stop(df, winner_fn, winner_live_sl, winner_name)

    print("\n" + "=" * 100)
    print("FINAL VERDICT")
    print("=" * 100)
    print(f"  Entry: {winner_name}, live_sl={winner_live_sl}%, live_tp={winner_live_tp}%")
    print(f"  Entry-bound splits (diagnosed, not assumed): {entry_bound_splits if entry_bound_splits else 'none'}")
    for label, result in [("ALL splits", result_all), ("excluding entry-bound split(s)", result_ex)]:
        if result is None:
            continue
        w_trail, w_step, w_rows, w_pass_all, w_n_pass, w_avg_ret, w_avg_dd = result["winner"]
        b_pass_all, b_avg_ret, b_avg_dd = result["baseline"]
        pass_str = "VALIDATES" if w_pass_all else f"{w_n_pass}/{result['n']} splits"
        print(f"  [{label}] recommended trail={w_trail:.2f}% step={_trail_step_label(w_step)} | "
              f"{pass_str} | "
              f"avg_ret={w_avg_ret:.2f}% avg_dd={w_avg_dd:.2f}% (baseline: {b_avg_ret:.2f}%/{b_avg_dd:.2f}%dd, "
              f"{'validated' if b_pass_all else 'not validated'})")


if __name__ == "__main__":
    main()
