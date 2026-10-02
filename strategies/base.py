"""
策略介面。新策略只要繼承 Strategy，實作 prepare() 和 on_bar()。

執行順序（和 Pine 的 process_orders_on_close = true 一致）：
  1. 第 i 根 K 棒盤中：如果有上一根收盤時掛的停損，碰到就出場
     （開盤就跳空穿過停損 → 用開盤價成交）
  2. 第 i 根收盤：呼叫 on_bar(i, ctx)，回傳 Decision
       exit=True     → 以收盤價平倉
       enter=+1/-1   → 以收盤價進場（反向持倉會先平倉）
       stop=價格      → 從下一根開始生效的停損
  3. on_bar 裡只能讀第 i 根（含）以前的資料，否則就是偷看未來。
     lab/causal.py 會自動檢查。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Decision:
    exit: bool = False
    enter: int = 0               # +1 做多 / -1 做空 / 0 不動作
    stop: float | None = None    # 下一根開始生效的停損價
    note: str = ""


@dataclass
class Ctx:
    position: int                # +1 / -1 / 0（決策前的持倉）
    entry_price: float
    stop: float | None
    equity: float
    bars_in_trade: int


class Strategy:
    id: str = "base"
    name: str = "base"
    timeframe: str = "1h"
    warmup: int = 200
    source: str = ""              # 策略出處（網址或說明）
    default_params: dict = {}

    def __init__(self, **params):
        self.params = {**self.default_params, **params}

    # 回傳至少含 open/high/low/close 的 DataFrame，索引 = K 棒開盤時間
    def prepare(self, market) -> pd.DataFrame:
        raise NotImplementedError

    # 每次回測開始前呼叫，重設內部狀態
    def reset(self) -> None:
        pass

    def on_bar(self, i: int, ctx: Ctx) -> Decision | None:
        raise NotImplementedError

    # 報告裡顯示的即時狀態（可選）
    def status(self, i: int, ctx: Ctx) -> str:
        return ""
