"""
HSO 分析系統 = Hades 趨勢 × Swing 擺動停損 × 訂單流濾網
Python 版，逐行對照使用者提供的 Pine v6 腳本。

和 TradingView 版的已知差異：
  - 資料來源是 Bybit/OKX（TradingView 看你圖表選的交易所）
  - OI 用 1 小時快照，週期 < 1h 時會比較粗
  - 滑價用 0.02%（Pine 設 2 ticks，幾乎等於 0）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from lab import indicators as ta

from .base import Ctx, Decision, Strategy

LTF = {"15m": "15m", "1h": "15m", "4h": "1h", "1d": "4h"}  # 對照 Pine 的 ltf 選擇


class HSO(Strategy):
    id = "hso_v1"
    name = "HSO（Hades × Swing × OrderFlow）"
    timeframe = "1h"
    warmup = 220
    source = "使用者提供的 Pine 腳本（依 Zekis 公開說明重建）"
    default_params = {
        "mode": "Long only",       # Long only / Long+Short / Short only
        "TREND_LEN": 120, "BASE_LEN": 200, "MA1_LEN": 41, "MA2_LEN": 72,
        "DC_LEN": 20, "ATR_BUF": 0.3,
        "FLOW_LEN": 24, "OI_TH": 0.5, "BASIS_Z_LEN": 200, "CROWD_Z": 2.0, "MIN_SCORE": 1,
    }

    def __init__(self, timeframe: str = "1h", **params):
        super().__init__(**params)
        self.timeframe = timeframe

    # ------------------------------------------------------------------
    def prepare(self, market) -> pd.DataFrame:
        p = self.params
        tf = self.timeframe
        df = market.bars(tf, "perp").copy()
        idx = df.index
        close = df["close"]

        # ① Hades
        trend = ta.wma(close, p["TREND_LEN"])
        base = ta.sma(close, p["BASE_LEN"])
        ma1 = ta.ema(close, p["MA1_LEN"])
        ma2 = ta.ema(close, p["MA2_LEN"])
        zone = np.where((trend > base) & (ma1 > ma2), 1, np.where((trend < base) & (ma1 < ma2), -1, 0))
        df["zone"] = zone

        # ② Swing（Donchian 破位確認的 zigzag）
        df["atr"] = ta.atr(df, 14)
        dc_hi = ta.highest(df["high"], p["DC_LEN"]).shift(1).to_numpy()
        dc_lo = ta.lowest(df["low"], p["DC_LEN"]).shift(1).to_numpy()
        hi, lo = df["high"].to_numpy(), df["low"].to_numpy()
        n = len(df)
        sw_hi = np.full(n, np.nan); sw_lo = np.full(n, np.nan)
        new_hi = np.zeros(n, bool); new_lo = np.zeros(n, bool)
        zz, ext = 1, np.nan
        cur_hi = cur_lo = np.nan
        for i in range(n):
            if zz == 1:
                if np.isnan(ext) or hi[i] >= ext:
                    ext = hi[i]
                if not np.isnan(dc_lo[i]) and lo[i] < dc_lo[i]:
                    cur_hi = ext; new_hi[i] = True
                    zz, ext = -1, lo[i]
            else:
                if np.isnan(ext) or lo[i] <= ext:
                    ext = lo[i]
                if not np.isnan(dc_hi[i]) and hi[i] > dc_hi[i]:
                    cur_lo = ext; new_lo[i] = True
                    zz, ext = 1, hi[i]
            sw_hi[i], sw_lo[i] = cur_hi, cur_lo
        df["swHi"], df["swLo"], df["newSwHi"], df["newSwLo"] = sw_hi, sw_lo, new_hi, new_lo

        # ③ 訂單流
        ltf = LTF[tf]
        spot_d = market.ltf_delta(tf, ltf, "spot").reindex(idx).fillna(0.0)
        perp_d = market.ltf_delta(tf, ltf, "perp").reindex(idx).fillna(0.0)
        spot_cvd = spot_d.rolling(p["FLOW_LEN"], min_periods=p["FLOW_LEN"]).sum()
        perp_cvd = perp_d.rolling(p["FLOW_LEN"], min_periods=p["FLOW_LEN"]).sum()

        oi = market.oi_close(tf, idx)
        oi_chg = (oi / oi.shift(p["FLOW_LEN"]) - 1) * 100
        px_chg = (close / close.shift(p["FLOW_LEN"]) - 1) * 100
        th = p["OI_TH"]
        regime = np.select(
            [oi_chg.isna(), (px_chg > 0) & (oi_chg > th), (px_chg > 0) & (oi_chg < -th),
             (px_chg < 0) & (oi_chg > th), (px_chg < 0) & (oi_chg < -th)],
            [0, 2, 1, -2, -1], 0)

        spot_c = market.bars(tf, "spot")["close"].reindex(idx)
        basis = (close / spot_c - 1) * 100
        b_mean = ta.sma(basis, p["BASIS_Z_LEN"])
        b_sd = ta.stdev(basis, p["BASIS_Z_LEN"])
        basis_z = ((basis - b_mean) / b_sd).where(b_sd > 0).fillna(0.0)

        score = (np.sign(spot_cvd) + np.sign(perp_cvd)
                 + np.where(regime == 2, 1, np.where(regime == -2, -1, 0))
                 + np.where(basis_z > p["CROWD_Z"], -1, np.where(basis_z < -p["CROWD_Z"], 1, 0)))
        score = score.fillna(0.0)  # Pine 的 nz()
        crowd_long = basis_z > p["CROWD_Z"]
        crowd_short = basis_z < -p["CROWD_Z"]
        df["flowScore"] = score
        df["flowOkLong"] = (score >= p["MIN_SCORE"]) & ~crowd_long
        df["flowOkShort"] = (-score >= p["MIN_SCORE"]) & ~crowd_short

        self.a = {k: df[k].to_numpy() for k in
                  ["close", "zone", "atr", "swHi", "swLo", "newSwHi", "newSwLo", "flowOkLong", "flowOkShort", "flowScore"]}
        return df

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.zone_used = False
        self.long_stop = np.nan
        self.short_stop = np.nan

    def on_bar(self, i: int, ctx: Ctx) -> Decision | None:
        a, p = self.a, self.params
        zone, close, atr = a["zone"][i], a["close"][i], a["atr"][i]
        sw_hi, sw_lo = a["swHi"][i], a["swLo"][i]
        buf = p["ATR_BUF"]
        pos = ctx.position

        if i == 0 or zone != a["zone"][i - 1]:
            self.zone_used = False

        allow_long = p["mode"] != "Short only"
        allow_short = p["mode"] != "Long only"
        long_cond = allow_long and zone == 1 and not self.zone_used and pos <= 0 and a["flowOkLong"][i]
        short_cond = allow_short and zone == -1 and not self.zone_used and pos >= 0 and a["flowOkShort"][i]

        d = Decision()

        # 停損移動（只針對「已經」持有的部位，和 Pine 的 position_size 一致）
        if pos > 0 and a["newSwLo"][i]:
            self.long_stop = max(self.long_stop, sw_lo - buf * atr) if not np.isnan(self.long_stop) else sw_lo - buf * atr
        if pos < 0 and a["newSwHi"][i]:
            self.short_stop = min(self.short_stop, sw_hi + buf * atr) if not np.isnan(self.short_stop) else sw_hi + buf * atr

        # Hades 出場
        if pos > 0:
            if zone != 1:
                d.exit, d.note = True, "Hades 平多"
            else:
                d.stop = self.long_stop
        elif pos < 0:
            if zone != -1:
                d.exit, d.note = True, "Hades 平空"
            else:
                d.stop = self.short_stop

        # 進場
        if long_cond:
            self.long_stop = close - 2 * atr if (np.isnan(sw_lo) or sw_lo >= close) else sw_lo - buf * atr
            d.enter, d.stop = 1, self.long_stop
            self.zone_used = True
        elif short_cond:
            self.short_stop = close + 2 * atr if (np.isnan(sw_hi) or sw_hi <= close) else sw_hi + buf * atr
            d.enter, d.stop = -1, self.short_stop
            self.zone_used = True
        return d

    def status(self, i: int, ctx: Ctx) -> str:
        a = self.a
        zone = {1: "綠區", -1: "紅區", 0: "黃區"}[int(a["zone"][i])]
        if ctx.position != 0:
            return f"{zone}｜持有中，黃區或停損出場｜訂單流分數 {a['flowScore'][i]:+.0f}"
        if zone == "黃區":
            return "黃區：等趨勢成形"
        if self.zone_used:
            return f"{zone}｜本區間已交易過，等下一個區間"
        return f"{zone}｜等訂單流確認（分數 {a['flowScore'][i]:+.0f}）"
