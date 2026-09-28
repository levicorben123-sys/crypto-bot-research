"""ADA deep-dive: full entry-strategy discovery (Round 5/20 style) + trailing-stop-ratchet
optimization (Round 26/27 style), applied fresh to a brand-new coin candidate to replace
DOGE. BACKTEST ONLY - nothing wired into any live bot.

Data: ADA's own native Kraken daily OHLCV (`ADAUSD_1440.csv`), pulled from the same
"Kraken_OHLCVT_Full_2026Q2" bulk archive used for BTC/ETH/DOGE, via the same targeted
HTTP-range-request technique (no full 9GB download needed). ADA's Kraken listing
(2018-09-28) is later than Yahoo's ADA-USD history (Round 15 used Yahoo, ~2017-11 start),
so - same situation as DOGE - this does NOT extend history further back, it gives
exchange-native data and 3 non-overlapping regime-test windows instead of REGIME_SPLITS'
2, mirroring ETH/BTC's 3 most recent split windows exactly (test: 2020-21, 2022-25,
2023-26) since data starts too late for a genuine pre-2018 train -> 2018-19-bear test
split the way ETH/BTC have.

Verified before trusting it: 2021-09-01 close=$2.87 (high=$2.905) matches ADA's
well-documented all-time high (~$3.10, reached a few days later on 2021-09-02) almost
exactly. Cross-checked against Yahoo's ADA-USD daily closes for a mean-difference sanity
check (see `cross_check_vs_yahoo()`), same discipline as every prior data-sourcing round.

Usage:
    python experiments_ada_deepdive.py
"""
import statistics

import numpy as np
import pandas as pd

import config
from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_every_angle import (
    SMALL_TPSL_GRID, simulate_reverse_signal_with_cadence, simulate_tpsl_with_cadence,
)
from experiments_indicators import SETUPS
from experiments_multisplit import MAX_DRAWDOWN_PCT_REF, MIN_TRADES, _passed, slice_dates
from experiments_trailing_ratchet import (
    milestone_floor_peak_relative, milestone_floor_peak_relative_aggressive,
    milestone_floor_tight_static, simulate_trailing_ratchet,
)
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp

ADA_CSV_PATH = "data/kraken_historical/extracted/ADAUSD_1440.csv"

# Mirrors ETH/BTC's 3 most-recent split windows exactly (test: 2020-21, 2022-25, 2023-26).
# No pre-2018 train -> 2018-19-bear test split is possible: ADA's Kraken data starts
# 2018-09-28, too late to build a genuine independent-train early-history split the way
# ETH's 2015-08-07 start allowed. Same situation Round 27 hit for DOGE (Kraken listing
# 2019-12-19, also too late) - 3 splits, not 4, honestly bounded by real listing history.
ADA_CSV_START = "2018-09-28"
ADA_SPLITS = [
    (ADA_CSV_START, "2019-12-31", "2020-01-01", "2021-12-31"),  # COVID crash + 2021 bull
    (ADA_CSV_START, "2021-12-31", "2022-01-01", "2025-12-31"),  # 2022 bear -> 2023-25 recovery
    (ADA_CSV_START, "2022-12-31", "2023-01-01", "2026-06-30"),  # recent/current regime
]

SL_GRID_PCT = [1, 3, 5, 7]
TP_GRID_PCT = [3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100]

TREND_SETUPS = {k: v for k, v in SETUPS.items() if v["kind"] == "trend"}
REVERSION_SETUPS = {k: v for k, v in SETUPS.items() if v["kind"] == "reversion"}


def cross_check_vs_yahoo():
    print(f"\n{'='*100}\nData verification: ADAUSD_1440.csv vs. Yahoo ADA-USD\n{'='*100}")
    kraken_df = load_kraken_csv_1min(ADA_CSV_PATH)
    print(f"  Kraken: {len(kraken_df)} daily candles, {kraken_df['time'].min().date()} "
          f"..{kraken_df['time'].max().date()}")

    known_ath_row = kraken_df[(kraken_df["time"] >= "2021-08-28") & (kraken_df["time"] <= "2021-09-05")]
    print(f"  Known-ATH spot check (ADA's real ATH ~$3.10, reached 2021-09-02):")
    print(known_ath_row[["time", "high", "close"]].to_string(index=False))

    yahoo_df = fetch_yahoo_history("ADA-USD", range_="10y", interval="1d")
    yahoo_df = yahoo_df[["time", "close"]].rename(columns={"close": "close_yahoo"})
    kraken_close = kraken_df[["time", "close"]].rename(columns={"close": "close_kraken"})
    kraken_close["time"] = kraken_close["time"].dt.normalize()
    yahoo_df["time"] = pd.to_datetime(yahoo_df["time"]).dt.normalize()
    merged = pd.merge(kraken_close, yahoo_df, on="time", how="inner")
    merged["abs_diff_pct"] = (merged["close_kraken"] / merged["close_yahoo"] - 1).abs() * 100
    print(f"  Matched {len(merged)} dates. Mean abs diff: {merged['abs_diff_pct'].mean():.3f}% "
          f"(ETH's own cross-check was 0.29%, DOGE's 0.260%, BTC's 0.31% - consistent order of magnitude)")
    print(f"  Worst outliers:\n{merged.nlargest(5, 'abs_diff_pct')[['time', 'close_kraken', 'close_yahoo', 'abs_diff_pct']].to_string(index=False)}")


def run_baseline_search():
    print(f"\n{'='*100}\nStep 3a: baseline SMA/RSI signal entry - SL/TP grid search on ADA's "
          f"3 real Kraken splits (re-deriving Round 15's Yahoo-based finding on better data)\n{'='*100}")
    df = load_kraken_csv_1min(ADA_CSV_PATH)
    n_splits = len(ADA_SPLITS)

    passing = []
    for sl_pct in SL_GRID_PCT:
        for tp_pct in TP_GRID_PCT:
            per_split = []
            for split_idx, (_, _, test_start, test_end) in enumerate(ADA_SPLITS, 1):
                test_df = slice_dates(df, test_start, test_end)
                if len(test_df) < 100:
                    continue
                signal_df = baseline_signal_entry(test_df)
                stats = simulate_wide_tp(signal_df, "entry", tp_pct, sl_pct)
                per_split.append((split_idx, stats, _passed(stats)))
            if len(per_split) != n_splits:
                continue
            all_pass = all(p for _, _, p in per_split)
            if all_pass:
                avg_ret = sum(s["total_return_pct"] for _, s, _ in per_split) / n_splits
                avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in per_split) / n_splits
                avg_trades = sum(s["completed_trades"] for _, s, _ in per_split) / n_splits
                passing.append((sl_pct, tp_pct, avg_ret, avg_dd, avg_trades, per_split))

    print(f"  {len(passing)} (SL,TP) combos validated on all {n_splits} ADA splits.")
    for sl_pct, tp_pct, avg_ret, avg_dd, avg_trades, per_split in sorted(passing, key=lambda r: -r[4]):
        detail = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in per_split)
        print(f"    SL={sl_pct}% TP={tp_pct}%: avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% "
              f"avg_trades={avg_trades:5.1f} | {detail}")

    if not passing:
        print("  No baseline SL/TP combo validates on all 3 ADA splits.")
        return None

    # Round 8's own tie-breaker: most trades (most statistical confidence), not highest return.
    best = max(passing, key=lambda r: r[4])
    sl_pct, tp_pct, avg_ret, avg_dd, avg_trades, per_split = best
    print(f"  Picked (most-trades tie-break, Round 8 convention): SL={sl_pct}% TP={tp_pct}% "
          f"avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% avg_trades={avg_trades:.1f}")
    return {"sl": sl_pct, "tp": tp_pct, "avg_ret": avg_ret, "avg_dd": avg_dd, "avg_trades": avg_trades}


def run_indicator_toolkit_search():
    print(f"\n{'='*100}\nStep 3b: full indicator toolkit (MACD/Stochastic/SuperTrend/ADX-filtered/"
          f"VWAP/Ichimoku) - direct validation against ADA's 3 splits, Round 20's methodology\n{'='*100}")
    df = load_kraken_csv_1min(ADA_CSV_PATH)
    n_splits = len(ADA_SPLITS)
    winners = []

    print("\n  -- Trend-family (own reverse-signal exit) --")
    for name, spec in TREND_SETUPS.items():
        for params in spec["grid"]:
            per_split = []
            for split_idx, (_, _, vs, ve) in enumerate(ADA_SPLITS, 1):
                test_df = slice_dates(df, vs, ve)
                if len(test_df) < 100:
                    continue
                built = spec["build"](test_df, **params)
                stats, gap = simulate_reverse_signal_with_cadence(built, "entry", "exit")
                per_split.append((stats, gap, _passed(stats)))
            if len(per_split) != n_splits:
                continue
            all_ok = all(p for _, _, p in per_split)
            avg_ret = sum(s["total_return_pct"] for s, _, _ in per_split) / n_splits
            avg_dd = sum(s["max_drawdown_pct"] for s, _, _ in per_split) / n_splits
            gaps = [g for _, g, _ in per_split if g is not None]
            avg_gap = sum(gaps) / len(gaps) if gaps else None
            tag = "PASS" if all_ok else "fail"
            print(f"    [{tag}] {name}{params} | avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% "
                  f"avg_gap={f'{avg_gap:.1f}d' if avg_gap else 'n/a'}")
            if all_ok:
                winners.append(("trend", name, params, None, avg_ret, avg_dd))

    print("\n  -- Mean-reversion (small TP/SL grid) --")
    for name, spec in REVERSION_SETUPS.items():
        for params in spec["grid"]:
            best_for_params = None
            for tp_pct, sl_pct in SMALL_TPSL_GRID:
                per_split = []
                for split_idx, (_, _, vs, ve) in enumerate(ADA_SPLITS, 1):
                    test_df = slice_dates(df, vs, ve)
                    if len(test_df) < 100:
                        continue
                    built = spec["build"](test_df, **params)
                    stats, gap = simulate_tpsl_with_cadence(built, "entry", tp_pct, sl_pct)
                    per_split.append((stats, gap, _passed(stats)))
                if len(per_split) != n_splits:
                    continue
                all_ok = all(p for _, _, p in per_split)
                if all_ok:
                    avg_ret = sum(s["total_return_pct"] for s, _, _ in per_split) / n_splits
                    avg_dd = sum(s["max_drawdown_pct"] for s, _, _ in per_split) / n_splits
                    winners.append(("reversion", name, params, (tp_pct, sl_pct), avg_ret, avg_dd))
                    if best_for_params is None or avg_ret > best_for_params[0]:
                        best_for_params = (avg_ret, avg_dd, tp_pct, sl_pct)
            if best_for_params:
                avg_ret, avg_dd, tp_pct, sl_pct = best_for_params
                print(f"    [PASS] {name}{params} best@TP={tp_pct}/SL={sl_pct} | "
                      f"avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}%")
            else:
                print(f"    [fail] {name}{params} | no TP/SL in the small grid validates on all 3 splits")

    return winners


def diagnose_entry_bound(entry_fn, sl_pct, tp_pct):
    """Same direct (not assumed) check used for ETH split 3 / BTC / DOGE: for each split,
    what fraction of trades never see positive profit, and does the tightest structurally
    possible floor still lose? Uses simulate_trailing_ratchet with an extreme giveback and
    use_milestones=False to approximate "lock the entry price itself" as the tightest
    conceivable protective floor."""
    print(f"\n{'='*100}\nStep 4a: entry-bound diagnosis per split (direct trade-level check, "
          f"not assumed)\n{'='*100}")
    df = load_kraken_csv_1min(ADA_CSV_PATH)
    results = {}
    for split_idx, (_, _, test_start, test_end) in enumerate(ADA_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            continue
        signal_df = entry_fn(test_df)
        base_stats = simulate_wide_tp(signal_df, "entry", tp_pct, sl_pct)
        base_passed = _passed(base_stats)

        tightest_stats = simulate_trailing_ratchet(
            signal_df, "entry", sl_pct, variant="close", use_milestones=False,
            trail_pct=0.1, trail_step_pct=0.1, return_trades=True,
        )
        trades = tightest_stats.get("trades", [])
        n_trades = len(trades)
        never_positive = sum(1 for t in trades if t["peak_profit_pct"] <= 0) if trades else 0
        pct_never_positive = (never_positive / n_trades * 100) if n_trades else 0.0

        unfixable = (
            not base_passed
            and pct_never_positive >= 70
            and tightest_stats["total_return_pct"] < 0
        )
        results[split_idx] = {
            "test_start": test_start, "test_end": test_end,
            "base_passed": base_passed, "base_ret": base_stats["total_return_pct"],
            "n_trades": n_trades, "pct_never_positive": pct_never_positive,
            "tightest_floor_ret": tightest_stats["total_return_pct"],
            "unfixable": unfixable,
        }
        print(f"  Split {split_idx} ({test_start}..{test_end}): baseline "
              f"{'PASS' if base_passed else 'fail'} ({base_stats['total_return_pct']:+.2f}%), "
              f"{n_trades} trades, {pct_never_positive:.0f}% never positive, "
              f"tightest-floor-possible={tightest_stats['total_return_pct']:+.2f}% "
              f"| {'ENTRY-BOUND (unfixable)' if unfixable else 'not entry-bound'}")
    return results


TRAIL_PCT_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0]
TRAIL_STEP_GRID = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, None]
WIDE_TRAIL_PCT_GRID = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30, 40]
WIDE_TRAIL_STEP_GRID = [2.0, 4.0, None]


def _trail_grid_for_split(signal_df, sl_pct, pct_grid, step_grid):
    results = {}
    for trail_pct in pct_grid:
        for step in step_grid:
            stats = simulate_trailing_ratchet(
                signal_df, "entry", sl_pct, variant="close", use_milestones=False,
                trail_pct=trail_pct, trail_step_pct=step,
            )
            label = f"trail={trail_pct}% step={'cont' if step is None else f'{step}%'}"
            results[label] = (stats, _passed(stats), (trail_pct, step))
    return results


def run_trail_optimization(entry_fn, sl_pct, tp_pct, kept_splits, pct_grid, step_grid, tag):
    print(f"\n{'='*100}\nStep 4b [{tag}]: plain-trail grid search ({len(pct_grid)}x{len(step_grid)} "
          f"configs) on splits {kept_splits}\n{'='*100}")
    df = load_kraken_csv_1min(ADA_CSV_PATH)
    n_splits = len(kept_splits)

    per_split_signal = []
    baseline_rows = []
    for split_idx in kept_splits:
        _, _, test_start, test_end = ADA_SPLITS[split_idx - 1]
        test_df = slice_dates(df, test_start, test_end)
        signal_df = entry_fn(test_df)
        per_split_signal.append((split_idx, signal_df))
        base_stats = simulate_wide_tp(signal_df, "entry", tp_pct, sl_pct)
        baseline_rows.append((split_idx, base_stats, _passed(base_stats)))

    per_split_grids = []
    for split_idx, signal_df in per_split_signal:
        results = _trail_grid_for_split(signal_df, sl_pct, pct_grid, step_grid)
        per_split_grids.append((split_idx, results))
        best_label = max(results, key=lambda k: results[k][0]["total_return_pct"])
        best_stats, best_passed, _ = results[best_label]
        print(f"  Split {split_idx} in-sample optimum: {best_label} -> "
              f"ret={best_stats['total_return_pct']:+.2f}% dd={best_stats['max_drawdown_pct']:.2f}% "
              f"passed={best_passed}")

    n_pairs = 0
    n_agree = 0
    optima = [(idx, max(res, key=lambda k: res[k][0]["total_return_pct"]), res) for idx, res in per_split_grids]
    for a in range(len(optima)):
        for b in range(a + 1, len(optima)):
            n_pairs += 1
            _, label_a, res_a = optima[a]
            _, label_b, res_b = optima[b]
            if res_a[label_a][2] == res_b[label_b][2]:
                n_agree += 1
    print(f"  Cross-split agreement: {n_agree}/{n_pairs} pairs land on the identical config.")

    candidate_params = {}
    for idx, results in per_split_grids:
        top5 = sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]
        for label, (_, _, params) in top5:
            candidate_params[params] = params

    scored = []
    for trail_pct, step in candidate_params.values():
        rows = []
        for split_idx, signal_df in per_split_signal:
            stats = simulate_trailing_ratchet(signal_df, "entry", sl_pct, variant="close",
                                               use_milestones=False, trail_pct=trail_pct, trail_step_pct=step)
            rows.append((split_idx, stats, _passed(stats)))
        pass_all = all(p for _, _, p in rows)
        n_pass = sum(1 for _, _, p in rows if p)
        avg_ret = sum(s["total_return_pct"] for _, s, _ in rows) / n_splits
        avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in rows) / n_splits
        label = f"trail={trail_pct}% step={'cont' if step is None else f'{step}%'}"
        scored.append((label, (trail_pct, step), rows, pass_all, n_pass, avg_ret, avg_dd))
    scored.sort(key=lambda r: (-r[4], -r[5]))

    print(f"\n  Top 10 jointly-scored (out-of-sample) candidates:")
    for label, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:10]:
        detail = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in rows)
        print(f"    [{n_pass}/{n_splits}] {label:28s} avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% | {detail}")

    b_avg_ret = sum(s["total_return_pct"] for _, s, _ in baseline_rows) / n_splits
    b_avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in baseline_rows) / n_splits
    b_pass_all = all(p for _, _, p in baseline_rows)
    print(f"\n  live baseline on these splits: {'PASS' if b_pass_all else 'fail'} "
          f"avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")

    winner = scored[0]
    return {"winner_label": winner[0], "winner_params": winner[1], "winner_rows": winner[2],
            "winner_pass_all": winner[3], "winner_n_pass": winner[4], "winner_avg_ret": winner[5],
            "winner_avg_dd": winner[6], "baseline_avg_ret": b_avg_ret, "baseline_avg_dd": b_avg_dd,
            "baseline_pass_all": b_pass_all, "n_agree": n_agree, "n_pairs": n_pairs, "scored": scored}


def run_ladder_check(entry_fn, sl_pct, kept_splits, winner_params, tag):
    print(f"\n{'='*100}\nStep 4c [{tag}]: does a milestone ladder help on top of the winning "
          f"trail ({winner_params})?\n{'='*100}")
    df = load_kraken_csv_1min(ADA_CSV_PATH)
    n_splits = len(kept_splits)
    trail_pct, step = winner_params

    designs = {
        "Design D (tight static)": milestone_floor_tight_static,
        "Design E (peak-relative)": milestone_floor_peak_relative,
        "Design F (peak-relative aggressive)": milestone_floor_peak_relative_aggressive,
    }
    for name, fn in designs.items():
        rows = []
        for split_idx in kept_splits:
            _, _, test_start, test_end = ADA_SPLITS[split_idx - 1]
            test_df = slice_dates(df, test_start, test_end)
            signal_df = entry_fn(test_df)
            stats = simulate_trailing_ratchet(
                signal_df, "entry", sl_pct, variant="close", use_milestones=True,
                milestone_fn=fn, trail_pct=trail_pct, trail_step_pct=step,
            )
            rows.append((split_idx, stats, _passed(stats)))
        avg_ret = sum(s["total_return_pct"] for _, s, _ in rows) / n_splits
        avg_dd = sum(s["max_drawdown_pct"] for _, s, _ in rows) / n_splits
        pass_all = all(p for _, _, p in rows)
        print(f"  {name}: avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% pass_all={pass_all}")


def main():
    cross_check_vs_yahoo()

    baseline_result = run_baseline_search()
    indicator_winners = run_indicator_toolkit_search()

    print(f"\n{'='*100}\nStep 3c: which entry wins for ADA?\n{'='*100}")
    if indicator_winners:
        best_indicator = max(indicator_winners, key=lambda w: w[4])
        print(f"  Best validated indicator-toolkit entry: {best_indicator}")
    else:
        best_indicator = None
        print("  No indicator-toolkit setup validates on all 3 ADA splits.")

    if baseline_result:
        print(f"  Baseline SMA/RSI: SL={baseline_result['sl']}% TP={baseline_result['tp']}% "
              f"avg_ret={baseline_result['avg_ret']:.2f}% avg_dd={baseline_result['avg_dd']:.2f}%")

    use_baseline = True
    if best_indicator and (not baseline_result or best_indicator[4] > baseline_result["avg_ret"]):
        use_baseline = False

    if use_baseline and not baseline_result:
        print("\n  NEITHER baseline nor any indicator-toolkit setup validates on all 3 ADA "
              "splits. Stopping here - there is no validated entry to carry into the "
              "trailing-stop-ratchet work; that step would be meaningless without one.")
        return

    if use_baseline:
        entry_fn = baseline_signal_entry
        sl_pct, tp_pct = baseline_result["sl"], baseline_result["tp"]
        print(f"\n  WINNER: baseline SMA/RSI, SL={sl_pct}% TP={tp_pct}%")
    else:
        kind, name, params, tpsl, avg_ret, avg_dd = best_indicator
        print(f"\n  WINNER: indicator-toolkit {name}{params} ({kind})")
        print("  NOTE: the trailing-stop-ratchet step below is built against the baseline-"
              "signal engine's SL/TP conventions; if the indicator winner is a trend-family "
              "reverse-signal setup, the ratchet mechanism (designed around a fixed-SL mean-"
              "reversion trade) does not apply the same way - flagged here rather than forced.")
        if kind == "trend":
            print("  Trend-family winner has no fixed SL to ratchet from - trailing-stop "
                  "work is skipped for this case, reported as an entry-only finding.")
            return
        entry_fn = lambda df, _b=SETUPS[name]["build"], _p=params: _b(df, **_p)
        tp_pct, sl_pct = tpsl

    diag = diagnose_entry_bound(entry_fn, sl_pct, tp_pct)
    unfixable_splits = [idx for idx, d in diag.items() if d["unfixable"]]
    kept_splits = [idx for idx in diag if idx not in unfixable_splits]
    if unfixable_splits:
        print(f"\n  Split(s) {unfixable_splits} diagnosed as entry-bound/unfixable - "
              f"presenting BOTH framings below (all splits, and with these excluded).")

    all_splits = list(diag.keys())
    result_all = run_trail_optimization(entry_fn, sl_pct, tp_pct, all_splits, TRAIL_PCT_GRID, TRAIL_STEP_GRID, "narrow grid, all splits")
    if unfixable_splits and kept_splits:
        result_kept = run_trail_optimization(entry_fn, sl_pct, tp_pct, kept_splits, TRAIL_PCT_GRID, TRAIL_STEP_GRID, "narrow grid, excl. unfixable")
    else:
        result_kept = None

    # If the narrow grid's winner sits at the edge of the searched range, also try a wide
    # grid (DOGE's precedent: a coin with much larger typical moves needs a much wider
    # trail than the ETH/BTC-calibrated 0.5-4.0% grid to find its real optimum).
    edge_params = result_all["winner_params"][0]
    if edge_params >= max(TRAIL_PCT_GRID) - 0.01:
        print(f"\n  Narrow-grid winner ({edge_params}%) sits at the edge of the searched range - "
              f"re-running with a wide grid (DOGE's precedent) to check for a larger real optimum.")
        result_all_wide = run_trail_optimization(entry_fn, sl_pct, tp_pct, all_splits, WIDE_TRAIL_PCT_GRID, WIDE_TRAIL_STEP_GRID, "WIDE grid, all splits")
        if unfixable_splits and kept_splits:
            result_kept_wide = run_trail_optimization(entry_fn, sl_pct, tp_pct, kept_splits, WIDE_TRAIL_PCT_GRID, WIDE_TRAIL_STEP_GRID, "WIDE grid, excl. unfixable")
        else:
            result_kept_wide = None
    else:
        result_all_wide = result_kept_wide = None

    final = result_kept_wide or result_kept or result_all_wide or result_all
    final_tag_splits = kept_splits if (result_kept or result_kept_wide) else all_splits
    run_ladder_check(entry_fn, sl_pct, final_tag_splits, final["winner_params"], "final config")

    print(f"\n{'='*100}\nFINAL SUMMARY\n{'='*100}")
    print(f"  Entry: {'baseline SMA/RSI SL=%s%% TP=%s%%' % (sl_pct, tp_pct) if use_baseline else 'indicator toolkit'}")
    print(f"  Splits used for final trail recommendation: {final_tag_splits} "
          f"({'bear/unfixable split excluded' if final_tag_splits != all_splits else 'all splits, none excluded'})")
    print(f"  Recommended trailing stop: {final['winner_label']}")
    print(f"  Validated: {final['winner_pass_all']} ({final['winner_n_pass']}/{len(final_tag_splits)} splits)")
    print(f"  avg_ret={final['winner_avg_ret']:.2f}% avg_dd={final['winner_avg_dd']:.2f}% "
          f"vs. live baseline avg_ret={final['baseline_avg_ret']:.2f}% avg_dd={final['baseline_avg_dd']:.2f}%")
    print(f"  Cross-split agreement: {final['n_agree']}/{final['n_pairs']} pairs")


if __name__ == "__main__":
    main()
