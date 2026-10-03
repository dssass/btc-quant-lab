"""
每日評估：跑所有上架策略，產出排行榜與模擬交易狀態。

  python -m lab.evaluate            → reports/latest.json、reports/latest.md、reports/YYYY-MM-DD.md

三段績效：
  IS  樣本內      資料起點 ~ IS_END
  OOS 樣本外      IS_END ~ 凍結日
  FWD 前測/模擬   凍結日之後（策略寫好時還不存在的資料，最重要）
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import engine, registry
from .config import IS_END
from .market import Market
from .metrics import fmt_num, fmt_pct, segment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
TPE = timezone(timedelta(hours=8))
ROLE = {"original": "原版", "champion": "修改版", "benchmark": "對照組"}
ORDER = {"原版": 0, "修改版": 1, "對照組": 2}


def _t(ts) -> str:
    return pd.Timestamp(ts).tz_convert(TPE).strftime("%Y-%m-%d %H:%M") if ts is not None else "—"


def evaluate(data_dir: str | None = None) -> dict:
    mkt = Market.load(data_dir)
    now = mkt.last_time
    out = {
        "generated_at": datetime.now(TPE).isoformat(timespec="minutes"),
        "data": {
            "source": mkt.source,
            "last_bar_close": _t(now),
            "hours_since_last_bar": round((pd.Timestamp.now(tz="UTC") - now).total_seconds() / 3600, 1),
            "oi_rows": int(len(mkt.oi1h)), "spot_rows": int(len(mkt.spot15)), "perp_rows": int(len(mkt.perp15)),
        },
        "price": float(mkt.perp15["close"].iloc[-1]),
        "strategies": [],
    }
    for e in registry.load():
        if e.get("status") != "active":
            continue
        row = {"id": e["id"], "name": e.get("name", e["id"]), "timeframe": e["timeframe"],
               "role": ROLE.get(e.get("role", ""), e.get("role", "")),
               "frozen_at": _t(e["frozen_at"]), "source": e.get("source", "")}
        try:
            row["code_ok"] = registry.verify(e)
            res = engine.run(registry.instantiate(e), mkt)
            frozen = pd.Timestamp(e["frozen_at"])
            row["IS"] = segment(res, None, IS_END)
            row["OOS"] = segment(res, IS_END, min(frozen, now))
            row["FWD"] = segment(res, frozen, None)
            ot = res.open_trade
            row["position"] = {
                "side": {1: "多", -1: "空", 0: "空手"}[ot.side if ot else 0],
                "entry_time": _t(ot.entry_time) if ot else None,
                "entry_price": round(float(ot.entry_price), 1) if ot else None,
                "stop": round(float(res.stop), 1) if ot and res.stop == res.stop and res.stop is not None else None,
                "unrealized": float(res.equity.iloc[-1] / (ot._equity_at_entry) - 1) if ot else None,
            }
            row["status"] = res.last_status
            fwd = [t for t in res.trades if t.entry_time > frozen]
            row["fwd_trades"] = [{
                "side": "多" if t.side > 0 else "空", "entry": _t(t.entry_time), "entry_price": round(t.entry_price, 1),
                "exit": _t(t.exit_time), "exit_price": round(t.exit_price, 1), "reason": t.exit_reason,
                "ret": t.ret, "R": None if t.R != t.R else round(t.R, 2),
            } for t in fwd[-20:]]
            day_ago = now - pd.Timedelta(hours=24)
            row["new_today"] = [x for x in row["fwd_trades"] if x["exit"] >= _t(day_ago)]
            row["equity_fwd"] = [round(float(v), 2) for v in
                                 res.equity[res.equity.index > frozen].resample("1D").last().dropna()]
        except Exception as ex:  # 一個策略壞掉不影響其他
            row["error"] = f"{type(ex).__name__}: {ex}"
        out["strategies"].append(row)
    out["strategies"].sort(key=lambda s: ORDER.get(s.get("role"), 9))
    if not any(s.get("role") == "修改版" for s in out["strategies"]):
        out["note"] = "目前還沒有修改版贏過原版，修改版 = 原版"
    duel = os.path.join(REPORTS, "duel.json")
    if os.path.exists(duel):
        with open(duel, encoding="utf-8") as fh:
            out["latest_duel"] = json.load(fh)
    return out


def to_markdown(r: dict) -> str:
    L = [f"# 策略模擬交易日報（{r['generated_at'][:10]}）", "",
         f"資料來源 **{r['data']['source']}**｜最新 K 棒收盤 {r['data']['last_bar_close']}（台北）"
         f"｜距今 {r['data']['hours_since_last_bar']} 小時｜BTC {r['price']:,.0f}", ""]
    if r["data"]["hours_since_last_bar"] > 3:
        L += ["> ⚠️ 資料超過 3 小時沒更新，請檢查 GitHub Actions。", ""]
    L += ["## 原版 vs 修改版", "",
          "| 角色 | 策略 | 前測報酬 | 前測交易 | OOS 報酬 | OOS PF | OOS 平均R | 目前持倉 |",
          "|---|---|---|---|---|---|---|---|"]
    rows = [s for s in r["strategies"] if "error" not in s]
    for s in rows:
        p = s["position"]
        pos = p["side"] if p["side"] == "空手" else f"{p['side']} @{p['entry_price']:,.0f}"
        L.append(f"| {s.get('role', '')} | {s['name']} | {fmt_pct(s['FWD'].get('return'))} | {s['FWD'].get('trades', 0)} "
                 f"| {fmt_pct(s['OOS'].get('return'))} | {fmt_num(s['OOS'].get('profit_factor'))} "
                 f"| {fmt_num(s['OOS'].get('avg_R'))} | {pos} |")
    L.append("")
    if r.get("note"):
        L += [f"> {r['note']}", ""]
    d = r.get("latest_duel")
    if d:
        c, k = d["challenger"], d["champion"]
        L += [f"## 最近一次挑戰（{d['date']}）", "",
              f"- 挑戰者：{c['name']}（`{c['id']}`）",
              f"- 點子：{c.get('note', '')}｜出處：{c.get('source', '')}",
              f"- 挑戰者 OOS：{fmt_pct(c['OOS'].get('return'))}｜PF {fmt_num(c['OOS'].get('profit_factor'))}"
              f"｜平均 {fmt_num(c['OOS'].get('avg_R'))}R｜回撤 {fmt_pct(c['OOS'].get('max_dd'))}｜{c['OOS'].get('trades')} 筆",
              f"- 當時的修改版（`{k['id']}`）OOS：{fmt_pct(k['OOS'].get('return'))}｜PF {fmt_num(k['OOS'].get('profit_factor'))}"
              f"｜平均 {fmt_num(k['OOS'].get('avg_R'))}R｜回撤 {fmt_pct(k['OOS'].get('max_dd'))}",
              f"- 結果：{'🏆 勝出，成為新的修改版' if d['won'] else '❌ 未勝出，修改版不變'}"]
        L += [f"  - {x}" for x in d.get("fail_reasons", [])]
        L.append("")
    for s in r["strategies"]:
        L += [f"## {s.get('role', '')}：{s['name']}（`{s['id']}`）", ""]
        if "error" in s:
            L += [f"❌ 執行失敗：{s['error']}", ""]
            continue
        if not s["code_ok"]:
            L += ["> ⚠️ 凍結後程式碼被修改過，前測結果不再可信。", ""]
        p = s["position"]
        L.append(f"- 凍結時間：{s['frozen_at']}｜出處：{s['source']}")
        L.append(f"- 狀態：{s['status'] or '—'}")
        if p["side"] != "空手":
            L.append(f"- 持倉：{p['side']} @{p['entry_price']:,.1f}，停損 {p['stop'] if p['stop'] else '—'}，"
                     f"浮動 {fmt_pct(p['unrealized'])}（{p['entry_time']} 進場）")
        for name, k in (("樣本內 IS", "IS"), ("樣本外 OOS", "OOS"), ("前測 FWD", "FWD")):
            seg = s[k]
            L.append(f"- {name}：報酬 {fmt_pct(seg.get('return'))}（買進持有 {fmt_pct(seg.get('buy_hold'))}）"
                     f"｜{seg.get('trades', 0)} 筆｜PF {fmt_num(seg.get('profit_factor'))}"
                     f"｜平均 {fmt_num(seg.get('avg_R'))}R｜最大回撤 {fmt_pct(seg.get('max_dd'))}")
        if s["new_today"]:
            L += ["", "過去 24 小時平倉："]
            for t in s["new_today"]:
                L.append(f"  - {t['side']} {t['entry_price']:,.0f} → {t['exit_price']:,.0f}（{t['reason']}）{fmt_pct(t['ret'])}")
        L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    args = ap.parse_args()
    r = evaluate(args.data)
    os.makedirs(REPORTS, exist_ok=True)
    md = to_markdown(r)
    with open(os.path.join(REPORTS, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(r, fh, ensure_ascii=False, indent=2, default=str)
    for name in ("latest.md", f"{r['generated_at'][:10]}.md"):
        with open(os.path.join(REPORTS, name), "w", encoding="utf-8") as fh:
            fh.write(md)
    print(md)


if __name__ == "__main__":
    main()
