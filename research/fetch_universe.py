"""
跨幣種驗證用的資料抓取（一次性，只用標準函式庫）。

從 OKX 抓多個幣的 1 小時 K 線（現貨 + USDT 永續），2020 年至今：
  data_universe/okx/<COIN>/spot_1h.csv
  data_universe/okx/<COIN>/perp_1h.csv
欄位：ts,open,high,low,close,volume（volume 一律是幣本位數量，ts = 開盤時間 UTC 毫秒，只存已收盤 K 棒）

OI 不抓：OKX 的 OI 歷史只有約 2 個月，回測期間本來就是中性（和 BTC 主資料一樣）。

用法：
  python research/fetch_universe.py                  # 預設幣種
  python research/fetch_universe.py --coins XRP DOGE
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data_universe", "okx")
OKX = "https://www.okx.com"
START_MS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
H1 = 3600 * 1000
UA = {"User-Agent": "btc-quant-lab/1.0 (+public market data, research)"}

# 從來沒在 TradingView 上測過的幣 = 真正的樣本外；BTC/ETH/SOL 只當對照
UNSEEN = ["XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "BCH", "ETC", "ATOM", "FIL", "BNB"]
SEEN = ["BTC", "ETH", "SOL"]


class RateLimiter:
    """history-candles 限制 20 次 / 2 秒，這裡抓 9 次 / 秒留一點餘裕。"""

    def __init__(self, per_sec: float):
        self.gap = 1.0 / per_sec
        self.lock = threading.Lock()
        self.next_t = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next_t)
            self.next_t = t + self.gap
        time.sleep(max(0.0, t - now))


RL = RateLimiter(9)


def get(path: str, params: dict, retries: int = 6) -> dict:
    url = OKX + path + "?" + urllib.parse.urlencode(params)
    err = None
    for k in range(retries):
        RL.wait()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                d = json.loads(r.read().decode())
            if d.get("code") == "50011":  # rate limit
                time.sleep(2 * (k + 1))
                continue
            return d
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (403, 451):
                raise SystemExit(f"OKX 封鎖這台機器：HTTP {e.code}")
            time.sleep(2 * (k + 1))
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"GET 失敗 {url}: {err}")


def fetch_series(coin: str, kind: str) -> tuple[str, str, int, str]:
    inst = f"{coin}-USDT" if kind == "spot" else f"{coin}-USDT-SWAP"
    folder = os.path.join(OUT, coin)
    path = os.path.join(folder, f"{kind}_1h.csv")
    have_last = None
    if os.path.exists(path):
        with open(path, newline="") as fh:
            rows = list(csv.reader(fh))
        if len(rows) > 1:
            have_last = int(rows[-1][0])
    stop_at = have_last if have_last is not None else START_MS - 1

    out: dict[int, list] = {}
    cursor = ""  # 空 = 從最新往回
    while True:
        params = {"instId": inst, "bar": "1H", "limit": 100}
        if cursor:
            params["after"] = cursor
        d = get("/api/v5/market/history-candles", params)
        if d.get("code") != "0":
            return coin, kind, 0, f"錯誤 {d.get('code')} {d.get('msg')}"
        data = d.get("data", [])
        if not data:
            break
        oldest = None
        for x in data:
            ts = int(x[0])
            oldest = ts if oldest is None else min(oldest, ts)
            if len(x) > 8 and x[8] != "1":
                continue  # 未收盤
            if ts <= stop_at:
                continue
            vol = x[5] if kind == "spot" else x[6]  # 永續 x[5] 是張數，x[6] 才是幣
            out[ts] = [ts, x[1], x[2], x[3], x[4], vol]
        if oldest is None or oldest <= stop_at:
            break
        cursor = str(oldest)

    rows = [out[k] for k in sorted(out)]
    if not rows:
        return coin, kind, 0, "沒有新資料" if have_last else "查無資料（可能沒有這個商品）"
    os.makedirs(folder, exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    first = datetime.fromtimestamp(rows[0][0] / 1000, tz=timezone.utc).date()
    return coin, kind, len(rows), f"從 {first} 起"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", nargs="*", default=UNSEEN + SEEN)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    jobs = [(c, k) for c in args.coins for k in ("perp", "spot")]
    bad = 0
    with ThreadPoolExecutor(args.workers) as ex:
        futs = [ex.submit(fetch_series, c, k) for c, k in jobs]
        for f in as_completed(futs):
            try:
                coin, kind, n, msg = f.result()
                print(f"[{coin} {kind}] +{n} 根｜{msg}", flush=True)
            except Exception as e:  # noqa: BLE001
                bad += 1
                print(f"[失敗] {e}", file=sys.stderr, flush=True)
    if bad == len(jobs):
        sys.exit(1)


if __name__ == "__main__":
    main()
