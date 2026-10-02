"""
測試一個新策略，決定能不能上架（凍結後開始模擬交易）。

  python -m lab.trial strategies.my_idea:MyIdea --tf 1h --params '{}' \
      --source "https://..." --note "一句話描述" [--register]

流程：偷看未來檢查 → IS / OOS 績效 → 上架門檻 → 寫入 research/log.csv
--register 且通過門檻 → 加進 strategies/registry.json，凍結時間 = 最新資料時間
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone

from . import engine, registry
from .causal import check
from .config import GATE, IS_END, VARIANT_GATE
from .market import Market
from .metrics import fmt_num, fmt_pct, segment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "research", "log.csv")
LOG_COLS = ["date", "id", "class", "timeframe", "params", "source", "note",
            "causal_ok", "is_return", "is_trades", "oos_return", "oos_trades", "oos_pf", "oos_dd",
            "oos_sharpe", "oos_buy_hold", "oos_avgR", "baseline", "base_oos_pf", "base_oos_avgR", "base_oos_dd",
            "passed", "registered", "fail_reasons"]


def gate(is_s: dict, oos: dict) -> list[str]:
    r = []
    if oos.get("trades", 0) < GATE["min_oos_trades"]:
        r.append(f"OOS 只有 {oos.get('trades', 0)} 筆交易（需要 ≥ {GATE['min_oos_trades']}）")
    pf = oos.get("profit_factor")
    if pf is None or pf < GATE["min_oos_pf"]:
        r.append(f"OOS Profit Factor {fmt_num(pf)}（需要 ≥ {GATE['min_oos_pf']}）")
    if (is_s.get("return") or -1) <= GATE["min_is_return"]:
        r.append(f"IS 報酬 {fmt_pct(is_s.get('return'))} 不為正")
    if (oos.get("return") or -1) <= GATE["min_oos_return"]:
        r.append(f"OOS 報酬 {fmt_pct(oos.get('return'))} 不為正")
    if oos.get("max_dd") is not None and -oos["max_dd"] > GATE["max_oos_dd"]:
        r.append(f"OOS 最大回撤 {fmt_pct(oos['max_dd'])} 超過 {GATE['max_oos_dd']:.0%}")
    return r


def variant_gate(is_s: dict, oos: dict, base_oos: dict) -> list[str]:
    """改良版要在 OOS 贏過原版。"""
    g, r = VARIANT_GATE, []
    if oos.get("trades", 0) < g["min_oos_trades"]:
        r.append(f"OOS 只有 {oos.get('trades', 0)} 筆交易（需要 ≥ {g['min_oos_trades']}）")
    pf, bpf = oos.get("profit_factor"), base_oos.get("profit_factor") or 0
    if pf is None or pf < bpf + g["min_pf_gain"]:
        r.append(f"OOS PF {fmt_num(pf)} 沒有比原版 {fmt_num(bpf)} 高 {g['min_pf_gain']} 以上")
    ar, bar = oos.get("avg_R"), base_oos.get("avg_R") or 0
    if ar is None or ar < bar + g["min_avgR_gain"]:
        r.append(f"OOS 平均 {fmt_num(ar)}R 不如原版 {fmt_num(bar)}R")
    dd, bdd = oos.get("max_dd") or 0, base_oos.get("max_dd") or 0
    if dd < bdd - g["max_dd_worse"]:
        r.append(f"OOS 最大回撤 {fmt_pct(dd)} 比原版 {fmt_pct(bdd)} 差太多")
    ipf = is_s.get("profit_factor")
    if ipf is None or ipf < g["min_is_pf"]:
        r.append(f"IS PF {fmt_num(ipf)} < {g['min_is_pf']}")
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cls")
    ap.add_argument("--id")
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--source", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--force", action="store_true", help="沒過門檻也上架（只給使用者手動指定的策略用）")
    ap.add_argument("--baseline", help="改良版：和這個已上架策略比較（例如 hso_v1），用改良版門檻")
    ap.add_argument("--data")
    args = ap.parse_args()

    cls = registry.get_class(args.cls)
    params = json.loads(args.params)
    sid = args.id or cls.id
    entries = registry.load()
    if any(e["id"] == sid for e in entries):
        raise SystemExit(f"id {sid} 已存在。凍結的策略不能改，請用新的 id（例如 {sid}_v2）。")

    mkt = Market.load(args.data)
    make = lambda: cls(timeframe=args.tf, **params)

    causal_ok, probs = check(make, mkt)
    res = engine.run(make(), mkt)
    is_s = segment(res, None, IS_END)
    oos = segment(res, IS_END, None)
    base_oos = {}
    if args.baseline:
        be = next(e for e in entries if e["id"] == args.baseline)
        base_oos = segment(engine.run(registry.instantiate(be), mkt), IS_END, None)
        checks = variant_gate(is_s, oos, base_oos)
    else:
        checks = gate(is_s, oos)
    reasons = ([] if causal_ok else ["偷看未來：" + "; ".join(probs)]) + checks
    passed = not reasons
    do_reg = args.register and (passed or args.force)

    if do_reg:
        entries.append({
            "id": sid, "name": getattr(cls, "name", sid), "class": args.cls, "timeframe": args.tf,
            "params": params, "source": args.source or getattr(cls, "source", ""), "note": args.note,
            "frozen_at": mkt.last_time.isoformat(),
            "code_sha": registry.code_sha(args.cls, params, args.tf),
            "status": "active",
            **({"parent": args.baseline} if args.baseline else {}),
        })
        registry.save(entries)

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, LOG_COLS)
        if new:
            w.writeheader()
        w.writerow({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "id": sid, "class": args.cls,
            "timeframe": args.tf, "params": json.dumps(params, ensure_ascii=False),
            "source": args.source, "note": args.note, "causal_ok": causal_ok,
            "is_return": is_s.get("return"), "is_trades": is_s.get("trades"),
            "oos_return": oos.get("return"), "oos_trades": oos.get("trades"),
            "oos_pf": oos.get("profit_factor"), "oos_dd": oos.get("max_dd"),
            "oos_sharpe": oos.get("sharpe"), "oos_buy_hold": oos.get("buy_hold"), "oos_avgR": oos.get("avg_R"),
            "baseline": args.baseline or "", "base_oos_pf": base_oos.get("profit_factor"),
            "base_oos_avgR": base_oos.get("avg_R"), "base_oos_dd": base_oos.get("max_dd"),
            "passed": passed, "registered": do_reg, "fail_reasons": " | ".join(reasons),
        })

    print(json.dumps({"id": sid, "causal_ok": causal_ok, "IS": is_s, "OOS": oos, "baseline_OOS": base_oos,
                      "passed": passed, "registered": do_reg, "fail_reasons": reasons},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
