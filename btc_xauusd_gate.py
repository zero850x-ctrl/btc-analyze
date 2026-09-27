#!/usr/bin/env python3
"""btc_xauusd_gate.py — XAUUSD 主力方法 (形態 + 多層對齊 + R:R 結構) 套落 BTC

用戶 2026-09-27 問:「BTC 用唔用 XAUUSD logic」— 要測嘅係主力方法,
唔係 S3 反彈附屬信號。

XAUUSD 主力 gate (analyze_v3.cron_push_eligible, 2026-09-24 版):
  1. kline_confirmed                    (K 線確認)
  2. quality in (OK, GOOD)              (R:R 結構)
  3. counter_trend_severity == ALIGNED  (多層對齊: daily + H1)
  4. TP1 / SL 必須存在                   (唔可以有裸倉)
  5. priority: breakout ≤2, 限價模式 ≤3

本 script 用同一引擎 (analyze_v3) 跑 BTC 歷史, 逐層加 gate 睇 meanR 點變。
entry = 訊號 bar 之後可得嘅價 (避免 look-ahead bias)。

用法: python3 btc_xauusd_gate.py [period] [interval]
輸出: ~/.hermes/reports/btc_xauusd_gate_results.json
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
OUT = os.path.expanduser("~/.hermes/reports/btc_xauusd_gate_results.json")


def out_path(interval):
    """按 interval 分開存 — 否則 M30 跑完會蓋掉 1h 結果."""
    return OUT.replace(".json", f"_{interval}.json")

SL_FLOOR_ATR = 0.8
MAX_HOLD_BARS = 96
STEP_BARS = 48
WARMUP = 500
COST_PCT = 0.001
# 對齊 XAUUSD analyze_v3: SPIKE_WINDOW_BARS=4, SPIKE_ATR_MULT=3.0
# (09-08 由 2.0 調高 — 2.0 喺普通趨勢延續都會 fire; 9/4  motivating move ≈9.5 ATR)
SPIKE_ATR_MULT = 3.0
SPIKE_WINDOW_BARS = 4


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


av3 = _load("analyze_v3", "analyze_v3.py")
pt = _load("paper_trade_xau", "paper_trade.py")


def parse_lvl(val):
    if val is None:
        return None
    import re
    if isinstance(val, (int, float)):
        return float(val)
    m = re.search(r"[\d,]+(?:\.\d+)?", str(val).replace(",", ""))
    return float(m.group()) if m else None


def load_bars(period="730d", interval="1h", csv_path=None, ticker="BTC-USD"):
    """同 walkforward_btc.load_bars 一致: 保留原大寫欄 + 加小寫別名.

    ⚠️ av3.analyze_h1_trend / add_indicators 要 Open/High/Low/Close/Volume (大寫),
    只留小寫會令每個窗口都 raise (而 except 會靜默食掉 → 0 樣本假象)。

    csv_path 有值 → 讀 CSV (yfinance 30m 只 60 日唔夠做統計)。
    """
    if csv_path:
        df = pd.read_csv(csv_path)
        df["datetime"] = pd.to_datetime(df["datetime"])
    else:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df.empty:
            raise SystemExit("冇數據")
        df = df.reset_index()
        if "Datetime" in df.columns:
            df = df.rename(columns={"Datetime": "datetime"})
        if "Date" in df.columns and "datetime" not in df.columns:
            df = df.rename(columns={"Date": "datetime"})
        if df["datetime"].dt.tz is not None:
            df["datetime"] = df["datetime"].dt.tz_convert("UTC").dt.tz_localize(None)
    # 兩向都要: yfinance 出大寫 (Open..Volume), CSV 出小寫.
    # av3 內部要 Close/High/Low/Open/Volume; sim 要 open/high/low/close/volume.
    for col in ("close", "high", "low", "open", "volume"):
        cap = col.capitalize()
        if col not in df.columns and cap in df.columns:
            df[col] = df[cap]
        if cap not in df.columns and col in df.columns:
            df[cap] = df[col]
    return df.reset_index(drop=True)


def gate_levels(setup, side, daily_trend, h1_trend):
    """回傳 XAUUSD 主力 gate 逐層結果 (True/False), 由最寬到最嚴。

    XAUUSD cron_push_eligible 嘅 5 個條件, 但 BTC 係 24/7 冇 broker session
    → 略去 time_quality == 'danger' 嗰條 (XAUUSD 專屬)。
    """
    try:
        sev = av3.counter_trend_severity(side, daily_trend, h1_trend)
    except Exception:
        sev = "?"
    q = setup.get("quality")
    prio = setup.get("priority", 99)
    mode = setup.get("entry_mode", "breakout")

    has_tp = bool(setup.get("tp1"))
    has_sl = bool(setup.get("stop_loss"))
    rr_ok = q in ("OK", "GOOD")
    aligned = sev == "ALIGNED"
    prio_ok = prio <= (3 if mode in ("pullback", "boundary", "fib", "fib0786") else 2)

    return {
        "sev": sev,
        "quality": q,
        "priority": prio,
        "mode": mode,
        # 逐層累加 (同 XAUUSD gate 一樣係 AND)
        "L1_naked_ok": has_tp and has_sl,
        "L2_rr_ok": rr_ok,
        "L3_aligned": aligned,
        "L4_prio_ok": prio_ok,
        "GATE_BASE": has_tp and has_sl,
        "GATE_RR": has_tp and has_sl and rr_ok,
        "GATE_ALIGN": has_tp and has_sl and rr_ok and aligned,
        "GATE_FULL": has_tp and has_sl and rr_ok and aligned and prio_ok,
    }


def spike_of(closes_tail, atr, mult=None, win=None):
    """複製 XAUUSD `analyze_v3._post_spike_state` 嘅公式 (純函數).

    ⚠️ BTC repo 嘅 analyze_v3.py 係**舊版**, 冇 `_post_spike_state`
    (post-spike gate 係 XAUUSD repo 2026-09-04 加嘅層, 未同步過嚟)
    → 要自己實作先測得到 nospike。

    公式 (同官方一字不差):
        move = close[-2] - close[-2-win]     # -1 = forming bar, -2 = 最後已收市
        |move| > mult × ATR → spike, 方向 = move 符號
    """
    mult = SPIKE_ATR_MULT if mult is None else mult
    win = SPIKE_WINDOW_BARS if win is None else win
    if not closes_tail or not atr or atr <= 0:
        return None
    if len(closes_tail) < win + 2:
        return None
    move = closes_tail[-2] - closes_tail[-2 - win]
    if abs(move) <= mult * atr:
        return None
    return "down" if move < 0 else "up"


def apply_interval_scaling(interval):
    """M30 每根 bar 30 分鐘 → WARMUP/STEP/MAX_HOLD 要 ×2 保持同樣時間跨度.

    XAUUSD 引擎設計係 M30, 之前用 1h 餵佢係 timeframe mismatch。
    """
    global WARMUP, STEP_BARS, MAX_HOLD_BARS
    if interval == "30m":
        WARMUP, STEP_BARS, MAX_HOLD_BARS = 1000, 96, 192
    else:                      # 1h (default)
        WARMUP, STEP_BARS, MAX_HOLD_BARS = 500, 48, 96
    print(f"  [縮放] interval={interval} → WARMUP={WARMUP} "
          f"STEP={STEP_BARS} MAX_HOLD={MAX_HOLD_BARS} bars")


def run_segment(bars, label, budget_s=900):
    """行 walk-forward, 回傳每筆 setup 嘅 (gate 層結果 + 模擬 R)."""
    import time
    t0 = time.time()
    rows = []
    i = WARMUP
    n_win = 0
    n_err = 0
    while i < len(bars) - MAX_HOLD_BARS - 5:
        if time.time() - t0 > budget_s:
            print(f"  [{label}] ⏱ 時間預算用盡, 停喺 {i}/{len(bars)}")
            break
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
            pts = av3.find_swings_ordered(df_a["High"].values, df_a["Low"].values,
                                          lookback=3, atr=df_a["ATR"].values,
                                          close=df_a["Close"].values)
            patterns = av3.detect_all_patterns(df_a, pts, atr=atr)
            setups = av3.generate_trade_setups(df_a, patterns, pts, dt, px, atr, h1_trend=ht)
            # nospike 診斷: 自算 (BTC repo 嘅 av3 冇 _post_spike_state; 公式
            # 同 XAUUSD 一字不差, 見 spike_of docstring)。唔 mutate setup,
            # 免污染 gate_levels 讀嘅 quality/priority。
            closes_tail = [float(x) for x in window["close"].values[-9:]]
            spike_state = spike_of(closes_tail, atr)
        except Exception as e:
            # 唔可以靜默: 若全部窗口都 raise, 之前會顯示「0 樣本」假象
            if n_err < 3:
                import traceback
                print(f"  ⚠️ window@{i} 失敗: {type(e).__name__}: {e}")
                if n_err == 0:
                    traceback.print_exc()
            n_err += 1
            i += STEP_BARS
            continue
        n_win += 1
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
            if len(fwd) < 2:
                continue
            entry_fill = px * (1 + COST_PCT / 2) if side == "BUY" else px * (1 - COST_PCT / 2)
            sim_bars = fwd[["open", "high", "low", "close", "volume", "datetime"]].copy()
            ts_aware = pd.Timestamp(ts)
            if ts_aware.tzinfo is None:
                ts_aware = ts_aware.tz_localize("UTC")
            sim = pt._simulate_staged_exit(sim_bars, entry_fill, stop, tp1 or 0, tp2 or 0,
                                           side, atr, seed_dt=ts_aware.to_pydatetime(),
                                           data_source="yf")
            pnl = sim.get("pnl_r")
            if pnl is None:
                last = float(fwd["close"].iloc[-1])
                pnl = ((last - entry_fill) / risk) if side == "BUY" else ((entry_fill - last) / risk)
            g = gate_levels(s, side, dt, ht)
            # nospike 診斷欄: closes_tail + atr → 之後可離線掃任何
            # SPIKE_ATR_MULT × SPIKE_WINDOW_BARS 組合 (唔使重跑 backtest)
            # ⚠️ 官方 gate 係 block「同 spike 方向一致」嘅 setup
            g["post_spike"] = bool(
                (spike_state == "down" and side == "SELL")
                or (spike_state == "up" and side == "BUY"))
            g["atr"] = round(float(atr), 4)
            g["ct"] = [round(c, 2) for c in closes_tail]
            rows.append({"ts": str(ts), "side": side, "pattern": str(s.get("pattern", "?"))[:22],
                         "pnl_r": round(float(pnl), 3), "label": label, **g})
        i += STEP_BARS
    print(f"  [{label}] 分析窗口 {n_win} 個 → {len(rows)} 個 setup")
    return rows


def stats(rs):
    n = len(rs)
    if n == 0:
        return None
    mean = sum(rs) / n
    wr = sum(1 for r in rs if r > 0) / n * 100
    if n > 1:
        var = sum((r - mean) ** 2 for r in rs) / (n - 1)
        sd = var ** 0.5
        t = mean / (sd / n ** 0.5) if sd else 0.0
    else:
        sd = t = 0.0
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    return {"n": n, "mean_r": mean, "wr": wr, "t": t, "sd": sd,
            "sum_r": sum(rs), "pf": round(pf, 2),
            "sig": "✅" if mean > 0 and t > 2.0 else ("❌" if mean < 0 and t < -2.0 else "·")}


def line(name, st):
    if not st:
        return f"  {name:22} 冇樣本"
    return (f"  {name:22} n={st['n']:5}  勝率 {st['wr']:5.1f}%  "
            f"meanR {st['mean_r']:+.3f}  t {st['t']:+5.2f}  "
            f"PF {st['pf']:>5}  sumR {st['sum_r']:+7.2f}  {st['sig']}")


GATES = ["GATE_BASE", "GATE_RR", "GATE_ALIGN", "GATE_FULL"]
GATE_DESC = {
    "GATE_BASE": "L1 TP/SL 齊",
    "GATE_RR": "L1+L2 R:R 結構",
    "GATE_ALIGN": "L1+L2+L3 多層對齊",
    "GATE_FULL": "L1+L2+L3+L4 完整 gate",
}


def main():
    period = sys.argv[1] if len(sys.argv) > 1 else "730d"
    interval = sys.argv[2] if len(sys.argv) > 2 else "1h"
    csv_path = sys.argv[3] if len(sys.argv) > 3 else None
    ticker = sys.argv[4] if len(sys.argv) > 4 else "BTC-USD"
    tag = (f"csv:{os.path.basename(csv_path)}" if csv_path else f"{ticker} {period}@{interval}")
    print(f"=== XAUUSD 主力方法 → {ticker} ({tag}) ===\n")
    apply_interval_scaling(interval)
    bars = load_bars(period, interval, csv_path, ticker)
    print(f"bars: {len(bars)}  {bars['datetime'].iloc[0]} → {bars['datetime'].iloc[-1]}\n")

    mid = len(bars) // 2
    all_rows = []
    for seg, sl in (("TRAIN", bars.iloc[:mid]), ("TEST", bars.iloc[mid:])):
        all_rows += run_segment(sl.reset_index(drop=True), seg)

    op = out_path(interval)
    if csv_path:
        op = op.replace(f"_{interval}.json", f"_{interval}_binance.json")
    elif ticker != "BTC-USD":
        safe = ticker.replace("=", "").replace("/", "")
        op = op.replace(".json", f"_{safe}_{interval}.json")
    with open(op, "w") as f:
        json.dump(all_rows, f)
    print(f"\n已存 {len(all_rows)} 筆 → {op}\n")

    for seg in ("TRAIN", "TEST"):
        rows = [r for r in all_rows if r["label"] == seg]
        if not rows:
            continue
        print(f"--- {seg} ---")
        print(line("全部 setup", stats([r["pnl_r"] for r in rows])))
        for g in GATES:
            sub = [r["pnl_r"] for r in rows if r[g]]
            print(line(GATE_DESC[g], stats(sub)))
        print()

    print("--- 全期合併 ---")
    print(line("全部 setup", stats([r["pnl_r"] for r in all_rows])))
    for g in GATES:
        print(line(GATE_DESC[g], stats([r["pnl_r"] for r in all_rows if r[g]])))
    print()

    # 對齊分層 (睇 ALIGNED 係咪真係關鍵)
    print("--- 按 counter_trend_severity 分層 (全期) ---")
    for sev in sorted({r["sev"] for r in all_rows}):
        print(line(f"  {sev}", stats([r["pnl_r"] for r in all_rows if r["sev"] == sev])))
    print()

    # 方向
    print("--- 按方向 (全期) ---")
    for sd in ("BUY", "SELL"):
        print(line(sd, stats([r["pnl_r"] for r in all_rows if r["side"] == sd])))


if __name__ == "__main__":
    main()
