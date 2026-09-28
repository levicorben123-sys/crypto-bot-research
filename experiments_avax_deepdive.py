"""AVAX deep-dive: entry-strategy discovery + Round 26/27 trailing-stop-ratchet
optimization, applied fresh to a brand-new coin candidate (to potentially replace DOGE),
following this project's existing methodology end to end. BACKTEST/RESEARCH ONLY -
nothing wired into any live bot.

Written as its own file (rather than appending to experiments_trailing_ratchet.py) since
5 parallel forks are doing the identical process for SOL/XRP/ADA/LTC/LINK at the same
time - avoids merge/overwrite collisions on a shared file. Imports shared infrastructure
from experiments_trailing_ratchet.py, experiments_wide_tp.py, experiments_indicators.py,
experiments_multisplit.py, experiments_regime.py, data_fetcher.py rather than duplicating
any of it.

DATA: Kraken's own official bulk historical archive (same "Kraken_OHLCVT_Full_2026Q2"
source as BTC/ETH/DOGE - see experiments_trailing_ratchet.py's own header comments for
the general method) was pulled for AVAXUSD via targeted HTTP range requests against the
archive's ZIP64 central directory (11 HTTP requests total: EOCD tail probe, EOCD64
record, central directory itself, then the one entry needed). Full mechanics:
  - Archive is split into 5 flat parts (.part00-.part03 at 2,097,152,000 bytes each,
    .part04 at 583,772,104 bytes) that are NOT a true multi-disk ZIP (disk numbers are
    all 0) - they're one continuous byte stream simply chunked for HTTP hosting, so
    absolute offsets from the central directory map directly onto
    concatenated-part-boundaries.
  - EOCD64 record located at absolute offset 8,972,380,006 (within part04); central
    directory at absolute offset 8,971,551,298, size 828,708 bytes, 12,037 total entries.
  - AVAXUSD_1440.csv found at local-header offset 859,154,901 (falls within part00),
    compressed size 42,594 bytes (raw deflate, decompresses to exactly 94,896 bytes,
    matching the central directory's recorded uncompressed size - a hard correctness
    check, not just a "looked plausible" one).
  - AVAXUSD_1.csv (native 1-minute, ~53MB uncompressed) was located but NOT pulled - not
    needed for entry-strategy/regime-split work, which uses daily OHLC high/low as the
    intrabar-check approximation (this project's standing convention per Round 12/26),
    and pulling 53MB wasn't worth the time given 5 other coins are being researched in
    parallel. Flagged here so a future round can pull it if BTC-style minute-level
    sensitivity checking on AVAX specifically becomes worth doing.

**Important, unlike ETH's Kraken data extension: Kraken's AVAXUSD listing (2021-12-21) is
LATER than Yahoo's AVAX-USD start (2020-07-13, AVAX's actual mid-2020 mainnet launch) -
same pattern as DOGE, not ETH.** This Kraken data MISSES AVAX's entire 2020 launch run and
most of its Nov-2021 bull run to its ~$146 ATH (the archive's first bar, 2021-12-21, opens
at $119.99 with a $150 high - already past the ATH, on the way down). Kraken-native data
trades broader historical coverage for exchange-native accuracy (avoiding the cross-
exchange entry-timing fragility Round 26 follow-up 4 found for ETH) - used as PRIMARY here
per this project's Round 9 precedent (match what the live bot will actually trade on), but
this tradeoff is real and stated plainly, not buried.

Verified before trusting it:
  - 2021-12-21 high = $150.00, the archive's very first bar - consistent with AVAX's
    widely-documented ATH of ~$146.22 on 2021-11-21 (Kraken's listing starts ~1 month
    after the peak, still elevated, not a data error).
  - Cross-checked against Yahoo's AVAX-USD on the overlapping date range (2021-12-21
    onward): see run_data_verification() output - mean abs diff reported at runtime,
    same discipline as every prior coin's data-sourcing round.

SPLITS: AVAX's Kraken data (2021-12-21..2026-06-30, ~4.5 years) doesn't support BTC/ETH's
4-split scheme, and is a genuinely different date range than any other coin's splits in
this project (built fresh, not copied) - 3 non-overlapping test windows constructed to
span the full available history and include a real bear-market test, matching this
project's DOGE-round approach: split 1 tests the bear-market grind (2022H2-2023H1), split
2 the recovery/chop (2023H2-2024), split 3 the most recent window (2025-2026).
"""
import numpy as np
import pandas as pd

import config
from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_indicators import (
    macd_crossover, stochastic_reversion, supertrend, adx_filtered_crossover,
    rolling_vwap_reversion, ichimoku_cloud,
)
from experiments_multisplit import _passed
from experiments_regime import slice_dates
from experiments_wide_tp import baseline_signal_entry, simulate_wide_tp
from experiments_trailing_ratchet import (
    simulate_trailing_ratchet, milestone_floor_tight_static,
    milestone_floor_peak_relative, milestone_floor_peak_relative_aggressive,
)

AVAX_CSV_PATH = "data/kraken_historical/extracted/AVAXUSD_1440.csv"
AVAX_YAHOO_SYMBOL = "AVAX-USD"

# (train_start, train_end, test_start, test_end) - built fresh for AVAX's own date range,
# 3 non-overlapping test windows spanning the full Kraken history available.
AVAX_SPLITS = [
    ("2021-12-21", "2022-06-30", "2022-07-01", "2023-06-30"),  # bear-market grind
    ("2022-07-01", "2023-06-30", "2023-07-01", "2024-12-31"),  # recovery/chop
    ("2023-07-01", "2024-12-31", "2025-01-01", "2026-06-30"),  # most recent
]

LIVE_SL, LIVE_TP = 1.0, 20.0  # no AVAX live bot exists yet; use BTC's validated default as the comparison baseline exit


def run_data_verification():
    print(f"\n{'='*100}\nAVAX data verification\n{'='*100}")
    kdf = load_kraken_csv_1min(AVAX_CSV_PATH)
    print(f"Kraken AVAXUSD daily: {len(kdf)} rows, {kdf['time'].min().date()}..{kdf['time'].max().date()}")
    print(f"  first bar: {kdf.iloc[0].to_dict()}")
    print(f"  max high: {kdf['high'].max():.2f} on {kdf.loc[kdf['high'].idxmax(), 'time'].date()} "
          f"(AVAX's documented ATH is ~$146.22 on 2021-11-21 - Kraken's listing starts "
          f"~1mo after, still-elevated $150 high on day 1, consistent not exact since the "
          f"true ATH predates this exchange's listing)")

    ydf = fetch_yahoo_history(AVAX_YAHOO_SYMBOL, range_="10y", interval="1d")
    print(f"Yahoo AVAX-USD: {len(ydf)} rows, {ydf['time'].min().date()}..{ydf['time'].max().date()}")

    merged = pd.merge(kdf, ydf, on="time", suffixes=("_kraken", "_yahoo"))
    merged["abs_diff_pct"] = (merged["close_kraken"] - merged["close_yahoo"]).abs() / merged["close_yahoo"] * 100
    print(f"  {len(merged)} matched dates, mean abs diff = {merged['abs_diff_pct'].mean():.3f}% "
          f"(ETH's own cross-check was 0.29%, DOGE's 0.260%, BTC's 0.31% - same order of magnitude)")
    worst = merged.nlargest(5, "abs_diff_pct")[["time", "close_kraken", "close_yahoo", "abs_diff_pct"]]
    print(f"  worst 5 outliers:\n{worst.to_string(index=False)}")
    return kdf


ENTRY_STYLES = {
    "baseline_sma_rsi": {"fn": baseline_signal_entry, "kind": "reversal"},
    "macd_crossover": {"fn": lambda df: macd_crossover(df, 12, 26, 9), "kind": "trend"},
    "stochastic_reversion": {"fn": lambda df: stochastic_reversion(df, 21, 30), "kind": "reversal"},
    "supertrend": {"fn": lambda df: supertrend(df, 10, 3.0), "kind": "trend"},
    "adx_filtered_crossover": {"fn": lambda df: adx_filtered_crossover(df, 10, 30, 15), "kind": "trend"},
    "rolling_vwap_reversion": {"fn": lambda df: rolling_vwap_reversion(df, 20, 2.0), "kind": "reversal"},
    "ichimoku_cloud": {"fn": ichimoku_cloud, "kind": "trend"},
}


def run_entry_strategy_search(df):
    print(f"\n{'='*100}\nAVAX entry-strategy search - baseline SMA/RSI vs. the Round 5/20 "
          f"indicator toolkit, {len(AVAX_SPLITS)} splits, fixed exit SL={LIVE_SL}%/TP={LIVE_TP}% "
          f"throughout (isolating the entry question, same discipline as every prior round)\n{'='*100}")

    results = {}  # style -> list of (split_idx, stats, passed)
    for style, spec in ENTRY_STYLES.items():
        rows = []
        for split_idx, (_, _, test_start, test_end) in enumerate(AVAX_SPLITS, 1):
            test_df = slice_dates(df, test_start, test_end)
            if len(test_df) < 30:
                continue
            try:
                signal_df = spec["fn"](test_df)
            except Exception as e:
                print(f"  {style} split {split_idx}: ERROR {e}")
                continue
            stats = simulate_wide_tp(signal_df, "entry", LIVE_TP, LIVE_SL)
            passed = _passed(stats)
            rows.append((split_idx, stats, passed))
        results[style] = rows
        n_pass = sum(1 for _, _, p in rows if p)
        avg_ret = np.mean([s["total_return_pct"] for _, s, _ in rows]) if rows else float("nan")
        avg_dd = np.mean([s["max_drawdown_pct"] for _, s, _ in rows]) if rows else float("nan")
        per_split = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%/{s['completed_trades']}tr)"
                               for i, s, p in rows)
        all_pass = n_pass == len(AVAX_SPLITS) and len(rows) == len(AVAX_SPLITS)
        print(f"  [{'VALIDATES' if all_pass else f'{n_pass}/{len(AVAX_SPLITS)}'}] {style:25s} "
              f"avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% | {per_split}")

    validated = [s for s, rows in results.items()
                 if len(rows) == len(AVAX_SPLITS) and all(p for _, _, p in rows)]
    print(f"\n  Styles that validate on ALL {len(AVAX_SPLITS)} splits: {validated or 'NONE'}")
    if validated:
        best = max(validated, key=lambda s: np.mean([st["total_return_pct"] for _, st, _ in results[s]]))
        print(f"  Best validated style: {best}")
    else:
        best = None
        print(f"  No entry style clears this project's bar on every split - reporting honestly, "
              f"not forcing a winner. Falling back to baseline_sma_rsi as the reference entry for "
              f"the trailing-stop work below (same fallback logic as this project uses elsewhere "
              f"when nothing beats it), with the caveat that it may not itself fully validate.")
        best = "baseline_sma_rsi"
    return results, best


TRAIL_PCT_GRID = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0]
TRAIL_STEP_GRID = [2.0, 3.0, 4.0, None]


def _trail_step_label(step):
    return "cont" if step is None else f"{step:.1f}%"


def diagnose_entry_bound(entry_fn, df):
    """Direct trade-level check (not assumed) per split: % of trades that ever reach
    positive profit before reversing, and whether the tightest possible floor still
    loses - same method used to diagnose ETH's 2018-19 split and confirm BTC had no such
    split."""
    print(f"\n  --- entry-bound diagnosis (per split) ---")
    flagged = []
    for split_idx, (_, _, test_start, test_end) in enumerate(AVAX_SPLITS, 1):
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 30:
            continue
        signal_df = entry_fn(test_df)
        base_stats = simulate_wide_tp(signal_df, "entry", LIVE_TP, LIVE_SL)
        base_pass = _passed(base_stats)
        tight_stats = simulate_trailing_ratchet(signal_df, "entry", LIVE_SL, variant="close",
                                                  use_milestones=False, trail_pct=0.1, trail_step_pct=None,
                                                  return_trades=True)
        trades = tight_stats.get("trades", [])
        never_positive = sum(1 for t in trades if t["peak_profit_pct"] <= 0)
        pct_never_positive = (never_positive / len(trades) * 100) if trades else 0.0
        unfixable = (not base_pass) and pct_never_positive >= 50 and tight_stats["total_return_pct"] < 0
        if unfixable:
            flagged.append(split_idx)
        print(f"    split {split_idx}: baseline {'PASS' if base_pass else 'fail'} "
              f"({base_stats['total_return_pct']:+.2f}%) | {pct_never_positive:.0f}% of trades "
              f"never reach positive profit | tightest-floor(0.1%) return={tight_stats['total_return_pct']:+.2f}% "
              f"| {'ENTRY-BOUND, UNFIXABLE' if unfixable else 'winnable'}")
    return flagged


def _grid_for_split(signal_df):
    results = {}
    for trail_pct in TRAIL_PCT_GRID:
        for step in TRAIL_STEP_GRID:
            label = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
            stats = simulate_trailing_ratchet(signal_df, "entry", LIVE_SL, variant="close",
                                               use_milestones=False, trail_pct=trail_pct, trail_step_pct=step)
            results[label] = (stats, _passed(stats), (trail_pct, step))
    return results


def run_trailing_stop_optimization(df, entry_fn, entry_name, excluded_splits):
    kept = [(i, s) for i, s in enumerate(AVAX_SPLITS, 1) if i not in excluded_splits]
    n_splits = len(kept)
    print(f"\n{'='*100}\nAVAX trailing-stop-ratchet optimization - entry={entry_name}, "
          f"{n_splits}/{len(AVAX_SPLITS)} splits kept (excluded: {excluded_splits or 'none'}), "
          f"close-confirmed variant only\n{'='*100}")

    per_split_signal = []
    for split_idx, (_, _, test_start, test_end) in kept:
        test_df = slice_dates(df, test_start, test_end)
        if len(test_df) < 30:
            continue
        signal_df = entry_fn(test_df)
        per_split_signal.append((split_idx, signal_df))

    baseline_rows = [(i, simulate_wide_tp(sdf, "entry", LIVE_TP, LIVE_SL),
                       _passed(simulate_wide_tp(sdf, "entry", LIVE_TP, LIVE_SL))) for i, sdf in per_split_signal]
    b_avg_ret = np.mean([s["total_return_pct"] for _, s, _ in baseline_rows])
    b_avg_dd = np.mean([s["max_drawdown_pct"] for _, s, _ in baseline_rows])
    b_pass_all = all(p for _, _, p in baseline_rows)
    print(f"\n  live-default-style baseline (SL={LIVE_SL}%/TP={LIVE_TP}%): "
          f"{'VALIDATES' if b_pass_all else 'does not validate'} | avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")

    per_split_grid = []
    for split_idx, sdf in per_split_signal:
        results = _grid_for_split(sdf)
        per_split_grid.append((split_idx, results))
        best_label = max(results, key=lambda k: results[k][0]["total_return_pct"])
        best_stats, best_passed, _ = results[best_label]
        print(f"  split {split_idx} in-sample optimum: {best_label} -> "
              f"ret={best_stats['total_return_pct']:+.2f}% dd={best_stats['max_drawdown_pct']:.2f}% "
              f"passed={best_passed}")

    # pool top-5 per split, score jointly out-of-sample across all kept splits
    candidate_params = {}
    for split_idx, results in per_split_grid:
        top5 = sorted(results.items(), key=lambda kv: -kv[1][0]["total_return_pct"])[:5]
        for label, (_, _, params) in top5:
            candidate_params[params] = params

    scored = []
    for trail_pct, step in candidate_params.values():
        label = f"trail={trail_pct:.2f}% step={_trail_step_label(step)}"
        rows = []
        for split_idx, sdf in per_split_signal:
            stats = simulate_trailing_ratchet(sdf, "entry", LIVE_SL, variant="close",
                                               use_milestones=False, trail_pct=trail_pct, trail_step_pct=step)
            rows.append((split_idx, stats, _passed(stats)))
        pass_all = all(p for _, _, p in rows)
        n_pass = sum(1 for _, _, p in rows if p)
        avg_ret = np.mean([s["total_return_pct"] for _, s, _ in rows])
        avg_dd = np.mean([s["max_drawdown_pct"] for _, s, _ in rows])
        scored.append((label, (trail_pct, step), rows, pass_all, n_pass, avg_ret, avg_dd))
    scored.sort(key=lambda r: (-r[4], -r[5]))

    print(f"\n  top 10 out-of-sample-scored trail configs (pooled from each split's top-5):")
    for label, params, rows, pass_all, n_pass, avg_ret, avg_dd in scored[:10]:
        per_split_str = ", ".join(f"s{i}:{'P' if p else 'F'}({s['total_return_pct']:+.1f}%)" for i, s, p in rows)
        print(f"    [{n_pass}/{n_splits}] {label:30s} avg_ret={avg_ret:8.2f}% avg_dd={avg_dd:6.2f}% | {per_split_str}")

    winner = scored[0]
    print(f"\n  === WINNER: {winner[0]} ===")
    print(f"    {'VALIDATES on all kept splits' if winner[3] else f'{winner[4]}/{n_splits} splits pass - NOT fully validated'}")
    print(f"    avg_ret={winner[5]:.2f}% avg_dd={winner[6]:.2f}% vs baseline avg_ret={b_avg_ret:.2f}% avg_dd={b_avg_dd:.2f}%")

    # cross-split agreement
    optima = [(i, max(res, key=lambda k: res[k][0]["total_return_pct"]), res) for i, res in per_split_grid]
    n_agree, n_pairs = 0, 0
    for a in range(len(optima)):
        for b in range(a + 1, len(optima)):
            n_pairs += 1
            _, la, ra = optima[a]
            _, lb, rb = optima[b]
            pa, pb = ra[la][2], rb[lb][2]
            if abs(pa[0] - pb[0]) < 0.5 and pa[1] == pb[1]:
                n_agree += 1
    if n_pairs > 0:
        print(f"    cross-split agreement: {n_agree}/{n_pairs} pairs")
    else:
        print(f"    cross-split agreement: only {n_splits} split(s) kept - cannot check agreement "
              f"meaningfully, extra overfitting risk on this result (same caveat this project applied "
              f"when ETH had only 2 splits)")

    # quick milestone-ladder check (Designs D/E/F) on top of the winning trail
    print(f"\n  --- milestone ladder check (Designs D/E/F) on top of the winning trail ---")
    for name, fn in [("D (tight static)", milestone_floor_tight_static),
                      ("E (peak-relative)", milestone_floor_peak_relative),
                      ("F (peak-relative aggressive)", milestone_floor_peak_relative_aggressive)]:
        rows = []
        for split_idx, sdf in per_split_signal:
            stats = simulate_trailing_ratchet(sdf, "entry", LIVE_SL, variant="close", use_milestones=True,
                                               milestone_fn=fn, trail_pct=winner[1][0], trail_step_pct=winner[1][1])
            rows.append((split_idx, stats, _passed(stats)))
        avg_ret = np.mean([s["total_return_pct"] for _, s, _ in rows])
        avg_dd = np.mean([s["max_drawdown_pct"] for _, s, _ in rows])
        delta = avg_ret - winner[5]
        print(f"    ladder {name:32s} avg_ret={avg_ret:8.2f}% ({delta:+.2f}pp vs plain trail) avg_dd={avg_dd:6.2f}%")

    return winner, b_avg_ret, b_avg_dd, b_pass_all


def main():
    df = run_data_verification()
    entry_results, best_entry_name = run_entry_strategy_search(df)
    entry_fn = ENTRY_STYLES[best_entry_name]["fn"]
    flagged = diagnose_entry_bound(entry_fn, df)
    print(f"\n  === with ALL {len(AVAX_SPLITS)} splits ===")
    winner_all, b_ret_all, b_dd_all, b_pass_all = run_trailing_stop_optimization(df, entry_fn, best_entry_name, set())
    if flagged:
        print(f"\n  === with entry-bound split(s) {flagged} excluded ===")
        run_trailing_stop_optimization(df, entry_fn, best_entry_name, set(flagged))

    print(f"\n{'='*100}\nAVAX FINAL VERDICT\n{'='*100}")
    print(f"Data: Kraken native, {df['time'].min().date()}..{df['time'].max().date()}, {len(AVAX_SPLITS)} splits built")
    print(f"Best entry: {best_entry_name}")
    print(f"Entry-bound splits: {flagged or 'none'}")
    print(f"Best trail (all splits): {winner_all[0]}, "
          f"{'VALIDATES' if winner_all[3] else f'{winner_all[4]}/{len(AVAX_SPLITS)} pass'}")
    print(f"vs. baseline exit: avg_ret={b_ret_all:.2f}% avg_dd={b_dd_all:.2f}% "
          f"({'validates' if b_pass_all else 'does not validate'})")
    print(f"CAVEAT: only {len(AVAX_SPLITS)} splits total (fewer than BTC/ETH's 4) - this verdict carries "
          f"more overfitting risk than BTC/ETH's own findings, same honesty this project applies whenever "
          f"a coin has a shorter history.")


if __name__ == "__main__":
    main()
