#!/usr/bin/env python3
"""backtest_gates.py — D 實驗: 驗證 A (RR gate) + B (pattern gate) + D (SL floor 0.8 vs 1.2)

方法: 重跑 backtest 邏輯但凍結「原始 setup」(唔加 gates), 事後用唔同 gate 組合過濾,
比較 sumR / 勝率 / 交易數。SL floor 對比: 重跑模擬, SL 放遠啲 (1.2×ATR)。
"""
import importlib.util
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
REPO = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("analyze_v3", os.path.join(REPO, "analyze_v3.py"))
av3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(av3)
spec2 = importlib.util.spec_from_file_location("paper_trade_xau", os.path.join(REPO, "paper_trade.py"))
pt = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(pt)

sys.path.insert(0, REPO)
import backtest_btc as bt

SL_FLOOR_ATR_MULT = 0.8
MAX_HOLD_BARS = 96          # 96 × 30min = 48h 最多持倉
STEP_BARS = 48              # 每 24h 抽一個樣本
WARMUP = 500                # 分析窗口最少 bars
COST_PCT = 0.001            # 來回手續費+滑點 ~0.1%


def run_collection(sl_mult):
    """跑 60 日 M30 凍結窗口, 收集原始 setups + 模擬 (用指定 SL floor mult)."""
    bars = bt.load_bars()
    results = []
    i = WARMUP
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
        ts = window["datetime"].iloc[-1]
        for s in setups:
            side = "SELL" if "SELL" in str(s.get("direction", "")) else "BUY"
            entry = bt.parse_lvl(s.get("entry_zone")) or bt.parse_lvl(s.get("entry_trigger")) or px
            stop = bt.parse_lvl(s.get("stop_loss"))
            tp1 = bt.parse_lvl(s.get("tp1"))
            if stop is None or entry is None:
                continue
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            min_d = sl_mult * atr
            if risk < min_d:
                stop = entry + min_d if side == "SELL" else entry - min_d
                risk = abs(entry - stop)
            sim_bars = bars[(bars["datetime"] > ts)].head(MAX_HOLD_BARS)[["open", "high", "low", "close", "volume", "datetime"]].copy()
            if sim_bars.empty:
                continue
            entry_fill = px * (1 + COST_PCT / 2) if side == "BUY" else px * (1 - COST_PCT / 2)
            ts_aware = pd.Timestamp(ts)
            if ts_aware.tzinfo is None:
                ts_aware = ts_aware.tz_localize("UTC")
            sim = pt._simulate_staged_exit(sim_bars, entry_fill, stop, tp1 or 0, 0,
                                           side, atr, seed_dt=ts_aware.to_pydatetime(), data_source="yf")
            pnl = sim.get("pnl_r")
            if pnl is None:
                last = float(sim_bars["close"].iloc[-1])
                pnl = ((last - entry_fill) / risk) if side == "BUY" else ((entry_fill - last) / risk)
            results.append({
                "ts": str(ts), "side": side, "pattern": s.get("pattern", "?"),
                "rr": round(abs(tp1 - entry) / risk, 3) if tp1 else None,
                "R": round(pnl, 3),
            })
        i += STEP_BARS
    return results


def evaluate(rows, name, rr_min=None, patterns=None):
    sel = rows
    if rr_min is not None:
        sel = [r for r in sel if r["rr"] is not None and r["rr"] >= rr_min]
    if patterns is not None:
        sel = [r for r in sel if any(k in r["pattern"] for k in patterns)]
    if not sel:
        print(f"{name:28s} n=0")
        return
    rs = [r["R"] for r in sel]
    wins = sum(1 for x in rs if x > 0)
    print(f"{name:28s} n={len(sel):3d}  勝率 {wins/len(sel)*100:5.1f}%  sumR {sum(rs):+7.2f}  meanR {np.mean(rs):+.3f}")


if __name__ == "__main__":
    print("跑 baseline (SL 0.8×ATR)…")
    base = run_collection(0.8)
    print("跑 SL 1.2×ATR…")
    wide = run_collection(1.2)
    json.dump({"base": base, "wide": wide}, open(os.path.join(REPO, "bt_gate_results.json"), "w"))

    FLAG = ("Flag",)
    print("\n=== Gate 組合對比 (SL 0.8×ATR) ===")
    evaluate(base, "無 gate (baseline)")
    evaluate(base, "A: RR>=1.0", rr_min=1.0)
    evaluate(base, "B: pattern=Flag", patterns=FLAG)
    evaluate(base, "A+B", rr_min=1.0, patterns=FLAG)
    print("\n=== SL floor 對比 (都係 A+B gates) ===")
    evaluate(wide, "SL 1.2×ATR + A+B", rr_min=1.0, patterns=FLAG)
    evaluate(base, "SL 0.8×ATR + A+B", rr_min=1.0, patterns=FLAG)
