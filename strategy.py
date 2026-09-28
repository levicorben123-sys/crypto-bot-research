import pandas as pd

import config


def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.where(avg_loss != 0, 100)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sma_fast"] = df["close"].rolling(config.FAST_MA_PERIOD).mean()
    df["sma_slow"] = df["close"].rolling(config.SLOW_MA_PERIOD).mean()
    df["rsi"] = compute_rsi(df["close"], config.RSI_PERIOD)
    return df


def generate_signal(df: pd.DataFrame) -> str:
    """SMA crossover strategy, filtered by RSI to avoid chasing overbought/oversold moves.

    Returns 'BUY', 'SELL', or 'HOLD' based on the two most recent closed candles.
    """
    df = add_indicators(df)
    if len(df) < max(config.SLOW_MA_PERIOD, config.RSI_PERIOD) + 2:
        return "HOLD"

    prev, curr = df.iloc[-3], df.iloc[-2]  # -1 is the still-forming candle

    crossed_up = prev["sma_fast"] <= prev["sma_slow"] and curr["sma_fast"] > curr["sma_slow"]
    crossed_down = prev["sma_fast"] >= prev["sma_slow"] and curr["sma_fast"] < curr["sma_slow"]

    if crossed_up and curr["rsi"] < config.RSI_OVERBOUGHT:
        return "BUY"
    if crossed_down and curr["rsi"] > config.RSI_OVERSOLD:
        return "SELL"
    return "HOLD"
