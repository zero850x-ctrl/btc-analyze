#!/usr/bin/env python3
"""btc_s3_compare.py — XAUUSD S3 反彈信號套落 BTC 會點?

背景: 用戶問「為何 XAUUSD 的方法 BTC 不行」。
XAUUSD S3 信號 (skill xauusd-m5m15-martingale):
  15m bar 三條件同時
  1. 陽燭 (close > open)
  2. close > SMA10
  3. close > 前 3 條 bar 最高 (唔含今 bar)
  → LONG, hold N 分鐘 snapshot 平倉

誠實協議 (XAUUSD look-ahead bias 教訓):
  entry 必須係信號 bar 收市之後嘅價 → 用 idx+1 open。

對照 baseline: 每個 bar 隨機 buy 都應該 ~50% (因為要同時間比較)。
"""
import os
import sys
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def load(period, interval):
    import yfinance as yf
    df = yf.Ticker("BTC-USD").history(period=period, interval=interval)
    if df.empty:
        raise SystemExit(f"冇數據 {period}/{interval}")
    df = df.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    if "datetime" not in df.columns:
        df = df.rename(columns={"date": "datetime"})
    return df[["datetime", "open", "high", "low", "close"]].dropna().reset_index(drop=True)


def s3_signals(d):
    """回傳 signal idx list (long only). 條件全部用已收市 bar, 唔含未來."""
    close, op = d["close"], d["open"]
    sma10 = close.rolling(10).mean()
    prev_high3 = d["high"].rolling(3).max().shift(1)
    cond = (close > op) & (close > sma10) & (close > prev_high3)
    return [i for i in range(20, len(d) - 1) if bool(cond.iloc[i])]


def hold_test(d, idxs, hold_bars, fee_rate=0.0):
    """entry = idx+1 open, exit = idx+1+hold_bars open. 回傳每單 net return (%)."""
    rets = []
    for i in idxs:
        e_i, x_i = i + 1, i + 1 + hold_bars
        if x_i >= len(d):
            continue
        e, x = d["open"].iloc[e_i], d["open"].iloc[x_i]
        if not e:
            continue
        r = (x - e) / e * 100
        r -= fee_rate * 2 * 100
        rets.append(r)
    return rets


def baseline(d, hold_bars, stride=10, fee_rate=0.0):
    """每個 bar 都入 (唔篩信號) → baseline."""
    return hold_test(d, list(range(20, len(d) - 1, stride)), hold_bars, fee_rate)


def report(name, rets, hold_bars):
    if not rets:
        print(f"  {name:26} 冇樣本")
        return None
    wr = sum(1 for r in rets if r > 0) / len(rets) * 100
    mean = statistics.mean(rets)
    sd = statistics.stdev(rets) if len(rets) > 1 else 0
    t = mean / (sd / len(rets) ** 0.5) if sd and len(rets) > 1 else 0
    print(f"  {name:26} n={len(rets):5}  勝率 {wr:5.1f}%  "
          f"mean {mean:+.3f}%  t {t:+5.2f}  {'✅' if wr > 50 and abs(t) > 2 else ''}")
    return {"n": len(rets), "wr": wr, "mean": mean, "t": t}


def main():
    print("=== BTC 上測 XAUUSD S3 反彈信號 ===\n")
    for period, interval, hb_list in (("60d", "15m", [1, 2, 4]),
                                      ("730d", "1h", [1, 4, 8])):
        try:
            d = load(period, interval)
        except SystemExit as e:
            print(f"  [{period}/{interval}] {e}\n")
            continue
        idxs = s3_signals(d)
        print(f"[{period} @ {interval}] 共 {len(d)} bar, S3 信號 {len(idxs)} 個 "
              f"({len(idxs)/len(d)*100:.1f}% of bars)")
        bars_per_hour = {"15m": 4, "1h": 1}[interval]
        for hb in hb_list:
            mins = hb * (15 if interval == "15m" else 60)
            print(f"  --- hold {hb} bar (~{mins} 分鐘) ---")
            report("S3 信號 (免 fee)", hold_test(d, idxs, hb), hb)
            report("S3 信號 (fee 0.1%×2)", hold_test(d, idxs, hb, 0.001), hb)
            report("baseline (全買)", baseline(d, hb, stride=max(1, hb)), hb)
        print()


if __name__ == "__main__":
    main()
