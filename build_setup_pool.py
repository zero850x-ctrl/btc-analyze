#!/usr/bin/env python3
"""build_setup_pool.py — 抽 engine setup pool 做快取, 供設計 sweep 用.

輸出每個 setup: ts / side / limit_px / raw_stop / atr / tp1 / tp2 / family / fill_idx
(原始值, 唔加任何 gate) — sweep 時可以任意重算 SL 闊度 / 出場結構.

⚠️ look-ahead 防護: fill_idx 只喺 forward bars 真係觸及 limit_px 才計 (同 production
limit 進場一致)。seed_dt = 觸及嗰根 bar, 模擬由下一根開始。

用法: python3 build_setup_pool.py [days] [step_bars]
"""
import importlib.util
import json
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
be = _load("btc_engine", os.path.join(REPO, "btc_engine.py"))
fbb = _load("fetch_btc_bars", os.path.join(REPO, "fetch_btc_bars.py"))

WARMUP = 1000
HORIZON = 96          # 48h 內要成交


def load_bars(days):
    csv_p = os.path.expanduser("~/.hermes/reports/btc_bars_30m.csv")
    if os.path.exists(csv_p):
        df = pd.read_csv(csv_p)
        df["datetime"] = pd.to_datetime(df["datetime"])
        span = (df["datetime"].max() - df["datetime"].min()).days
        if span >= days:
            print(f"[bars] 本地 CSV {len(df)} 根 ({span} 日)")
            return df.reset_index(drop=True)
    print(f"[bars] 下載 {days} 日...")
    rows = fbb.fetch("30m", days)
    df = pd.read_csv(fbb.save(rows, "30m"))
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.reset_index(drop=True)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 540
    step = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    bars = load_bars(days)
    ma = bars["close"].rolling(50).mean()
    print(f"[bars] {len(bars)} 根  {bars['datetime'].iloc[0]} → {bars['datetime'].iloc[-1]}  step={step}")

    pool = []
    i, n_win = WARMUP, 0
    while i < len(bars) - HORIZON - 5:
        window = bars.iloc[max(0, i - WARMUP):i + 1].reset_index(drop=True)
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
        except Exception:
            i += step
            continue
        n_win += 1
        ts = str(window["datetime"].iloc[-1])
        ma50 = float(ma.iloc[i]) if i < len(ma) and np.isfinite(ma.iloc[i]) else None

        for s in setups:
            side = "SELL" if "SELL" in str(s.get("direction", "")) else "BUY"
            entry = be._parse_setup_level(s.get("entry_zone"))
            if entry is None:
                entry = be._parse_setup_level(s.get("entry_trigger"))
            limit_px = be.parse_entry_limit(s.get("entry_trigger"), s.get("entry_zone")) or entry
            stop = be._parse_setup_level(s.get("stop_loss"))
            tp1 = be._parse_setup_level(s.get("tp1"))
            tp2 = be._parse_setup_level(s.get("tp2"))
            if entry is None or stop is None or not np.isfinite(stop) or not tp1:
                continue
            fam = be.setup_family(s)
            # forward 觸及 limit
            fwd = bars.iloc[i + 1: i + 1 + HORIZON]
            if fwd.empty:
                continue
            touched = fwd[fwd["low"] <= limit_px] if side == "BUY" else fwd[fwd["high"] >= limit_px]
            fill_idx = int(bars.index.get_loc(touched.index[0])) if len(touched) else None
            pool.append({
                "ts": ts, "side": side, "family": fam, "pattern": str(s.get("pattern", "?"))[:28],
                "aligned_daily": 1 if (side == "BUY" and dt.get("trend") == "BULLISH")
                or (side == "SELL" and dt.get("trend") == "BEARISH") else 0,
                "entry": round(float(entry), 2), "limit_px": round(float(limit_px), 2),
                "raw_stop": round(float(stop), 2), "atr": round(atr, 2),
                "tp1": round(float(tp1), 2), "tp2": round(float(tp2), 2) if tp2 else None,
                "ma50_ext_pct": round((entry / ma50 - 1) * 100, 3) if ma50 else None,
                "win_idx": i, "fill_idx": fill_idx,
            })
        i += step
        if n_win % 200 == 0:
            print(f"  ...{n_win} windows, pool {len(pool)}", flush=True)

    out = os.path.join(REPO, "setup_pool.json")
    with open(out, "w") as f:
        json.dump({"days": days, "step": step, "n_windows": n_win,
                   "bars_start": str(bars["datetime"].iloc[0]),
                   "bars_end": str(bars["datetime"].iloc[-1]),
                   "bars": len(bars), "pool": pool}, f)
    filled = [p for p in pool if p["fill_idx"] is not None]
    print(f"\nwindows {n_win}  pool {len(pool)}  成交 {len(filled)}")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
