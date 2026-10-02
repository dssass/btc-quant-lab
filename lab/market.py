"""
讀取收集器存下的 CSV，提供策略需要的各種時間週期資料。

所有序列的索引都是 K 棒「開盤時間」(UTC)。
一根 K 棒只有在完整收盤後才會出現在 bars() 裡，所以策略在第 i 根做決策時，
看到的都是已經確定的資料。
"""

from __future__ import annotations

import glob
import json
import os

import numpy as np
import pandas as pd

from .config import TIMEFRAMES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M15 = pd.Timedelta(minutes=15)


def _read_series(folder: str, cols: list[str]) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    if not files:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], tz="UTC", name="ts"))
    df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index()
    return df[cols].astype(float)


class Market:
    def __init__(self, spot15: pd.DataFrame, perp15: pd.DataFrame, oi1h: pd.DataFrame, source: str = "?"):
        self.spot15 = spot15
        self.perp15 = perp15
        self.oi1h = oi1h
        self.source = source
        self._cache: dict = {}

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, data_dir: str | None = None, source: str | None = None) -> "Market":
        data_dir = data_dir or os.path.join(ROOT, "data")
        if source is None:
            with open(os.path.join(data_dir, "manifest.json")) as fh:
                source = json.load(fh)["source"]
        base = os.path.join(data_dir, source)
        ohlcv = ["open", "high", "low", "close", "volume"]
        return cls(
            _read_series(os.path.join(base, "spot_15m"), ohlcv),
            _read_series(os.path.join(base, "perp_15m"), ohlcv),
            _read_series(os.path.join(base, "oi_1h"), ["oi"]),
            source,
        )

    def truncate(self, end: pd.Timestamp) -> "Market":
        """只保留在 end 之前已經知道的資料（給偷看未來檢查用）。"""
        return Market(
            self.spot15[self.spot15.index + M15 <= end],
            self.perp15[self.perp15.index + M15 <= end],
            self.oi1h[self.oi1h.index <= end],
            self.source,
        )

    @property
    def last_time(self) -> pd.Timestamp:
        return self.perp15.index[-1] + M15

    # ------------------------------------------------------------------
    def bars(self, tf: str = "1h", kind: str = "perp") -> pd.DataFrame:
        """重新取樣成 tf 週期的 OHLCV，只保留已收盤的 K 棒。"""
        key = ("bars", tf, kind)
        if key in self._cache:
            return self._cache[key]
        src = self.perp15 if kind == "perp" else self.spot15
        if src.empty:
            out = src.copy()
        elif tf == "15m":
            out = src.copy()
        else:
            rule = TIMEFRAMES[tf]
            out = src.resample(rule, label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(subset=["close"])
            width = pd.Timedelta(rule)
            data_end = src.index[-1] + M15
            out = out[out.index + width <= data_end]
        self._cache[key] = out
        return out

    def ltf_delta(self, tf: str, ltf: str, kind: str = "perp") -> pd.Series:
        """
        Pine 的 request.security_lower_tf(f_delta) 再加總：
        每根低週期 K 棒 delta = sign(close - open) * volume，加總到 tf 週期。
        沒有資料的 K 棒回傳 NaN。
        """
        low = self.bars(ltf, kind)
        if low.empty:
            return pd.Series(dtype=float)
        d = np.sign(low["close"] - low["open"]) * low["volume"]
        if ltf == tf:
            return d
        return d.resample(TIMEFRAMES[tf], label="left", closed="left").sum(min_count=1)

    def oi_close(self, tf: str, index: pd.DatetimeIndex) -> pd.Series:
        """每根 K 棒收盤時最新的 OI 快照（時間戳 <= 收盤時間）。"""
        if self.oi1h.empty:
            return pd.Series(np.nan, index=index)
        width = pd.Timedelta(TIMEFRAMES[tf])
        ends = index + width
        oi = self.oi1h["oi"]
        vals = oi.reindex(oi.index.union(ends)).ffill().reindex(ends)
        # 太舊的快照（超過 3 小時）視為缺漏
        last_ts = pd.Series(oi.index, index=oi.index).reindex(oi.index.union(ends)).ffill().reindex(ends)
        age = ends - pd.DatetimeIndex(last_ts)
        vals = vals.where(~np.asarray(age > pd.Timedelta(hours=3)))
        return pd.Series(vals.values, index=index)
