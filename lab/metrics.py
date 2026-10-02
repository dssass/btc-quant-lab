"""績效統計：把回測結果切成 IS / OOS / FWD 三段。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .engine import Result


def segment(res: Result, start: pd.Timestamp | None, end: pd.Timestamp | None) -> dict:
    eq = res.equity
    start = start if start is not None else eq.index[0]
    end = end if end is not None else eq.index[-1] + pd.Timedelta(seconds=1)
    seg = eq[(eq.index > start) & (eq.index <= end)]
    # 起點用區段開始前最後一個權益值
    before = eq[eq.index <= start]
    base = before.iloc[-1] if len(before) else (seg.iloc[0] if len(seg) else np.nan)
    trades = [t for t in res.trades if start < t.entry_time <= end]
    if res.open_trade is not None and start < res.open_trade.entry_time <= end:
        open_n = 1
    else:
        open_n = 0

    out = {"bars": int(len(seg)), "trades": len(trades), "open": open_n}
    if len(seg) == 0 or not np.isfinite(base):
        return out | {"return": None}

    curve = pd.concat([pd.Series([base]), seg.reset_index(drop=True)])
    peak = curve.cummax()
    dd = float(((curve - peak) / peak).min())
    ret = float(seg.iloc[-1] / base - 1)
    days = max((seg.index[-1] - start).total_seconds() / 86400, 1e-9)
    daily = seg.resample("1D").last().dropna()
    dr = pd.concat([pd.Series([base]), daily.reset_index(drop=True)]).pct_change().dropna()
    sharpe = float(dr.mean() / dr.std() * math.sqrt(365)) if len(dr) > 5 and dr.std() > 0 else None

    px = pd.Series(res.bars["close"].to_numpy(), index=res.equity.index)
    pseg = px[(px.index > start) & (px.index <= end)]
    pbefore = px[px.index <= start]
    pbase = pbefore.iloc[-1] if len(pbefore) else pseg.iloc[0]

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [-t.pnl for t in trades if t.pnl <= 0]
    Rs = [t.R for t in trades if np.isfinite(t.R)]
    exposure = float((res.position[(res.position.index > start) & (res.position.index <= end)] != 0).mean())

    return out | {
        "return": ret,
        "cagr": float((1 + ret) ** (365 / days) - 1) if days > 30 and ret > -1 else None,
        "max_dd": dd,
        "sharpe": sharpe,
        "buy_hold": float(pseg.iloc[-1] / pbase - 1),
        "win_rate": len(wins) / len(trades) if trades else None,
        "profit_factor": float(sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (None if not wins else float("inf")),
        "avg_R": float(np.mean(Rs)) if Rs else None,
        "best_R": float(np.max(Rs)) if Rs else None,
        "exposure": exposure,
        "days": round(days, 1),
    }


def fmt_pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def fmt_num(x, d: int = 2) -> str:
    if x is None:
        return "—"
    if x == float("inf"):
        return "∞"
    return f"{x:.{d}f}"
