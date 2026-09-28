"""Round 28: do free outside data sources improve the aggressive ETH_FAST config?

Baseline = the live ETH_FAST bot / Round 18's fastest event entry for ETH:
SMA(2,3) crossover + RSI<70 entry, exit only at SL 2% / TP 50%, daily candles, 0.26% fee.
Each variant adds exactly ONE filter on top of the baseline's entries (never adds entries),
tested on Round 18's regime splits (test windows 2022-2025 and 2020-2021) with the same
pass rules, plus a fresh 2026 window no round has used before (reported, not pass/fail).

No look-ahead: the simulator buys at the signal day's close. Outside data (Fear & Greed,
Deribit funding, Deribit DVOL) is lagged one day; BTC's close prints at the same time as
ETH's, so BTC uses the same day. Percentile thresholds are trailing-365-day (past only).
Thresholds are fixed up front (not tuned), but 6 variants were tried - a lone narrow win
could be luck; a real improvement should show on every split.

Data sources (free, no keys): Yahoo daily ETH/BTC; alternative.me Fear & Greed (2018+);
Deribit ETH-PERPETUAL funding (2019+; Binance blocks US IPs); Deribit ETH DVOL (Mar 2021+).
See ETH_DATA_SOURCES.md.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

from data_fetcher import fetch_yahoo_history
from experiments_fast_entry import fast_entry, simulate_with_cadence
from experiments_multisplit import _passed, slice_dates
from experiments_regime import REGIME_SPLITS

SL, TP = 2.0, 50.0
FRESH = ("2026-01-01", "2099-12-31")
DERIBIT = "https://www.deribit.com/api/v2/public"


def ms(d: dt.datetime) -> int:
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def fear_greed() -> pd.Series:
    rows = requests.get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"},
                        timeout=30).json()["data"]
    s = pd.Series({pd.to_datetime(int(r["timestamp"]), unit="s").normalize(): float(r["value"])
                   for r in rows}).sort_index()
    return s


def deribit_funding() -> pd.Series:
    start, end = dt.datetime(2019, 8, 1), dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    vals = {}
    while start < end:
        stop = min(start + dt.timedelta(days=30), end)
        r = requests.get(f"{DERIBIT}/get_funding_rate_history",
                         params={"instrument_name": "ETH-PERPETUAL", "start_timestamp": ms(start),
                                 "end_timestamp": ms(stop)}, timeout=30).json()["result"]
        for x in r:
            vals[pd.to_datetime(x["timestamp"], unit="ms")] = x["interest_8h"]
        start = stop
    s = pd.Series(vals).sort_index()
    return s.groupby(s.index.normalize()).mean()   # daily mean of the 8h funding rate


def deribit_dvol() -> pd.Series:
    start, end = dt.datetime(2021, 3, 1), dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    vals = {}
    while start < end:
        stop = min(start + dt.timedelta(days=300), end)
        r = requests.get(f"{DERIBIT}/get_volatility_index_data",
                         params={"currency": "ETH", "start_timestamp": ms(start),
                                 "end_timestamp": ms(stop), "resolution": "1D"}, timeout=30).json()
        for t, _o, _h, _l, c in r["result"]["data"]:
            vals[pd.to_datetime(t, unit="ms").normalize()] = c
        start = stop
    return pd.Series(vals).sort_index()


def trailing_pct_rank(s: pd.Series, window: int = 365) -> pd.Series:
    """Percentile of each value within the previous `window` values (past only)."""
    return s.rolling(window, min_periods=60).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)


def build():
    eth = fetch_yahoo_history("ETH-USD", range_="10y", interval="1d")
    btc = fetch_yahoo_history("BTC-USD", range_="10y", interval="1d")
    eth["date"] = pd.to_datetime(eth["time"]).dt.tz_localize(None).dt.normalize()
    btc["date"] = pd.to_datetime(btc["time"]).dt.tz_localize(None).dt.normalize()
    b = btc.set_index("date")["close"]
    ext = pd.DataFrame({
        "btc_ret": b.pct_change(),
        "btc_up_cross": (b.rolling(2).mean() > b.rolling(3).mean()),
    })
    fg, fund, dvol = fear_greed(), deribit_funding(), deribit_dvol()
    lagged = pd.DataFrame({"fg": fg, "fund": fund, "dvol": dvol}).sort_index()
    lagged["fund_pct"] = trailing_pct_rank(lagged["fund"].dropna()).reindex(lagged.index)
    lagged["dvol_pct"] = trailing_pct_rank(lagged["dvol"].dropna()).reindex(lagged.index)
    lagged = lagged.shift(1)                     # yesterday's values only
    df = eth.merge(ext, left_on="date", right_index=True, how="left") \
            .merge(lagged, left_on="date", right_index=True, how="left")
    cover = {k: (lagged[k].first_valid_index().date() if lagged[k].notna().any() else None)
             for k in ("fg", "fund", "dvol")}
    return df, cover


# Each filter returns a boolean mask of days where a baseline entry is ALLOWED.
# Missing outside data -> allow (the filter is neutral where it has no information).
FILTERS = {
    "baseline (live ETH_FAST)": lambda d: pd.Series(True, index=d.index),
    "F&G: skip Extreme Greed (>75)": lambda d: ~(d["fg"] > 75),
    "F&G: only buy below 50": lambda d: d["fg"].isna() | (d["fg"] < 50),
    "Funding: skip top-10% crowded longs": lambda d: ~(d["fund_pct"] > 0.90),
    "BTC: same-day return > 0": lambda d: d["btc_ret"].isna() | (d["btc_ret"] > 0),
    "BTC: SMA(2)>SMA(3) too": lambda d: d["btc_up_cross"].fillna(True).astype(bool),
    "DVOL: skip top-10% vol spikes": lambda d: ~(d["dvol_pct"] > 0.90),
}


def entries(test_df: pd.DataFrame, allow) -> pd.DataFrame:
    sig = fast_entry(test_df, 2, 3)
    sig["entry"] = sig["entry"] & allow(test_df).fillna(True).astype(bool).values
    return sig


def main():
    df, cover = build()
    print(f"ETH daily {df['date'].min().date()}..{df['date'].max().date()} | outside data starts: "
          f"F&G {cover['fg']}, funding {cover['fund']}, DVOL {cover['dvol']} (DVOL only covers part "
          f"of split 2's 2020-2021 window; elsewhere in it the DVOL filter is neutral)\n")
    windows = [(f"split{i} test {ts}..{te}", ts, te) for i, (_, _, ts, te) in enumerate(REGIME_SPLITS, 1)]
    windows.append((f"fresh {FRESH[0]}..now", *FRESH))
    results = []
    for name, allow in FILTERS.items():
        row = {"variant": name}
        for label, ts, te in windows:
            w = slice_dates(df, ts, te)
            stats, gap, _ = simulate_with_cadence(entries(w, allow), TP, SL)
            row[label] = (stats["total_return_pct"], stats["max_drawdown_pct"],
                          stats["completed_trades"], gap, _passed(stats))
        results.append(row)

    base = results[0]
    print(f"{'variant':38} | " + " | ".join(f"{lbl[:22]:>34}" for lbl, *_ in windows))
    for r in results:
        cells = []
        for lbl, *_ in windows:
            ret, dd, n, gap, ok = r[lbl]
            g = f"{gap:.1f}d" if gap else "n/a"
            cells.append(f"{ret:8.1f}% dd {dd:5.1f}% n={n:3d} gap={g:>6}")
        print(f"{r['variant']:38} | " + " | ".join(cells))

    print("\nVerdict vs baseline (must beat baseline on return AND drawdown on BOTH regime splits,"
          " and pass the project's rules on both):")
    for r in results[1:]:
        split_labels = [lbl for lbl, *_ in windows[:2]]
        better = all(r[l][0] > base[l][0] and r[l][1] < base[l][1] for l in split_labels)
        passes = all(r[l][4] for l in split_labels)
        fresh = windows[2][0]
        fr = f"fresh 2026: {r[fresh][0]:+.1f}% vs baseline {base[fresh][0]:+.1f}%"
        verdict = "IMPROVES" if (better and passes) else "no improvement"
        print(f"  {r['variant']:38} -> {verdict:15} ({fr})")


if __name__ == "__main__":
    main()
