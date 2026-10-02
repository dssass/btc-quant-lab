"""
HSO v2：原版 HSO + ADX 趨勢強度濾網

診斷（2026-10-02，OKX 4H 資料）：
  - OOS 中由「Hades 黃區出場」結束的 18 筆交易，勝率 11%、平均 -0.25R，
    代表很多單是在沒有趨勢的盤整期進場，訊號翻來翻去後小賠出場。
  - OOS 進場後最大有利幅度（MFE）中位數只有 0.61R，進場後常常走不出去。

改良點子：Welles Wilder（1978）的 ADX。Wilder 定義 ADX < 20 為「沒有趨勢」。
只在 ADX(14) ≥ 20 時允許新進場；出場、停損邏輯完全不變。
被擋下的訊號不會用掉該區間的進場機會（和訂單流濾網的行為一致），
ADX 之後升上 20 仍可在同一個綠區/紅區進場。

出處：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from lab import indicators as ta

from .hso import HSO


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder 的 ADX（和 Pine ta.dmi 相同的 RMA 平滑）。"""
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = ta.rma(ta.true_range(df), n)
    plus_di = 100 * ta.rma(plus_dm, n) / tr
    minus_di = 100 * ta.rma(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return ta.rma(dx, n)


class HSOAdx(HSO):
    id = "hso_v2_adx"
    name = "HSO v2（+ ADX 趨勢強度濾網）"
    source = "Wilder (1978) ADX；StockCharts ChartSchool"
    default_params = {**HSO.default_params, "ADX_LEN": 14, "ADX_MIN": 20}

    def prepare(self, market) -> pd.DataFrame:
        df = super().prepare(market)
        p = self.params
        df["adx"] = adx(df, p["ADX_LEN"])
        trending = (df["adx"] >= p["ADX_MIN"]).to_numpy()
        df["flowOkLong"] = df["flowOkLong"].to_numpy() & trending
        df["flowOkShort"] = df["flowOkShort"].to_numpy() & trending
        self.a["flowOkLong"] = df["flowOkLong"].to_numpy()
        self.a["flowOkShort"] = df["flowOkShort"].to_numpy()
        self.a["adx"] = df["adx"].to_numpy()
        return df

    def status(self, i, ctx) -> str:
        return super().status(i, ctx) + f"｜ADX {self.a['adx'][i]:.0f}"
