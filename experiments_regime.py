"""Regime-based validation using years of Yahoo Finance daily history.

Kraken's public OHLC endpoint only covers ~720 recent candles, which turned out to be a
single continuous uptrend window - so experiments.py only ever tested "active strategy vs
a rally". This reuses the exact same simulation engine and strategy families from
experiments.py, but on multi-year Yahoo daily data, split by actual market regime
(pre-covid / covid crash+recovery / 2022 bear / recovery) instead of a blind chronological
70/30 cut - so a strategy has to prove itself on a genuinely different kind of market than
whatever it was tuned on.

Usage:
    python experiments_regime.py
"""
from data_fetcher import fetch_yahoo_history
from experiments import FAMILIES, run_family

SYMBOLS = {"XBTUSD": "BTC-USD", "ETHUSD": "ETH-USD"}

# (train_start, train_end, test_start, test_end) - test regime must differ structurally
# from train regime, not just be a later slice of the same trend.
REGIME_SPLITS = [
    ("2018-01-01", "2021-12-31", "2022-01-01", "2025-12-31"),  # pre-covid+covid+2021 bull -> 2022 bear + recovery
    ("2017-01-01", "2019-12-31", "2020-01-01", "2021-12-31"),  # pre-covid only -> covid crash + recovery + 2021 bull
]


def slice_dates(df, start, end):
    mask = (df["time"] >= start) & (df["time"] <= end)
    return df.loc[mask].reset_index(drop=True)


def main():
    all_winners = []

    for pair, yahoo_symbol in SYMBOLS.items():
        print(f"\n### {pair} ({yahoo_symbol}) ###")
        df = fetch_yahoo_history(yahoo_symbol, range_="10y", interval="1d")
        print(f"History: {len(df)} candles, {df['time'].min().date()} to {df['time'].max().date()}")

        for split_idx, (train_start, train_end, test_start, test_end) in enumerate(REGIME_SPLITS, 1):
            train_df = slice_dates(df, train_start, train_end)
            test_df = slice_dates(df, test_start, test_end)
            if len(train_df) < 100 or len(test_df) < 100:
                print(f"  Split {split_idx}: insufficient data for {yahoo_symbol} in this range, skipping")
                continue

            print(
                f"\n  Split {split_idx}: train {train_start}..{train_end} ({len(train_df)}d) "
                f"-> test {test_start}..{test_end} ({len(test_df)}d)"
            )

            for family_name, spec in FAMILIES.items():
                validated = run_family(family_name, spec, train_df, test_df)
                params, tp_pct, sl_pct, train_stats, test_stats, passed = validated[0]
                tag = "PASS" if passed else "fail"
                print(
                    f"    [{tag}] {family_name:26s} tp={tp_pct} sl={sl_pct} {params} | "
                    f"train_ret={train_stats['total_return_pct']:7.2f}% | "
                    f"test_ret={test_stats['total_return_pct']:7.2f}% test_bh={test_stats['buy_hold_return_pct']:7.2f}% "
                    f"test_dd={test_stats['max_drawdown_pct']:6.2f}% test_trades={test_stats['round_trips']}"
                )
                if passed:
                    all_winners.append((pair, split_idx, family_name, params, tp_pct, sl_pct, test_stats))

    print("\n" + "=" * 80)
    if all_winners:
        print(f"{len(all_winners)} config(s) validated across a real regime split:")
        for w in all_winners:
            print(" ", w)
    else:
        print(
            "No config validated across any regime split. Honest finding: none of these "
            "signal families show a real edge even with years of multi-regime daily data."
        )

    return all_winners


if __name__ == "__main__":
    main()
