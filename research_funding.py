#!/usr/bin/env python3
"""research_funding.py — Funding rate 作為市場情緒信號 (只研究, 唔做 futures).

Binance 永續 funding rate (每 8h) 係公開數據。研究用途:
  1. funding 極高 = 槓桿多頭擁擠 → 之後價格? (反向指標假設)
  2. funding 極低/負 = 空頭擁擠 → 之後價格?
  3. 用統一 exit harness 測「funding 極值反向入場」策略

⚠️ 只測試 spot 可行嘅用法。Funding 套利 (現貨+永續對沖) 涉及 futures,
   唔喺本系統範圍 (香港地域 + 只做 spot testnet)。
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from research_premium import load_daily
from research_harness import simulate, stats, fmt

CACHE = os.path.expanduser("~/.hermes/reports/btc_funding_history.json")


def fetch_funding(days=1200):
    """Binance fapi fundingRate — 由舊到新分頁 (API 每頁最多 1000, 實測回 500)."""
    out = []
    start = int((time.time() - days * 86400) * 1000)
    for p in range(40):
        url = (f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT"
               f"&limit=1000&startTime={start}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
        if not d:
            break
        out += d
        last = d[-1]["fundingTime"]
        if last <= start:
            break
        start = last + 1
        if p % 5 == 0:
            print(f"  page {p+1}: 共 {len(out)} 筆 ...")
        if len(out) >= days * 3 + 10:
            break
        time.sleep(0.2)
    return out


def load_funding():
    if os.path.exists(CACHE):
        raw = json.load(open(CACHE))
    else:
        print("拎 funding history...")
        raw = fetch_funding()
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(raw, open(CACHE, "w"))
    df = pd.DataFrame([{"ts": x["fundingTime"], "rate": float(x["fundingRate"]),
                        "mark": float(x.get("markPrice") or 0)} for x in raw])
    df["dt"] = pd.to_datetime(df["ts"], unit="ms").dt.tz_localize(None).dt.floor("h")
    df["dt"] = df["dt"].astype("datetime64[ns]")
    return df.drop_duplicates("dt").sort_values("dt").reset_index(drop=True)


def main():
    f = load_funding()
    print(f"\nfunding 數據: {len(f)} 筆 {f['dt'].iloc[0]} → {f['dt'].iloc[-1]}")
    r = f["rate"] * 100
    print(f"8h funding%: mean={r.mean():+.4f} median={r.median():+.4f} "
          f"std={r.std():.4f} min={r.min():+.4f} max={r.max():+.4f}")
    print(f"正 funding 比例: {(r > 0).mean() * 100:.1f}%  |  年化 mean: {r.mean() * 3 * 365:+.1f}%")

    # 對齊日線價格
    d = load_daily(6)
    d["dt"] = pd.to_datetime(d["date"]).astype("datetime64[ns]")
    f8 = f.set_index("dt").resample("1D").agg({"rate": ["mean", "max", "min", "sum"]})
    f8.columns = ["f_mean", "f_max", "f_min", "f_sum"]
    f8 = f8.reset_index().rename(columns={"dt": "dt"})
    m = pd.merge_asof(d.sort_values("dt"), f8.sort_values("dt"), on="dt", direction="nearest")
    m = m.dropna(subset=["f_mean"]).reset_index(drop=True)
    print(f"\n對齊後: {len(m)} 日")

    # ── 1. funding vs 未來回報 (IC) ──
    def spearman(a, b):
        """冇 scipy: rank 後 Pearson."""
        msk = a.notna() & b.notna()
        return a[msk].rank().corr(b[msk].rank())

    print("\n=== funding 同未來回報嘅關係 (Spearman 相關) ===")
    for lag in (1, 3, 7, 14):
        fut = m["close"].shift(-lag) / m["close"] - 1
        ic = spearman(m["f_mean"], fut)
        print(f"  未來 {lag:>2} 日回報: IC = {ic:+.3f}")

    # ── 2. 分位數分組 ──
    print("\n=== 按 funding 分位數分組 (未來 7 日回報) ===")
    m["q"] = pd.qcut(m["f_mean"].rank(method="first"), 5,
                     labels=["Q1 最低", "Q2", "Q3", "Q4", "Q5 最高"])
    fut7 = (m["close"].shift(-7) / m["close"] - 1) * 100
    g = pd.DataFrame({"q": m["q"], "fut7": fut7}).dropna()
    for name, sub in g.groupby("q", observed=True):
        print(f"  {name:<8} n={len(sub):>4} 未來 7 日: mean={sub['fut7'].mean():+.2f}% "
              f"median={sub['fut7'].median():+.2f}% 升比例={(sub['fut7'] > 0).mean() * 100:.0f}%")

    # ── 3. 策略: funding 極值反向 (統一 exit harness) ──
    print("\n=== 策略測試 (統一 exit, cap 1, TRAIN/TEST) ===")
    # 需要 bar 級數據: 用日線, 信號 = funding 分位
    q90 = m["f_mean"].quantile(0.90)
    q10 = m["f_mean"].quantile(0.10)
    q75 = m["f_mean"].quantile(0.75)
    q25 = m["f_mean"].quantile(0.25)
    print(f"閾值: q10={q10 * 100:+.4f}% q25={q25 * 100:+.4f}% "
          f"q75={q75 * 100:+.4f}% q90={q90 * 100:+.4f}% (每 8h)")

    dd = m.copy()
    dd["datetime"] = dd["dt"]          # harness 用 "datetime" 欄名
    dd["atr"] = (dd["high"] - dd["low"]).rolling(14).mean()
    for n in (50, 200):
        dd[f"ma{n}"] = dd["close"].rolling(n).mean()

    def backtest(sig_fn, label):
        sig = []
        for i in range(210, len(dd)):
            s = sig_fn(i)
            if s:
                sig.append((i, s))
        if not sig:
            print(f"  {label}: 0 信號")
            return
        mid = len(dd) // 2
        tr = stats(simulate(dd.iloc[:mid].reset_index(drop=True),
                            [s for s in sig if s[0] < mid], max_hold=21), "TRAIN")
        te = stats(simulate(dd.iloc[mid:].reset_index(drop=True),
                            [(s[0] - mid, s[1]) for s in sig if s[0] >= mid], max_hold=21), "TEST")
        print(f"  {label}")
        print(f"    {fmt(tr)}")
        print(f"    {fmt(te)}")

    fv = dd["f_mean"].values

    def s_hi_short(i):
        return "SELL" if fv[i] > q90 else None

    def s_lo_long(i):
        return "BUY" if fv[i] < q10 else None

    def s_ext_short(i):
        return "SELL" if fv[i] > q75 else None

    def s_ext_long(i):
        return "BUY" if fv[i] < q25 else None

    def s_neg_long(i):
        return "BUY" if fv[i] < 0 else None

    def s_hi_short_trend(i):
        # funding 高 + 價喺 MA200 上 (過熱) → 反向沽
        if fv[i] > q75 and dd["close"].iloc[i] > dd["ma200"].iloc[i]:
            return "SELL"
        return None

    def s_lo_long_trend(i):
        if fv[i] < q25 and dd["close"].iloc[i] < dd["ma200"].iloc[i]:
            return "BUY"
        return None

    for fn, label in ((s_hi_short, "funding > q90 (極端看多) → SELL"),
                      (s_lo_long, "funding < q10 (極端看空) → BUY"),
                      (s_ext_short, "funding > q75 → SELL"),
                      (s_ext_long, "funding < q25 → BUY"),
                      (s_neg_long, "funding < 0 → BUY"),
                      (s_hi_short_trend, "funding > q75 且價 > MA200 → SELL"),
                      (s_lo_long_trend, "funding < q25 且價 < MA200 → BUY")):
        backtest(fn, label)


if __name__ == "__main__":
    main()
