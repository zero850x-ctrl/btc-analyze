#!/usr/bin/env python3
"""fetch_btc_bars.py — 用 Binance public API 拎 BTC K 線 (免 credentials).

yfinance 30m 只 60 日 (17 個 setup, 唔夠做統計)。
Binance public /api/v3/klines 可以拎多年, 而且同 live 系統用同一數據源。

用法:
  python3 fetch_btc_bars.py 30m [days]   → ~/.hermes/reports/btc_bars_30m.csv
  python3 fetch_btc_bars.py 1h  [days]
"""
import csv
import json
import os
import sys
import time
import urllib.request

BASE = "https://api.binance.com/api/v3/klines"
OUT_DIR = os.path.expanduser("~/.hermes/reports")


def fetch(interval="30m", days=540, symbol="BTCUSDT"):
    """分頁拎 K 線. Binance 每次上限 1000 根."""
    ms_per_bar = {"30m": 30 * 60_000, "1h": 3_600_000, "15m": 15 * 60_000}[interval]
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows, cur = [], start
    while cur < end:
        url = (f"{BASE}?symbol={symbol}&interval={interval}"
               f"&startTime={cur}&limit=1000")
        with urllib.request.urlopen(url, timeout=30) as r:
            batch = json.load(r)
        if not batch:
            break
        for k in batch:
            # [openTime, open, high, low, close, volume, closeTime, ...]
            rows.append([k[0], float(k[1]), float(k[2]), float(k[3]),
                         float(k[4]), float(k[5])])
        nxt = batch[-1][0] + ms_per_bar
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.15)          # 唔好撞 rate limit
    # 去重 + 排序
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: x[0]):
        if r[0] in seen:
            continue
        seen.add(r[0])
        out.append(r)
    return out


def save(rows, interval):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, f"btc_bars_{interval}.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["datetime", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.gmtime(r[0] / 1000))] + r[1:])
    return p


if __name__ == "__main__":
    iv = sys.argv[1] if len(sys.argv) > 1 else "30m"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 540
    print(f"拎 BTCUSDT {iv} {days} 日...")
    rows = fetch(iv, days)
    if not rows:
        raise SystemExit("❌ 拎唔到數據")
    p = save(rows, iv)
    t0 = time.strftime("%Y-%m-%d", time.gmtime(rows[0][0] / 1000))
    t1 = time.strftime("%Y-%m-%d", time.gmtime(rows[-1][0] / 1000))
    print(f"✅ {len(rows)} 根  {t0} → {t1}")
    print(f"   {p}")
