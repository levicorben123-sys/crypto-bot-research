"""Step C for the XRP deep-dive: trailing-stop-ratchet optimization on the winning
baseline entry (SL=7%/TP=40%, found in experiments_xrp_deepdive.py Step A/B), following
the exact same methodology as run_doge_trail_optimization (entry-bound diagnosis with an
ultra-wide sweep, DOGE-scaled wide grid since XRP's winning SL/TP is DOGE-scale not
ETH/BTC-scale, cross-split agreement, joint out-of-sample scoring, milestone-ladder
check). Separate file from experiments_xrp_deepdive.py purely to avoid re-running Steps
A/B (already confirmed) while iterating on Step C.
"""
import statistics
from functools import partial

from experiments_multisplit import slice_dates
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp
from experiments_xrp_deepdive import XRP_SPLITS, load_xrp
from experiments_trailing_ratchet import (
    MAX_DRAWDOWN_PCT_REF, _passed, _trail_params_agree, _trail_step_label,
    milestone_floor_peak_relative, milestone_floor_peak_relative_aggressive,
    milestone_floor_tight_static, simulate_trailing_ratchet,
)

XRP_LIVE_SL, XRP_LIVE_TP = 7.0, 40.0  # winner from Step A (excluding split 3): avg_ret=94.70%

# DOGE-scaled wide grid (XRP's winning SL/TP is DOGE-scale, not ETH/BTC-scale)
XRP_TRAIL_PCT_GRID = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 20, 25, 30, 40]
XRP_TRAIL_STEP_GRID = [2.0, 4.0, None]


def _xrp_trail_grid_for_split(signal_df, live_sl):
    results = {}
    for trail_pct in XRP_TRAIL_PCT_GRID:
        for step in XRP_TRAIL_STEP_GRID:
            label = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
            stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                               use_milestones=False, trail_pct=trail_pct,
                                               trail_step_pct=step)
            results[label] = (stats, _passed(stats), (trail_pct, step))
    return results


def _split_summary(rows, n_splits):
    pass_all = len(rows) == n_splits and all(p for _, _, p in rows)
    avg_ret = statistics.mean(s["total_return_pct"] for _, s, _ in rows)
    avg_dd = statistics.mean(s["max_drawdown_pct"] for _, s, _ in rows)
    return pass_all, avg_ret, avg_dd


def main():
    live_sl, live_tp = XRP_LIVE_SL, XRP_LIVE_TP
    df = load_xrp()

    print(f"\n{'='*100}\nSTEP C: XRP trailing-stop optimization (entry: baseline signal, "
          f"SL={live_sl}%/TP={live_tp}%, DOGE-scaled wide grid). close-confirmed variant "
          f"only.\n{'='*100}")

    per_split_signal, baseline_rows, diag_rows = [], [], []
    for split_idx, (_, _, test_start, test_end) in enumerate(XRP_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 100:
            print(f"  Split {split_idx}: insufficient data, skipping")
            continue
        signal_df = baseline_signal_entry(test_df)
        per_split_signal.append((split_idx, test_start, test_end, signal_df))

        base_stats = simulate_wide_tp(signal_df, "entry", live_tp, live_sl)
        base_passed = _passed(base_stats)
        baseline_rows.append((split_idx, base_stats, base_passed))

        diag_stats = simulate_trailing_ratchet(signal_df, "entry", live_sl, variant="close",
                                                use_milestones=False, trail_pct=0.1,
                                                trail_step_pct=None, return_trades=True)
        trades = diag_stats.get("trades", [])
        n_trades = len(trades)
        n_never_positive = sum(1 for t in trades if t["peak_profit_pct"] <= 0) if trades else 0
        avg_peak = statistics.mean(t["peak_profit_pct"] for t in trades) if trades else 0.0
        pct_never_positive = (n_never_positive / n_trades * 100) if n_trades else 0.0
        diag_rows.append({"split_idx": split_idx, "n_trades": n_trades,
                           "pct_never_positive": pct_never_positive, "avg_peak_profit_pct": avg_peak,
                           "tightest_floor_return_pct": diag_stats["total_return_pct"]})

        print(f"\n  --- Split {split_idx} ({test_start}..{test_end}) ---")
        print(f"    [{'PASS' if base_passed else 'fail'}] live-style baseline (SL=7%/TP=40%): "
              f"ret={base_stats['total_return_pct']:+.2f}% dd={base_stats['max_drawdown_pct']:.2f}% "
              f"trades={base_stats['completed_trades']}")
        print(f"    entry-bound diagnosis (tightest-possible 0.1pp floor): {n_trades} trades, "
              f"{pct_never_positive:.0f}% never reach positive profit before reversing, "
              f"avg peak profit={avg_peak:+.2f}%, tightest-floor return={diag_stats['total_return_pct']:+.2f}%")

    b_pass_all, b_avg_ret, b_avg_dd = _split_summary(baseline_rows, len(XRP_SPLITS))
    print(f"\n  === XRP live-style baseline, all {len(XRP_SPLITS)} splits ===")
    print(f"    {'VALIDATES' if b_pass_all else 'does NOT validate'} | avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")

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
              f"{'still negative, confirms entry-bound' if best_ultra_wide < 0 else 'RESCUED by a wide trail, not entry-bound'}")
        if best_ultra_wide < 0:
            unfixable.append(split_idx)
    print(f"\n  entry-bound/un-fixable split diagnosis: {unfixable if unfixable else 'NONE found (checked)'}")

    def _run_grid(kept_split_ids, label):
        kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in kept_split_ids]
        n_kept = len(kept)
        b_rows = [(i, s, p) for i, s, p in baseline_rows if i in kept_split_ids]
        bb_pass_all, bb_avg_ret, bb_avg_dd = _split_summary(b_rows, n_kept)

        per_split_grids = [(i, _xrp_trail_grid_for_split(sdf, live_sl)) for i, ts, te, sdf in kept]
        optima = [(i, max(res, key=lambda k: res[k][0]["total_return_pct"]), res) for i, res in per_split_grids]
        n_pairs = len(optima) * (len(optima) - 1) // 2
        n_agree = 0
        for a in range(len(optima)):
            for bidx in range(a + 1, len(optima)):
                _, i_label, i_res = optima[a]
                _, j_label, j_res = optima[bidx]
                agree, _ = _trail_params_agree(i_res[i_label][2], j_res[j_label][2])
                n_agree += agree

        candidate_params = {(live_sl, None): None}  # placeholder removed below
        candidate_params = {}
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
        winner = scored[0]

        print(f"\n  --- {label}: splits {sorted(kept_split_ids)} ---")
        print(f"    live-style baseline: {'VALIDATES' if bb_pass_all else 'does NOT validate'} | "
              f"avg_ret={bb_avg_ret:.2f}% avg_dd={bb_avg_dd:.2f}%")
        print(f"    cross-split agreement: {n_agree}/{n_pairs} pairs")
        for lbl, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:8]:
            per_split_str = ", ".join(f"s{i}:{'P' if p else 'F'}(ret={s['total_return_pct']:+.1f}% "
                                       f"dd={s['max_drawdown_pct']:.1f}%)" for i, s, p in rows)
            print(f"      [{n_pass}/{n_kept}] {lbl:30s} avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% | {per_split_str}")
        print(f"    WINNER: {winner[0]} | "
              f"{'VALIDATES all ' + str(n_kept) if winner[3] else f'{winner[4]}/{n_kept} pass'} "
              f"| avg_ret={winner[5]:.2f}% avg_dd={winner[6]:.2f}%")

        return {"label": label, "kept_splits": sorted(kept_split_ids), "n_agree": n_agree, "n_pairs": n_pairs,
                "baseline_pass_all": bb_pass_all, "baseline_avg_ret": bb_avg_ret, "baseline_avg_dd": bb_avg_dd,
                "winner_label": winner[0], "winner_params": winner[1], "winner_pass_all": winner[3],
                "winner_n_pass": winner[4], "winner_avg_ret": winner[5], "winner_avg_dd": winner[6]}

    all_split_ids = set(i for i, *_ in per_split_signal)
    full_result = _run_grid(all_split_ids, "ALL 4 SPLITS")
    exunfixable_result = _run_grid(all_split_ids - set(unfixable), f"EXCLUDING {unfixable}") \
        if unfixable else None

    ladder_ref = exunfixable_result or full_result
    kept_ids = set(ladder_ref["kept_splits"])
    kept = [(i, ts, te, sdf) for i, ts, te, sdf in per_split_signal if i in kept_ids]
    n_kept = len(kept)
    trail_pct_w, step_w = ladder_ref["winner_params"]

    print(f"\n  --- does the ETH-calibrated milestone ladder help XRP on top of the winning "
          f"trail ({ladder_ref['winner_label']}, splits {sorted(kept_ids)})? ---")
    ladder_results = {}
    for name, fn in [("D (tight static 0.75pp)", milestone_floor_tight_static),
                      ("E (peak-relative shrinking)", milestone_floor_peak_relative),
                      ("F (peak-relative aggressive)", milestone_floor_peak_relative_aggressive)]:
        rows = []
        for i, ts, te, sdf in kept:
            stats = simulate_trailing_ratchet(sdf, "entry", live_sl, variant="close", use_milestones=True,
                                               milestone_fn=fn, trail_pct=trail_pct_w, trail_step_pct=step_w)
            rows.append((i, stats, _passed(stats)))
        pass_all, avg_ret, avg_dd = _split_summary(rows, n_kept)
        ladder_results[name] = (pass_all, avg_ret, avg_dd)
        print(f"    {name}: {'PASS all' if pass_all else 'not all pass'} | avg_ret={avg_ret:.2f}% avg_dd={avg_dd:.2f}% "
              f"(plain trail alone: avg_ret={ladder_ref['winner_avg_ret']:.2f}% avg_dd={ladder_ref['winner_avg_dd']:.2f}%)")

    print(f"\n  === FINAL RECOMMENDATION FOR XRP: {ladder_ref['winner_label']}, entry SL={live_sl}%/TP={live_tp}% ===")
    print(f"    Splits used: {sorted(kept_ids)}"
          + (f" (split(s) {unfixable} excluded as entry-bound)" if unfixable else ""))
    print(f"    avg_ret={ladder_ref['winner_avg_ret']:.2f}% avg_dd={ladder_ref['winner_avg_dd']:.2f}% vs. "
          f"live-style baseline avg_ret={ladder_ref['baseline_avg_ret']:.2f}% avg_dd={ladder_ref['baseline_avg_dd']:.2f}%")
    cap_note = "MET" if ladder_ref["winner_pass_all"] else "NOT met on these splits"
    print(f"    Strict <{MAX_DRAWDOWN_PCT_REF}% drawdown cap: {cap_note}")

    return {"baseline_all": {"pass_all": b_pass_all, "avg_ret": b_avg_ret, "avg_dd": b_avg_dd, "rows": baseline_rows},
            "diagnosis": diag_rows, "unfixable_splits": unfixable,
            "full": full_result, "ex_unfixable": exunfixable_result, "ladder_check": ladder_results}


if __name__ == "__main__":
    main()
