"""
冠軍挑戰：原版 / 修改版（冠軍）/ 今日挑戰者，三方比較。

  python -m lab.duel strategies.hso_vN_xxx:Cls --source "網址" --note "弱點 → 改法"

規則：
  - 挑戰者必須「繼承目前的冠軍」，只多改一個點（改良會一路累積）
  - 挑戰者要贏冠軍（lab/config.py 的 VARIANT_GATE），而且 OOS 前半段、後半段都不能輸冠軍超過 2%
  - 贏了：挑戰者成為新的修改版（冠軍），上架做模擬交易；舊冠軍退役（原版永遠保留）
  - 輸了：冠軍保持不變，挑戰者只留在 research/log.csv
  - 還沒有任何修改版贏過原版時，冠軍 = 原版

結果寫到 reports/duel.json（給每日報告用）和 research/log.csv。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import engine, registry
from .causal import check
from .config import IS_END
from .market import Market
from .metrics import fmt_pct, segment
from .trial import LOG, LOG_COLS, variant_gate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUEL = os.path.join(ROOT, "reports", "duel.json")
TPE = timezone(timedelta(hours=8))
HALF_TOL = 0.02


def roles(entries: list[dict]) -> tuple[dict, dict]:
    original = next(e for e in entries if e.get("role") == "original")
    champ = next((e for e in entries if e.get("role") == "champion" and e.get("status") == "active"), original)
    return original, champ


def summarize(res, frozen: pd.Timestamp | None, now: pd.Timestamp) -> dict:
    mid = IS_END + (now - IS_END) / 2
    out = {"IS": segment(res, None, IS_END), "OOS": segment(res, IS_END, None),
           "OOS_1": segment(res, IS_END, mid), "OOS_2": segment(res, mid, None)}
    if frozen is not None:
        out["FWD"] = segment(res, frozen, None)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cls")
    ap.add_argument("--id")
    ap.add_argument("--params", default="{}", help="只放這次新增的參數；冠軍的參數會自動繼承")
    ap.add_argument("--source", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--data")
    ap.add_argument("--dry-run", action="store_true", help="只比較，不寫入 registry / log")
    args = ap.parse_args()

    entries = registry.load()
    original, champ = roles(entries)
    cls = registry.get_class(args.cls)
    champ_cls = registry.get_class(champ["class"])
    if not issubclass(cls, champ_cls):
        raise SystemExit(f"挑戰者 {cls.__name__} 必須繼承目前的修改版 {champ_cls.__name__}（{champ['class']}），"
                         "這樣改良才會累積。")
    sid = args.id or cls.id
    if any(e["id"] == sid for e in entries):
        raise SystemExit(f"id {sid} 已存在，請用新的版本號。")

    params = {**champ.get("params", {}), **json.loads(args.params)}
    tf = champ["timeframe"]
    mkt = Market.load(args.data)
    now = mkt.last_time
    make = lambda: cls(timeframe=tf, **params)

    causal_ok, probs = check(make, mkt)
    r_ch = engine.run(make(), mkt)
    r_champ = engine.run(registry.instantiate(champ), mkt)
    r_orig = r_champ if champ is original else engine.run(registry.instantiate(original), mkt)

    S_ch = summarize(r_ch, None, now)
    S_champ = summarize(r_champ, pd.Timestamp(champ["frozen_at"]), now)
    S_orig = summarize(r_orig, pd.Timestamp(original["frozen_at"]), now)

    reasons = ([] if causal_ok else ["偷看未來：" + "; ".join(probs)])
    reasons += variant_gate(S_ch["IS"], S_ch["OOS"], S_champ["OOS"])
    for half, label in (("OOS_1", "OOS 前半"), ("OOS_2", "OOS 後半")):
        a, b = S_ch[half].get("return"), S_champ[half].get("return")
        if a is None or b is None or a < b - HALF_TOL:
            reasons.append(f"{label}報酬 {fmt_pct(a)} 輸修改版 {fmt_pct(b)}（不一致，可能只是運氣）")
    won = not reasons

    result = {
        "date": datetime.now(TPE).strftime("%Y-%m-%d"),
        "challenger": {"id": sid, "name": getattr(cls, "name", sid), "class": args.cls, "params": params,
                       "source": args.source, "note": args.note, "causal_ok": causal_ok, **S_ch},
        "champion": {"id": champ["id"], "name": champ.get("name"), "is_original": champ is original, **S_champ},
        "original": {"id": original["id"], "name": original.get("name"), **S_orig},
        "won": won, "fail_reasons": reasons,
        "new_champion": sid if won else champ["id"],
    }

    if not args.dry_run:
        if won:
            if champ is not original:
                champ["status"] = "retired"
                champ["role"] = "former_champion"
                champ["retired_at"] = now.isoformat()
            entries.append({
                "id": sid, "name": getattr(cls, "name", sid), "class": args.cls, "timeframe": tf,
                "params": params, "source": args.source, "note": args.note,
                "frozen_at": now.isoformat(), "code_sha": registry.code_sha(args.cls, params, tf),
                "status": "active", "role": "champion", "parent": champ["id"],
            })
            registry.save(entries)

        new = not os.path.exists(LOG)
        with open(LOG, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, LOG_COLS)
            if new:
                w.writeheader()
            ch, co = S_ch, S_champ
            w.writerow({
                "date": result["date"], "id": sid, "class": args.cls, "timeframe": tf,
                "params": json.dumps(params, ensure_ascii=False), "source": args.source, "note": args.note,
                "causal_ok": causal_ok, "is_return": ch["IS"].get("return"), "is_trades": ch["IS"].get("trades"),
                "oos_return": ch["OOS"].get("return"), "oos_trades": ch["OOS"].get("trades"),
                "oos_pf": ch["OOS"].get("profit_factor"), "oos_dd": ch["OOS"].get("max_dd"),
                "oos_sharpe": ch["OOS"].get("sharpe"), "oos_buy_hold": ch["OOS"].get("buy_hold"),
                "oos_avgR": ch["OOS"].get("avg_R"), "baseline": champ["id"],
                "base_oos_pf": co["OOS"].get("profit_factor"), "base_oos_avgR": co["OOS"].get("avg_R"),
                "base_oos_dd": co["OOS"].get("max_dd"), "passed": won, "registered": won,
                "fail_reasons": " | ".join(reasons),
            })
        os.makedirs(os.path.dirname(DUEL), exist_ok=True)
        with open(DUEL, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2, default=str)

    def row(name, S):
        o = S["OOS"]
        return (f"{name:<10} OOS 報酬 {fmt_pct(o.get('return')):>8}  PF {o.get('profit_factor') or 0:5.2f}  "
                f"平均 {o.get('avg_R') or 0:+.2f}R  回撤 {fmt_pct(o.get('max_dd')):>7}  {o.get('trades')} 筆  "
                f"| 前半 {fmt_pct(S['OOS_1'].get('return')):>7} 後半 {fmt_pct(S['OOS_2'].get('return')):>7}")
    print(row("原版", S_orig))
    if champ is not original:
        print(row("修改版", S_champ))
    print(row("今日挑戰", S_ch))
    print(("🏆 挑戰成功，" + sid + " 成為新的修改版") if won else
          ("❌ 挑戰失敗，修改版維持 " + champ["id"] + "\n  - " + "\n  - ".join(reasons)))


if __name__ == "__main__":
    main()
