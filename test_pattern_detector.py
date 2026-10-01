#!/usr/bin/env python3
"""test_pattern_detector.py — 驗證 pattern_detector 嘅正確性.

最關鍵嘅三樣 (錯咗所有下游結論都錯):
  A. look-ahead — swing 只可以喺 j+lookback 之後被用; 信號 bar 唔可以用未來 bar
  B. 雙頂/雙底 條件 (價差 tol / 深度 depth / 頸線突破) 逐項
  C. 平行通道 — 平行 vs 非平行、R²、闊度、斜率反轉
"""
import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import pattern_detector as pd_

N = 0
FAILS = []


def check(cond, label, extra=""):
    global N
    N += 1
    if cond:
        print(f"  OK   {label}")
    else:
        print(f"  FAIL {label}" + (f"  [{extra}]" if extra else ""))
        FAILS.append(label)


def arrays(H, L, C, A):
    return (np.asarray(H, float), np.asarray(L, float),
            np.asarray(C, float), np.asarray(A, float))


# ══════════════════════════════════════════════════════════════════════
print("=== A. find_swings ===")
# 單峰: idx 5 係明確高位, 單谷 idx 12
# ⚠️ n 要夠長: find_swings 只掃 range(lookback, n-lookback) → n=15 時 j 最多 11,
#    idx 12 根本唔會被評估 (初版 test 就係錯呢度)。
H = [95, 96, 97, 98, 99, 100, 99, 98, 97, 96, 95, 94, 93, 94, 95, 96, 97, 98, 99]
L = [94, 95, 96, 97, 98, 99, 98, 97, 96, 95, 94, 93, 92, 93, 94, 95, 96, 97, 98]
Al = [5.0] * len(H)
H_a, L_a, C_a, A_a = arrays(H, L, H, Al)
sh, sl = pd_.find_swings(H_a, L_a, 3)
check([p[0] for p in sh] == [5], "A1 單峰 → 只有 idx 5", f"({[p[0] for p in sh]})")
check([p[0] for p in sl] == [12], "A2 單谷 → 只有 idx 12", f"({[p[0] for p in sl]})")
check(all(p[0] + 3 < len(H) for p in sh), "A3 swing 一定有 lookback 右邊空間")

# 平台 (兩點同值) 唔算 swing — 避免重複偵測
H2 = [95, 96, 100, 100, 96, 95, 94, 93, 92, 91]
H2a, L2a, C2a, A2a = arrays(H2, [x - 1 for x in H2], H2, [5.0] * len(H2))
sh2, _ = pd_.find_swings(H2a, L2a, 3)
check(not any(p[0] in (2, 3) for p in sh2), "A4 平台頂 (兩點同值) 唔當 swing",
      f"({[p[0] for p in sh2]})")

print("\n=== B. 雙頂/雙底 ===")
# 完美雙頂: 頂 idx5 & idx19 @100, 谷 idx12 @92 → 頸線 92
Hd = [95, 96, 97, 98, 99, 100, 99, 98, 97, 96, 95, 94, 93, 94, 95, 96, 97, 98, 99, 100, 99, 98, 97, 96, 95]
Ld = [94, 95, 96, 97, 98, 99, 98, 97, 96, 95, 94, 93, 92, 93, 94, 95, 96, 97, 98, 99, 98, 97, 96, 95, 94]
Cd = list(Hd)
Cd[24] = 91.0                      # 收市穿頸線 92 → 確認
Cd_a = np.asarray(Cd, float)
Hd_a, Ld_a, _, Ad_a = arrays(Hd, Ld, Cd, [5.0] * len(Hd))
dtb = pd_.double_top_bottom(Hd_a, Ld_a, Cd_a, Ad_a, lookback=3, tol_atr=0.5,
                            depth_atr=1.0, min_gap=5, max_gap=80)
check(len(dtb) == 1, "B1 完美雙頂 → 1 個信號", f"({len(dtb)})")
if dtb:
    d = dtb[0]
    check(d["side"] == "SELL", "B2 方向 SELL", d["side"])
    check(abs(d["neckline"] - 92.0) < 1e-9, "B3 頸線 = 92", f"({d['neckline']})")
    check(d["idx"] == 24, "B4 確認 bar = 24 (穿頸線嗰根)", f"({d['idx']})")
    check(d["j2"] == 19, "B5 第二頂 idx = 19", f"({d['j2']})")
    # look-ahead: 信號一定要喺第二頂確認之後
    check(d["idx"] >= d["j2"] + 3, "B6 信號 bar >= j2+lookback (無 look-ahead)",
          f"(idx={d['idx']}, j2={d['j2']})")

# 深度唔夠 → 唔應該出
# ⚠️ 只改 L[12] 冇用: 頸線 = min(L[5:20]), 改一格只會令最低點移去 L[11]=93 (深度仍 7)。
#    要令深度真係不足, 要抬高**整個中間區間**嘅 low。
Hs = list(Hd)
Ls = list(Ld)
for k in range(9, 17):
    Ls[k] = 99.0                   # 中間全部 low 抬到 99 → 深度 = 100-99 = 1 < 5
Hs_a, Ls_a, Cs_a, As_a = arrays(Hs, Ls, Cd, [5.0] * len(Hs))
dts = pd_.double_top_bottom(Hs_a, Ls_a, Cs_a, As_a, lookback=3, tol_atr=0.5,
                            depth_atr=1.0, min_gap=5, max_gap=80)
check(len(dts) == 0, "B7 深度不足 (1 vs 需 5) → 冇信號", f"({len(dts)})")

# 價差太大 → 唔應該出
Hw = list(Hd)
Hw[19] = 106.0                     # 第二頂高 6 (> 0.5×ATR=2.5)
Hw_a, Lw_a, Cw_a, Aw_a = arrays(Hw, Ld, Cd, [5.0] * len(Hw))
dtw = pd_.double_top_bottom(Hw_a, Lw_a, Cw_a, Aw_a, lookback=3, tol_atr=0.5,
                            depth_atr=1.0, min_gap=5, max_gap=80)
check(len(dtw) == 0, "B8 兩頂價差 6 > tol 2.5 → 冇信號", f"({len(dtw)})")

# 未穿頸線 → 唔應該出
Cu = list(Hd)
Cu_a = np.asarray(Cu, float)       # 全部 close = H, 最低 92 但冇 <92
Hu_a, Lu_a, _, Au_a = arrays(Hd, Ld, Cu, [5.0] * len(Hd))
dtu = pd_.double_top_bottom(Hu_a, Lu_a, Cu_a, Au_a, lookback=3, tol_atr=0.5,
                            depth_atr=1.0, min_gap=5, max_gap=80)
check(len(dtu) == 0, "B9 未穿頸線 → 冇信號", f"({len(dtu)})")

# 雙底鏡像 — 用 reflect(105) 令 Hd 高位變低位
# ⚠️ 鏡像後頸線係 **max(Hb)** 而唔係固定值, 要實際計出嚟再穿, 唔可以亂設 close。
Hb = [105.0 - (x - 95.0) for x in Hd]
Lb = [104.0 - (x - 95.0) for x in Ld]
neck = max(Hb[5:20])
Cb = list(Lb)
Cb[24] = neck + 1.0                # 收市穿頸線向上 → BUY
Hb_a, Lb_a, Cb_a, Ab_a = arrays(Hb, Lb, Cb, [5.0] * len(Hb))
dtb2 = pd_.double_top_bottom(Hb_a, Lb_a, Cb_a, Ab_a, lookback=3, tol_atr=0.5,
                             depth_atr=1.0, min_gap=5, max_gap=80)
check(len(dtb2) >= 1 and dtb2[0]["side"] == "BUY",
      "B10 雙底鏡像 → BUY", f"({[x['side'] for x in dtb2]}, 頸線={neck})")
if dtb2:
    check(abs(dtb2[0]["neckline"] - neck) < 1e-9, "B10a 頸線 = max(中間 high)",
          f"({dtb2[0]['neckline']} vs {neck})")

print("\n=== C. 平行通道 ===")


def build_channel(n=220, upper_slope=0.5, lower_slope=0.5, base_up=105.0,
                  base_dn=95.0, peaks=(10, 30, 50, 70, 90), troughs=(20, 40, 60, 80),
                  bump=0.8, clip=4):
    """合成通道: H 喺 peaks 觸上軌, L 喺 troughs 觸下軌.

    ⚠️ bump 一定要 > 斜率增量 (upper_slope × clip), 否則峰根本唔係 local max:
    初版 bump=0.3, clip=6 → 最大 1.8 < 0.5×4=2.0 → find_swings 搵唔到任何峰 → 通道永遠無效。
    而要 H 一直 > L, 要 bump × clip × 2 < (base_up - base_dn)。
    """
    H = np.zeros(n)
    L = np.zeros(n)
    for i in range(n):
        up = base_up + upper_slope * i
        dn = base_dn + lower_slope * i
        dp = min(abs(i - p) for p in peaks)
        dt = min(abs(i - t) for t in troughs)
        H[i] = up - bump * min(dp, clip)
        L[i] = dn + bump * min(dt, clip)
    C = (H + L) / 2.0
    A = np.full(n, 2.0)
    return H, L, C, A


Hc, Lc, Cc, Ac = build_channel()
sh_c, sl_c = pd_.find_swings(Hc, Lc, 3)
ch = pd_.parallel_channel(Hc, Lc, Ac, win=150, lookback=3, r2_min=0.70,
                          slope_tol_atr=0.05, swings=(sh_c, sl_c))
valid = [i for i, x in enumerate(ch) if x]
check(len(valid) > 0, "C1 完美平行通道 → 有有效 bar", f"({len(valid)} 個)")
if valid:
    i0 = valid[-1]
    c0 = ch[i0]
    check(abs(c0["slope"] - 0.5) < 0.05, "C2 斜率 ≈ 0.5", f"({c0['slope']:.3f})")
    check(abs((c0["up"] - c0["lo"]) - 10.0) < 1.0, "C3 闊度 ≈ 10 (base_up-base_dn)",
          f"({c0['up'] - c0['lo']:.2f})")
    check(c0["r2_up"] > 0.9 and c0["r2_dn"] > 0.9, "C4 兩軌 R² 高",
          f"(up={c0['r2_up']:.3f}, dn={c0['r2_dn']:.3f})")

# 非平行 → 唔應該有效
Hn, Ln, Cn, An = build_channel(lower_slope=0.0)
sh_n, sl_n = pd_.find_swings(Hn, Ln, 3)
chn = pd_.parallel_channel(Hn, Ln, An, win=150, lookback=3, r2_min=0.70,
                           slope_tol_atr=0.05, swings=(sh_n, sl_n))
check(sum(1 for x in chn if x) == 0, "C5 斜率差 0.5 > 容許 0.1 → 全部無效",
      f"({sum(1 for x in chn if x)})")

# look-ahead: 最新 swing 唔可以早過確認時間被用
# (搵每個有效 bar, 檢查所用 swing 全部 <= i - lookback)
lookahead_ok = True
sh_idx = np.array([p[0] for p in sh_c] if sh_c else [], dtype=int)
for i, x in enumerate(ch):
    if x is None:
        continue
    lo_i = i - 150
    used = sh_idx[(sh_idx >= lo_i) & (sh_idx <= i - 3)]
    if len(used) != x["n_up"]:
        lookahead_ok = False
        break
check(lookahead_ok, "C6 只用已確認 swing (<= i-lookback)")

print("\n=== D. channel_signals ===")
# breakout: 收市穿軌 + k×ATR
# ⚠️ 通道上軌係擬合出嚟, 唔可以自己手算 (base_up 改過就會算錯)。
#    次序: 先建通道 (只用 H/L) → 再讀 ch[200]["up"] → 再設 close。
Hb2, Lb2, Cb2, Ab2 = build_channel()
sh_b, sl_b = pd_.find_swings(Hb2, Lb2, 3)
ch_b = pd_.parallel_channel(Hb2, Lb2, Ab2, win=150, lookback=3, r2_min=0.70,
                            slope_tol_atr=0.05, swings=(sh_b, sl_b))
if ch_b[200]:
    up200 = ch_b[200]["up"]
    Cb2 = Cb2.copy()
    Cb2[200] = up200 + 0.25 * 2.0 + 1.0    # 明確穿出 (k=0.25, ATR=2)
    sig_bo = pd_.channel_signals(ch_b, Hb2, Lb2, Cb2, Ab2, mode="breakout")
    got = [s for s in sig_bo if s["idx"] == 200]
    check(len(got) == 1 and got[0]["side"] == "BUY",
          "D1 breakout 穿出上軌 → BUY",
          f"({got}, up200={up200:.1f}, close={Cb2[200]:.1f})")
    # 貼近但未穿 (差 0.1×ATR) → 唔應該 fire
    Cb3 = Cb2.copy()
    Cb3[200] = up200 + 0.05 * 2.0
    sig_lo = pd_.channel_signals(ch_b, Hb2, Lb2, Cb3, Ab2, mode="breakout")
    check(len([s for s in sig_lo if s["idx"] == 200]) == 0,
          "D1b 未夠 k×ATR 緩衝 → 唔 fire")
    sig_mr = pd_.channel_signals(ch_b, Hb2, Lb2, Cb2, Ab2, mode="meanrev")
    gotm = [s for s in sig_mr if s["idx"] == 200]
    check(len(gotm) == 0, "D2 meanrev 唔會同一情況出信號", f"({gotm})")
else:
    check(False, "D1 bar 200 通道無效 — 建造失敗")

print("\n=== E. combo (雙頂 @ 通道上軌) ===")
Hd_a2, Ld_a2, Cd_a2, Ad_a2 = arrays(Hd, Ld, Cd, [5.0] * len(Hd))
dts_base = pd_.double_top_bottom(Hd_a2, Ld_a2, Cd_a2, Ad_a2, lookback=3,
                                 tol_atr=0.5, depth_atr=1.0)
# 假通道: 上軌喺 idx19 啱好 = 100 (即第二頂價) → 應該 fire
ch_fake = [None] * len(Hd)
ch_fake[19] = {"slope": 0.0, "up": 100.0, "lo": 95.0, "width": 5.0,
               "n_up": 2, "n_dn": 2, "r2_up": 1.0, "r2_dn": 1.0}
cmb = pd_.combo_dtb_at_channel(Hd_a2, Ld_a2, Cd_a2, Ad_a2, ch_fake,
                               near_atr=1.0, dtbs=dts_base)
check(len(cmb) == 1, "E1 第二頂貼上軌 (差 0) → fire", f"({len(cmb)})")
# 上軌遠離 → 唔應該 fire
ch_far = [None] * len(Hd)
ch_far[19] = {"slope": 0.0, "up": 130.0, "lo": 95.0, "width": 35.0,
              "n_up": 2, "n_dn": 2, "r2_up": 1.0, "r2_dn": 1.0}
cmb2 = pd_.combo_dtb_at_channel(Hd_a2, Ld_a2, Cd_a2, Ad_a2, ch_far,
                                near_atr=1.0, dtbs=dts_base)
check(len(cmb2) == 0, "E2 上軌差 30 >> near 5 → 唔 fire", f"({len(cmb2)})")

print("\n" + "=" * 62)
print(f"結果: {N - len(FAILS)} PASS / {len(FAILS)} FAIL")
if FAILS:
    for f in FAILS:
        print(f"  ❌ {f}")
    sys.exit(1)
print("✅ 全部通過")
