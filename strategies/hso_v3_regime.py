"""
HSO v3：長期趨勢方向濾網（Faber 10 個月均線）

診斷（2026-10-03，OKX 4H）：
  - OOS 空單 19 筆、平均 -0.11R、勝率 21%；多單 15 筆、平均 +0.31R。
  - 空單大多在長期多頭中逆勢進場，賺不到。

改良點子：Mebane Faber, "A Quantitative Approach to Tactical Asset Allocation"（2007）
  原規則：價格在 10 個月簡單均線之上才持有，否則空手。
  套用到 HSO：只在「日線收盤 > 10 個月 SMA」時做多、「< 10 個月 SMA」時做空。
  10 個月 ≈ 210 個交易日（加密貨幣 7 天都交易，用 300 天 ≈ 10 個日曆月）。
  均線用「已收盤的日 K」計算，4H 每根只看到前一天以前的日線，不會偷看。
  出場、停損完全不變。

出處：https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461
      https://quantpedia.com/strategies/asset-class-trend-following
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .hso import HSO


class HSORegime(HSO):
    id = "hso_v3_regime"
    name = "HSO v3（+ Faber 10 個月均線方向濾網）"
    source = "Faber (2007) SSRN 962461"
    default_params = {**HSO.default_params, "REGIME_DAYS": 300}

    def prepare(self, market) -> pd.DataFrame:
        df = super().prepare(market)
        n = self.params["REGIME_DAYS"]
        daily = market.bars("1d", "perp")["close"]
        sma = daily.rolling(n, min_periods=n).mean()
        # 日 K 在隔天 00:00 UTC 收盤；只把「已收盤」的日線資訊給 4H K 棒
        avail = pd.DataFrame({"close_d": daily, "sma_d": sma})
        avail.index = avail.index + pd.Timedelta("1D")
        bar_close = df.index + pd.Timedelta("4h")
        aligned = avail.reindex(avail.index.union(bar_close)).ffill().reindex(bar_close)
        bull = (aligned["close_d"] > aligned["sma_d"]).to_numpy()
        bear = (aligned["close_d"] < aligned["sma_d"]).to_numpy()
        df["regime"] = np.where(bull, 1, np.where(bear, -1, 0))
        df["flowOkLong"] = df["flowOkLong"].to_numpy() & bull
        df["flowOkShort"] = df["flowOkShort"].to_numpy() & bear
        self.a["flowOkLong"] = df["flowOkLong"].to_numpy()
        self.a["flowOkShort"] = df["flowOkShort"].to_numpy()
        self.a["regime"] = df["regime"].to_numpy()
        return df

    def status(self, i, ctx) -> str:
        r = {1: "長期多頭（只做多）", -1: "長期空頭（只做空）", 0: "長期中性"}[int(self.a["regime"][i])]
        return super().status(i, ctx) + f"｜{r}"
