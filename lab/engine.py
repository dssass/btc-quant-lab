"""逐根 K 棒回測引擎（行為說明見 strategies/base.py）。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategies.base import Ctx, Decision, Strategy

from .config import CAPITAL, FEE, SLIPPAGE, TIMEFRAMES


@dataclass
class Trade:
    side: int
    entry_time: pd.Timestamp
    entry_price: float
    qty: float
    init_stop: float | None
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    pnl: float = 0.0          # 扣完手續費的損益（USDT）
    ret: float = 0.0          # 相對進場時權益的報酬率
    R: float = float("nan")   # 以初始停損距離衡量的 R 倍數（未扣費）


@dataclass
class Result:
    strategy: Strategy
    bars: pd.DataFrame
    equity: pd.Series                     # 索引 = K 棒收盤時間
    position: pd.Series                   # 每根收盤後的持倉方向
    trades: list[Trade] = field(default_factory=list)
    open_trade: Trade | None = None
    stop: float | None = None             # 目前掛著的停損
    last_status: str = ""


def run(strategy: Strategy, market, fee: float = FEE, slip: float = SLIPPAGE,
        capital: float = CAPITAL) -> Result:
    df = strategy.prepare(market)
    strategy.reset()
    n = len(df)
    width = pd.Timedelta(TIMEFRAMES[strategy.timeframe])
    close_t = df.index + width
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)

    realized = capital
    pos = 0
    qty = 0.0
    entry_px = 0.0
    stop: float | None = None
    bars_in = 0
    cur: Trade | None = None
    trades: list[Trade] = []
    eq = np.empty(n)
    posarr = np.zeros(n, dtype=int)
    status = ""

    def close_pos(i: int, raw_px: float, reason: str) -> None:
        nonlocal realized, pos, qty, entry_px, stop, bars_in, cur
        px = raw_px * (1 - slip) if pos > 0 else raw_px * (1 + slip)
        gross = pos * qty * (px - entry_px)
        cost = qty * px * fee
        realized += gross - cost
        cur.exit_time = close_t[i]
        cur.exit_price = px
        cur.exit_reason = reason
        cur.pnl += gross - cost
        cur.ret = cur.pnl / cur._equity_at_entry
        if cur.init_stop is not None and cur.init_stop != cur.entry_price:
            cur.R = pos * (raw_px - cur.entry_price) / abs(cur.entry_price - cur.init_stop)
        trades.append(cur)
        pos, qty, entry_px, stop, bars_in, cur = 0, 0.0, 0.0, None, 0, None

    def open_pos(i: int, side: int, raw_px: float, new_stop: float | None) -> None:
        nonlocal realized, pos, qty, entry_px, stop, bars_in, cur
        px = raw_px * (1 + slip) if side > 0 else raw_px * (1 - slip)
        q = realized / px
        cost = q * px * fee
        realized -= cost
        pos, qty, entry_px, stop, bars_in = side, q, px, new_stop, 0
        cur = Trade(side, close_t[i], raw_px, q, new_stop)
        cur.pnl = -cost
        cur._equity_at_entry = realized + cost

    for i in range(n):
        # 1) 盤中停損
        if pos != 0 and stop is not None:
            if pos > 0 and l[i] <= stop:
                close_pos(i, o[i] if o[i] <= stop else stop, "stop")
            elif pos < 0 and h[i] >= stop:
                close_pos(i, o[i] if o[i] >= stop else stop, "stop")

        # 2) 收盤決策
        if i >= strategy.warmup and not np.isnan(c[i]):
            ctx = Ctx(pos, entry_px, stop, realized + pos * qty * (c[i] - entry_px), bars_in)
            d: Decision | None = strategy.on_bar(i, ctx)
            if d is not None:
                if d.exit and pos != 0:
                    close_pos(i, c[i], d.note or "exit")
                if d.enter and d.enter != pos:
                    if pos != 0:
                        close_pos(i, c[i], "reverse")
                    open_pos(i, d.enter, c[i], d.stop)
                elif d.stop is not None and pos != 0:
                    stop = d.stop
            if i == n - 1:
                status = strategy.status(i, Ctx(pos, entry_px, stop, realized, bars_in))

        if pos != 0:
            bars_in += 1
        eq[i] = realized + pos * qty * (c[i] - entry_px)
        posarr[i] = pos

    return Result(strategy, df, pd.Series(eq, index=close_t), pd.Series(posarr, index=close_t),
                  trades, cur, stop, status)
