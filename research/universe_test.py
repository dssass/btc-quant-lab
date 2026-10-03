"""
跨幣種樣本外驗證：HSO 的每個條件到底有沒有預測力？

原則（2026-10-03 和使用者約定，跑之前就寫死，跑完不改）：
  - 參數全部凍結：Hades 120/200/41/72、Donchian 20、ATR 14、4H、只做多。不調任何數字。
  - 只測「開 / 關」、沒有數字可調的條件：
        A 基準        原版 HSO（訂單流門檻 1 分）
        B 訂單流關    綠區第一根就進場，不等訂單流
        C 結構失敗    虧損中出現新的擺動高點（Sell）就平多
  - 樣本外 = 從來沒在 TradingView 上測過的幣（UNSEEN）。BTC/ETH/SOL 只當對照，不參與判斷。
  - 採用標準（三個都要成立）：
        1. 合併所有樣本外幣的交易，淨期望值（R）比基準高
        2. 該版本自己的合併期望值 t 值 > 2
        3. 至少 70% 的幣期望值比基準高
  - 另外看「訊號預測力」：每個條件成立時，接下來的報酬是否顯著比不成立時好。

R = (出場價 − 進場價) ÷ (進場價 − 初始停損)；淨 R 再扣掉來回手續費 + 滑價。

用法：python research/universe_test.py [--data data_universe/okx] [--out reports]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lab import engine  # noqa: E402
from lab.config import FEE, SLIPPAGE, TIMEFRAMES  # noqa: E402
from strategies.base import Ctx, Decision  # noqa: E402
from strategies.hso import HSO  # noqa: E402

UNSEEN = ["XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "BCH", "ETC", "ATOM", "FIL", "BNB"]
SEEN = ["BTC", "ETH", "SOL"]
SPLIT = pd.Timestamp("2024-07-01", tz="UTC")
COST = 2 * (FEE + SLIPPAGE)  # 來回成本（佔名目）
H1 = pd.Timedelta("1h")


# ---------------------------------------------------------------------------
# 資料：1 小時現貨 + 永續，介面和 lab.market.Market 相同（HSO 只用到這三個方法）
# ---------------------------------------------------------------------------

def _read(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop_duplicates("ts").set_index("ts").sort_index()[["open", "high", "low", "close", "volume"]].astype(float)


class HourlyMarket:
    def __init__(self, folder: str):
        self.perp1h = _read(os.path.join(folder, "perp_1h.csv"))
        sp = os.path.join(folder, "spot_1h.csv")
        self.spot1h = _read(sp) if os.path.exists(sp) else self.perp1h.iloc[0:0]
        self._cache: dict = {}

    def bars(self, tf: str = "4h", kind: str = "perp") -> pd.DataFrame:
        key = (tf, kind)
        if key in self._cache:
            return self._cache[key]
        src = self.perp1h if kind == "perp" else self.spot1h
        if src.empty or tf == "1h":
            out = src.copy()
        else:
            rule = TIMEFRAMES[tf]
            out = src.resample(rule, label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(subset=["close"])
            out = out[out.index + pd.Timedelta(rule) <= src.index[-1] + H1]  # 只留已收盤
        self._cache[key] = out
        return out

    def ltf_delta(self, tf: str, ltf: str, kind: str = "perp") -> pd.Series:
        low = self.bars(ltf, kind)
        if low.empty:
            return pd.Series(dtype=float)
        d = np.sign(low["close"] - low["open"]) * low["volume"]
        if ltf == tf:
            return d
        return d.resample(TIMEFRAMES[tf], label="left", closed="left").sum(min_count=1)

    def oi_close(self, tf: str, index: pd.DatetimeIndex) -> pd.Series:
        return pd.Series(np.nan, index=index)  # OKX 沒有長期 OI 歷史 → 中性（和 BTC 主資料一致）


# ---------------------------------------------------------------------------
# 變體 C：結構失敗出場（和 Pine 的 useFailX 一致）
# ---------------------------------------------------------------------------

class HSOFailX(HSO):
    id = "hso_failx"

    def on_bar(self, i: int, ctx: Ctx) -> Decision | None:
        d = super().on_bar(i, ctx)
        a, pos = self.a, ctx.position
        close = a["close"][i]
        if d is not None and not d.exit:
            if pos > 0 and a["newSwHi"][i] and close < ctx.entry_price and a["zone"][i] == 1:
                d.exit, d.note, d.stop = True, "結構失敗", None
            elif pos < 0 and a["newSwLo"][i] and close > ctx.entry_price and a["zone"][i] == -1:
                d.exit, d.note, d.stop = True, "結構失敗", None
        return d


VARIANTS = {
    "A 基準": (HSO, {}),
    "B 訂單流關": (HSO, {"MIN_SCORE": -99, "CROWD_Z": 1e9}),
    "C 結構失敗": (HSOFailX, {}),
}


# ---------------------------------------------------------------------------
# 統計
# ---------------------------------------------------------------------------

def trades_df(res, coin: str, variant: str) -> pd.DataFrame:
    rows = []
    for t in res.trades:
        risk = abs(t.entry_price - t.init_stop) / t.entry_price if t.init_stop else np.nan
        rows.append({
            "coin": coin, "variant": variant, "entry": t.entry_time, "exit": t.exit_time,
            "reason": t.exit_reason, "R": t.R, "risk_pct": risk * 100,
            "netR": t.R - COST / risk if risk and np.isfinite(risk) else np.nan,
            "ret_pct": t.ret * 100,
        })
    return pd.DataFrame(rows)


def tstat(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def summarize(x: pd.Series) -> dict:
    v = x.dropna().to_numpy()
    if len(v) == 0:
        return {"n": 0}
    w = v[v > 0]
    s = np.sort(v)
    return {
        "n": int(len(v)), "expR": float(v.mean()), "t": tstat(v), "median": float(np.median(v)),
        "win": float(len(w) / len(v)), "pfR": float(w.sum() / -v[v <= 0].sum()) if (v <= 0).any() and v[v <= 0].sum() < 0 else float("inf"),
        "exp_ex_top1": float(s[:-1].mean()) if len(s) > 1 else float("nan"),
        "exp_ex_top3": float(s[:-3].mean()) if len(s) > 3 else float("nan"),
    }


def welch(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return float("nan")
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else float("nan")


# ---------------------------------------------------------------------------
# 訊號預測力：條件成立時，接下來 N 根的報酬（不重疊取樣）
# ---------------------------------------------------------------------------

def predictive(markets: dict[str, HourlyMarket], horizons=(6, 30)) -> pd.DataFrame:
    rows = []
    for coin, m in markets.items():
        s = HSO(timeframe="4h", mode="Long only")
        df = s.prepare(m)
        c = df["close"]
        zone = df["zone"]
        flow = df["flowOkLong"].astype(bool)
        first_green = (zone == 1) & (zone.shift(1) != 1)
        for h in horizons:
            fwd = np.log(c.shift(-h) / c) * 100
            # 不重疊：每 h 根取一個樣本
            idx = np.arange(s.warmup, len(df) - h, h)
            sub = pd.DataFrame({"fwd": fwd.iloc[idx].to_numpy(), "zone": zone.iloc[idx].to_numpy(),
                                "flow": flow.iloc[idx].to_numpy()})
            for name, mask in [("全部 K 棒", np.ones(len(sub), bool)),
                               ("綠區", sub["zone"] == 1), ("黃區", sub["zone"] == 0), ("紅區", sub["zone"] == -1),
                               ("綠區＋訂單流 OK", (sub["zone"] == 1) & sub["flow"]),
                               ("綠區＋訂單流不 OK", (sub["zone"] == 1) & ~sub["flow"])]:
                v = sub.loc[mask, "fwd"].dropna().to_numpy()
                rows.append({"coin": coin, "h": h, "cond": name, "n": len(v), "sum": v.sum(), "sumsq": (v ** 2).sum()})
            # 綠區開始那根（進場時機）的報酬，用全部事件（事件本身很稀疏，不會重疊太多）
            v = fwd[first_green].dropna().to_numpy()
            rows.append({"coin": coin, "h": h, "cond": "綠區第一根", "n": len(v), "sum": v.sum(), "sumsq": (v ** 2).sum()})
    return pd.DataFrame(rows)


def pool_pred(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["h", "cond"])[["n", "sum", "sumsq"]].sum().reset_index()
    g["mean"] = g["sum"] / g["n"]
    g["sd"] = np.sqrt(g["sumsq"] / g["n"] - g["mean"] ** 2)
    g["t"] = g["mean"] / (g["sd"] / np.sqrt(g["n"]))
    return g[["h", "cond", "n", "mean", "t"]]


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data_universe", "okx"))
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"))
    args = ap.parse_args()

    markets: dict[str, HourlyMarket] = {}
    for folder in sorted(glob.glob(os.path.join(args.data, "*"))):
        coin = os.path.basename(folder)
        if os.path.exists(os.path.join(folder, "perp_1h.csv")):
            markets[coin] = HourlyMarket(folder)

    all_tr = []
    coin_info = []
    for coin, m in markets.items():
        b = m.bars("4h", "perp")
        coin_info.append({"coin": coin, "seen": coin in SEEN, "start": str(b.index[0].date()), "end": str(b.index[-1].date()),
                          "bars": len(b), "spot": not m.spot1h.empty,
                          "buy_hold_pct": float((b["close"].iloc[-1] / b["close"].iloc[220] - 1) * 100) if len(b) > 220 else None})
        for vname, (cls, params) in VARIANTS.items():
            res = engine.run(cls(timeframe="4h", mode="Long only", **params), m)
            all_tr.append(trades_df(res, coin, vname))
        print(f"[{coin}] {len(b)} 根 4H，{coin_info[-1]['start']} ~ {coin_info[-1]['end']}", flush=True)
    tr = pd.concat(all_tr, ignore_index=True)
    tr["seen"] = tr["coin"].isin(SEEN)
    tr["period"] = np.where(tr["entry"] < SPLIT, "前段", "後段")

    oos = tr[~tr["seen"]]
    coins_oos = sorted(oos["coin"].unique())
    out: dict = {"coins": coin_info, "variants": {}, "per_coin": {}, "decision": {}}

    base = oos[oos["variant"] == "A 基準"]
    base_coin = base.groupby("coin")["netR"].mean()
    for v in VARIANTS:
        sub = oos[oos["variant"] == v]
        out["variants"][v] = {
            "pooled": summarize(sub["netR"]),
            "pooled_grossR": summarize(sub["R"]),
            "前段": summarize(sub[sub["period"] == "前段"]["netR"]),
            "後段": summarize(sub[sub["period"] == "後段"]["netR"]),
            "seen": summarize(tr[(tr["seen"]) & (tr["variant"] == v)]["netR"]),
        }
        pc = sub.groupby("coin")["netR"].agg(["count", "mean"])
        out["per_coin"][v] = {c: {"n": int(pc.loc[c, "count"]), "expR": float(pc.loc[c, "mean"])} for c in pc.index}
        if v != "A 基準":
            better = sum(1 for c in coins_oos if c in pc.index and c in base_coin.index and pc.loc[c, "mean"] > base_coin[c])
            pooled_v, pooled_b = out["variants"][v]["pooled"], out["variants"]["A 基準"]["pooled"]
            diff_t = welch(sub["netR"].to_numpy(), base["netR"].to_numpy())
            # C 和 A 進場相同 → 用同一筆進場配對比較，檢定力更高
            paired = None
            if v == "C 結構失敗":
                m_ = sub.merge(base, on=["coin", "entry"], suffixes=("_v", "_b"))
                dd = (m_["netR_v"] - m_["netR_b"]).to_numpy()
                paired = {"n": int(len(dd)), "mean_diff": float(dd.mean()), "t": tstat(dd), "changed": int((np.abs(dd) > 1e-9).sum())}
            ok1 = pooled_v["expR"] > pooled_b["expR"]
            ok2 = pooled_v["t"] > 2
            ok3 = better >= math.ceil(0.7 * len(coins_oos))
            out["decision"][v] = {"更高": bool(ok1), "t>2": bool(ok2), "變好幣數": f"{better}/{len(coins_oos)}", "≥70%": bool(ok3),
                                  "差異 t（Welch）": diff_t, "配對": paired, "採用": bool(ok1 and ok2 and ok3)}

    pred = predictive({c: m for c, m in markets.items() if c not in SEEN})
    out["predictive"] = pool_pred(pred).to_dict("records")

    os.makedirs(args.out, exist_ok=True)
    tr.to_csv(os.path.join(args.out, "universe_trades.csv"), index=False)
    with open(os.path.join(args.out, "universe_result.json"), "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=str)

    # 簡易文字輸出
    print("\n=== 樣本外幣種（合併，淨 R）===")
    for v, d in out["variants"].items():
        p = d["pooled"]
        print(f"{v:8s} n={p['n']:4d} 期望值={p['expR']:+.3f}R t={p['t']:.2f} 中位數={p['median']:+.2f} 勝率={p['win']:.0%} "
              f"去最大1筆={p['exp_ex_top1']:+.3f} 去最大3筆={p['exp_ex_top3']:+.3f}｜前段 {d['前段'].get('expR', float('nan')):+.3f}"
              f"(n={d['前段']['n']}) 後段 {d['後段'].get('expR', float('nan')):+.3f}(n={d['後段']['n']})｜BTC/ETH/SOL {d['seen'].get('expR', float('nan')):+.3f}(n={d['seen']['n']})")
    for v, d in out["decision"].items():
        print(v, d)
    print("\n=== 訊號預測力（樣本外幣種合併，未來報酬 %，對數）===")
    print(pool_pred(pred).to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
