"""Round 19: search for a genuinely safe DAILY-frequency strategy for ETH/DOGE, accepting
small per-trade wins instead of hunting for the biggest return - per explicit user
request ("even if some days the wins aren't huge... run it until you find the winning
strat").

Why this is a genuinely new corner of the search space, not a repeat of prior rounds:
every previous attempt at frequent trading in this project paired a fast/loose entry with
either (a) the SAME wide take-profit as the patient config (Round 18 - fixed TP=50%/100%,
only entry speed varied), or (b) a tiny take-profit with a WIDE stop "for wiggle room"
(Round 14 - the opposite pairing, which produced the "picking up pennies in front of a
steamroller" failure). Nobody has tested a SMALL stop paired with a SMALL target - the one
combination that could plausibly support frequent trading without either diluting into a
wide-target design that can't resolve daily, or exposing a tiny win to a large loss.

Grid: 6 entry speeds (from the project default down to very fast) x 6 small stop-losses
(0.5%-5%) x 7 small take-profits (1%-10%) = 252 combos per coin x 2 splits = 504
backtests/coin, 1,008 total. Same validation bar as always (profitable, >=5 trades, <40%
drawdown, passes BOTH independent splits) - "small wins are fine" does not mean "no safety
bar," so the bar is unchanged.

Ranking: among combos that pass validation, rank by CADENCE first (closest to daily),
not by return - directly answering what was asked, rather than quietly optimizing for
the metric this project has defaulted to every other round.

Usage:
    python experiments_daily_small_wins.py
"""
from data_fetcher import fetch_yahoo_history
from experiments_fast_entry import fast_entry, simulate_with_cadence
from experiments_multisplit import slice_dates, _passed
from experiments_regime import REGIME_SPLITS

MA_GRID = [(10, 30), (5, 10), (5, 8), (3, 5), (2, 4), (2, 3)]
SL_GRID_PCT = [0.5, 1, 1.5, 2, 3, 5]
TP_GRID_PCT = [1, 1.5, 2, 3, 5, 7, 10]

COINS = {"ETH": "ETH-USD", "DOGE": "DOGE-USD"}


def main():
    all_results = {}

    for coin, symbol in COINS.items():
        df = fetch_yahoo_history(symbol, range_="10y", interval="1d")
        print(f"### {coin}: {len(df)} candles ###")
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
                avg_exp = sum(s["expectancy_pct"] for _, s, *_ in rows) / len(rows)
                gaps = [g for _, _, g, _, _ in rows if g is not None]
                avg_gap = sum(gaps) / len(gaps) if gaps else 9999
                all_pass.append({
                    "fast": fast, "slow": slow, "sl": sl, "tp": tp,
                    "avg_ret": avg_ret, "avg_dd": avg_dd, "avg_trades": avg_trades,
                    "avg_wr": avg_wr, "avg_exp": avg_exp, "avg_gap": avg_gap,
                })

        print(f"\n{'='*100}\n{coin}: {len(all_pass)} combos pass BOTH splits (small SL/TP grid)\n{'='*100}")

        by_cadence = sorted(all_pass, key=lambda c: c["avg_gap"])
        print(f"\n-- Top 10 by CADENCE (closest to daily), among validated combos --")
        for c in by_cadence[:10]:
            print(f"  SMA({c['fast']},{c['slow']}) SL={c['sl']}% TP={c['tp']}% | avg_gap={c['avg_gap']:.1f}d "
                  f"avg_ret={c['avg_ret']:.2f}% avg_dd={c['avg_dd']:.2f}% avg_trades={c['avg_trades']:.1f} "
                  f"avg_wr={c['avg_wr']:.1f}% avg_expectancy={c['avg_exp']:.3f}%/trade")

        if not all_pass:
            print("  NONE - no small-SL/TP combo validated on both splits at any entry speed tested.")

    return all_results


if __name__ == "__main__":
    main()
