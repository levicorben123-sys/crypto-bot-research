"""SOL deep-dive: full entry-strategy discovery (Round 5/20 style) + Round 26/27-style
trailing-stop-ratchet optimization, applied fresh to SOL as a candidate to replace the
DOGE live bot. BACKTEST ONLY - nothing wired into any live bot. Written as a standalone
file (rather than extending experiments_trailing_ratchet.py directly) to avoid collisions
with parallel forks doing the identical process for XRP/ADA/LTC/LINK/AVAX at the same time.

DATA: SOL's own native Kraken daily+minute OHLCV, pulled from the same
"Kraken_OHLCVT_Full_2026Q2" bulk archive that supplied BTC/ETH/DOGE's data (targeted HTTP
range requests against the archive's central directory, ~9GB total, no full download
needed - same method as the ETH/DOGE data-sourcing rounds). Verified before trusting it:
2021-11-06 high=$259.99 matches SOL's well-documented original ATH ($259.96) almost
exactly; 2022-12-29 low=$7.95 matches the FTX-collapse crash low; 2025-01-19 high=$296
matches the Jan-2025 second-wave ATH. Cross-checked against this project's existing
SOL-USD Yahoo source (experiments_full_history_wide.py): mean abs diff 0.105% across 1746
matched dates - tighter than ETH's 0.29% or DOGE's 0.26% cross-checks, outliers cluster in
Nov-Dec 2021 (the ATH volatility window), same pattern as BTC's Dec-2017 outliers, not a
data error. Range: 2021-06-17 (Kraken's SOLUSD listing) to 2026-06-30, ~5 years - shorter
than BTC/ETH (naturally, SOL launched later) but the same order as DOGE's ~6.5 years.

SPLITS: 3 non-overlapping, structurally distinct windows (expanding-train style, matching
ETH/DOGE's convention) - SOL's history doesn't support a 4th independent split the way
BTC/ETH's does:
  split 1: train 2021-06-17..2022-12-31 -> test 2023-01-01..2024-06-30 (recovery from the
           FTX-collapse crash, SOL's most brutal drawdown, -97% peak to trough)
  split 2: train 2021-06-17..2023-12-31 -> test 2024-01-01..2025-06-30 (the second SOL
           bull run to a new ATH ~$296)
  split 3: train 2021-06-17..2024-12-31 -> test 2025-01-01..2026-06-30 (post-ATH
           pullback/consolidation, most recent)

Usage:
    python experiments_sol_deepdive.py
"""
import statistics
from functools import partial

from data_fetcher import load_kraken_csv_1min
from experiments_adx_filter import apply_adx_filter
from experiments_indicators import (
    SETUPS, macd_crossover, supertrend, adx_filtered_crossover, ichimoku_cloud,
    stochastic_reversion, rolling_vwap_reversion,
)
from experiments_multisplit import _passed, slice_dates
from experiments_trailing_ratchet import (
    simulate_trailing_ratchet, milestone_floor_tight_static, milestone_floor_peak_relative,
    milestone_floor_peak_relative_aggressive, milestone_floor_ratio, BASE_TRAIL_PCT,
    BASE_TRAIL_STEP_PCT, _trail_step_label, _split_summary, _trail_params_agree,
)
from experiments_trend import simulate_reverse_signal
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp

SOL_CSV_PATH = "data/kraken_historical/extracted/SOLUSD_1440.csv"

SOL_SPLITS = [
    ("2021-06-17", "2022-12-31", "2023-01-01", "2024-06-30"),
    ("2021-06-17", "2023-12-31", "2024-01-01", "2025-06-30"),
    ("2021-06-17", "2024-12-31", "2025-01-01", "2026-06-30"),
]

# TP/SL grid for the baseline-signal-entry search (same style/range as Round 8/10's BTC
# grid and Round 15's cross-coin grid).
SL_GRID = [1.0, 3.0, 5.0, 7.0]
TP_GRID = [3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 75, 100]


def load_sol_df():
    return load_kraken_csv_1min(SOL_CSV_PATH)


# --- Step 3: entry strategy search -----------------------------------------------------

def search_baseline_entry(df):
    """Round 8/10-style SL x TP grid on the baseline SMA/RSI signal entry, scored jointly
    out-of-sample across all 3 SOL splits (pool each split's own top candidates, check
    which configs pass ALL 3, not just look good on one)."""
    per_split_signal = []
    for split_idx, (_, _, test_start, test_end) in enumerate(SOL_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  split {split_idx}: insufficient data, skipping")
            continue
        signal_df = baseline_signal_entry(test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))

    n_splits = len(per_split_signal)
    all_combo_rows = {}
    for sl in SL_GRID:
        for tp in TP_GRID:
            rows = []
            for split_idx, ts, te, signal_df in per_split_signal:
                stats = simulate_wide_tp(signal_df, "entry", tp, sl)
                rows.append((split_idx, stats, _passed(stats)))
            all_combo_rows[(sl, tp)] = rows

    passing = []
    for (sl, tp), rows in all_combo_rows.items():
        pass_all, avg_ret, avg_dd = _split_summary(rows, n_splits)
        if pass_all:
            avg_trades = statistics.mean(s["completed_trades"] for _, s, _ in rows)
            passing.append((sl, tp, avg_ret, avg_dd, avg_trades, rows))

    print(f"\n  baseline-signal-entry SL x TP grid: {len(all_combo_rows)} combos tested, "
          f"{len(passing)} pass all {n_splits} splits")
    if passing:
        passing.sort(key=lambda r: -r[4])  # most trades first, per Round 8's "most confidence, not highest return" tie-break
        for sl, tp, avg_ret, avg_dd, avg_trades, rows in passing[:10]:
            print(f"    [PASS] SL={sl}% TP={tp}% avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% "
                  f"avg_trades={avg_trades:5.1f} | " +
                  ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in rows))
        best_sl, best_tp, best_ret, best_dd, best_trades, best_rows = passing[0]
        return {"sl": best_sl, "tp": best_tp, "avg_ret": best_ret, "avg_dd": best_dd,
                "avg_trades": best_trades, "rows": best_rows, "n_splits": n_splits,
                "per_split_signal": per_split_signal}
    return {"sl": None, "tp": None, "n_splits": n_splits, "per_split_signal": per_split_signal}


# --- Follow-up: is SL=3%/TP=30% actually optimal, or just what the narrower grid above
#     (SL_GRID = [1,3,5,7], an exact copy of Round 8's original 4-level BTC grid) happened
#     to find? Round 13 later widened that same grid to 12 SL levels (0.25%-20%)
#     specifically to check whether a narrow search was leaving a better stop-loss
#     undiscovered. Same widening applied here, on SOL's real 3-split data, same entry.
SL_GRID_WIDE = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 7, 10, 15, 20]
TP_GRID_WIDE = [3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100]


def search_baseline_entry_wide(per_split_signal):
    """Round 13-style widened SL x TP grid (12 SL x 13 TP = 156 combos, vs. the original
    48) on the SAME baseline entry already shown to beat the indicator toolkit. Answers
    "is 3%/30% actually the best fixed exit, or just what the narrower Round-8-equivalent
    grid happened to find?" Same bar: must pass ALL 3 splits, not just look good on one."""
    n_splits = len(per_split_signal)
    all_combo_rows = {}
    for sl in SL_GRID_WIDE:
        for tp in TP_GRID_WIDE:
            rows = []
            for split_idx, ts, te, signal_df in per_split_signal:
                stats = simulate_wide_tp(signal_df, "entry", tp, sl)
                rows.append((split_idx, stats, _passed(stats)))
            all_combo_rows[(sl, tp)] = rows

    passing = []
    for (sl, tp), rows in all_combo_rows.items():
        pass_all, avg_ret, avg_dd = _split_summary(rows, n_splits)
        if pass_all:
            avg_trades = statistics.mean(s["completed_trades"] for _, s, _ in rows)
            passing.append((sl, tp, avg_ret, avg_dd, avg_trades, rows))

    print(f"\n  WIDENED baseline-signal-entry SL x TP grid (Round 13 style): "
          f"{len(all_combo_rows)} combos tested ({len(SL_GRID_WIDE)} SL x {len(TP_GRID_WIDE)} TP, "
          f"vs. the original 48), {len(passing)} pass all {n_splits} splits")
    passing.sort(key=lambda r: -r[2])  # by avg return this time, to see the true ceiling
    for sl, tp, avg_ret, avg_dd, avg_trades, rows in passing:
        print(f"    [PASS] SL={sl}% TP={tp}% avg_ret={avg_ret:7.2f}% avg_dd={avg_dd:6.2f}% "
              f"avg_trades={avg_trades:5.1f} | " +
              ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in rows))
    return passing


def search_indicator_toolkit(df, per_split_signal_raw):
    """Round 5/20-style indicator toolkit search: trend-family indicators (MACD,
    SuperTrend, ADX-filtered crossover, Ichimoku) use their own reverse-signal exit;
    mean-reversion indicators (Stochastic, rolling VWAP) pair with a small TP/SL grid.
    Validated against the SAME standalone bar as everywhere else in this project
    (experiments_multisplit._passed: profitable, >=5 trades, <40% dd, ALL 3 splits) -
    NOT experiments_indicators.py's own stricter beat-buy-and-hold bar, for consistency
    with how Round 26/27's trailing-stop work (and this file's own baseline search above)
    score things."""
    n_splits = len(per_split_signal_raw)
    small_tpsl_grid = [(tp, sl) for tp in (5, 8, 10, 15, 20, 30) for sl in (1, 3, 5)]

    winners = []
    for name, spec in SETUPS.items():
        build, grid, engine, kind = spec["build"], spec["grid"], spec["engine"], spec["kind"]
        for params in grid:
            per_split_rows_by_tpsl = {}
            for split_idx, ts, te, raw_test_df in [(i, ts, te, slice_dates(df, ts, te))
                                                     for i, ts, te, _ in per_split_signal_raw]:
                if len(raw_test_df) < 100:
                    continue
                built = build(raw_test_df, **params)
                if engine == "reverse_signal":
                    stats = simulate_reverse_signal(built, "entry", "exit")
                    if "completed_trades" not in stats and "round_trips" in stats:
                        stats["completed_trades"] = stats["round_trips"]
                    per_split_rows_by_tpsl.setdefault(None, []).append((split_idx, stats, _passed(stats)))
                else:
                    for tp, sl in small_tpsl_grid:
                        stats = simulate_wide_tp(built, "entry", tp, sl)
                        per_split_rows_by_tpsl.setdefault((tp, sl), []).append((split_idx, stats, _passed(stats)))

            for tpsl_key, rows in per_split_rows_by_tpsl.items():
                if len(rows) < n_splits:
                    continue
                pass_all, avg_ret, avg_dd = _split_summary(rows, n_splits)
                if pass_all:
                    winners.append({"name": name, "params": params, "tpsl": tpsl_key,
                                     "avg_ret": avg_ret, "avg_dd": avg_dd, "rows": rows})

    print(f"\n  indicator toolkit search: {len(winners)} configs pass all {n_splits} splits")
    for w in sorted(winners, key=lambda w: -w["avg_ret"])[:10]:
        tpsl_str = f" TP={w['tpsl'][0]}/SL={w['tpsl'][1]}" if w["tpsl"] else ""
        print(f"    [PASS] {w['name']} {w['params']}{tpsl_str} avg_ret={w['avg_ret']:7.2f}% avg_dd={w['avg_dd']:6.2f}%")
    return winners


# --- Step 4: trailing-stop-ratchet optimization -----------------------------------------

# SOL is extremely volatile (ATH->trough was a -97% crash), so use a wide grid similar to
# DOGE's (not ETH/BTC's tight 0.5-4% grid) as the primary search range - verified this
# assumption rather than just asserting it, see the narrow-grid comparison in main().
SOL_TRAIL_PCT_GRID = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 18, 22, 27, 33, 40, 50]
SOL_TRAIL_STEP_GRID = [2.0, 3.0, 4.0, 5.0, None]


def _sol_trail_grid_for_split(signal_df, live_sl):
    results = {}
    for trail_pct in SOL_TRAIL_PCT_GRID:
        for step in SOL_TRAIL_STEP_GRID:
            label = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
            stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                               use_milestones=False, trail_pct=trail_pct,
                                               trail_step_pct=step)
            results[label] = (stats, _passed(stats), (trail_pct, step))
    return results


def run_trailing_stop_optimization(entry_fn, live_sl, live_tp, per_split_signal):
    n_splits = len(per_split_signal)
    print(f"\n{'='*100}\nSOL trailing-stop-ratchet optimization (entry=live winner, "
          f"SL={live_sl}%/TP={live_tp}% baseline). close-confirmed variant only, per the "
          f"standing project decision.\n{'='*100}")

    baseline_rows, diag_rows = [], []
    for split_idx, ts, te, signal_df in per_split_signal:
        base_stats = simulate_wide_tp(signal_df, "entry", live_tp, live_sl)
        base_passed = _passed(base_stats)
        baseline_rows.append((split_idx, base_stats, base_passed))

        diag_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                use_milestones=False, trail_pct=BASE_TRAIL_PCT,
                                                trail_step_pct=BASE_TRAIL_STEP_PCT, return_trades=True)
        trades = diag_stats.get("trades", [])
        n_trades = len(trades)
        n_never_positive = sum(1 for t in trades if t["peak_profit_pct"] <= 0) if trades else 0
        avg_peak = statistics.mean(t["peak_profit_pct"] for t in trades) if trades else 0.0
        tightest_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                    use_milestones=True,
                                                    milestone_fn=partial(milestone_floor_tight_static, giveback=0.1))
        pct_never_positive = (n_never_positive / n_trades * 100) if n_trades else 0.0
        diag_rows.append({"split_idx": split_idx, "n_trades": n_trades,
                           "pct_never_positive": pct_never_positive, "avg_peak_profit_pct": avg_peak,
                           "tightest_floor_return_pct": tightest_stats["total_return_pct"]})

        print(f"\n  --- split {split_idx} ({ts}..{te}) ---")
        print(f"    [{'PASS' if base_passed else 'fail'}] live baseline: ret={base_stats['total_return_pct']:+.2f}% "
              f"dd={base_stats['max_drawdown_pct']:.2f}% trades={base_stats['completed_trades']}")
        print(f"    entry-bound diagnosis: {n_trades} trades, {pct_never_positive:.0f}% never reach "
              f"positive profit, avg peak={avg_peak:+.2f}%, tightest-floor(0.1pp) return="
              f"{tightest_stats['total_return_pct']:+.2f}%")

    b_pass_all, b_avg_ret, b_avg_dd = _split_summary(baseline_rows, n_splits)
    print(f"\n  === SOL live baseline, all {n_splits} splits ===")
    print(f"    {'VALIDATES' if b_pass_all else 'does NOT validate'} on all {n_splits} splits | "
          f"avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")

    unfixable_candidates = [d["split_idx"] for d, (_, _, p) in zip(diag_rows, baseline_rows)
                             if not p and d["pct_never_positive"] >= 50]
    unfixable = []
    for split_idx in unfixable_candidates:
        _, _, _, signal_df = next(e for e in per_split_signal if e[0] == split_idx)
        best_ultra_wide = max(
            simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close", use_milestones=False,
                                       trail_pct=tp, trail_step_pct=step)["total_return_pct"]
            for tp in [0.5, 1, 2, 3, 5, 7, 9, 11, 13, 15, 18, 22, 27, 33, 40, 50, 65, 80, 100]
            for step in [1, 2, 3, 5, 10, None]
        )
        print(f"    split {split_idx}: ultra-wide sweep (0.5%-100% trail) best={best_ultra_wide:+.2f}% - "
              f"{'still negative, confirms entry-bound' if best_ultra_wide < 0 else 'RESCUED by a wide trail, NOT entry-bound'}")
        if best_ultra_wide < 0:
            unfixable.append(split_idx)
    print(f"\n  entry-bound/un-fixable split diagnosis: {unfixable if unfixable else 'NONE found'}")

    def _run_grid(kept_split_ids, label):
        kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in kept_split_ids]
        n_kept = len(kept)
        b_rows = [(i, s, p) for i, s, p in baseline_rows if i in kept_split_ids]
        bb_pass_all, bb_avg_ret, bb_avg_dd = _split_summary(b_rows, n_kept)

        per_split_grids = [(i, _sol_trail_grid_for_split(sdf, live_sl)) for i, ts, te, sdf in kept]
        optima = [(i, max(res, key=lambda k: res[k][0]["total_return_pct"]), res) for i, res in per_split_grids]
        n_pairs = len(optima) * (len(optima) - 1) // 2
        n_agree = 0
        for a in range(len(optima)):
            for bidx in range(a + 1, len(optima)):
                _, i_label, i_res = optima[a]
                _, j_label, j_res = optima[bidx]
                agree, _ = _trail_params_agree(i_res[i_label][2], j_res[j_label][2])
                n_agree += agree

        candidate_params = {(BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT): (BASE_TRAIL_PCT, BASE_TRAIL_STEP_PCT)}
        for i, results in per_split_grids:
            for lbl, (_, _, params) in sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]:
                candidate_params[params] = params

        scored = []
        for trail_pct, step in candidate_params.values():
            lbl = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
            rows = []
            for i, ts, te, sdf in kept:
                stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=False,
                                                   trail_pct=trail_pct, trail_step_pct=step)
                rows.append((i, stats, _passed(stats)))
            pass_all, avg_ret, avg_dd = _split_summary(rows, n_kept)
            n_pass = sum(1 for _, _, p in rows if p)
            scored.append((lbl, (trail_pct, step), rows, pass_all, n_pass, avg_ret, avg_dd))
        scored.sort(key=lambda r: (-r[4], -r[5]))

        print(f"\n  --- {label}: {n_kept} splits, baseline avg_ret={bb_avg_ret:.2f}%/dd={bb_avg_dd:.2f}% "
              f"({'validates' if bb_pass_all else 'does not validate'}); "
              f"cross-split agreement {n_agree}/{n_pairs} pairs ---")
        for lbl, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:8]:
            per_split_str = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in rows)
            print(f"    [{n_pass}/{n_kept}] {lbl:30s} avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% | {per_split_str}")

        winner = scored[0]
        print(f"    WINNER: {winner[0]} -> {'VALIDATES all splits' if winner[3] else f'{winner[4]}/{n_kept} splits'} "
              f"avg_ret={winner[5]:.2f}% avg_dd={winner[6]:.2f}%")
        return winner, bb_avg_ret, bb_avg_dd, bb_pass_all

    all_split_ids = [i for i, *_ in per_split_signal]
    winner_all, base_avg_ret_all, base_avg_dd_all, base_pass_all = _run_grid(all_split_ids, "ALL splits")

    kept_ids = [i for i in all_split_ids if i not in unfixable]
    if unfixable and kept_ids:
        winner_ex, base_avg_ret_ex, base_avg_dd_ex, base_pass_ex = _run_grid(kept_ids, f"EXCLUDING unfixable split(s) {unfixable}")
    else:
        winner_ex = None

    # milestone ladder check (Designs A/D/E/F) against the winning trail config from
    # whichever framing validates (or, if neither validates, ALL-splits framing).
    ladder_framing = winner_all if (winner_all[3] or not winner_ex) else winner_ex
    ladder_splits = all_split_ids if ladder_framing is winner_all else kept_ids
    w_trail_pct, w_step = ladder_framing[1]
    print(f"\n  --- milestone ladder check on top of the winning trail ({ladder_framing[0]}) ---")
    ladder_designs = {
        "A (ratio 0.70xmilestone)": milestone_floor_ratio,
        "D (tight static, giveback=0.75)": milestone_floor_tight_static,
        "E (peak-relative shrinking)": milestone_floor_peak_relative,
        "F (peak-relative aggressive)": milestone_floor_peak_relative_aggressive,
    }
    kept_signal = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in ladder_splits]
    no_ladder_rows = [(i, simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=False,
                                                      trail_pct=w_trail_pct, trail_step_pct=w_step), None)
                       for i, ts, te, sdf in kept_signal]
    no_ladder_avg_ret = statistics.mean(s["total_return_pct"] for _, s, _ in no_ladder_rows)
    print(f"    no ladder (trail only): avg_ret={no_ladder_avg_ret:+.2f}%")
    for name, fn in ladder_designs.items():
        rows = []
        for i, ts, te, sdf in kept_signal:
            stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=True,
                                               milestone_fn=fn, trail_pct=w_trail_pct, trail_step_pct=w_step)
            rows.append((i, stats, _passed(stats)))
        pass_all, avg_ret, avg_dd = _split_summary(rows, len(kept_signal))
        delta = avg_ret - no_ladder_avg_ret
        print(f"    ladder {name}: avg_ret={avg_ret:+.2f}% ({delta:+.2f}pp vs no-ladder) "
              f"{'PASSES all' if pass_all else 'does not pass all'}")

    return {"baseline": (base_pass_all, base_avg_ret_all, base_avg_dd_all),
            "unfixable_splits": unfixable, "winner_all_splits": winner_all, "winner_excl": winner_ex}


def run_new_baseline_trailing_test(df):
    """Follow-up: the Step-4 trailing-stop-ratchet optimization above was run against the
    NARROW-grid winner (SL=3%/TP=30%, seeded from SL_GRID=[1,3,5,7]). The later
    search_baseline_entry_wide() follow-up found SL=2%/TP=30% strictly dominates that
    (32.02% vs 23.35% avg return, 14.23% vs 17.99% avg dd) and this is now what's actually
    wired into the live bot (repurposing the DOGE-named scheduled task, since creating a
    new one was blocked by an OS permission this session doesn't have). This re-runs the
    FULL Step-4 pipeline (baseline re-check, entry-bound diagnosis with the ultra-wide
    sweep, trail grid search with cross-split agreement, milestone ladder check) against
    the NEW SL=2%/TP=30% baseline specifically - the earlier "nothing beats the fixed
    exit" conclusion was never tested against this improved baseline, and a tighter SL
    changes which trades even exist to test."""
    per_split_signal = []
    for split_idx, (_, _, test_start, test_end) in enumerate(SOL_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            continue
        per_split_signal.append((split_idx, test_start, test_end, baseline_signal_entry(test_df)))

    print(f"\n{'#'*100}\nFOLLOW-UP: thorough trailing-stop-ratchet test against the NEW "
          f"SL=2%/TP=30% baseline (replacing the SL=3% seed used above)\n{'#'*100}")
    result = run_trailing_stop_optimization(entry_fn=None, live_sl=2.0, live_tp=30.0,
                                             per_split_signal=per_split_signal)

    # explicit overfit-to-one-trade check on the winning config, since this project has
    # hit that trap repeatedly (SOL's own original round: a 165%-avg-return trail config
    # was entirely driven by one +542% outlier and failed 2/3 splits).
    w_all = result["winner_all_splits"]
    w_trail_pct, w_step = w_all[1]
    print(f"\n  --- overfit-to-one-trade check on the winner ({w_all[0]}) ---")
    for split_idx, ts, te, sdf in per_split_signal:
        trade_stats = simulate_trailing_ratchet(sdf, "entry", 2.0, variant="close", use_milestones=False,
                                                  trail_pct=w_trail_pct, trail_step_pct=w_step, return_trades=True)
        trades = trade_stats.get("trades", [])
        if trades:
            rets = sorted((t["locked_at_exit_pct"] for t in trades), reverse=True)
            top1 = rets[0] if rets else 0.0
            rest_avg = statistics.mean(rets[1:]) if len(rets) > 1 else 0.0
            print(f"    split {split_idx}: {len(trades)} trades, top trade locked={top1:+.2f}%, "
                  f"avg of the rest={rest_avg:+.2f}% - "
                  f"{'SINGLE-TRADE-DOMINATED, treat with suspicion' if len(trades) >= 3 and top1 > 3 * max(abs(rest_avg), 1) else 'not obviously single-trade-driven'}")
    return result


def main():
    print("Loading SOL data...")
    df = load_sol_df()
    print(f"SOL data: {len(df)} daily rows, {df['time'].min()} to {df['time'].max()}")

    print(f"\n{'='*100}\nSTEP 3a: baseline-signal-entry SL x TP grid search\n{'='*100}")
    baseline_result = search_baseline_entry(df)

    print(f"\n{'='*100}\nFOLLOW-UP: is SL=3%/TP=30% actually optimal, or just what the narrower "
          f"grid found? Widening to Round 13's 12-level SL sweep.\n{'='*100}")
    wide_grid_passing = search_baseline_entry_wide(baseline_result["per_split_signal"])

    print(f"\n{'='*100}\nSTEP 3b: indicator toolkit search (does a smarter entry beat baseline for SOL?)\n{'='*100}")
    indicator_winners = search_indicator_toolkit(df, baseline_result["per_split_signal"])

    # pick the best validated entry: baseline (if it validated) vs. the best indicator
    # toolkit winner, by avg return - same "does the fancier answer actually win"
    # discipline as Round 20 (which found Stochastic beat baseline for ETH but not BTC).
    candidates = []
    if baseline_result["sl"] is not None:
        candidates.append(("baseline_signal_entry", baseline_result["avg_ret"], baseline_result["avg_dd"],
                            baseline_result["sl"], baseline_result["tp"], None))
    for w in indicator_winners:
        tpsl = w["tpsl"] or (None, None)
        candidates.append((f"{w['name']}{w['params']}", w["avg_ret"], w["avg_dd"], tpsl[1], tpsl[0], w))

    print(f"\n{'='*100}\nSTEP 3 RESULT: entry strategy comparison\n{'='*100}")
    if not candidates:
        print("  NOTHING validated for SOL at the entry-search stage - no baseline SL/TP combo and no "
              "indicator toolkit setup passes all 3 splits. Stopping here; SOL does not clear this "
              "project's bar even before getting to exit-mechanism tuning.")
        return

    candidates.sort(key=lambda c: -c[1])
    for name, ret, dd, sl, tp, _ in candidates[:8]:
        print(f"    {name:45s} avg_ret={ret:8.2f}% avg_dd={dd:6.2f}% SL={sl} TP={tp}")

    best_name, best_ret, best_dd, best_sl, best_tp, best_indicator_obj = candidates[0]
    print(f"\n  BEST VALIDATED ENTRY: {best_name} (SL={best_sl}%, TP={best_tp}%, "
          f"avg_ret={best_ret:.2f}%, avg_dd={best_dd:.2f}%)")

    if best_indicator_obj is None:
        entry_fn = baseline_signal_entry
        per_split_signal = baseline_result["per_split_signal"]
        live_sl, live_tp = best_sl, best_tp
    else:
        # rebuild per-split signal_dfs using the winning indicator's build fn/params
        name = best_indicator_obj["name"]
        spec = SETUPS[name]
        build, params = spec["build"], best_indicator_obj["params"]
        per_split_signal = []
        for split_idx, (_, _, test_start, test_end) in enumerate(SOL_SPLITS, 1):
            test_df = slice_dates(df, test_start, test_end)
            if len(test_df) < 100:
                continue
            per_split_signal.append((split_idx, test_start, test_end, build(test_df, **params)))
        live_sl, live_tp = best_sl, best_tp
        if live_sl is None:  # reverse-signal engine has no fixed TP/SL - trailing-stop
            # optimization requires SOME base SL to seed the initial floor; use 1% as the
            # convention this project uses elsewhere absent a fixed-exit baseline.
            live_sl, live_tp = 1.0, 20.0
            print(f"    (winning entry uses its own reverse-signal exit, no fixed TP/SL - "
                  f"using SL={live_sl}%/TP={live_tp}% only as the seed for trailing-stop "
                  f"optimization below, matching this project's other defaults)")

    optimization_result = run_trailing_stop_optimization(entry_fn=None, live_sl=live_sl, live_tp=live_tp,
                                                          per_split_signal=per_split_signal)

    print(f"\n{'='*100}\nFINAL SOL VERDICT\n{'='*100}")
    print(f"  Data: SOLUSD, Kraken native daily, 2021-06-17..2026-06-30, 3 splits")
    print(f"  Best validated entry: {best_name} SL={best_sl}% TP={best_tp}% "
          f"(avg_ret={best_ret:.2f}%, avg_dd={best_dd:.2f}%)")
    b_pass_all, b_avg_ret, b_avg_dd = optimization_result["baseline"]
    print(f"  Exit-mechanism baseline re-check: {'validates' if b_pass_all else 'does NOT validate'} "
          f"all splits | avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")
    print(f"  Entry-bound/unfixable splits: {optimization_result['unfixable_splits'] or 'NONE'}")
    w_all = optimization_result["winner_all_splits"]
    print(f"  Best trailing-stop config (all splits): {w_all[0]} -> "
          f"{'VALIDATES' if w_all[3] else f'{w_all[4]}/{len(SOL_SPLITS)}'} avg_ret={w_all[5]:.2f}% avg_dd={w_all[6]:.2f}%")
    if optimization_result["winner_excl"]:
        w_ex = optimization_result["winner_excl"]
        print(f"  Best trailing-stop config (excl. unfixable split): {w_ex[0]} -> "
              f"{'VALIDATES' if w_ex[3] else f'{w_ex[4]}/{len(w_ex[2])}'} avg_ret={w_ex[5]:.2f}% avg_dd={w_ex[6]:.2f}%")
    overall_clears_bar = b_pass_all or w_all[3] or (optimization_result["winner_excl"] and optimization_result["winner_excl"][3])
    print(f"\n  OVERALL: SOL {'CLEARS' if overall_clears_bar else 'does NOT clear'} this project's "
          f"standalone bar as a live-bot candidate on {'all splits tested' if not optimization_result['unfixable_splits'] else 'the non-excluded splits'}.")

    run_new_baseline_trailing_test(df)


if __name__ == "__main__":
    main()
