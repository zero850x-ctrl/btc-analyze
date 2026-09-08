#!/usr/bin/env python3
"""walkforward_btc.py — BTC 誠實分段驗證 (2026-09-09, 移植 XAUUSD walk-forward 框架)

用 XAUUSD 引擎形態訊號跑 BTC 歷史 1h bars，2 年數據 split train/test:
每段計 n / 勝率 / mean R / PF (R 基) / maxDD (R 基) — 有冇 edge 一目了然。

背景 (08-09 之後學識):
- XAUUSD 舊模擬假 fill 出 92% 勝率假像 → 修正後 boundary 兩段全負
- BTC 用市價進場 (backtest_btc.py 已是 entry_fill=px) → 冇假 fill 問題
- 照樣分 train/test 睇穩定性: 得一段正 = 唔可靠

用法:  python3 walkforward_btc.py
輸出:  每 segment 統計 + 按 side/aligned 分層
"""
import importlib.util
import json
import os
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
MAX_HOLD_BARS = 96          # 96 × 60min = 96h 最多持倉 (1h)
STEP_BARS = 48              # 每 48h 抽一個樣本
WARMUP = 500
COST_PCT = 0.001            # 來回手續費+滑點 ~0.1%


def load_bars(period="730d", interval="1h"):
    """BTC-USD 2 年 1h (yfinance 1h 上限 730 日)."""
    t = yf.Ticker("BTC-USD")
    df = t.history(period=period, interval=interval)
    if df.empty or len(df) < 2000:
        raise SystemExit(f"數據太少: {len(df)}")
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


def seg_stats(results):
    df = pd.DataFrame(results)
    if df.empty:
        return {"n": 0}
    closed = df[df["result"] != "OPEN"]
    if closed.empty:
        return {"n": len(df), "closed": 0}
    pnl = closed["pnl_r"].values
    wins = pnl[pnl > 0]; losses = pnl[pnl <= 0]
    gw = wins.sum() if len(wins) else 0.0
    gl = abs(losses.sum()) if len(losses) else 0.0
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    eq = np.cumsum(pnl); peak = np.maximum.accumulate(eq)
    maxdd = float((peak - eq).max()) if len(pnl) else 0.0
    return {
        "n": len(df), "closed": len(closed), "open": len(df) - len(closed),
        "win_rate": round(float((pnl > 0).mean()) * 100, 1),
        "mean_r": round(float(pnl.mean()), 3),
        "median_r": round(float(np.median(pnl)), 3),
        "pf": round(pf, 2) if pf != float("inf") else "inf",
        "max_dd_r": round(maxdd, 2),
        "sum_r": round(float(pnl.sum()), 2),
    }


def run_segment(bars, label):
    results = []
    i = WARMUP
    n_analyzed = 0
    while i < len(bars) - MAX_HOLD_BARS - 5:
        window = bars.iloc[max(0, i - WARMUP):i + 1].reset_index(drop=True)
        w = window.copy()
        w["dt"] = pd.to_datetime(w["datetime"])
        w = w.set_index("dt")
        day = w[["open", "high", "low", "close"]].resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(day) < 8:
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
                continue
            fwd = bars.iloc[i + 1: i + 1 + MAX_HOLD_BARS]
            entry_fill = px
            if side == "BUY":
                entry_fill *= (1 + COST_PCT / 2)
            else:
                entry_fill *= (1 - COST_PCT / 2)
            sim_bars = fwd[["open", "high", "low", "close", "volume", "datetime"]].copy()
            ts_aware = pd.Timestamp(ts)
            if ts_aware.tzinfo is None:
                ts_aware = ts_aware.tz_localize("UTC")
            sim = pt._simulate_staged_exit(sim_bars, entry_fill, stop, tp1 or 0, tp2 or 0,
                                           side, atr, seed_dt=ts_aware.to_pydatetime(), data_source="yf")
            pnl = sim.get("pnl_r")
            if pnl is None:
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
                "bars_held": sim.get("bars_held", MAX_HOLD_BARS),
            })
        i += STEP_BARS
    st = seg_stats(results)
    print(f"\n{'='*64}\n{label}: analyzed {n_analyzed} windows, {st['n']} setups "
          f"({st.get('closed',0)} closed / {st.get('open',0)} open)")
    if st.get("closed", 0) == 0:
        print("  (冇 closed 樣本)")
        return results, st
    print(f"  勝率 {st['win_rate']:.1f}% | mean R {st['mean_r']:+.3f} | median R {st['median_r']:+.3f} "
          f"| PF {st['pf']} | maxDD {st['max_dd_r']}R | sumR {st['sum_r']:+.2f}")
    # 分層
    df = pd.DataFrame(results)
    closed = df[df["result"] != "OPEN"]
    for side in ["BUY", "SELL"]:
        c = closed[closed["side"] == side]
        if len(c):
            print(f"    {side}: n={len(c)} 勝率 {(c['pnl_r']>0).mean()*100:.1f}% meanR {c['pnl_r'].mean():+.3f}")
    al = closed[closed["aligned"] == 1]; ct = closed[closed["aligned"] == 0]
    if len(al):
        print(f"    順勢: n={len(al)} 勝率 {(al['pnl_r']>0).mean()*100:.1f}% meanR {al['pnl_r'].mean():+.3f}")
    if len(ct):
        print(f"    逆勢: n={len(ct)} 勝率 {(ct['pnl_r']>0).mean()*100:.1f}% meanR {ct['pnl_r'].mean():+.3f}")
    return results, st


def main():
    print("Fetching BTC-USD 2y 1h...")
    bars = load_bars()
    print(f"bars: {len(bars)}  {bars['datetime'].iloc[0]} -> {bars['datetime'].iloc[-1]}")
    n = len(bars); mid = n // 2
    _, st1 = run_segment(bars.iloc[:mid].reset_index(drop=True), "TRAIN (第1年)")
    _, st2 = run_segment(bars.iloc[mid:].reset_index(drop=True), "TEST (第2年)")
    print("\n=== 裁決 ===")
    for name, st in [("TRAIN", st1), ("TEST", st2)]:
        if st.get("closed"):
            verdict = "✅ 正" if st["mean_r"] > 0 else "❌ 負"
            print(f"  {name}: meanR {st['mean_r']:+.3f} ({verdict})")
    if st1.get("closed") and st2.get("closed"):
        both = st1["mean_r"] > 0 and st2["mean_r"] > 0
        print("  兩段都正 = edge 可信" if both else "  至少一段負 = 冇穩定 edge, 唔好信")


if __name__ == "__main__":
    main()