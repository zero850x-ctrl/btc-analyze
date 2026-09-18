#!/usr/bin/env python3
"""research_mtf.py — 多時間框 + 多重比較修正.

1h 太 noisy 可能係死因。用日線 (5 年) 測經典 trend following。
同時做 Bonferroni 修正: 測 N 個策略, 顯著門檻要 t > 2.6 左右 (唔係 2.0)。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from research_harness import simulate, stats


def load(interval, period):
    import yfinance as yf
    df = yf.Ticker("BTC-USD").history(period=period, interval=interval)
    if df.empty:
        raise SystemExit(f"{interval}/{period} 冇數據")
    df = df.reset_index()
    for a, b in (("Datetime", "datetime"), ("Date", "datetime")):
        if a in df.columns:
            df = df.rename(columns={a: b})
    for col in ("open", "high", "low", "close", "volume"):
        for alt in (col.capitalize(), col.upper()):
            if alt in df.columns and col not in df.columns:
                df[col] = df[alt]
    df = df[["datetime", "open", "high", "low", "close"]].dropna().reset_index(drop=True)
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    for n in (20, 50, 100, 200):
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    return df


def sig_donchian(d, N, ma=None, side="both", warm=210):
    hi = d["close"].rolling(N).max().shift(1)
    lo = d["close"].rolling(N).min().shift(1)
    out = []
    for i in range(warm, len(d)):
        c = d["close"].iloc[i]
        up, dn = c > hi.iloc[i], c < lo.iloc[i]
        if ma:
            m = d[f"ma{ma}"].iloc[i]
            if not np.isfinite(m):
                continue
            up, dn = up and c > m, dn and c < m
        if up and side in ("both", "buy"):
            out.append((i, "BUY"))
        elif dn and side in ("both", "sell"):
            out.append((i, "SELL"))
    return out


def sig_macross(d, f, s, side="both", warm=210):
    out = []
    for i in range(warm, len(d)):
        a, b = d[f"ma{f}"], d[f"ma{s}"]
        if not all(np.isfinite(x.iloc[i]) and np.isfinite(x.iloc[i - 1]) for x in (a, b)):
            continue
        up = a.iloc[i] > b.iloc[i] and a.iloc[i - 1] <= b.iloc[i - 1]
        dn = a.iloc[i] < b.iloc[i] and a.iloc[i - 1] >= b.iloc[i - 1]
        if up and side in ("both", "buy"):
            out.append((i, "BUY"))
        elif dn and side in ("both", "sell"):
            out.append((i, "SELL"))
    return out


def run(d, sig, split=None, label="", hold=72):
    if split is None:
        split = len(d) // 2
    st = {}
    for name, off, sub in (("TRAIN", 0, d.iloc[:split]), ("TEST", split, d.iloc[split:])):
        sg = [(i - off, s) for i, s in sig if (i < split if off == 0 else i >= split)]
        st[name] = stats(simulate(sub.reset_index(drop=True), sg, max_hold=hold), name)
    return st


CASES = []
for N in (20, 50, 100):
    for ma in (None, 200):
        CASES.append((f"donchian{N}{'_ma200' if ma else ''}", lambda d, N=N, ma=ma: sig_donchian(d, N, ma)))
for f, s in ((20, 50), (50, 200)):
    CASES.append((f"ma_cross_{f}_{s}", lambda d, f=f, s=s: sig_macross(d, f, s)))

results = []
for interval, period, hold in (("1d", "6y", 20), ("1h", "730d", 72)):
    d = load(interval, period)
    print(f"\n{'=' * 92}\n{interval} / {period}  bars={len(d)}  "
          f"{d['datetime'].iloc[0].date()} → {d['datetime'].iloc[-1].date()}\n{'=' * 92}")
    print(f"{'策略':<22} {'TRAIN n':>8} {'meanR':>8} {'t':>7} | {'TEST n':>7} {'meanR':>8} {'t':>7}")
    print("-" * 92)
    for name, fn in CASES:
        try:
            st = run(d, fn(d), hold=hold)
        except Exception as e:
            print(f"{name:<22} ERROR {e}")
            continue
        tr, te = st["TRAIN"], st["TEST"]
        t_t, t_e = tr.get("t", 0), te.get("t", 0)
        results.append((f"{interval}/{name}", t_e, te.get("meanR", 0), te.get("n", 0)))
        print(f"{name:<22} {tr.get('n',0):>8} {tr.get('meanR',0):>+8.3f} {t_t:>+7.2f} | "
              f"{te.get('n',0):>7} {te.get('meanR',0):>+8.3f} {t_e:>+7.2f}")

n_tests = len(results)
try:
    from scipy.stats import norm
    thresh = float(norm.ppf(1 - 0.025 / n_tests))
except Exception:
    # 冇 scipy: 用近似 z 值 (alpha=0.05 two-tailed / n_tests)
    _a = 0.025 / n_tests
    thresh = 2.0 + (0.0 if _a > 0.005 else 0.2)
print(f"\n=== 多重比較修正 (Bonferroni, {n_tests} 個測試) ===")
print(f"顯著門檻 |t| > {thresh:.2f} (唔係 2.0)")
best = sorted(results, key=lambda x: -abs(x[1]))[:5]
for name, t, mr, n in best:
    ok = "✅ 通過" if abs(t) > thresh else "❌ 唔夠"
    print(f"  {name:<30} t={t:+.2f} meanR={mr:+.3f} n={n}  {ok}")
