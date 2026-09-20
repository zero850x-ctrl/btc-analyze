#!/usr/bin/env python3
"""research_harness.py — 策略研究用 backtest 引擎 (唔係 production code).

統一 exit 結構 (同 live 3 段一致):
  entry = 訊號 bar 之後嘅 open (避免 look-ahead bias)
  SL    = entry ∓ 1.5×ATR
  TP1   = 1R  (賣 1/3)
  TP2   = 2R  (賣 1/3)
  尾倉 1/3 = TP1 成交後推 breakeven, 之後 ATR trail

任何策略都用同一 exit, 所以 R 可比較。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd


def load_bars(period="730d", interval="1h"):
    import yfinance as yf
    t = yf.Ticker("BTC-USD")
    df = t.history(period=period, interval=interval)
    if df.empty or len(df) < 2000:
        raise SystemExit(f"數據太少: {len(df)}")
    df = df.reset_index()
    if "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "datetime"})
    if "Date" in df.columns and "datetime" not in df.columns:
        df = df.rename(columns={"Date": "datetime"})
    for col in ["open", "high", "low", "close", "volume"]:
        for alt in (col.capitalize(), col.upper()):
            if alt in df.columns and col not in df.columns:
                df[col] = df[alt]
    df = df[["datetime", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)
    return df


def add_indicators(df):
    d = df.copy()
    d["atr"] = (d["high"] - d["low"]).rolling(14).mean()
    for n in (20, 50, 100, 200):
        d[f"ma{n}"] = d["close"].rolling(n).mean()
    delta = d["close"].diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    d["hh20"] = d["high"].rolling(20).max().shift(1)
    d["ll20"] = d["low"].rolling(20).min().shift(1)
    return d


def simulate(bars, signals, tp1_r=1.0, tp2_r=2.0, sl_atr=1.5, trail_atr=1.5,
             max_hold=72, one_at_a_time=True):
    """signals: list of (idx, 'BUY'|'SELL') — 訊號喺 bar idx 收盤確認, idx+1 open 入場.

    one_at_a_time=True (default): 一次最多一倉 — 對應 live 系統 cap 1。
    若允許重疊, 同一時間會開多倉 = 隱形槓桿, 誇大結果 (而且 live 做唔到)。
    """
    res = []
    busy_until = -1
    used = set()
    for idx, side in sorted(signals):
        if idx < 200 or idx + 2 >= len(bars):
            continue
        if one_at_a_time and idx <= busy_until:
            continue
        key = (idx, side)
        if key in used:
            continue
        used.add(key)
        e = float(bars["open"].iloc[idx + 1])
        atr = float(bars["atr"].iloc[idx])
        if not np.isfinite(atr) or atr <= 0:
            continue
        d = 1 if side == "BUY" else -1
        risk = sl_atr * atr
        sl = e - d * risk
        tp1 = e + d * tp1_r * risk
        tp2 = e + d * tp2_r * risk
        q1 = q2 = q3 = 1 / 3
        realized = 0.0
        got1 = False
        trail = sl
        end = min(idx + 1 + max_hold, len(bars) - 1)
        exit_idx = end
        for j in range(idx + 1, end + 1):
            hi, lo = float(bars["high"].iloc[j]), float(bars["low"].iloc[j])
            if d == 1:
                hit_sl, hit1, hit2 = lo <= trail, hi >= tp1, hi >= tp2
            else:
                hit_sl, hit1, hit2 = hi >= trail, lo <= tp1, lo <= tp2
            # SL 先判 (保守: 同一 bar 兩邊都中算 SL)
            if hit_sl:
                exit_idx = j
                break
            if hit2 and q2 > 0:
                realized += q2 * tp2_r
                q2 = 0.0
                q3 = max(q3, 0.0)
                trail = max(trail, tp2) if d == 1 else min(trail, tp2)
            if hit1 and q1 > 0:
                realized += q1 * tp1_r
                q1 = 0.0
                got1 = True
                trail = max(trail, e) if d == 1 else min(trail, e)
            if got1 and q3 > 0:
                a = float(bars["atr"].iloc[j])
                if np.isfinite(a) and a > 0:
                    nxt = hi - trail_atr * a if d == 1 else lo + trail_atr * a
                    trail = max(trail, nxt) if d == 1 else min(trail, nxt)
        # 收尾
        px_end = float(bars["close"].iloc[exit_idx])
        r_end = (px_end - e) / risk * d
        r_end = max(min(r_end, 50), -50)
        remaining = q1 + q2 + q3
        if remaining > 0:
            realized += remaining * r_end
        res.append({"entry_time": bars["datetime"].iloc[idx + 1], "side": side,
                    "pnl_r": round(realized, 4), "bars_held": exit_idx - idx})
        busy_until = exit_idx
    return res


def stats(res, label=""):
    if not res:
        return {"label": label, "n": 0}
    p = np.array([r["pnl_r"] for r in res])
    wins, losses = p[p > 0], p[p <= 0]
    eq = np.cumsum(p)
    peak = np.maximum.accumulate(eq)
    gw, gl = wins.sum() if len(wins) else 0.0, abs(losses.sum()) if len(losses) else 0.0
    # t-stat: meanR / (std/sqrt(n))
    tstat = float(p.mean() / (p.std(ddof=1) / np.sqrt(len(p)))) if len(p) > 1 and p.std(ddof=1) > 0 else 0.0
    return {
        "label": label, "n": len(p),
        "win%": round(float((p > 0).mean()) * 100, 1),
        "meanR": round(float(p.mean()), 4),
        "medianR": round(float(np.median(p)), 3),
        "pf": round(gw / gl, 2) if gl > 0 else 99.0,
        "sumR": round(float(p.sum()), 2),
        "maxDD_R": round(float((peak - eq).max()), 2),
        "t": round(tstat, 2),
    }


def fmt(st):
    if not st.get("n"):
        return f"{st.get('label','')}: 0 單"
    return (f"{st['label']}: n={st['n']} win={st['win%']}% meanR={st['meanR']:+.3f} "
            f"medR={st['medianR']:+.2f} PF={st['pf']} sumR={st['sumR']:+.1f} "
            f"maxDD={st['maxDD_R']:.1f}R t={st['t']:+.2f}")
