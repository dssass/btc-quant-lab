"""
基準策略：經典 Donchian 通道突破（海龜法則簡化版）
收盤突破前 N 根最高 → 做多；跌破前 M 根最低 → 出場；初始停損 2 ATR。
用途是當「隨便一個趨勢策略能做到多少」的對照組。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from lab import indicators as ta

from .base import Ctx, Decision, Strategy


class DonchianTrend(Strategy):
    id = "baseline_donchian"
    name = "基準：Donchian 突破"
    timeframe = "1h"
    source = "經典海龜交易法則（Richard Dennis）"
    default_params = {"entry_len": 55, "exit_len": 20, "atr_mult": 2.0, "long_only": True}

    def __init__(self, timeframe: str = "1h", **params):
        super().__init__(**params)
        self.timeframe = timeframe
        self.warmup = max(self.params["entry_len"], self.params["exit_len"]) + 15

    def prepare(self, market) -> pd.DataFrame:
        p = self.params
        df = market.bars(self.timeframe, "perp").copy()
        df["up"] = ta.highest(df["high"], p["entry_len"]).shift(1)
        df["dn"] = ta.lowest(df["low"], p["entry_len"]).shift(1)
        df["xl"] = ta.lowest(df["low"], p["exit_len"]).shift(1)
        df["xs"] = ta.highest(df["high"], p["exit_len"]).shift(1)
        df["atr"] = ta.atr(df, 14)
        self.a = {k: df[k].to_numpy() for k in ["close", "up", "dn", "xl", "xs", "atr"]}
        return df

    def on_bar(self, i: int, ctx: Ctx) -> Decision | None:
        a, p = self.a, self.params
        c = a["close"][i]
        if ctx.position > 0 and c < a["xl"][i]:
            return Decision(exit=True, note="跌破出場通道")
        if ctx.position < 0 and c > a["xs"][i]:
            return Decision(exit=True, note="突破出場通道")
        if ctx.position == 0:
            if c > a["up"][i]:
                return Decision(enter=1, stop=c - p["atr_mult"] * a["atr"][i])
            if not p["long_only"] and c < a["dn"][i]:
                return Decision(enter=-1, stop=c + p["atr_mult"] * a["atr"][i])
        return None
