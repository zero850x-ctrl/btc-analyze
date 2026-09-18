#!/usr/bin/env python3
"""research_robust.py — 驗證 ma_pullback_buy 係真 edge 定 beta/噪音.

檢查:
  1. buy-and-hold 對照 (long-only 策略喺升市自然賺錢 = beta 唔係 alpha)
  2. 分 4 段時間穩定性 (真 edge 應該段段都正)
  3. 參數敏感度 (tol / MA / exit 參數)
  4. 只喺跌市/橫行期間表現
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from research_harness import load_bars, add_indicators, simulate, stats, fmt
from research_strategies import sig_ma_pullback


def bh_return(d, i0, i1):
    c0, c1 = float(d["close"].iloc[i0]), float(d["close"].iloc[i1])
    return (c1 / c0 - 1) * 100


def main():
    bars = load_bars()
    d = add_indicators(bars)

    print("=== 1. 分段 (每 25%) ===")
    n = len(d)
    segs = [(0, n // 4), (n // 4, n // 2), (n // 2, 3 * n // 4), (3 * n // 4, n)]
    print(f"{'期間':<26} {'B&H':>8} | {'n':>5} {'win%':>6} {'meanR':>8} {'t':>6} {'sumR':>8}")
    print("-" * 78)
    sig_all = sig_ma_pullback(d, 50, side="buy")
    for a, b in segs:
        sub = d.iloc[a:b].reset_index(drop=True)
        sg = [(i - a, s) for i, s in sig_all if a <= i < b]
        st = stats(simulate(sub, sg), "")
        bh = bh_return(d, a, b - 1)
        t0 = d["datetime"].iloc[a].strftime("%y-%m-%d")
        t1 = d["datetime"].iloc[b - 1].strftime("%y-%m-%d")
        if st.get("n"):
            print(f"{t0}→{t1:<16} {bh:>+7.1f}% | {st['n']:>5} {st['win%']:>5.1f}% "
                  f"{st['meanR']:>+8.3f} {st['t']:>+6.2f} {st['sumR']:>+8.1f}")
        else:
            print(f"{t0}→{t1:<16} {bh:>+7.1f}% | 0 單")

    print("\n=== 2. 參數敏感度 (TEST 段) ===")
    mid = n // 2
    print(f"{'參數':<34} {'n':>5} {'win%':>6} {'meanR':>8} {'t':>6}")
    print("-" * 66)
    for ma in (20, 50, 100, 200):
        for tol in (0.005, 0.01, 0.02):
            sg = [s for s in sig_ma_pullback(d, ma, tol, "buy") if s[0] >= mid]
            st = stats(simulate(d.iloc[mid:].reset_index(drop=True),
                                [(i - mid, s) for i, s in sg]), "")
            if st.get("n", 0) >= 20:
                print(f"MA{ma:<4} tol={tol:<6} (TEST)          {st['n']:>5} {st['win%']:>5.1f}% "
                      f"{st['meanR']:>+8.3f} {st['t']:>+6.2f}")

    print("\n=== 3. exit 參數敏感度 (MA50 tol=1%, TEST) ===")
    sg = [s for s in sig_ma_pullback(d, 50, 0.01, "buy") if s[0] >= mid]
    sub = d.iloc[mid:].reset_index(drop=True)
    sg2 = [(i - mid, s) for i, s in sg]
    print(f"{'SL/TP1/TP2/trail (ATR)':<34} {'n':>5} {'win%':>6} {'meanR':>8} {'t':>6}")
    print("-" * 66)
    for slx in (1.0, 1.5, 2.0, 2.5):
        for tp1r, tp2r in ((1.0, 2.0), (1.5, 3.0), (1.0, 3.0)):
            st = stats(simulate(sub, sg2, tp1_r=tp1r, tp2_r=tp2r, sl_atr=slx), "")
            if st.get("n", 0) >= 20:
                print(f"SL={slx} TP1={tp1r} TP2={tp2r:<5}       {st['n']:>5} {st['win%']:>5.1f}% "
                      f"{st['meanR']:>+8.3f} {st['t']:>+6.2f}")

    print("\n=== 4. 對照: 隨機入場 (同數量, 100 次 bootstrap) ===")
    rng = np.random.default_rng(42)
    means = []
    for _ in range(100):
        idxs = rng.choice(range(200, len(sub) - 3), size=min(len(sg2), 1500), replace=False)
        st = stats(simulate(sub, [(int(i), "BUY") for i in idxs]), "")
        if st.get("n"):
            means.append(st["meanR"])
    means = np.array(means)
    print(f"隨機 BUY meanR: 平均 {means.mean():+.4f} 標準差 {means.std():.4f} "
          f"95% 區間 [{np.percentile(means, 2.5):+.4f}, {np.percentile(means, 97.5):+.4f}]")
    st_real = stats(simulate(sub, sg2), "")
    print(f"ma_pullback_buy TEST meanR = {st_real['meanR']:+.4f} "
          f"→ {'超出隨機區間 ✅' if st_real['meanR'] > np.percentile(means, 97.5) else '喺隨機區間內 ❌ (冇 edge)'}")


if __name__ == "__main__":
    main()
