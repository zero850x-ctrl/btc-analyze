#!/usr/bin/env python3
"""longshort_decomp.py — 決定性拆解: 日線趨勢跟隨賺嘅係 alpha 定 beta?

背景:
  pooled_validation 1d 三項全過 (11/11 資產正, block bootstrap CI [+0.088,+0.146],
  TRAIN +0.155 / TEST +0.079)。但有一個**未解嘅致命疑點**:

    crypto 2017-2026 係長期大牛市 (BTC B&H +2053%)。
    任何「有 long 又有 short」嘅趨勢策略, 喺上升市場自然會賺。
    若 edge 全部來自 long → 只係 **beta** (跟牛市), 唔係 alpha。
    若 long 同 short **都賺** → 真 **trend persistence alpha**。

呢個 script 拆 long / short:
  1. 每資產拆 long-only vs short-only meanR
  2. 對照該資產同期 B&H 回報 (long 賺 + 市場升 = 可疑; short 都賺 = 真)
  3. Block bootstrap 分別對 long / short
  4. 亦拆 quintile: 按市場方向 (MA200 上/下) 分開

同樣做 rsi_mr (均值回歸) 作對照 — 佢應該係 long/short 都負。

用法: python3 longshort_decomp.py [1d|4h]
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

RNG = np.random.default_rng(20261001)


def run_sides(path, iv, variants):
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.reset_index(drop=True)
    ss.COST, ss.SL_MULT, ss.TRAIL_MULT, ss.TP_R, ss.MAX_HOLD = (
        pv.COST, pv.SL_MULT, 2.0, 3.0, pv.MAX_HOLD[iv])
    atr = ss.atr_series(df)
    half = len(df) // 2
    ma200 = df["close"].rolling(200).mean().values
    out = {"LONG": [], "SHORT": [], "LONG_TRAIN": [], "LONG_TEST": [],
           "SHORT_TRAIN": [], "SHORT_TEST": [],
           "UP": [], "DOWN": []}
    SIDE = {"BUY": "LONG", "SELL": "SHORT"}
    for name, fn in variants:
        sigs = fn(df, atr)
        if not sigs:
            continue
        for ex in ("trail", "tp3r"):
            for t in ss.run_single_slot(df, atr, sigs, ex):
                r, i, side = t["r"], t["idx"], SIDE[t["side"]]
                out[side].append(r)
                key = f"{side}_{'TRAIN' if i < half else 'TEST'}"
                out[key].append(r)
                # 市場方向 (MA200): 進場時 price 在 MA200 上/下
                if np.isfinite(ma200[i]):
                    out["UP" if df["close"].iloc[i] > ma200[i] else "DOWN"].append(r)
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    return out, len(df), bh


def mt(v):
    v = np.asarray(v, float)
    if len(v) < 2:
        return float("nan"), float("nan")
    sd = v.std(ddof=1)
    return float(v.mean()), (float(v.mean() / (sd / math.sqrt(len(v)))) if sd > 0 else 0.0)


def main():
    iv = sys.argv[1] if len(sys.argv) > 1 else "1d"
    files = pv.discover(iv)
    print("=" * 108)
    print(f"Long/Short 拆解 — interval={iv}  成本 {pv.COST * 100:.2f}%  SL {pv.SL_MULT}×ATR")
    print("=" * 108)

    data, bhs = {}, {}
    for sym, p in sorted(files.items()):
        o, nb, bh = run_sides(p, iv, pv.TREND)
        if o["LONG"] or o["SHORT"]:
            data[sym] = o
            bhs[sym] = bh

    print("\n【1】逐資產: 趨勢族 long vs short")
    print(f"  {'資產':>6} {'B&H':>9} | {'LONG n':>7} {'meanR':>8} {'t':>7} | "
          f"{'SHORT n':>8} {'meanR':>8} {'t':>7} | 判定")
    L, S = {}, {}
    for sym, o in data.items():
        ml, tl = mt(o["LONG"])
        ms, ts = mt(o["SHORT"])
        L[sym], S[sym] = ml, ms
        if ml > 0 and ms > 0:
            verdict = "✅ 兩邊都賺 = 真 trend alpha"
        elif ml > 0 and ms <= 0:
            verdict = "⚠️ 只 long 賺 = 疑似 beta"
        else:
            verdict = "❌ long 唔賺"
        print(f"  {sym:>6} {bhs[sym] * 100:>+8.0f}% | {len(o['LONG']):>7} {ml:>+8.3f} {tl:>+7.2f} | "
              f"{len(o['SHORT']):>8} {ms:>+8.3f} {ts:>+7.2f} | {verdict}")

    lv, sv = np.array(list(L.values())), np.array(list(S.values()))
    print(f"\n  跨資產 LONG : 正 {int((lv > 0).sum())}/{len(lv)}  中位 {np.median(lv):+.3f}")
    print(f"  跨資產 SHORT: 正 {int((sv > 0).sum())}/{len(sv)}  中位 {np.median(sv):+.3f}")

    # 市場方向拆解
    print("\n【2】按進場時市場方向拆 (price vs MA200)")
    up_all, dn_all = [], []
    up_t, dn_t = [], []
    for sym, o in data.items():
        up_all += o["UP"]
        dn_all += o["DOWN"]
    mu, tu = mt(up_all)
    md, td = mt(dn_all)
    print(f"  MA200 之上 (牛市 regime): n={len(up_all):>6}  meanR {mu:+.3f}  t {tu:+.2f}")
    print(f"  MA200 之下 (熊市 regime): n={len(dn_all):>6}  meanR {md:+.3f}  t {td:+.2f}")

    # ── 3. Block bootstrap 分別對 long / short ─────────────────
    print("\n【3】Block bootstrap (重抽資產) — LONG vs SHORT 分開")
    syms = list(data.keys())
    for label, arr in (("LONG", o_key := "LONG"), ("SHORT", "SHORT")):
        boots = []
        for _ in range(2000):
            pick = RNG.choice(len(syms), size=len(syms), replace=True)
            m = [np.mean(data[syms[i]][arr]) for i in pick if data[syms[i]][arr]]
            if m:
                boots.append(np.mean(m))
        boots = np.array(boots)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        print(f"    {label:>6}: 95% CI [{lo:+.3f}, {hi:+.3f}]  "
              f"P(>0)={float((boots > 0).mean()):.3f}  "
              f"{'✅ 排除 0' if lo > 0 else '❌ 含 0'}")

    # ── 4. 對照: rsi_mr 應該 long/short 都負 ────────────────────
    print("\n【4】Sanity check — rsi_mr (均值回歸) long/short 都應負")
    ml_all, ms_all = [], []
    for sym, p in sorted(files.items()):
        o, _, _ = run_sides(p, iv, pv.MR)
        ml, _ = mt(o["LONG"])
        ms, _ = mt(o["SHORT"])
        if np.isfinite(ml):
            ml_all.append(ml)
        if np.isfinite(ms):
            ms_all.append(ms)
    if ml_all:
        a, b = np.array(ml_all), np.array(ms_all)
        print(f"    rsi_mr LONG : 負 {int((a < 0).sum())}/{len(a)}  中位 {np.median(a):+.3f}")
        print(f"    rsi_mr SHORT: 負 {int((b < 0).sum())}/{len(b)}  中位 {np.median(b):+.3f}")

    # ── 5. TRAIN/TEST 分開 ──────────────────────────────────────
    print("\n【5】Long / Short × TRAIN / TEST")
    for k in ("LONG_TRAIN", "LONG_TEST", "SHORT_TRAIN", "SHORT_TEST"):
        pooled = []
        for o in data.values():
            pooled += o[k]
        m, t = mt(pooled)
        print(f"    {k:>12}: n={len(pooled):>6}  meanR {m:+.3f}  t {t:+.2f}")

    print("\n" + "=" * 108)
    print("判讀")
    print("=" * 108)
    both = int(((lv > 0) & (sv > 0)).sum())
    print(f"  兩邊都賺嘅資產: {both}/{len(lv)}")
    if both >= len(lv) * 0.7:
        print("  🎯 大部分資產 long 同 short 都賺 → **真 trend persistence alpha**, 唔係 beta")
    elif (lv > 0).sum() > (sv > 0).sum():
        print("  ⚠️ long 明顯好過 short → 大部分係 **beta** (牛市), alpha 成分有限")
    else:
        print("  ❌ 兩邊都唔穩定")


if __name__ == "__main__":
    main()
