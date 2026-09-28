"""Round 15, part B: the same wide grid (SL 1%-50% x TP 1%-100%, unconditional +
baseline-signal entry) as experiments_recent_window.py's 2-week exploratory test, but run
against the maximum available multi-year history per coin, using this project's
established train/test regime-split discipline (Rounds 8/10) - a direct, apples-to-apples
comparison of "wide search on 2 weeks" vs. "wide search validated across independent
multi-year regimes," on the same grid, same coins where possible.

Data/splits per coin:
  - BTC: Kraken native daily OHLCV, the 4 independent splits from Round 10
    (experiments_multisplit_kraken.py) - this project's most rigorous validation.
  - ETH: Yahoo Finance daily, the 2 splits from Round 2 onward (experiments_regime.py).
  - XRP, ADA, LTC, DOGE, LINK: Yahoo Finance daily, ~9 years of history each - same 2
    splits as ETH (REGIME_SPLITS), since their history covers the same span.
  - SOL, DOT, AVAX: Yahoo Finance daily, only ~5-6 years of history (2020 launch) - ONE
    train/test split each, same limitation and same caveat as Round 14's SOL test: less
    rigorous than the 2-4 split coins, reported honestly as such, not dressed up as
    equivalent evidence.

Usage:
    python experiments_full_history_wide.py
"""
import csv

from data_fetcher import fetch_yahoo_history, load_kraken_csv_1min
from experiments_multisplit import slice_dates, _passed
from experiments_multisplit_kraken import CSV_PATH as BTC_CSV_PATH
from experiments_multisplit_kraken import SPLITS as BTC_SPLITS
from experiments_regime import REGIME_SPLITS
from experiments_recent_window import SL_GRID_PCT, TP_GRID_PCT, ENTRY_STYLES
from experiments_wide_tp import simulate_wide_tp

ONE_SPLIT = {
    "SOL-USD": [("2020-04-10", "2023-06-30", "2023-07-01", "2026-09-17")],
    "DOT-USD": [("2020-08-20", "2023-06-30", "2023-07-01", "2026-09-17")],
    "AVAX-USD": [("2020-07-13", "2023-06-30", "2023-07-01", "2026-09-17")],
}

COINS = {
    "BTC": {"kind": "kraken_csv", "path": BTC_CSV_PATH, "splits": BTC_SPLITS},
    "ETH": {"kind": "yahoo", "symbol": "ETH-USD", "splits": REGIME_SPLITS},
    "XRP": {"kind": "yahoo", "symbol": "XRP-USD", "splits": REGIME_SPLITS},
    "ADA": {"kind": "yahoo", "symbol": "ADA-USD", "splits": REGIME_SPLITS},
    "LTC": {"kind": "yahoo", "symbol": "LTC-USD", "splits": REGIME_SPLITS},
    "DOGE": {"kind": "yahoo", "symbol": "DOGE-USD", "splits": REGIME_SPLITS},
    "LINK": {"kind": "yahoo", "symbol": "LINK-USD", "splits": REGIME_SPLITS},
    "SOL": {"kind": "yahoo", "symbol": "SOL-USD", "splits": ONE_SPLIT["SOL-USD"]},
    "DOT": {"kind": "yahoo", "symbol": "DOT-USD", "splits": ONE_SPLIT["DOT-USD"]},
    "AVAX": {"kind": "yahoo", "symbol": "AVAX-USD", "splits": ONE_SPLIT["AVAX-USD"]},
}

OUT_CSV = "experiments_full_history_wide_results.csv"


def load_coin_df(coin_cfg):
    if coin_cfg["kind"] == "kraken_csv":
        return load_kraken_csv_1min(coin_cfg["path"])
    return fetch_yahoo_history(coin_cfg["symbol"], range_="10y", interval="1d")


def main():
    all_rows = []
    by_combo_pass = {}  # (coin, entry, sl, tp) -> list of (split_idx, passed)

    for coin, coin_cfg in COINS.items():
        df = load_coin_df(coin_cfg)
        splits = coin_cfg["splits"]
        n_splits = len(splits)
        note = " (SINGLE split - limited history)" if n_splits == 1 else f" ({n_splits} independent splits)"
        print(f"\n### {coin}: {len(df)} candles, {df['time'].min().date()}..{df['time'].max().date()}{note} ###")

        for style_name, entry_fn in ENTRY_STYLES.items():
            for split_idx, (train_start, train_end, test_start, test_end) in enumerate(splits, 1):
                test_df = slice_dates(df, test_start, test_end)
                if len(test_df) < 100:
                    continue
                test_signal_df = entry_fn(test_df)
                test_bh = (test_df["close"].iloc[-1] / test_df["close"].iloc[0] - 1) * 100

                for sl_pct in SL_GRID_PCT:
                    for tp_pct in TP_GRID_PCT:
                        stats = simulate_wide_tp(test_signal_df, "entry", tp_pct, sl_pct)
                        passed = _passed(stats)
                        key = (coin, style_name, sl_pct, tp_pct)
                        by_combo_pass.setdefault(key, []).append((split_idx, passed))

                        all_rows.append({
                            "coin": coin, "entry": style_name, "split": split_idx,
                            "test_start": test_start, "test_end": test_end,
                            "sl_pct": sl_pct, "tp_pct": tp_pct,
                            "total_return_pct": stats["total_return_pct"],
                            "buy_hold_return_pct": test_bh,
                            "max_drawdown_pct": stats["max_drawdown_pct"],
                            "completed_trades": stats["completed_trades"],
                            "win_rate_pct": stats["win_rate_pct"],
                            "expectancy_pct": stats["expectancy_pct"],
                            "passed": passed,
                        })

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\nFull grid ({len(all_rows)} rows) written to {OUT_CSV}")

    print("\n" + "=" * 110)
    print("Combos passing ALL splits available for their coin (this project's actual validation bar):")
    all_pass = []
    for (coin, style_name, sl_pct, tp_pct), results in by_combo_pass.items():
        n_splits = len(COINS[coin]["splits"])
        if len(results) == n_splits and all(p for _, p in results):
            avg_ret = sum(
                r["total_return_pct"] for r in all_rows
                if r["coin"] == coin and r["entry"] == style_name and r["sl_pct"] == sl_pct and r["tp_pct"] == tp_pct
            ) / n_splits
            all_pass.append((coin, style_name, sl_pct, tp_pct, avg_ret, n_splits))

    if all_pass:
        for coin, style_name, sl_pct, tp_pct, avg_ret, n_splits in sorted(all_pass, key=lambda r: -r[4]):
            print(f"  {coin:>5} {style_name:>16} SL={sl_pct:>3}% TP={tp_pct:>4}% | "
                  f"avg return {avg_ret:7.2f}% across {n_splits} split(s)")
        print(f"\n{len(all_pass)} combo(s) total passed on every split available for their coin.")
    else:
        print("  none")

    return all_rows, all_pass


if __name__ == "__main__":
    main()
