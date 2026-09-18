#!/usr/bin/env python3
"""research_strategies.py — 用統一 exit harness 比較多個 BTC 策略方向.

每個策略都跑 TRAIN(第1年)/TEST(第2年) walk-forward, 同一 exit 結構。
判準: TEST meanR > 0 且 t > 2 (唔靠 TRAIN 揀參數)。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from research_harness import load_bars, add_indicators, simulate, stats, fmt


# ── 策略信號 ────────────────────────────────────────────────
def sig_donchian(d, lookback=20, trend_ma=None, side="both"):
    """突破 N bar 高/低 (momentum)。trend_ma: 只用 MA 方向 filter."""
    L = d["close"].rolling(lookback).max().shift(1)
    S = d["close"].rolling(lookback).min().shift(1)
    out = []
    for i in range(200, len(d)):
        c = d["close"].iloc[i]
        up = c > L.iloc[i]
        dn = c < S.iloc[i]
        if trend_ma:
            ma = d[trend_ma].iloc[i]
            if not np.isfinite(ma):
                continue
            up = up and c > ma
            dn = dn and c < ma
        if up and side in ("both", "buy"):
            out.append((i, "BUY"))
        elif dn and side in ("both", "sell"):
            out.append((i, "SELL"))
    return out


def sig_ma_cross(d, fast=20, slow=50, side="both"):
    """快線穿越慢線 (trend following)."""
    f, s = d[f"ma{fast}"], d[f"ma{slow}"]
    out = []
    for i in range(200, len(d)):
        if not (np.isfinite(f.iloc[i]) and np.isfinite(s.iloc[i])):
            continue
        if not (np.isfinite(f.iloc[i - 1]) and np.isfinite(s.iloc[i - 1])):
            continue
        up = f.iloc[i] > s.iloc[i] and f.iloc[i - 1] <= s.iloc[i - 1]
        dn = f.iloc[i] < s.iloc[i] and f.iloc[i - 1] >= s.iloc[i - 1]
        if up and side in ("both", "buy"):
            out.append((i, "BUY"))
        elif dn and side in ("both", "sell"):
            out.append((i, "SELL"))
    return out


def sig_ma_pullback(d, ma=50, tol=0.01, side="both"):
    """趨勢中回調到 MA 再入 (trend continuation)。"""
    out = []
    for i in range(200, len(d)):
        m = d[f"ma{ma}"].iloc[i]
        m_prev = d[f"ma{ma}"].iloc[i - 5] if i >= 205 else np.nan
        c = d["close"].iloc[i]
        if not np.isfinite(m) or not np.isfinite(m_prev):
            continue
        near = abs(c - m) / m <= tol
        if not near:
            continue
        rising = m > m_prev
        falling = m < m_prev
        if rising and c > m and side in ("both", "buy"):
            out.append((i, "BUY"))
        elif falling and c < m and side in ("both", "sell"):
            out.append((i, "SELL"))
    return out


def sig_rsi_revert(d, lo=30, hi=70, side="both"):
    """RSI 極值反向 (mean reversion)."""
    out = []
    for i in range(200, len(d)):
        r = d["rsi"].iloc[i]
        if not np.isfinite(r):
            continue
        if r < lo and side in ("both", "buy"):
            out.append((i, "BUY"))
        elif r > hi and side in ("both", "sell"):
            out.append((i, "SELL"))
    return out


STRATS = {
    "donchian20_both": lambda d: sig_donchian(d, 20),
    "donchian20_buy": lambda d: sig_donchian(d, 20, side="buy"),
    "donchian20_sell": lambda d: sig_donchian(d, 20, side="sell"),
    "donchian20_ma200_buy": lambda d: sig_donchian(d, 20, "ma200", "buy"),
    "donchian20_ma200_sell": lambda d: sig_donchian(d, 20, "ma200", "sell"),
    "donchian50_both": lambda d: sig_donchian(d, 50),
    "ma_cross_20_50_both": lambda d: sig_ma_cross(d),
    "ma_cross_20_50_sell": lambda d: sig_ma_cross(d, side="sell"),
    "ma_pullback50_buy": lambda d: sig_ma_pullback(d, 50, side="buy"),
    "ma_pullback50_sell": lambda d: sig_ma_pullback(d, 50, side="sell"),
    "rsi_revert_both": lambda d: sig_rsi_revert(d),
}


def main():
    bars = load_bars()
    d = add_indicators(bars)
    mid = len(d) // 2
    print(f"bars={len(d)} {d['datetime'].iloc[0]} → {d['datetime'].iloc[-1]}")
    print(f"TRAIN = 前 {mid} bars | TEST = 後 {len(d) - mid} bars\n")

    rows = []
    for name, fn in STRATS.items():
        sig = fn(d)
        s_tr = stats(simulate(d.iloc[:mid].reset_index(drop=True),
                              [s for s in sig if s[0] < mid]), "TRAIN")
        s_te = stats(simulate(d.iloc[mid:].reset_index(drop=True),
                              [(s[0] - mid, s[1]) for s in sig if s[0] >= mid]), "TEST")
        rows.append((name, s_tr, s_te))

    print(f"{'策略':<24} {'TRAIN n':>7} {'meanR':>8} {'t':>6} | {'TEST n':>6} {'meanR':>8} {'t':>6} {'sumR':>7}")
    print("-" * 88)
    for name, tr, te in rows:
        trn, trm, trt = tr.get("n", 0), tr.get("meanR", 0), tr.get("t", 0)
        ten, tem, tet = te.get("n", 0), te.get("meanR", 0), te.get("t", 0)
        tes = te.get("sumR", 0)
        flag = " ⭐" if (ten >= 30 and tem > 0 and tet > 2) else ""
        print(f"{name:<24} {trn:>7} {trm:>+8.3f} {trt:>+6.2f} | {ten:>6} {tem:>+8.3f} {tet:>+6.2f} {tes:>+7.1f}{flag}")

    print("\n=== 詳細 ===")
    for name, tr, te in rows:
        print(fmt(tr))
        print(fmt(te))
        print()


if __name__ == "__main__":
    main()
