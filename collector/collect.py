"""
公開行情收集器（只用 Python 標準函式庫，不需要 API key）

收集三種資料，按月分檔存成 CSV：
  data/<source>/spot_15m/YYYY-MM.csv   現貨 15 分 K（ts,open,high,low,close,volume）
  data/<source>/perp_15m/YYYY-MM.csv   永續合約 15 分 K（volume 一律換成 BTC）
  data/<source>/oi_1h/YYYY-MM.csv      永續未平倉量 1 小時快照（ts,oi）

資料來源：Bybit 為主、OKX 備援。第一次成功的來源會寫進 data/manifest.json，
之後固定使用同一個來源，避免把兩家交易所的資料混在一起。
被擋（403/451）時直接報錯，GitHub 會寄信通知。

時間一律是 UTC 毫秒，ts = K 棒開盤時間。只存已收盤的 K 棒。

用法：
  python collector/collect.py              # 增量更新（第一次會回補 2020 年至今）
  python collector/collect.py --source okx # 第一次指定來源
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MANIFEST = os.path.join(DATA, "manifest.json")

START_MS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
M15 = 15 * 60 * 1000
H1 = 60 * 60 * 1000

UA = {"User-Agent": "btc-quant-lab/1.0 (+public market data collector)"}


class Blocked(Exception):
    """交易所拒絕這個 IP（地區封鎖或 CDN 封鎖）。"""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_get(url: str, params: dict, retries: int = 4) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                # CDN 封鎖時常回 HTML
                raise Blocked(f"非 JSON 回應（可能被封鎖）：{body[:120]!r}")
        except urllib.error.HTTPError as e:
            if e.code in (403, 451):
                raise Blocked(f"HTTP {e.code} from {url}")
            last_err = e
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET 失敗 {full}: {last_err}")


# ---------------------------------------------------------------------------
# Bybit v5
# ---------------------------------------------------------------------------

BYBIT = "https://api.bybit.com"


def bybit_probe() -> None:
    d = http_get(BYBIT + "/v5/market/time", {})
    if d.get("retCode") != 0:
        raise Blocked(f"Bybit probe: {d}")


def bybit_klines(category: str, start: int, end: int) -> list[list]:
    """回傳 [ts, o, h, l, c, vol_btc]，舊到新。category = spot | linear"""
    d = http_get(BYBIT + "/v5/market/kline", {
        "category": category, "symbol": "BTCUSDT", "interval": "15",
        "start": start, "end": end, "limit": 1000,
    })
    if d.get("retCode") != 0:
        raise RuntimeError(f"Bybit kline error: {d.get('retMsg')}")
    rows = [[int(x[0]), x[1], x[2], x[3], x[4], x[5]] for x in d["result"]["list"]]
    return sorted(rows)


def bybit_oi(start: int, end: int) -> list[list]:
    """回傳 [ts, oi_btc]，舊到新。"""
    out, cursor = [], None
    while True:
        p = {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "1h",
             "startTime": start, "endTime": end, "limit": 200}
        if cursor:
            p["cursor"] = cursor
        d = http_get(BYBIT + "/v5/market/open-interest", p)
        if d.get("retCode") != 0:
            raise RuntimeError(f"Bybit OI error: {d.get('retMsg')}")
        lst = d["result"].get("list", [])
        out += [[int(x["timestamp"]), x["openInterest"]] for x in lst]
        cursor = d["result"].get("nextPageCursor")
        if not cursor or not lst:
            break
        time.sleep(0.12)
    return sorted({r[0]: r for r in out}.values())


# ---------------------------------------------------------------------------
# OKX v5
# ---------------------------------------------------------------------------

OKX = "https://www.okx.com"


def okx_probe() -> None:
    d = http_get(OKX + "/api/v5/public/time", {})
    if d.get("code") != "0":
        raise Blocked(f"OKX probe: {d}")


def okx_klines(kind: str, start: int, end: int) -> list[list]:
    """kind = spot | perp。OKX 每次最多 100 根，用 before/after 夾區間。"""
    inst = "BTC-USDT" if kind == "spot" else "BTC-USDT-SWAP"
    d = http_get(OKX + "/api/v5/market/history-candles", {
        "instId": inst, "bar": "15m", "before": start - 1, "after": end + 1, "limit": 100,
    })
    if d.get("code") != "0":
        raise RuntimeError(f"OKX candle error: {d.get('msg')}")
    rows = []
    for x in d["data"]:
        if len(x) > 8 and x[8] != "1":  # 未收盤
            continue
        # 現貨 vol(x[5]) 是 BTC；永續 x[5] 是張數，x[6] 才是 BTC
        vol = x[5] if kind == "spot" else x[6]
        rows.append([int(x[0]), x[1], x[2], x[3], x[4], vol])
    return sorted(rows)


def okx_oi(start: int, end: int) -> list[list]:
    """OKX 未平倉量歷史（盡力而為，失敗就略過 OI）。"""
    d = http_get(OKX + "/api/v5/rubik/stat/contracts/open-interest-history", {
        "instId": "BTC-USDT-SWAP", "period": "1H", "begin": start, "end": end, "limit": 100,
    })
    if d.get("code") != "0":
        raise RuntimeError(f"OKX OI error: {d.get('msg')}")
    # [ts, oi(張), oiCcy(BTC), oiUsd]
    return sorted([int(x[0]), x[2]] for x in d["data"])


# ---------------------------------------------------------------------------
# 儲存（按月分檔）
# ---------------------------------------------------------------------------

def series_dir(source: str, series: str) -> str:
    return os.path.join(DATA, source, series)


def last_ts(source: str, series: str) -> int | None:
    d = series_dir(source, series)
    if not os.path.isdir(d):
        return None
    files = sorted(f for f in os.listdir(d) if f.endswith(".csv"))
    for f in reversed(files):
        with open(os.path.join(d, f), newline="") as fh:
            rows = list(csv.reader(fh))
        if len(rows) > 1:
            return int(rows[-1][0])
    return None


def append_rows(source: str, series: str, header: list[str], rows: list[list], after: int | None) -> int:
    rows = sorted({r[0]: r for r in rows}.values())
    if after is not None:
        rows = [r for r in rows if r[0] > after]
    if not rows:
        return 0
    d = series_dir(source, series)
    os.makedirs(d, exist_ok=True)
    by_month: dict[str, list] = {}
    for r in rows:
        m = datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc).strftime("%Y-%m")
        by_month.setdefault(m, []).append(r)
    for m, rs in by_month.items():
        path = os.path.join(d, f"{m}.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(header)
            w.writerows(rs)
    return len(rows)


# ---------------------------------------------------------------------------
# 增量抓取
# ---------------------------------------------------------------------------

def sync_klines(source: str, kind: str, now: int) -> int:
    series = f"{kind}_15m"
    prev = last_ts(source, series)
    cursor = (prev + M15) if prev is not None else START_MS
    last_closed_open = (now // M15) * M15 - M15  # 最後一根已收盤 K 棒的開盤時間
    step = (1000 if source == "bybit" else 100) * M15
    total = 0
    while cursor <= last_closed_open:
        end = min(cursor + step - M15, last_closed_open)
        if source == "bybit":
            rows = bybit_klines("spot" if kind == "spot" else "linear", cursor, end)
        else:
            rows = okx_klines(kind, cursor, end)
        rows = sorted(r for r in rows if cursor <= r[0] <= last_closed_open)
        total += append_rows(source, series, ["ts", "open", "high", "low", "close", "volume"], rows, prev)
        if rows:
            prev = rows[-1][0]
        cursor = end + M15
        time.sleep(0.12)
    return total


def sync_oi(source: str, now: int) -> int:
    series = "oi_1h"
    prev = last_ts(source, series)
    cursor = (prev + H1) if prev is not None else START_MS
    last_hour = (now // H1) * H1
    step = (200 if source == "bybit" else 100) * H1
    total = 0
    while cursor <= last_hour:
        end = min(cursor + step - H1, last_hour)
        rows = bybit_oi(cursor, end) if source == "bybit" else okx_oi(cursor, end)
        rows = sorted(r for r in rows if cursor <= r[0] <= last_hour)
        total += append_rows(source, series, ["ts", "oi"], rows, prev)
        if rows:
            prev = rows[-1][0]
        cursor = end + H1
        time.sleep(0.12)
    return total


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def choose_source(forced: str | None) -> str:
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as fh:
            src = json.load(fh)["source"]
        print(f"[source] 使用 manifest 記錄的來源：{src}")
        return src
    candidates = [forced] if forced else ["bybit", "okx"]
    errors = []
    for src in candidates:
        try:
            (bybit_probe if src == "bybit" else okx_probe)()
            os.makedirs(DATA, exist_ok=True)
            with open(MANIFEST, "w") as fh:
                json.dump({"source": src, "symbol": "BTCUSDT",
                           "chosen_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, fh, indent=2)
            print(f"[source] 選用 {src}")
            return src
        except (Blocked, RuntimeError) as e:
            errors.append(f"{src}: {e}")
            print(f"[source] {src} 無法使用：{e}")
    raise SystemExit("所有來源都無法連線（多半是地區封鎖）：\n  " + "\n  ".join(errors) +
                     "\n請改在自己的電腦上執行收集器，見 README『方案 B』。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["bybit", "okx"])
    args = ap.parse_args()

    src = choose_source(args.source)
    now = int(time.time() * 1000)
    failed = False
    for kind in ("perp", "spot"):
        try:
            n = sync_klines(src, kind, now)
            print(f"[{kind}_15m] +{n} 根")
        except Blocked as e:
            raise SystemExit(f"{src} 封鎖了這台機器：{e}\n請改在自己的電腦上執行，見 README『方案 B』。")
        except Exception as e:  # 單一序列失敗不影響其他序列
            failed = True
            print(f"[{kind}_15m] 失敗：{e}", file=sys.stderr)
    try:
        n = sync_oi(src, now)
        print(f"[oi_1h] +{n} 筆")
    except Exception as e:
        print(f"[oi_1h] 略過：{e}（策略會把 OI 當成中性）", file=sys.stderr)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
