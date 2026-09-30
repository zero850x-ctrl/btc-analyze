#!/usr/bin/env python3
"""validate_rr_blended_gate.py — 驗證 RR gate 換 metric 嘅實際效果 (feat/rr-blended-gate).

問題: 引擎 TP1 按設計擺 ~1:1 → RR(TP1) 中位 0.83, gate 要 >= 1.2 → 主系統 17 日 0 單。
修法: gate metric 改為 3 段出場 blended R = (1/3)rr1 + (1/3)rr2 + (1/3)·尾倉(0)。

呢個 script 用同一批歷史 setup 對照兩個 gate:
    OLD: rr1 >= 1.2          (TP1 單段)
    NEW: blended >= 1.2      (3 段)

公平性:
  - 兩個 gate 用**完全相同**嘅 setup pool (同一 window、同一 family/MA50 gate、同一 SL floor)
  - 進場一律用 limit 價 (production ENTRY_MODE=limit), 而且要求 forward bars 真係觸及 limit 才算成交
  - 出場用 production 嘅 _simulate_staged_exit (同 live 同一段 code)

用法:
  python3 validate_rr_blended_gate.py [days] [step_bars]
"""
import importlib.util
import json
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REPO = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


av3 = _load("analyze_v3", os.path.join(REPO, "analyze_v3.py"))
pt = _load("paper_trade_xau", os.path.join(REPO, "paper_trade.py"))
be = _load("btc_engine", os.path.join(REPO, "btc_engine.py"))
fbb = _load("fetch_btc_bars", os.path.join(REPO, "fetch_btc_bars.py"))

WARMUP = 1000
MAX_HOLD_BARS = 96          # 48h
# COST_PCT = **來回**總成本 (entry fee + exit fee + 滑價) 佔名義值比例。
# Binance spot 標準 0.1%/邊 → 來回 0.2%。以 entry 價偏移方式套用:
# 每個 R 都會下降 COST_PCT * entry / risk (相等於來回成本)。
COST_PCT = float(os.environ.get("VBT_COST_PCT", "0.002"))
TAIL = be.TAIL_R_ASSUMED


def load_bars(days):
    csv_p = os.path.expanduser("~/.hermes/reports/btc_bars_30m.csv")
    if os.path.exists(csv_p):
        df = pd.read_csv(csv_p)
        df["datetime"] = pd.to_datetime(df["datetime"])
        span = (df["datetime"].max() - df["datetime"].min()).days
        if span >= days:
            print(f"[bars] 用本地 CSV: {len(df)} 根 ({span} 日)")
            return df.reset_index(drop=True)
    print(f"[bars] 下載 {days} 日...")
    rows = fbb.fetch("30m", days)
    p = fbb.save(rows, "30m")
    df = pd.read_csv(p)
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.reset_index(drop=True)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 540
    step = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    bars = load_bars(days)
    print(f"[bars] {len(bars)} 根  {bars['datetime'].iloc[0]} → {bars['datetime'].iloc[-1]}  step={step}")
    ma = bars["close"].rolling(50).mean()

    rows = []
    i = WARMUP
    n_win = 0
    while i < len(bars) - MAX_HOLD_BARS - 5:
        window = bars.iloc[max(0, i - WARMUP):i + 1].reset_index(drop=True)
        # av3 內部函數要大寫欄名 (analyze_h1_trend / add_indicators) — 用小寫副本做模擬
        win_cap = window.rename(columns={"open": "Open", "high": "High",
                                         "low": "Low", "close": "Close",
                                         "volume": "Volume"})
        w = window.copy()
        w["dt"] = pd.to_datetime(w["datetime"])
        w = w.set_index("dt")
        day = w[["open", "high", "low", "close"]].resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(day) < 8:
            i += step
            continue
        day_df = pd.DataFrame({"Open": day["open"], "High": day["high"],
                               "Low": day["low"], "Close": day["close"]})
        try:
            dt = av3.analyze_daily_trend(day_df)
            ht = av3.analyze_h1_trend(win_cap)
            df_a = av3.add_indicators(win_cap.copy())
            atr = float(df_a["ATR"].iloc[-1])
            px = float(win_cap["Close"].iloc[-1])
            if not np.isfinite(atr) or atr <= 0:
                i += step
                continue
            pts = av3.find_swings_ordered(df_a["High"].values, df_a["Low"].values,
                                          lookback=3, atr=df_a["ATR"].values,
                                          close=df_a["Close"].values)
            patterns = av3.detect_all_patterns(df_a, pts, atr=atr)
            setups = av3.generate_trade_setups(df_a, patterns, pts, dt, px, atr, h1_trend=ht)
        except Exception as e:
            if n_win == 0:
                print(f"  [warn] window {i} 分析失敗: {type(e).__name__}: {e}", flush=True)
            i += step
            continue
        n_win += 1
        ts = window["datetime"].iloc[-1]
        ma50 = float(ma.iloc[i]) if i < len(ma) and np.isfinite(ma.iloc[i]) else None

        for s in setups:
            # ── 同引擎完全一致嘅前處理 (兩個 gate 共用) ──────────────
            fam = be.setup_family(s)
            if be.ALLOWED_PATTERN_FAMILIES is not None and fam not in be.ALLOWED_PATTERN_FAMILIES:
                continue
            side = "SELL" if "SELL" in str(s.get("direction", "")) else "BUY"
            entry = be._parse_setup_level(s.get("entry_zone"))
            if entry is None:
                entry = be._parse_setup_level(s.get("entry_trigger"))
            limit_px = be.parse_entry_limit(s.get("entry_trigger"), s.get("entry_zone")) or entry
            stop = be._parse_setup_level(s.get("stop_loss"))
            tp1 = be._parse_setup_level(s.get("tp1"))
            tp2 = be._parse_setup_level(s.get("tp2"))
            if entry is None or stop is None or not np.isfinite(stop):
                continue
            risk = abs(limit_px - stop)
            if risk <= 0:
                continue
            floor = be.SL_FLOOR_ATR_MULT * atr
            if risk < floor:
                stop = limit_px + floor if side == "SELL" else limit_px - floor
                risk = abs(limit_px - stop)
            # MA50 gate (同引擎)
            if ma50:
                ext = (entry / ma50 - 1) * 100.0
                if side == "BUY" and ext > be.MAX_MA50_EXT_PCT + 1e-6:
                    continue
                if side == "SELL" and ext < -(be.MAX_MA50_EXT_PCT + 1e-6):
                    continue
            if not tp1:
                continue
            rr1 = abs(tp1 - limit_px) / risk
            rr2 = (abs(tp2 - limit_px) / risk) if tp2 else None
            blended = ((rr1 + 2 * TAIL) / 3.0) if rr2 is None else ((rr1 + rr2 + TAIL) / 3.0)

            # ── limit 進場: 要 forward bars 真係觸及 limit 才算成交 ──────
            fwd = bars.iloc[i + 1: i + 1 + MAX_HOLD_BARS]
            if fwd.empty:
                continue
            if side == "BUY":
                touched = fwd[fwd["low"] <= limit_px]
            else:
                touched = fwd[fwd["high"] >= limit_px]
            if touched.empty:
                rows.append({"ts": str(ts), "side": side, "filled": 0,
                             "rr1": round(rr1, 3), "rr2": round(rr2, 3) if rr2 is not None else None,
                             "blended": round(blended, 3), "pnl_r": None})
                continue
            fill_idx = bars.index.get_loc(touched.index[0])
            fill_ts = bars["datetime"].iloc[fill_idx]
            sim_bars = bars.iloc[fill_idx: i + 1 + MAX_HOLD_BARS][
                ["open", "high", "low", "close", "volume", "datetime"]].copy()
            fill_px = limit_px
            # 來回成本一次過以 entry 偏移套用 (見 COST_PCT 註釋)
            if side == "BUY":
                fill_px *= (1 + COST_PCT)
            else:
                fill_px *= (1 - COST_PCT)
            ts_aware = pd.Timestamp(fill_ts)
            if ts_aware.tzinfo is None:
                ts_aware = ts_aware.tz_localize("UTC")
            sim = pt._simulate_staged_exit(sim_bars, fill_px, stop, tp1 or 0, tp2 or 0,
                                           side, atr, seed_dt=ts_aware.to_pydatetime(),
                                           data_source="yf")
            pnl = sim.get("pnl_r")
            result = sim.get("result", "?")
            if pnl is None:
                last = float(sim_bars["close"].iloc[-1])
                pnl = ((last - fill_px) / risk) if side == "BUY" else ((fill_px - last) / risk)
                result = "OPEN"
            rows.append({"ts": str(ts), "side": side, "filled": 1,
                         "rr1": round(rr1, 3), "rr2": round(rr2, 3) if rr2 is not None else None,
                         "blended": round(blended, 3), "pnl_r": round(float(pnl), 3),
                         "result": result, "bars_held": sim.get("bars_held")})
        i += step
        if n_win % 200 == 0:
            print(f"  ...{n_win} windows, {len(rows)} setups", flush=True)

    df = pd.DataFrame(rows)
    print(f"\nanalyzed windows: {n_win}, raw setups (Flag + MA50 過關): {len(df)}")
    if df.empty:
        print("冇樣本"); return
    filled = df[df["filled"] == 1].copy()
    print(f"limit 成交: {len(filled)}  ({len(df) - len(filled)} 冇觸及 limit)")

    def report(sel, label):
        v = sel["pnl_r"].dropna().astype(float)
        if len(v) == 0:
            print(f"  {label:34} n=0"); return
        m = v.mean()
        sd = v.std(ddof=1) if len(v) > 1 else 0.0
        t = m / (sd / math.sqrt(len(v))) if sd > 0 else 0.0
        print(f"  {label:34} n={len(v):4}  sumR {v.sum():+8.2f}  meanR {m:+.3f}  "
              f"t {t:+5.2f}  勝率 {(v > 0).mean() * 100:5.1f}%")

    print("\n=== 同一批 setup, 唔同 gate ===")
    report(filled, "全部 (冇 RR gate)")
    report(filled[filled["rr1"] >= 1.2], "OLD: rr1(TP1 單段) >= 1.2")
    report(filled[filled["blended"] >= 1.2], "NEW: blended(3 段) >= 1.2")

    print("\n=== 敏感度: blended 門檻 ===")
    for th in (0.9, 1.0, 1.1, 1.2, 1.3, 1.5):
        report(filled[filled["blended"] >= th], f"blended >= {th}")

    print("\n=== 敏感度: 尾倉假設 (門檻固定 1.2) ===")
    for tv in (0.0, 0.5, 1.0):
        b = (filled["rr1"] + filled["rr2"].fillna(filled["rr1"]) + tv) / 3.0
        report(filled[b >= 1.2], f"blended(tail={tv}) >= 1.2")

    print("\n=== 新 gate 額外放行嘅單 (OLD 擋 / NEW 放) ===")
    extra = filled[(filled["rr1"] < 1.2) & (filled["blended"] >= 1.2)]
    report(extra, "額外單")
    if len(extra):
        print(f"    額外單 os: 平均 rr1 {extra['rr1'].mean():.2f} / blended {extra['blended'].mean():.2f}"
              f" / 成交率 {len(extra) / max(1, len(df[df['rr1'] < 1.2])) * 100:.0f}%")

    out = os.path.join(REPO, f"btc_rr_gate_validation_cost{COST_PCT}.json")
    df.to_json(out, orient="records")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
