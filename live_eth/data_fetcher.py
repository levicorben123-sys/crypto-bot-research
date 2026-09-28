import pandas as pd
import requests

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"


def fetch_ohlc(pair: str, interval_minutes: int, since: int | None = None) -> pd.DataFrame:
    """Fetch OHLC candles from Kraken's public (no-auth) market data API.

    Kraken only retains ~720 candles of history per pair/interval combination.
    """
    params = {"pair": pair, "interval": interval_minutes}
    if since is not None:
        params["since"] = since

    resp = requests.get(KRAKEN_OHLC_URL, params=params, timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("error"):
        raise RuntimeError(f"Kraken API error: {payload['error']}")

    result = payload["result"]
    pair_key = next(k for k in result if k != "last")
    rows = result[pair_key]

    df = pd.DataFrame(rows, columns=[
        "time", "open", "high", "low", "close", "vwap", "volume", "count",
    ])
    numeric_cols = ["open", "high", "low", "close", "vwap", "volume"]
    df[numeric_cols] = df[numeric_cols].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    return df[["time", "open", "high", "low", "close", "volume"]]


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def fetch_yahoo_history(symbol: str, range_: str = "10y", interval: str = "1d") -> pd.DataFrame:
    """Fetch long-history OHLCV from Yahoo Finance's public chart API (no auth needed).

    For backtesting/research only - the live paper-trading loop still uses Kraken's
    fetch_ohlc for near-real-time prices. Yahoo has years of history (e.g. BTC-USD back
    to Oct 2014), covering multiple market regimes that Kraken's ~720-candle public
    endpoint can't reach. Symbols look like "BTC-USD", "ETH-USD".

    Gotcha: range_="max" silently downsamples crypto tickers to ~monthly candles instead
    of daily (145 "days" across 12 years). Use an explicit bounded range like "10y" to
    keep true daily granularity - verified via len(df) against the date span.
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"range": range_, "interval": interval}
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    results = payload.get("chart", {}).get("result")
    if not results:
        raise RuntimeError(f"Yahoo Finance returned no data for {symbol}: {payload.get('chart', {}).get('error')}")

    result = results[0]
    quote = result["indicators"]["quote"][0]

    df = pd.DataFrame({
        "time": pd.to_datetime(result["timestamp"], unit="s"),
        "open": quote["open"],
        "high": quote["high"],
        "low": quote["low"],
        "close": quote["close"],
        "volume": quote["volume"],
    })
    return df.dropna().reset_index(drop=True)


def load_kraken_csv_1min(path: str) -> pd.DataFrame:
    """Load Kraken's official bulk-download 1-minute OHLCVT CSV (no header row).

    Columns are unix_timestamp,open,high,low,close,volume,trades. This is Kraken's own
    historical data (not Yahoo's), used for research/backtest at the timeframe the live
    bot actually trades on - the live loop still uses fetch_ohlc for near-real-time
    prices, this is not wired into main.py.
    """
    df = pd.read_csv(path, header=None, names=[
        "time", "open", "high", "low", "close", "volume", "trades",
    ])
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "volume"]]


def resample_ohlc(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Resample OHLCV candles to a coarser interval via standard OHLC aggregation."""
    return (
        df.set_index("time")
        .resample(f"{minutes}min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
