#!/usr/bin/env python3
"""equity_sim.py — 最後一步: long-only 日線趨勢跟隨 vs Buy&Hold, 邊個好?

已知 (2026-10-01):
  - 日線趨勢跟隨: LONG 11/11 資產正 (中位 +0.250), block bootstrap CI [+0.167,+0.274]
  - SHORT 冇 edge (+0.011, CI 含 0, t 0.34) → **賺錢全部來自 long**
  - 即係「跨資產一致」嘅來源係 crypto 長期上升 (beta), 唔係 pure alpha
  - MA200 之上 meanR +0.187 vs 之下 +0.039 → 牛市 regime 貢獻大部分

  → 所以問題變成: **long-only trend following 追唔追得上直接揸 (B&H)?**
    若跑唔贏 B&H, 就冇理由用 (直接揸更簡單)。
    若 DD 細好多 / Sharpe 高 → 有價值 (風險調整後)。

模擬:
  單槽 (同時 1 倉), long-only, 進場 = 信號 bar 下一根 open
  出場 = SL 3×ATR / trail 2×ATR 或 tp3r
  倉位 = 每筆風險固定 % of equity (複利), 來回成本 0.2%
  對比: 同期 B&H

  跑: (a) BTC 單資產  (b) 多資產等權組合 (每資產獨立 1 倉)

用法: python3 equity_sim.py [1d|4h] [risk_pct]
"""
import glob
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import strategy_sweep as ss
import pooled_validation as pv

OUT = os.path.expanduser("~/.hermes/reports")


def equity_curve(trades, df, half=None, risk=0.01, start=1.0):
    """交易序列 → 複利 equity。每筆風險 = risk × 當前 equity。"""
    eq = [start]
    for t in trades:
        eq.append(eq[-1] * (1 + risk * t["r"]))
    return np.array(eq)


def maxdd(eq):
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1).min())


def cagr(eq, n_bars, bar_per_year):
    yrs = n_bars / bar_per_year
    if yrs <= 0 or eq[-1] <= 0:
        return float("nan")
    return float((eq[-1] / eq[0]) ** (1 / yrs) - 1)


def sharpe_trade(trades):
    v = np.array([t["r"] for t in trades], float)
    if len(v) < 3 or v.std(ddof=1) == 0:
        return float("nan")
    return float(v.mean() / v.std(ddof=1) * math.sqrt(len(v)))


def run_asset(path, iv, risk=0.01, long_only=True):
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.reset_index(drop=True)
    ss.COST, ss.SL_MULT, ss.TRAIL_MULT, ss.TP_R, ss.MAX_HOLD = (
        pv.COST, pv.SL_MULT, 2.0, 3.0, pv.MAX_HOLD[iv])
    atr = ss.atr_series(df)
    # 用 momentum(20) 做代表 (n 最足、跨資產一致)
    sigs = [s for s in ss.sig_momentum(df, atr, 20)
            if (not long_only or s[1] == "BUY")]
    trades = ss.run_single_slot(df, atr, sigs, "trail")
    if not trades:
        return None
    eq = equity_curve(trades, df, risk=risk)
    bpy = {"30m": 365 * 48, "4h": 365 * 6, "1d": 365}[iv]
    # B&H equity = 真實收盤價序列 (唔可以只用 2 點, 否則 maxDD 永遠 0)
    bh_eq = (df["close"] / df["close"].iloc[0]).values.astype(float)
    # 以「等 maxDD」放大策略風險 → 可比嘅 CAGR
    dd_bh = abs(maxdd(bh_eq))
    dd_s = abs(maxdd(eq))
    scale = (dd_bh / dd_s) if dd_s > 1e-9 else float("nan")
    eq_scaled = equity_curve(trades, df, risk=risk * scale) if np.isfinite(scale) else None
    return {"n": len(trades), "eq": eq, "bh_eq": bh_eq,
            "cagr": cagr(eq, len(df), bpy),
            "bh_cagr": cagr(bh_eq, len(df), bpy),
            "dd": maxdd(eq), "bh_dd": maxdd(bh_eq),
            "eq_scaled": eq_scaled, "scale": scale,
            "cagr_scaled": (cagr(eq_scaled, len(df), bpy)
                            if eq_scaled is not None else float("nan")),
            "dd_scaled": (maxdd(eq_scaled) if eq_scaled is not None else float("nan")),
            "meanR": float(np.mean([t["r"] for t in trades])),
            "sharpe": sharpe_trade(trades),
            "trades_per_year": len(trades) / (len(df) / bpy),
            "total": float(eq[-1] / eq[0] - 1),
            "bh_total": float(bh_eq[-1] / bh_eq[0] - 1),
            "bars": len(df),
            "start": str(df["datetime"].iloc[0].date()),
            "end": str(df["datetime"].iloc[-1].date())}


def main():
    iv = sys.argv[1] if len(sys.argv) > 1 else "1d"
    risk = float(sys.argv[2]) if len(sys.argv) > 2 else 0.01
    files = pv.discover(iv)
    print("=" * 104)
    print(f"Long-only 日線趨勢跟隨 vs Buy&Hold — interval={iv}  每筆風險 {risk * 100:.1f}% equity")
    print(f"策略: momentum(20) + trail 2×ATR  單槽  成本 {pv.COST * 100:.2f}%")
    print("=" * 104)

    res = {}
    for sym, p in sorted(files.items()):
        r = run_asset(p, iv, risk=risk, long_only=True)
        if r:
            res[sym] = r

    print(f"\n  {'資產':>6} {'期間':>22} | {'n':>4} {'meanR':>7} {'/年':>5} | "
          f"{'策略CAGR':>9} {'B&H CAGR':>9} | {'策略DD':>8} {'B&H DD':>8} | "
          f"{'等DD後CAGR':>11} | 勝")
    for sym, r in res.items():
        better = r["cagr_scaled"] > r["bh_cagr"]
        print(f"  {sym:>6} {r['start']}→{r['end']} | {r['n']:>4} {r['meanR']:>+7.3f} "
              f"{r['trades_per_year']:>5.1f} | "
              f"{r['cagr'] * 100:>+8.1f}% {r['bh_cagr'] * 100:>+8.1f}% | "
              f"{r['dd'] * 100:>+7.1f}% {r['bh_dd'] * 100:>+7.1f}% | "
              f"{r['cagr_scaled'] * 100:>+10.1f}% | {'✅' if better else '❌'}")

    wins = sum(1 for r in res.values() if r["cagr_scaled"] > r["bh_cagr"])
    print(f"\n  等 maxDD 放大後, 策略 CAGR 勝 B&H 嘅資產: {wins}/{len(res)}")
    print("  (放大倍數 = B&H maxDD / 策略 maxDD, 即用槓桿/倉位令兩者風險相同)")
    for sym, r in list(res.items())[:3]:
        print(f"    {sym}: 放大 {r['scale']:.1f}×  → 策略 DD {r['dd_scaled'] * 100:+.1f}% "
              f"vs B&H {r['bh_dd'] * 100:+.1f}%,  CAGR {r['cagr_scaled'] * 100:+.1f}% "
              f"vs B&H {r['bh_cagr'] * 100:+.1f}%")

    # ── 等權組合 ────────────────────────────────────────────────
    print("\n" + "=" * 104)
    print("多資產等權組合 (各資產曲線平均)")
    print("=" * 104)
    L = min(len(r["eq_scaled"]) for r in res.values()
            if r["eq_scaled"] is not None and len(r["eq_scaled"]) > 2)
    curves = np.array([r["eq_scaled"][:L] for r in res.values()
                       if r["eq_scaled"] is not None and len(r["eq_scaled"]) >= L])
    comb = curves.mean(axis=0)
    btc = res.get("BTC")
    print(f"  等權組合 ({len(curves)} 資產, 等 maxDD 放大): 總回 "
          f"{comb[-1] / comb[0] - 1:+.0%}  maxDD {maxdd(comb):+.1%}")
    if btc:
        print(f"  BTC B&H 同期         :  總回 {btc['bh_total']:+.1%}  "
              f"maxDD {btc['bh_dd']:+.1%}  CAGR {btc['bh_cagr']:+.1%}")
        print(f"  BTC 策略 (long-only) :  總回 {btc['total']:+.1%}  "
              f"maxDD {btc['dd']:+.1%}  CAGR {btc['cagr']:+.1%}")
        ra = abs(btc["bh_dd"]) / max(abs(btc["dd"]), 1e-9)
        print(f"\n  風險調整: 策略 maxDD 係 B&H 嘅 1/{ra:.1f}"
              f"  ({abs(btc['dd']) * 100:.1f}% vs {abs(btc['bh_dd']) * 100:.1f}%)")
        print(f"  Calmar (CAGR/|maxDD|): 策略 {btc['cagr'] / max(abs(btc['dd']), 1e-9):.2f}"
              f"   B&H {btc['bh_cagr'] / max(abs(btc['bh_dd']), 1e-9):.2f}")

    print("\n" + "=" * 104)
    print("判讀")
    print("=" * 104)
    if btc:
        if btc["cagr"] > btc["bh_cagr"]:
            print("  ✅ 策略 CAGR 勝 B&H → 有獨立價值")
        elif abs(btc["dd"]) < abs(btc["bh_dd"]) * 0.6:
            print("  ⚠️ 策略 CAGR 輸 B&H, 但 maxDD 細好多 → 風險調整後可能仍有價值")
            print("     (要同 60/40 再平衡比較 — 後者 Sharpe 1.18 / CAGR 48%)")
        else:
            print("  ❌ 策略 CAGR 輸 B&H 而且 DD 冇明顯改善 → **直接揸更好**")
    print("\n  ⚠️ 提醒: SHORT 冇 edge 已知 → 呢個係 long-only, 本質上有 beta 暴露,")
    print("     唔係市場中性 alpha。要同 60/40 再平衡 (Sharpe 1.18) 直接比較。")


if __name__ == "__main__":
    main()
