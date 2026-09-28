"""BTC/ETH pairs (relative-value) mean-reversion strategy.

Instead of predicting direction on one asset, this trades the *relationship* between
two correlated assets: when BTC gets unusually expensive relative to ETH (or vice
versa), short the rich one and long the cheap one in equal dollar size, betting the
ratio reverts toward its recent average. Market-neutral in principle — profit doesn't
require either coin to go up or down, just for the ratio to mean-revert.
"""
import numpy as np
import pandas as pd


def align_pair(df_a: pd.DataFrame, df_b: pd.DataFrame) -> pd.DataFrame:
    """Inner-join two OHLCV DataFrames (as returned by fetch_yahoo_history) on date."""
    merged = pd.merge(
        df_a[["time", "close"]], df_b[["time", "close"]],
        on="time", suffixes=("_a", "_b"),
    )
    return merged.reset_index(drop=True)


def compute_ratio_zscore(merged: pd.DataFrame, window: int) -> pd.DataFrame:
    df = merged.copy()
    df["log_ratio"] = np.log(df["close_a"] / df["close_b"])
    roll_mean = df["log_ratio"].rolling(window).mean()
    roll_std = df["log_ratio"].rolling(window).std()
    df["zscore"] = (df["log_ratio"] - roll_mean) / roll_std
    return df


def mean_reversion_sanity_check(log_ratio: pd.Series) -> dict:
    """Checks whether the BTC/ETH log ratio actually behaves in a mean-reverting way
    over the given window, rather than trending (one coin secularly out/underperforming
    the other) - a real risk with only two assets. Uses an ADF test (p < 0.05 suggests
    stationary/mean-reverting) plus a zero-crossing-frequency heuristic as a plain-English
    cross-check.
    """
    from statsmodels.tsa.stattools import adfuller

    series = log_ratio.dropna()
    adf_stat, p_value, *_ = adfuller(series)

    demeaned = series - series.mean()
    sign_changes = (np.sign(demeaned).diff().fillna(0) != 0).sum()
    avg_days_between_crossings = len(series) / sign_changes if sign_changes else float("inf")

    return {
        "adf_p_value": p_value,
        "likely_mean_reverting": p_value < 0.05,
        "zero_crossings": int(sign_changes),
        "avg_days_between_crossings": avg_days_between_crossings,
    }
