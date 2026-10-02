"""
和 TradingView Pine 行為一致的指標（只用過去資料）。
全部輸入 / 輸出 pandas Series，前面不足的部分是 NaN。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def wma(x: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return x.rolling(n, min_periods=n).apply(lambda a: np.dot(a, w) / w.sum(), raw=True)


def _recursive(x: pd.Series, n: int, alpha: float) -> pd.Series:
    """Pine 的 ema / rma：以前 n 根的 SMA 當起始值。"""
    v = x.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    seed = pd.Series(v).rolling(n, min_periods=n).mean().to_numpy()
    prev = np.nan
    for i in range(len(v)):
        if np.isnan(prev):
            prev = seed[i]
        elif not np.isnan(v[i]):
            prev = alpha * v[i] + (1 - alpha) * prev
        out[i] = prev
    return pd.Series(out, index=x.index)


def ema(x: pd.Series, n: int) -> pd.Series:
    return _recursive(x, n, 2.0 / (n + 1))


def rma(x: pd.Series, n: int) -> pd.Series:
    return _recursive(x, n, 1.0 / n)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def highest(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).max()


def lowest(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).min()


def rsi(x: pd.Series, n: int = 14) -> pd.Series:
    d = x.diff()
    up = rma(d.clip(lower=0), n)
    dn = rma((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn)


def stdev(x: pd.Series, n: int) -> pd.Series:
    """Pine ta.stdev 是母體標準差（ddof=0）。"""
    return x.rolling(n, min_periods=n).std(ddof=0)
