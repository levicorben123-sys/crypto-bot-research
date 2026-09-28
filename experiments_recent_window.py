"""Round 15: EXPLORATORY, NOT VALIDATED under this project's own bar. Per explicit user
instruction, this and all future rounds test only against the last ~2 weeks of live
market data, with no train/test split and no independent regime cross-check - a
deliberate departure from Rounds 1-14's methodology (multi-year, multi-regime,
train-on-one/test-on-another). That methodology existed specifically to prevent finding
a config that looks great by coincidence rather than because it has a real edge (see
Round 2's write-up). A wide grid search (12 stop-losses x 15 take-profits = 180 combos,
x2 entry styles, x9 coins = 3,240 backtests) against a single ~14-day window WILL turn up
some combo with a great-looking number - that is expected from the search size alone, not
evidence of a repeatable edge. Read every number below as "what happened to hit in these
specific 14 days," not as a forecast. No live bot config is changed based on this round.

Data: Kraken's live public OHLC endpoint (data_fetcher.fetch_ohlc), 1-hour candles,
trimmed to the most recent 14 days, for 9 non-BTC coins (ETH, SOL, XRP, ADA, LTC, DOGE,
LINK, DOT, AVAX) - whatever Kraken's public API actually serves live, not a curated
historical file.

Two entry styles, both already used elsewhere in this project (not new/untested
mechanisms):
  - "unconditional": buy immediately whenever flat (experiments_wide_tp.py's
    unconditional_entry). Guarantees the bot is never sitting idle waiting for a signal -
    the most direct way to satisfy "make it trade every day" as a hard requirement,
    independent of whatever TP/SL is chosen.
  - "baseline signal": this project's standard SMA(10)/SMA(30)+RSI crossover, included
    for comparison only - on 14 days of hourly bars a 10-30 HOUR lookback may or may not
    fire at all, so this is not expected to trade daily.

Usage:
    python experiments_recent_window.py
"""
import csv

import config
from data_fetcher import fetch_ohlc
from experiments_wide_tp import baseline_signal_entry, unconditional_entry, simulate_wide_tp, _passed

COINS = ["ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD", "LTCUSD", "DOGEUSD", "LINKUSD", "DOTUSD", "AVAXUSD"]
INTERVAL_MINUTES = 60
WINDOW_DAYS = 14

SL_GRID_PCT = [1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50]
TP_GRID_PCT = [1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100]

ENTRY_STYLES = {
    "unconditional": unconditional_entry,
    "baseline_signal": baseline_signal_entry,
}

OUT_CSV = "experiments_recent_window_results.csv"


def trim_to_window(df, days):
    cutoff = df["time"].max() - pd_timedelta(days)
    return df[df["time"] >= cutoff].reset_index(drop=True)


def pd_timedelta(days):
    import pandas as pd
    return pd.Timedelta(days=days)


def main():
    all_rows = []

    for coin in COINS:
        df = fetch_ohlc(coin, INTERVAL_MINUTES)
        df = trim_to_window(df, WINDOW_DAYS)
        span_days = (df["time"].max() - df["time"].min()).total_seconds() / 86400
        print(f"\n### {coin}: {len(df)} hourly candles, "
              f"{df['time'].min()}..{df['time'].max()} ({span_days:.1f} days) ###")

        for style_name, entry_fn in ENTRY_STYLES.items():
            signal_df = entry_fn(df)

            best_for_style = None
            n_combos = 0
            n_profitable = 0

            for sl_pct in SL_GRID_PCT:
                for tp_pct in TP_GRID_PCT:
                    stats = simulate_wide_tp(signal_df, "entry", tp_pct, sl_pct)
                    n_combos += 1
                    trades_per_day = stats["completed_trades"] / span_days if span_days else 0
                    if stats["total_return_pct"] > 0:
                        n_profitable += 1

                    row = {
                        "coin": coin, "entry": style_name, "sl_pct": sl_pct, "tp_pct": tp_pct,
                        "total_return_pct": stats["total_return_pct"],
                        "max_drawdown_pct": stats["max_drawdown_pct"],
                        "completed_trades": stats["completed_trades"],
                        "trades_per_day": trades_per_day,
                        "win_rate_pct": stats["win_rate_pct"],
                        "expectancy_pct": stats["expectancy_pct"],
                        "buy_hold_return_pct": stats["buy_hold_return_pct"],
                    }
                    all_rows.append(row)

                    if best_for_style is None or stats["total_return_pct"] > best_for_style["total_return_pct"]:
                        best_for_style = row

            print(f"  [{style_name}] {n_combos} combos tested, {n_profitable} profitable "
                  f"({n_profitable/n_combos*100:.0f}%) | buy&hold this window: "
                  f"{best_for_style['buy_hold_return_pct']:+.2f}%")
            b = best_for_style
            print(f"    BEST (by raw return, no cross-validation): SL={b['sl_pct']}% TP={b['tp_pct']}% | "
                  f"return={b['total_return_pct']:+.2f}% dd={b['max_drawdown_pct']:.2f}% "
                  f"trades={b['completed_trades']} ({b['trades_per_day']:.2f}/day) "
                  f"winrate={b['win_rate_pct']:.1f}% expectancy={b['expectancy_pct']:.3f}%")

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\nFull grid ({len(all_rows)} rows) written to {OUT_CSV}")

    print("\n" + "=" * 100)
    print("Top 5 results overall by raw return (context only - see the overfitting caveat at the top of this file):")
    for row in sorted(all_rows, key=lambda r: -r["total_return_pct"])[:5]:
        print(f"  {row['coin']:>8} {row['entry']:>16} SL={row['sl_pct']:>3}% TP={row['tp_pct']:>4}% | "
              f"return={row['total_return_pct']:+7.2f}% dd={row['max_drawdown_pct']:6.2f}% "
              f"trades={row['completed_trades']:3d} ({row['trades_per_day']:.2f}/day) "
              f"winrate={row['win_rate_pct']:5.1f}%")

    uncond_rows = [r for r in all_rows if r["entry"] == "unconditional"]
    avg_tpd = sum(r["trades_per_day"] for r in uncond_rows) / len(uncond_rows)
    print(f"\nUnconditional-entry average trades/day across all coins/SL/TP combos: {avg_tpd:.2f}/day "
          f"(this is the entry style that guarantees daily action, independent of TP/SL).")

    return all_rows


if __name__ == "__main__":
    main()
