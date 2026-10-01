#!/usr/bin/env python3
"""pooled_validation.py — 決定性驗證: 日線趨勢跟隨嘅正毛利係真 alpha 定 regime 噪音?

背景 (2026-10-01):
  M30 上全部策略負 (成本 0.170R/單)。跑高週期後結論升級:
    - 4h 零成本 18/20 正, 1d 零成本 16/20 正  → **毛利本身係正, 唔止成本問題**
    - oos_cross_asset (BTC/ETH/SOL 1d): 符號一致率 88% (14/16), 二項 p=0.002;
      相關 BTC-ETH r=+0.575, BTC-SOL r=+0.883
    - 一致模式: 趨勢跟隨 (donchian/momentum/ma_cross) 正; 均值回歸 (rsi_mr) 負

  ⚠️ 但 crypto 資產高度相關 (同一 beta), 所以「三個資產一致」比表面弱。
     而且 oos_cross_asset 冇報 TRAIN/TEST per-strategy。

呢個 script 做決定性測試 — **多資產 pooled**:
  1. 每個資產獨立跑, 各自 TRAIN/TEST
  2. Pool 所有資產嘅交易 → 提高統計功效 (n 由 147 → 數千)
  3. Asset-level 檢定: 每個資產嘅 momentum/donchian meanR 係唔係一致正
  4. **Block bootstrap**: 重抽「資產」而唔係重抽「交易」— 處理資產間相關
  5. 對照: 均值回歸 (應一致負) — 作為 sanity check, 證明檢定有力

用法: python3 pooled_validation.py [1d|4h]
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

OUT = os.path.expanduser("~/.hermes/reports")
COST = 0.002
SL_MULT = 3.0
MAX_HOLD = {"30m": 96, "4h": 12, "1d": 10}

# 趨勢跟隨族 (預期正) vs 均值回歸 (預期負, sanity check)
TREND = [
    ("donchian(20)", lambda df, atr: ss.sig_donchian(df, atr, 20)),
    ("donchian(50)", lambda df, atr: ss.sig_donchian(df, atr, 50)),
    ("donchian(100)", lambda df, atr: ss.sig_donchian(df, atr, 100)),
    ("momentum(20)", lambda df, atr: ss.sig_momentum(df, atr, 20)),
    ("momentum(50)", lambda df, atr: ss.sig_momentum(df, atr, 50)),
    ("ma_cross(10,50)", lambda df, atr: ss.sig_ma_cross(df, atr, 10, 50)),
]
MR = [("rsi_mr(30,70)", lambda df, atr: ss.sig_rsi_mr(df, atr, 30, 70))]

RNG = np.random.default_rng(20261001)


def discover(iv):
    """搵所有 <SYM>_<iv> 檔 (btc_bars_<iv>.csv = BTC)。"""
    out = {}
    for p in glob.glob(os.path.join(OUT, f"btc_bars_{iv}.csv")):
        out["BTC"] = p
    for p in glob.glob(os.path.join(OUT, f"btc_bars_*_{iv}.csv")):
        base = os.path.basename(p)[len(f"btc_bars_"):-len(f"_{iv}.csv")]
        out[base.replace("USDT", "")] = p
    return out


def run_one(path, iv, variants):
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.reset_index(drop=True)
    ss.COST, ss.SL_MULT, ss.TRAIL_MULT, ss.TP_R, ss.MAX_HOLD = (
        COST, SL_MULT, 2.0, 3.0, MAX_HOLD[iv])
    atr = ss.atr_series(df)
    half = len(df) // 2
    res = {}
    for name, fn in variants:
        sigs = fn(df, atr)
        if not sigs:
            continue
        for ex in ("trail", "tp3r"):
            trades = ss.run_single_slot(df, atr, sigs, ex)
            if len(trades) < 10:
                continue
            res[f"{name}|{ex}"] = {
                "r": [t["r"] for t in trades],
                "idx": [t["idx"] for t in trades],
                "half": half, "n_bars": len(df),
            }
    return res


def mean_t(v):
    v = np.asarray(v, float)
    if len(v) < 2:
        return float("nan"), float("nan")
    sd = v.std(ddof=1)
    return float(v.mean()), float(v.mean() / (sd / math.sqrt(len(v)))) if sd > 0 else 0.0


def main():
    iv = sys.argv[1] if len(sys.argv) > 1 else "1d"
    files = discover(iv)
    print("=" * 104)
    print(f"多資產 pooled 驗證 — interval={iv}  成本 {COST * 100:.2f}%  SL {SL_MULT}×ATR")
    print(f"資產 ({len(files)}): {', '.join(sorted(files))}")
    print("=" * 104)

    data = {}
    for sym, p in sorted(files.items()):
        r = run_one(p, iv, TREND)
        if r:
            data[sym] = r
    if len(data) < 3:
        print("❌ 資產太少")
        return

    # ── 1. 每個資產嘅 pooled trend meanR (跨策略平均) ──────────────
    print("\n【1】每個資產: 趨勢族 meanR (跨 6 策略 × 2 出場 = 12 組合平均)")
    print(f"  {'資產':>6} {'n_mean':>7} {'meanR':>9} {'t':>7} | {'TRAIN':>9} {'TEST':>9} | 正比例")
    asset_means = {}
    asset_pool = {}
    for sym, r in data.items():
        allr, trr, ter = [], [], []
        poscnt = 0
        for k, v in r.items():
            allr += v["r"]
            h = v["half"]
            trr += [x for x, i in zip(v["r"], v["idx"]) if i < h]
            ter += [x for x, i in zip(v["r"], v["idx"]) if i >= h]
            if np.mean(v["r"]) > 0:
                poscnt += 1
        m, t = mean_t(allr)
        mt, _ = mean_t(trr)
        me, _ = mean_t(ter)
        asset_means[sym] = m
        asset_pool[sym] = allr
        print(f"  {sym:>6} {len(allr) / 12:>7.0f} {m:>+9.3f} {t:>+7.2f} | "
              f"{mt:>+9.3f} {me:>+9.3f} | {poscnt}/12")

    vals = np.array(list(asset_means.values()))
    print(f"\n  跨資產: meanR 中位 {np.median(vals):+.3f}  "
          f"正的資產 {int((vals > 0).sum())}/{len(vals)}  "
          f"範圍 [{vals.min():+.3f}, {vals.max():+.3f}]")
    # 資產層面符號檢定
    k = int((vals > 0).sum())
    n = len(vals)
    p_sign = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    print(f"  符號檢定 (所有資產都正?): {k}/{n},  二項 p = {p_sign:.4f}"
          f"  {'✅ 顯著' if p_sign < 0.05 else '❌ 唔顯著'}")

    # ── 2. Pooled (所有資產所有交易) ──────────────────────────────
    pooled, ptr, pte = [], [], []
    for sym, r in data.items():
        for k2, v in r.items():
            pooled += v["r"]
            h = v["half"]
            ptr += [x for x, i in zip(v["r"], v["idx"]) if i < h]
            pte += [x for x, i in zip(v["r"], v["idx"]) if i >= h]
    m, t = mean_t(pooled)
    mt, tt = mean_t(ptr)
    me, te = mean_t(pte)
    print("\n【2】Pooled (所有資產 × 所有趨勢組合, 注意: 交易間唔獨立!)")
    print(f"  全期: n={len(pooled)}  meanR {m:+.3f}  t {t:+.2f}  win {(np.array(pooled) > 0).mean():.1%}")
    print(f"  TRAIN: n={len(ptr)}  meanR {mt:+.3f}  t {tt:+.2f}")
    print(f"  TEST : n={len(pte)}  meanR {me:+.3f}  t {te:+.2f}")
    print(f"  → {'✅ TRAIN 同 TEST 都正' if mt > 0 and me > 0 else '❌ 唔一致'}")

    # ── 3. Block bootstrap (重抽資產) ─────────────────────────────
    print("\n【3】Block bootstrap — 重抽**資產** (處理資產間相關, 唔可以重抽交易)")
    syms = list(asset_pool.keys())
    boots = []
    for _ in range(2000):
        pick = RNG.choice(len(syms), size=len(syms), replace=True)
        vals_b = []
        for i in pick:
            v = asset_pool[syms[i]]
            if v:
                vals_b.append(np.mean(v))
        boots.append(np.mean(vals_b))
    boots = np.array(boots)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    print(f"  meanR 的 95% CI: [{lo:+.3f}, {hi:+.3f}]   "
          f"{'✅ 排除 0' if lo > 0 else '❌ 包含 0'}")
    print(f"  P(meanR > 0) = {(boots > 0).mean():.3f}")

    # ── 4. Sanity check: 均值回歸應該一致負 ───────────────────────
    print("\n【4】Sanity check — 均值回歸 (rsi_mr) 應一致負, 證明檢定有力")
    mr_means = {}
    for sym, p in sorted(files.items()):
        r = run_one(p, iv, MR)
        if not r:
            continue
        a = []
        for v in r.values():
            a += v["r"]
        if a:
            mr_means[sym] = np.mean(a)
    if mr_means:
        mv = np.array(list(mr_means.values()))
        print(f"  {len(mv)} 個資產:  負的 {int((mv < 0).sum())}/{len(mv)}  "
              f"中位 {np.median(mv):+.3f}  範圍 [{mv.min():+.3f}, {mv.max():+.3f}]")
        k2 = int((mv < 0).sum())
        n2 = len(mv)
        p2 = sum(math.comb(n2, i) for i in range(k2, n2 + 1)) / 2 ** n2
        print(f"  符號檢定: p = {p2:.4f}  "
              f"{'✅ 一致負 (檢定有力)' if p2 < 0.05 else '⚠️ 唔一致 — 檢定力不足'}")
    else:
        print("  (冇 rsi_mr 數據)")

    # ── 5. 判讀 ─────────────────────────────────────────────────
    print("\n" + "=" * 104)
    print("判讀")
    print("=" * 104)
    cond_a = k == n
    cond_b = lo > 0
    cond_c = mt > 0 and me > 0
    print(f"  A. 所有資產 meanR > 0      : {'✅' if cond_a else '❌'} ({k}/{n})")
    print(f"  B. Block bootstrap CI > 0  : {'✅' if cond_b else '❌'} [{lo:+.3f}, {hi:+.3f}]")
    print(f"  C. TRAIN 同 TEST 都正      : {'✅' if cond_c else '❌'}")
    if cond_a and cond_b and cond_c:
        print("\n  🎯 三項全過 → 日線趨勢跟隨有跨資產一致的 edge, 值得進一步研究")
    else:
        print("\n  ⚠️ 未全過 → 未足以支持加落 production")


if __name__ == "__main__":
    main()
