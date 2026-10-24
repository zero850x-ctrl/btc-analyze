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
# DeepSeek minor (2026-10-24): sys.path 口徑統一 —— 其他 import 全部用
# absolute importlib + REPO，呢度都插 REPO 等 `python -m` / 唔同 cwd 都 work。
if REPO not in sys.path:
    sys.path.insert(0, REPO)
spec = importlib.util.spec_from_file_location("analyze_v3", os.path.join(REPO, "analyze_v3.py"))
av3 = importlib.util.module_from_spec(spec); spec.loader.exec_module(av3)
spec2 = importlib.util.spec_from_file_location("paper_trade_xau", os.path.join(REPO, "paper_trade.py"))
pt = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(pt)

import btc_engine as be  # btc_engine 唔 import backtest_btc, 唔會 cycle
# (GLM R1: 呢個前提寫低，免日後有人加返轉頭)。btc_engine 經 importlib
# 讀 analyze_v3，backtest 又 load 一份 —— 兩個 av3 係唔同 instance
# (module-level state 唔共享；今次行為無問題，實跑過)。

MAX_HOLD_BARS = 96          # 96 × 30min = 48h 最多持倉
STEP_BARS = 48              # 每 24h 抽一個樣本
WARMUP = 500                # 分析窗口最少 bars
COST_PCT = 0.001            # 來回手續費+滑點 ~0.1%

# Vendor pin (ENGINE_XAU_PIN.md): 本 backtest 讀嘅引擎版本。
ENGINE_PIN = "70c362031987bb1cca6ced49a3b7abf544dfc6c9"


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
    all_gate_skips = {}   # 跨 windows 累加: 被 live gate 擋走嘅原因 → 數
    n_raw_setups = 0
    n_no_live_entry = 0   # 過 gate 但缺 live entry/tp (DeepSeek R1: 唔靜默 fallback)
    n_bad_risk = 0        # risk<=0 (provenance invariant: raw=sim+skips+entries+risk)
    n_no_ma50 = 0         # ma50 讀唔到 (Haiku P1: 唔靜默降級, 計數)
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
        # ── 2026-10-24 (feat/xau-engine-sync-c): 回測 mirror live gate ──
        # 之前直接 loop setups，只擋 SL floor；live 經 btc_filter_setups
        # (RR≥1.2 + pattern + MA50 + SL floor)，回測 trade 緊 live 唔會 trade 嘅單 =
        # XAU PR#52 修之前同一個病。而家經同一個 filter，
        # fidelity 修正，唔係 alpha。diff_check 俾 OK（歷史無交易所價差），
        # ma50 用 window 嘅 m30 MA50（跟 live 同一個口徑）。
        # Grok R1 實證: filter 內 266-274 行做 SL floor (min_stop_dist=0.8×atr,
        # 寫 sl_floor_applied) —— live 淨係喺 filter 做，filter 外冇第二個，
        # 所以舊版 backtest 自家 SL floor 刪得 (而家用 filter 寫好嘅 btc_stop)。
        # 被擋嘅計入 gate_skips，唔靜默 drop。
        # Haiku P1 (2026-10-24): ma50 讀唔到唔可以靜默降級做 None
        # (None = 跳過 MA50 檢查 = gate 唔等於 live) —— 計數，等 provenance 對數。
        try:
            ma50_w = float(df_a["MA50"].iloc[-1]) if "MA50" in df_a.columns else None
        except Exception:
            ma50_w = None
        if ma50_w is None or not np.isfinite(ma50_w):
            n_no_ma50 += 1
        live_ok = be.btc_filter_setups(
            setups, atr, px, {"status": "OK", "diff_pct": 0.0}, ma50=ma50_w)
        n_raw_setups += len(setups)
        for _s in setups:
            _r = _s.get("_gate_skip")
            if _r:
                all_gate_skips[_r] = all_gate_skips.get(_r, 0) + 1
        for s in live_ok:
            side = "SELL" if "SELL" in str(s.get("direction", "")) else "BUY"
            # 用 filter 寫好嘅 live 口徑 (btc_entry/limit/stop/tp)，唔自己重計 ——
            # 之前用 zone 下緣 entry + 自家 SL floor，同 live (limit_px) 兩套數字
            # (09-14 實證差 0.10-0.17%，RR 系統性樂觀)。filter 已保證非 None。
            # Kimi R1 (2026-10-24): 移除 `or px` 靜默 fallback —— 缺 live entry
            # 就 skip + 計數，唔扮有數。
            # GLM R1: 價格欄位唔用 `or` (0.0 會 fallback) —— 顯式 is not None。
            _limit = s.get("btc_limit_px")
            _entry = s.get("btc_entry")
            entry = _limit if _limit is not None else _entry
            stop = s.get("btc_stop")
            tp1 = s.get("btc_tp1")
            tp2 = s.get("btc_tp2")
            # DeepSeek R1 (2026-10-24): tp1/tp2 都要有 guard —— filter 只保證
            # entry/stop 非 None，tp 係 None 會令 _simulate_staged_exit 爆。
            if stop is None or entry is None or tp1 is None or tp2 is None:
                n_no_live_entry += 1
                continue
            risk = abs(entry - stop)
            if risk <= 0:
                n_bad_risk += 1
                continue
            # 模擬: 由 ts 之後嘅 bars 行
            fwd = bars.iloc[i + 1: i + 1 + MAX_HOLD_BARS]
            # Kimi R1: fill 模型限制 —— entry_fill 用 signal 當時市價 (market_at_signal)，
            # risk 用 live limit 口徑計 → R 雙向失真 (例: BUY limit 100/stop 99/fill 100.5
            # → stop 損係 -1.5R 唔係 -1R)。而且 limit 單假設必成交 (冇等觸價)。
            # Haiku P0 (2026-10-24): 唔只「唔可比」，係 r_valid=false —— 個 R 本身
            # 有方向性偏差，唔可以用於決策。完整 limit-fill simulator 另開 PR。
            entry_fill = px  # fill_model="market_at_signal" (簡化, 見上)
            fill_model = "market_at_signal"  # r_valid=false (唔係淨 r_live_comparable)
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
                # Grok R1: 每行帶 fill_model —— 唔好只靠旁邊個 json，
                # CSV 單獨流出都會知個 R 咩口徑。
                "fill_model": fill_model,
                "r_valid": False,
            })
        i += STEP_BARS

    df_r = pd.DataFrame(results)
    print(f"\nanalyzed windows: {n_analyzed}, raw setups: {n_raw_setups}, "
          f"simulated (過 live gate): {len(df_r)}, 缺 live entry/tp skip: {n_no_live_entry}, "
          f"risk<=0 skip: {n_bad_risk}, ma50缺: {n_no_ma50}")
    # Grok R1: stdout 警告 —— 只讀 log 會當 meanR 可用。
    # r_valid=false: 個 R 本身有方向性偏差，唔可以用於決策 (Haiku P0)。
    print("⚠️ r_valid=false (fill_model=market_at_signal): R 同 live 唔同口徑，"
          "唔可以用於決策；只證明接線 (gate mirror + provenance)。")
    # gate 擋咗幾多 (fidelity 證據: 回測 trade 緊 live 會 trade 嘅子集)
    if all_gate_skips:
        print(f"\n=== live gate 擋走 (唔入回測) ===")
        for _r, _n in sorted(all_gate_skips.items(), key=lambda x: -x[1])[:8]:
            print(f"  {_r}: {_n}")
    # provenance 先寫 (Kimi R4): 「全部畀 gate 擋走」正正係最值得留 provenance
    # 嘅 run —— 唔可以早退唔寫。r_valid=false (Haiku P0) —— 唔可以用於決策。
    import hashlib, re
    _actual_sha = hashlib.sha256(
        open(os.path.join(REPO, "analyze_v3.py"), "rb").read()).hexdigest()
    # Haiku P2: provenance sha 係讀工作樹 —— 同 pin 檔交叉檢查，
    # 唔一致就 abort，唔好寫個錯 sha 落去呃人。
    _pin_txt = open(os.path.join(REPO, "ENGINE_XAU_PIN.md")).read()
    _m = re.search(r"analyze_v3\.py sha256: ([0-9a-f]{16,64})", _pin_txt)
    _pin_sha = _m.group(1) if _m else ""
    if not _pin_sha or not _actual_sha.startswith(_pin_sha[:16]):
        print(f"⛔ 工作樹 analyze_v3.py ({_actual_sha[:12]}…) 同 ENGINE_XAU_PIN.md "
              f"({(_pin_sha[:12] or '?')}…) 對唔上 —— 唔跑。先更新 pin + ENGINE_PIN。")
        return
    prov = {
        "engine_pin": ENGINE_PIN,
        "engine_sha256": _actual_sha,
        "engine_pin_match": True,
        "bars": f"{bars['datetime'].iloc[0]} -> {bars['datetime'].iloc[-1]}",
        "analyzed_windows": n_analyzed,
        "raw_setups": n_raw_setups,
        "simulated": len(df_r),
        "gate_skips": all_gate_skips,
        "no_live_entry_skipped": n_no_live_entry,
        "bad_risk_skipped": n_bad_risk,
        "no_ma50_windows": n_no_ma50,
        "fill_model": "market_at_signal",
        "r_valid": False,   # Haiku P0: R 本身有方向性偏差, 唔可以用於決策
        "r_live_comparable": False,
        "run_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    json.dump(prov, open(os.path.join(REPO, "btc_backtest_provenance.json"), "w"),
              ensure_ascii=False, indent=1)
    print("saved btc_backtest_provenance.json")
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
