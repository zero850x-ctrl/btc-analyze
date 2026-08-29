#!/usr/bin/env python3
"""backtest_btc.py — 用 XAUUSD 引擎形態訊號跑 BTC 歷史 M30 backtest

方法: 對過去 N 個月 BTC M30 bars, 每 24h 一次「凍結窗口」分析:
  - 攞截至 t 嘅 bars → 引擎檢測 patterns → generate setups
  - setup 模擬進場 (現價 / entry_zone) → _simulate_staged_exit 行到 SL/TP
  - 統計勝率 / 平均 R / 長短分佈
"""
import importlib.util
import json
import os
import sys
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
REPO = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("analyze_v3", os.path.join(REPO, "analyze_v3.py"))
av3 = importlib.util.module_from_spec(spec); spec.loader.exec_module(av3)
spec2 = importlib.util.spec_from_file_location("paper_trade_xau", os.path.join(REPO, "paper_trade.py"))
pt = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(pt)

SL_FLOOR_ATR = 0.8
MAX_HOLD_BARS = 96          # 96 × 30min = 48h 最多持倉
STEP_BARS = 48              # 每 24h 抽一個樣本
WARMUP = 500                # 分析窗口最少 bars
COST_PCT = 0.001            # 來回手續費+滑點 ~0.1%


def load_bars():
    # yfinance 30m 只回 60 日 — 180 日要拆 3 段 OR 只用 60 日。先用 60 日 (M30 引擎窗口合理)
    t = yf.Ticker("BTC-USD")
    df = t.history(period="60d", interval="30m")
    if df.empty:
        raise SystemExit("no data")
    df = df.reset_index()
    if "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "datetime"})
    if df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_convert("UTC").dt.tz_localize(None)
    for col in ["open", "high", "low", "close", "volume"]:
        for alt in [col.capitalize(), col.upper()]:
            if alt in df.columns and col not in df.columns:
                df[col] = df[alt]
    return df.reset_index(drop=True)


def parse_lvl(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    import re
    m = re.search(r"-?[\d,]+\.?\d*", str(val).replace("$", "").replace(",", ""))
    return float(m.group(0)) if m else None


def run():
    bars = load_bars()
    print(f"bars: {len(bars)}  {bars['datetime'].iloc[0]} -> {bars['datetime'].iloc[-1]}")
    results = []
    i = WARMUP
    n_analyzed = 0
    while i < len(bars) - MAX_HOLD_BARS - 5:
        window = bars.iloc[max(0, i - WARMUP):i + 1].reset_index(drop=True)
        # 日線 proxy: 用 window 內嘅日線趨勢 (resample)
        w = window.copy()
        w["dt"] = pd.to_datetime(w["datetime"])
        w = w.set_index("dt")
        # 各欄各自 resample (close-only resample 造唔出 open/high/low)
        day = w[["open", "high", "low", "close"]].resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(day) < 8:   # 500×M30 ≈ 10.4 日 — 8 日日線夠 trend 判斷
            i += STEP_BARS
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
                i += STEP_BARS
                continue
            pts = av3.find_swings_ordered(df_a["High"].values, df_a["Low"].values, lookback=3,
                                          atr=df_a["ATR"].values, close=df_a["Close"].values)
            patterns = av3.detect_all_patterns(df_a, pts, atr=atr)
            setups = av3.generate_trade_setups(df_a, patterns, pts, dt, px, atr, h1_trend=ht)
        except Exception:
            i += STEP_BARS
            continue
        n_analyzed += 1
        ts = window["datetime"].iloc[-1]
        for s in setups:
            side = "SELL" if "SELL" in str(s.get("direction", "")) else "BUY"
            entry = parse_lvl(s.get("entry_zone")) or parse_lvl(s.get("entry_trigger")) or px
            stop = parse_lvl(s.get("stop_loss"))
            tp1 = parse_lvl(s.get("tp1"))
            tp2 = parse_lvl(s.get("tp2"))
            if stop is None or entry is None:
                continue
            risk = abs(entry - stop)
            if risk <= 0 or risk < SL_FLOOR_ATR * atr:
                continue  # 跟 BTC SL floor
            # 模擬: 由 ts 之後嘅 bars 行
            fwd = bars.iloc[i + 1: i + 1 + MAX_HOLD_BARS]
            entry_fill = px  # 市價進場簡化
            # 滑點/費用
            if side == "BUY":
                entry_fill *= (1 + COST_PCT / 2)
            else:
                entry_fill *= (1 - COST_PCT / 2)
            # 用原版 _simulate_staged_exit (bars 需要 open/high/low/close 欄)
            sim_bars = fwd[["open", "high", "low", "close", "volume", "datetime"]].copy()
            # datetime 去 tz (tz-naive) — 原版 sim 比較 seed_dt (naive)
            sim_bars = fwd[["open", "high", "low", "close", "volume", "datetime"]].copy()
            # seed_dt 轉 tz-aware UTC 字串 — _parse_dt 內部會統一 tz
            ts_aware = pd.Timestamp(ts)
            if ts_aware.tzinfo is None:
                ts_aware = ts_aware.tz_localize("UTC")
            sim = pt._simulate_staged_exit(sim_bars, entry_fill, stop, tp1 or 0, tp2 or 0,
                                           side, atr, seed_dt=ts_aware.to_pydatetime(), data_source="yf")
            pnl = sim.get("pnl_r")
            if pnl is None:
                # 未平倉 (timeout 無 SL/TP) — 用最後 close 計浮動
                last = float(fwd["close"].iloc[-1])
                pnl = ((last - entry_fill) / risk) if side == "BUY" else ((entry_fill - last) / risk)
                result = "OPEN"
            else:
                result = sim.get("result", "?")
            results.append({
                "ts": str(ts), "side": side, "pattern": s.get("pattern", "?"),
                "entry_mode": s.get("entry_mode", "?"),
                "aligned": 1 if (side == "BUY" and dt.get("trend") == "BULLISH") or (side == "SELL" and dt.get("trend") == "BEARISH") else 0,
                "result": result, "pnl_r": round(pnl, 3),
                "rr_tp1": s.get("rr_tp1"),
                "bars_held": sim.get("bars_held", MAX_HOLD_BARS),
            })
        i += STEP_BARS

    df_r = pd.DataFrame(results)
    print(f"\nanalyzed windows: {n_analyzed}, setups simulated: {len(df_r)}")
    if df_r.empty:
        print("冇樣本 — 放鬆條件再跑")
        return
    closed = df_r[df_r["result"] != "OPEN"]
    print(f"\n=== 總體 (closed {len(closed)}/{len(df_r)}) ===")
    print(f"勝率: {(closed['pnl_r'] > 0).mean()*100:.1f}%   mean R: {closed['pnl_r'].mean():+.3f}   median R: {closed['pnl_r'].median():+.3f}")
    print(f"\n=== 按 side ===")
    for side in ["BUY", "SELL"]:
        c = closed[closed["side"] == side]
        if len(c):
            print(f"{side}: n={len(c)}  勝率 {(c['pnl_r']>0).mean()*100:.1f}%  meanR {c['pnl_r'].mean():+.3f}")
    print(f"\n=== ALIGNED vs counter-trend (日線) ===")
    for al in [1, 0]:
        c = closed[closed["aligned"] == al]
        if len(c):
            label = "ALIGNED" if al else "COUNTER"
            print(f"{label}: n={len(c)}  勝率 {(c['pnl_r']>0).mean()*100:.1f}%  meanR {c['pnl_r'].mean():+.3f}")
    print(f"\n=== 按 pattern (n>=8) ===")
    for pat, grp in closed.groupby("pattern"):
        if len(grp) >= 8:
            print(f"{pat[:36]:36} n={len(grp):3}  勝率 {(grp['pnl_r']>0).mean()*100:5.1f}%  meanR {grp['pnl_r'].mean():+.3f}")
    df_r.to_json(os.path.join(REPO, "btc_backtest_results.json"), orient="records")
    print(f"\nsaved btc_backtest_results.json")


if __name__ == "__main__":
    run()
