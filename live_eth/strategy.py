import numpy as np
import pandas as pd

import config


def compute_stochastic(df: pd.DataFrame, k_period: int) -> tuple[pd.Series, pd.Series]:
    low_n = df["low"].rolling(k_period).min()
    high_n = df["high"].rolling(k_period).max()
    percent_k = 100 * (df["close"] - low_n) / (high_n - low_n)
    percent_d = percent_k.rolling(3).mean()
    return percent_k, percent_d


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def compute_adx(df: pd.DataFrame, period: int) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = _true_range(df)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["percent_k"], df["percent_d"] = compute_stochastic(df, config.STOCH_K_PERIOD)
    df["adx"] = compute_adx(df, config.ADX_PERIOD)
    return df


def generate_signal(df: pd.DataFrame) -> str:
    """Stochastic %K/%D mean-reversion entry (Round 20), with an ADX regime filter added
    in Round 23: buy when %K crosses above %D while still below the oversold threshold,
    AND ADX is above ADX_THRESHOLD (sits out dead-flat chop where the weakest signals in
    backtesting occurred) - a bet that a short-term dip is reverting, not a trend-
    following signal like the rest of this project's bots.

    Exit is handled entirely by paper_trader.py's fixed TP/SL; this strategy has no
    reverse-signal exit (SELL is unused by the live loop, same as the SMA-crossover
    strategy elsewhere - paper_trader only checks TP/SL once in a position).

    Returns 'BUY' or 'HOLD' based on the two most recent closed candles.
    """
    df = add_indicators(df)
    min_len = max(config.STOCH_K_PERIOD + 3, config.ADX_PERIOD * 2) + 2
    if len(df) < min_len:
        return "HOLD"

    prev, curr = df.iloc[-3], df.iloc[-2]  # -1 is the still-forming candle

    crossed_up = prev["percent_k"] <= prev["percent_d"] and curr["percent_k"] > curr["percent_d"]

    if crossed_up and curr["percent_k"] < config.STOCH_OVERSOLD and curr["adx"] > config.ADX_THRESHOLD:
        return "BUY"
    return "HOLD"
