#!/usr/bin/env python3
"""btc_rr_gate_diag.py — 診斷「RR gate 同引擎 TP1 數學上唔相交」

## 問題 (2026-09-28 用戶指出)

    btc_engine.py:  MIN_RR = 1.2;  if rr < MIN_RR: skip
    analyze_v3.py:  tp1 = min(fib_tp, entry + risk)   # BUY
                    tp1 = max(fib_tp, entry - risk)   # SELL

`min/max` 令 TP1 **永遠唔會遠過 1:1** → RR(TP1) <= 1.0。
再計埋 gate 用 `limit_px` (zone 中點) 而引擎用 `entry` (zone 邊緣),
risk 變大 + TP1 距離變細 → RR(TP1) **恆 < 1.0**。
=> MIN_RR=1.2 永遠達唔到, 主系統結構性唔可能開單。

## 本腳本做嘅事

跑 walk-forward 掃歷史 M30, 逐個 raw setup 計:
  - family (家族 gate 結果)
  - rr1 = RR at TP1   (現行 gate 用嘅 metric)
  - rr2 = RR at TP2
  - blended = 實際 3 段出場嘅期望 R (1/3 TP1 + 1/3 TP2 + 1/3 尾倉)

目的: 睇「改用 blended metric」會放行幾多 setup, 而 threshold 應該係幾多。

用法: python3 btc_rr_gate_diag.py [bars_csv] [interval]
"""
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


av3 = _load("analyze_v3", "analyze_v3.py")
eng = _load("btc_engine", "btc_engine.py")

CSV = os.path.expanduser("~/.hermes/reports/btc_bars_30m.csv")
WARMUP, STEP = 1000, 96


def load_bars(path):
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ("close", "high", "low", "open", "volume"):
        cap = col.capitalize()
        if col not in df.columns and cap in df.columns:
            df[col] = df[cap]
        if cap not in df.columns and col in df.columns:
            df[cap] = df[col]
    return df.reset_index(drop=True)


def collect(bars):
    rows = []
    i = WARMUP
    n_err = 0
    while i < len(bars) - 200:
        window = bars.iloc[max(0, i - WARMUP):i + 1].reset_index(drop=True)
        w = window.copy()
        w["dt"] = pd.to_datetime(w["datetime"])
        w = w.set_index("dt")
        day = w[["open", "high", "low", "close"]].resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(day) < 8:
            i += STEP
            continue
        day_df = pd.DataFrame({"Open": day["open"], "High": day["high"],
                               "Low": day["low"], "Close": day["close"]})
        try:
            dt = av3.analyze_daily_trend(day_df)
            ht = av3.analyze_h1_trend(window)
            df_a = av3.add_indicators(window.copy())
            atr = float(df_a["ATR"].iloc[-1])
            px = float(window["close"].iloc[-1])
            if not np.isfinite(atr) or atr <= 0:
                i += STEP
                continue
            pts = av3.find_swings_ordered(df_a["High"].values, df_a["Low"].values,
                                          lookback=3, atr=df_a["ATR"].values,
                                          close=df_a["Close"].values)
            patterns = av3.detect_all_patterns(df_a, pts, atr=atr)
            setups = av3.generate_trade_setups(df_a, patterns, pts, dt, px, atr, h1_trend=ht)
            ma50 = float(df_a["MA50"].iloc[-1]) if "MA50" in df_a.columns and np.isfinite(df_a["MA50"].iloc[-1]) else None
        except Exception as e:
            if n_err < 3:
                print(f"  ⚠️ window@{i}: {type(e).__name__}: {e}")
            n_err += 1
            i += STEP
            continue

        for s in setups:
            side = "SELL" if "SELL" in str(s.get("direction", "")) else "BUY"
            fam = eng.setup_family(s)
            entry = eng._parse_setup_level(s.get("entry_zone"))
            limit_px = eng.parse_entry_limit(s.get("entry_trigger"), s.get("entry_zone")) or entry
            stop = eng._parse_setup_level(s.get("stop_loss"))
            tp1 = eng._parse_setup_level(s.get("tp1"))
            tp2 = eng._parse_setup_level(s.get("tp2"))
            if None in (entry, limit_px, stop) or tp1 is None:
                continue
            risk = abs(limit_px - stop)
            if risk <= 0:
                continue
            # SL floor (同引擎一致)
            floor = eng.SL_FLOOR_ATR_MULT * atr
            if risk < floor:
                risk = floor
            rr1 = abs(tp1 - limit_px) / risk
            rr2 = (abs(tp2 - limit_px) / risk) if tp2 else None
            # MA50 extended gate
            ext = ((entry / ma50 - 1) * 100.0) if (ma50 and ma50 > 0) else None
            ma_ok = True
            if ext is not None:
                if side == "BUY" and ext > eng.MAX_MA50_EXT_PCT + 1e-6:
                    ma_ok = False
                if side == "SELL" and ext < -(eng.MAX_MA50_EXT_PCT + 1e-6):
                    ma_ok = False
            rows.append({
                "ts": str(window["datetime"].iloc[-1]), "side": side, "fam": fam,
                "pattern": str(s.get("pattern", "?"))[:26],
                "rr1": round(rr1, 3), "rr2": round(rr2, 3) if rr2 else None,
                "ma_ok": ma_ok, "ext": round(ext, 2) if ext is not None else None,
                "limit": round(limit_px, 1), "stop": round(stop, 1),
                "tp1": round(tp1, 1), "tp2": round(tp2, 1) if tp2 else None,
            })
        i += STEP
    print(f"  掃 {i} bars → {len(rows)} 個 raw setup")
    return rows


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else CSV
    print(f"=== RR gate 診斷 ({os.path.basename(path)}) ===\n")
    bars = load_bars(path)
    print(f"bars: {len(bars)}  {bars['datetime'].iloc[0]} → {bars['datetime'].iloc[-1]}\n")
    rows = collect(bars)

    fam_ok = [r for r in rows if r["fam"] in eng.ALLOWED_PATTERN_FAMILIES]
    print(f"\n--- 家族 gate ---")
    from collections import Counter
    print(f"  ALLOWED_PATTERN_FAMILIES = {eng.ALLOWED_PATTERN_FAMILIES}")
    for f, n in Counter(r["fam"] for r in rows).most_common():
        mark = " ✅ 過" if f in eng.ALLOWED_PATTERN_FAMILIES else " ❌ 擋"
        print(f"    {f:22} {n:5}{mark}")
    print(f"  → 過家族 gate: {len(fam_ok)} / {len(rows)} "
          f"({len(fam_ok)/max(len(rows),1)*100:.1f}%)")

    print(f"\n--- 過家族 gate 嘅 setup, RR(TP1) 分佈 ---")
    r1 = sorted(r["rr1"] for r in fam_ok)
    if r1:
        print(f"  n={len(r1)}  min {r1[0]:.3f}  median {r1[len(r1)//2]:.3f}  max {r1[-1]:.3f}")
        for th in (1.2, 1.0, 0.8, 0.6, 0.5, 0.4, 0.3):
            k = sum(1 for x in r1 if x >= th)
            print(f"    RR >= {th}: {k:5} ({k/len(r1)*100:5.1f}%)")
    print(f"  MIN_RR = {eng.MIN_RR}")

    print(f"\n--- 3 段出場嘅 blended R (1/3 TP1 + 1/3 TP2 + 1/3 尾倉) ---")
    print("  尾倉假設: 0 = 最保守 (BE 止損); rr1 = 尾倉約等 TP1")
    bl = []
    for r in fam_ok:
        rr2 = r["rr2"] if r["rr2"] is not None else r["rr1"]
        bl.append((r["rr1"], rr2))
    if bl:
        for lbl, fn in (("尾倉=0 (保守)", lambda a, b: (a + b + 0) / 3),
                        ("尾倉=rr1", lambda a, b: (a + b + a) / 3),
                        ("尾倉=rr2", lambda a, b: (a + b + b) / 3)):
            v = sorted(fn(a, b) for a, b in bl)
            print(f"  {lbl:16} n={len(v)} min {v[0]:+.3f} median {v[len(v)//2]:+.3f} max {v[-1]:+.3f}")
            for th in (1.2, 1.0, 0.8, 0.6):
                k = sum(1 for x in v if x >= th)
                print(f"      >= {th}: {k:5} ({k/len(v)*100:5.1f}%)")

    print(f"\n--- 結論對比 ---")
    cur = sum(1 for r in fam_ok if r["rr1"] >= eng.MIN_RR)
    print(f"  現行 (家族 gate + RR(TP1)>={eng.MIN_RR}):          {cur} 個")
    for lbl, fn in (("blended(尾倉=0) >= 1.2", lambda a, b: (a + b) / 3),
                    ("blended(尾倉=rr1) >= 1.2", lambda a, b: (a + b + a) / 3)):
        k = sum(1 for a, b in bl if fn(a, b) >= eng.MIN_RR)
        print(f"  改 {lbl:32} {k} 個")
    k = sum(1 for r in fam_ok if r["rr1"] >= 0.8)
    print(f"  只降 MIN_RR -> 0.8 (metric 不變):                  {k} 個")
    k = sum(1 for r in fam_ok if r["ma_ok"])
    print(f"  再加埋 MA50 gate 後 (現行 metric, RR>=0.8):         "
          f"{sum(1 for r in fam_ok if r['ma_ok'] and r['rr1'] >= 0.8)} 個")

    out = os.path.expanduser("~/.hermes/reports/btc_rr_gate_diag.json")
    json.dump(rows, open(out, "w"))
    print(f"\n已存 {len(rows)} 筆 → {out}")


if __name__ == "__main__":
    main()
