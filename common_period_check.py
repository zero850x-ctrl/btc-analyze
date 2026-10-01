#!/usr/bin/env python3
"""common_period_check.py — 穩健性: 11 資產期間唔同 (2017-09 ~ 2020-09 開始),
「11/11 正」會唔會只係因為後開始嘅資產避開咗 2018 熊市?

發現: 資產開始日由 2017-09-19 (BTC/ETH) 到 2020-09-22 (AVAX), 差距 3 年。
      AVAX/SOL/DOT 2020 年才上市 → 冇 2018 熊市 → 若高 meanR 集中喺佢哋,
      就唔可以話「跨資產一致」。

做法: 將全部資產限制到**共同期間** (最遲開始日之後) 重跑,
      同全期數字對比。若共同期間仍然 11/11 正 → 結論穩健。

用法: python3 common_period_check.py [1d]
"""
import glob
import math
import os
import re
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import strategy_sweep as ss
import pooled_validation as pv

RNG = np.random.default_rng(20261001)


def load_all(iv):
    OUT = os.path.expanduser("~/.hermes/reports")
    files = glob.glob(os.path.join(OUT, f"btc_bars_{iv}.csv")) + \
            glob.glob(os.path.join(OUT, f"btc_bars_*_{iv}.csv"))
    out = {}
    for f in files:
        b = os.path.basename(f)
        m = re.match(rf"btc_bars_(?:(\w+)_)?{iv}\.csv", b)
        sym = m.group(1).replace("USDT", "") if m and m.group(1) else "BTC"
        d = pd.read_csv(f)
        d["datetime"] = pd.to_datetime(d["datetime"])
        out[sym] = d.reset_index(drop=True)
    return out


def trend_means(df, iv, cut=None):
    """回傳 (pooled_list, per_strategy 平均 list)。cut = 起始日期 (含)。"""
    if cut is not None:
        df = df[df["datetime"] >= cut].reset_index(drop=True)
    if len(df) < 300:
        return None, None
    ss.COST, ss.SL_MULT, ss.TRAIL_MULT, ss.TP_R, ss.MAX_HOLD = (
        pv.COST, pv.SL_MULT, 2.0, 3.0, pv.MAX_HOLD[iv])
    atr = ss.atr_series(df)
    pooled, per_strat = [], []
    for name, fn in pv.TREND:
        sigs = fn(df, atr)
        if not sigs:
            continue
        for ex in ("trail", "tp3r"):
            trades = ss.run_single_slot(df, atr, sigs, ex)
            if len(trades) < 8:
                continue
            rs = [t["r"] for t in trades]
            pooled += rs
            per_strat.append(np.mean(rs))
    if not pooled:
        return None, None
    return pooled, per_strat


def main():
    iv = sys.argv[1] if len(sys.argv) > 1 else "1d"
    data = load_all(iv)
    last_start = max(d["datetime"].iloc[0] for d in data.values())
    print("=" * 100)
    print(f"共同期間穩健性檢查 — {iv}")
    print(f"資產開始日: 最早 {min(d['datetime'].iloc[0] for d in data.values()).date()}"
          f"  最遲 {last_start.date()}")
    print(f"共同期間切割點: {last_start.date()} 之後 (全部資產都有數據)")
    print("=" * 100)

    for label, cut in (("全期 (各自期間)", None), (f"共同期間 ({last_start.date()}+)", last_start)):
        print(f"\n【{label}】")
        print(f"  {'資產':>6} {'n':>6} {'meanR':>9} {'t':>7} | 策略正比例")
        means = {}
        pools = {}
        for sym, d in sorted(data.items()):
            pooled, per = trend_means(d, iv, cut)
            if pooled is None:
                print(f"  {sym:>6}   — 數據不足")
                continue
            v = np.array(pooled)
            sd = v.std(ddof=1)
            t = v.mean() / (sd / math.sqrt(len(v))) if sd > 0 else 0
            means[sym] = v.mean()
            pools[sym] = pooled
            pos = sum(1 for x in per if x > 0)
            print(f"  {sym:>6} {len(pooled):>6} {v.mean():>+9.3f} {t:>+7.2f} | {pos}/{len(per)}")
        mv = np.array(list(means.values()))
        k, n = int((mv > 0).sum()), len(mv)
        p = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
        print(f"  → 正的資產 {k}/{n}   二項 p = {p:.4f}   中位 {np.median(mv):+.3f}")

        # block bootstrap
        syms = list(pools.keys())
        boots = []
        for _ in range(2000):
            pick = RNG.choice(len(syms), size=len(syms), replace=True)
            boots.append(np.mean([np.mean(pools[syms[i]]) for i in pick]))
        lo, hi = np.percentile(boots, [2.5, 97.5])
        print(f"  → Block bootstrap CI: [{lo:+.3f}, {hi:+.3f}]  "
              f"{'✅ 排除 0' if lo > 0 else '❌ 含 0'}")

    print("\n" + "=" * 100)
    print("判讀")
    print("=" * 100)
    print("  若共同期間同樣 11/11 正 + CI 排除 0 → 之前結論穩健, 唔係期間 artifact")
    print("  若共同期間大跌 → 「11/11」依賴後上市資產避開 2018 熊市, 要 downweight")


if __name__ == "__main__":
    main()
