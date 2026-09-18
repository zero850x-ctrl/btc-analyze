#!/usr/bin/env python3
"""research_grid.py — Grid trading 回測 (spot 可行).

機制: 喺中心價 ±range% 內設 N 格。價格跌穿格位 → 買入一份;
升穿格位 → 賣出該份。價格離開 range 就重設中心 (跟隨市價)。

特性預期: 橫行市賺錢 (低買高賣), 單邊趨勢市跑輸 B&H (賣早咗 / 買唔返)。
所以關鍵指標 = 相對 B&H 喺「橫行段 vs 趨勢段」嘅表現。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from research_premium import load_daily, metrics


def sim_grid(d, range_pct=0.10, n_grid=20, capital=10000.0, fee=0.001,
             reset=True, per_grid_frac=0.5):
    """每格投入 capital * per_grid_frac / n_grid。起始持一半倉 (標準 grid 做法)。"""
    px0 = float(d["close"].iloc[0])
    per_grid = capital * per_grid_frac / n_grid
    # 起始: 一半資金買入 BTC, 一半現金擺買單
    btc = (capital * 0.5) / px0
    cash = capital * 0.5
    center = px0
    step = center * range_pct * 2 / n_grid
    lo = center - step * n_grid / 2
    grid = [lo + step * i for i in range(n_grid + 1)]
    held = [i < n_grid / 2 for i in range(n_grid + 1)]   # 下半格 = 未買, 上半 = 已買
    eq = []
    trades = 0
    for i in range(len(d)):
        px = float(d["close"].iloc[i])
        # 價格移動: 逐格處理
        for k in range(len(grid)):
            g = grid[k]
            if px <= g and not held[k] and cash >= per_grid:
                btc += per_grid * (1 - fee) / px
                cash -= per_grid
                held[k] = True
                trades += 1
            elif px >= g and held[k] and k < len(grid) - 1 and btc > 0:
                qty = min(per_grid / px, btc)
                btc -= qty
                cash += qty * px * (1 - fee)
                held[k] = False
                trades += 1
        # 出 range → 重設
        if reset and (px < grid[0] or px > grid[-1]):
            center = px
            step = center * range_pct * 2 / n_grid
            lo = center - step * n_grid / 2
            grid = [lo + step * j for j in range(n_grid + 1)]
            held = [False] * (n_grid + 1)
        eq.append(btc * px + cash)
    return np.array(eq) / capital, trades


def sim_grid_vh(d, band=0.10, n_grid=20, fee=0.001):
    """Volatility harvesting: 固定比例 60/40, 每次偏離 target 超過 band 就再平衡."""
    px0 = float(d["close"].iloc[0])
    btc_val = 0.6
    cash = 0.4
    btc = btc_val / px0
    eq = []
    rebs = 0
    for i in range(len(d)):
        px = float(d["close"].iloc[i])
        tot = btc * px + cash
        w = (btc * px) / tot
        if w > 0.6 + band or w < 0.6 - band:
            tgt = tot * 0.6
            diff = tgt - btc * px
            if diff > 0:
                buy = min(diff, cash)
                btc += buy * (1 - fee) / px
                cash -= buy
            else:
                sell = min(-diff, btc * px)
                btc -= sell / px
                cash += sell * (1 - fee)
            rebs += 1
        eq.append(btc * px + cash)
    return np.array(eq), rebs


def main():
    d = load_daily(6)
    print(f"數據: {len(d)} 日  {d['date'].iloc[0].date()} → {d['date'].iloc[-1].date()}")
    print(f"BTC: ${d['close'].iloc[0]:,.0f} → ${d['close'].iloc[-1]:,.0f}  "
          f"({(d['close'].iloc[-1]/d['close'].iloc[0]-1)*100:+.0f}%)\n")

    bh = (d["close"] / d["close"].iloc[0]).values

    cases = [("Grid ±10% 20格", lambda x: sim_grid(x, 0.10, 20)),
             ("Grid ±20% 20格", lambda x: sim_grid(x, 0.20, 20)),
             ("Grid ±5% 10格", lambda x: sim_grid(x, 0.05, 10)),
             ("Grid ±30% 30格", lambda x: sim_grid(x, 0.30, 30)),
             ("VolHarvest 60/40 ±10%", lambda x: sim_grid_vh(x, 0.10)),
             ("VolHarvest 60/40 ±5%", lambda x: sim_grid_vh(x, 0.05))]

    print(f"{'策略':<26} {'淨值':>8} {'CAGR%':>7} {'maxDD%':>8} {'Sharpe':>7} {'vs B&H':>7} {'交易':>6}")
    print("-" * 80)
    r = metrics(bh, bh, "Buy&Hold")
    print(f"{'Buy&Hold':<26} {r['final']:>8.3f} {r['CAGR%']:>7.1f} {r['maxDD%']:>8.1f} "
          f"{r['Sharpe']:>7.2f} {'1.00':>7}")
    for name, fn in cases:
        eq, ntr = fn(d)
        m = metrics(eq, bh, name)
        print(f"{name:<26} {m['final']:>8.3f} {m['CAGR%']:>7.1f} {m['maxDD%']:>8.1f} "
              f"{m['Sharpe']:>7.2f} {m['vs_BH']:>7.2f} {ntr:>6}")

    # 分段: 睇橫行 vs 趨勢
    print("\n=== 分段 (睇 grid 喺餐行 vs 趨勢市表現) ===")
    n = len(d)
    for k in range(4):
        a, b = k * n // 4, (k + 1) * n // 4
        sub = d.iloc[a:b].reset_index(drop=True)
        chg = (sub["close"].iloc[-1] / sub["close"].iloc[0] - 1) * 100
        # 橫行程度: 期內 (max-min)/mean
        rng = (sub["close"].max() - sub["close"].min()) / sub["close"].mean() * 100
        print(f"\n第 {k+1}/4 段 {sub['date'].iloc[0].date()} → {sub['date'].iloc[-1].date()} "
              f"(BTC {chg:+.0f}%, 振幅 {rng:.0f}%)")
        sub_bh = (sub["close"] / sub["close"].iloc[0]).values
        for name, fn in cases[:2] + cases[4:5]:
            eq, ntr = fn(sub)
            m = metrics(eq, sub_bh, name)
            print(f"   {name:<26} 淨值 {m['final']:.3f} CAGR {m['CAGR%']:>+6.1f}% "
                  f"maxDD {m['maxDD%']:>5.1f}% Sharpe {m['Sharpe']:>5.2f} vsBH {m['vs_BH']:.2f}")


if __name__ == "__main__":
    main()
