#!/usr/bin/env python3
"""test_sweep_sim.py — 驗證 sweep_design.simulate 嘅正確性.

教訓 (memory): backtest 最容易死喺 (a) look-ahead bias (b) 同一根 bar 內
SL/TP 次序 (c) 成本漏計。呢個 test 針對呢三樣。
"""
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.environ.setdefault("SWEEP_COST", "0.0")

import sweep_design as sd

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


def mk_bars(rows):
    """rows: list of (open, high, low, close) — 由 index 0 起."""
    global O, H, L, C
    a = np.array(rows, dtype=float)
    sd.O, sd.H, sd.L, sd.C = a[:, 0].copy(), a[:, 1].copy(), a[:, 2].copy(), a[:, 3].copy()


# entry=100, risk=10 (stop 90), atr=10
E, K = 100.0, 10.0

print("=== A. look-ahead: fill 嗰根 bar 唔可以被用 ===")
# bar0 (fill) 內部有巨大波動, 但應該完全被跳過
mk_bars([(100, 200, 50, 100),          # idx0 = fill bar (唔可以用)
         (100, 101, 99, 100),          # idx1
         (100, 101, 99, 100)])         # idx2
r = sd.simulate(E, E - K, K, "BUY", [(2.0, 1.0)], 0)
check(r is not None and abs(r) < 0.2, "A1 fill bar 嘅 200/50 冇被當成 TP/SL", f"(R={r})")

print("\n=== B. 同一根 bar SL 同 TP 都中 → 當 SL (保守) ===")
mk_bars([(100, 100, 100, 100),
         (100, 130, 80, 100)])         # 同一根: high 130 (過 TP 120), low 80 (過 SL 90)
r = sd.simulate(E, E - K, K, "BUY", [(2.0, 1.0)], 0)
check(r is not None and abs(r - (-1.0)) < 1e-9, "B1 當 SL = -1R", f"(R={r})")

print("\n=== C. 正常 TP 命中 ===")
mk_bars([(100, 100, 100, 100),
         (100, 105, 99, 104),          # 未到
         (104, 121, 103, 120)])        # high 121 >= TP 120
r = sd.simulate(E, E - K, K, "BUY", [(2.0, 1.0)], 0)
check(r is not None and abs(r - 2.0) < 1e-9, "C1 TP 2R 命中 = +2R", f"(R={r})")

print("\n=== D. 分段: 第一段到 + 第二段到 ===")
# segs: 1R 一半, 2R 一半
mk_bars([(100, 100, 100, 100),
         (100, 111, 99, 110),          # TP1 110 中
         (110, 121, 109, 120)])        # TP2 120 中
r = sd.simulate(E, E - K, K, "BUY", [(1.0, 0.5), (2.0, 0.5)], 0)
check(r is not None and abs(r - 1.5) < 1e-9, "D1 (1R·0.5 + 2R·0.5) = +1.5R", f"(R={r})")

print("\n=== E. SELL 對稱 ===")
mk_bars([(100, 100, 100, 100),
         (100, 101, 89, 90)])          # SELL: TP 80? 用 2R → 100-20=80 未到; SL 110 未到
r = sd.simulate(E, E + K, K, "SELL", [(2.0, 1.0)], 0)
check(r is not None and abs(r - ((100 - 90) / 10)) < 1e-9, "E1 SELL 到期用最後 close 計 R", f"(R={r})")
mk_bars([(100, 100, 100, 100),
         (100, 100, 79, 80)])          # SELL TP 80 命中
r = sd.simulate(E, E + K, K, "SELL", [(2.0, 1.0)], 0)
check(r is not None and abs(r - 2.0) < 1e-9, "E2 SELL TP 命中 +2R", f"(R={r})")

print("\n=== F. 成本一定要扣 ===")
mk_bars([(100, 100, 100, 100),
         (100, 121, 99, 120)])
sd.COST = 0.002
r_c = sd.simulate(E, E - K, K, "BUY", [(2.0, 1.0)], 0)
sd.COST = 0.0
r_0 = sd.simulate(E, E - K, K, "BUY", [(2.0, 1.0)], 0)
check(r_c is not None and r_0 is not None and r_c < r_0,
      "F1 有成本 < 無成本", f"(cost={r_c}, noco={r_0})")
# 成本 ≈ entry 側 c·entry/risk + 出場側 c·exit_px/risk。
# entry=100, exit=120 (2R), risk=10 → 0.001*100/10 + 0.001*120/10 = 0.022R
exp = (0.001 * 100 + 0.001 * 120) / 10
check(abs((r_0 - r_c) - exp) < 0.003, f"F2 成本幅度 ≈ {exp:.3f}R (兩邊各 c×價/risk)",
      f"(Δ={r_0 - r_c:.4f})")

print("\n=== G. trail 段 ===")
# trail_mult=2, atr=10 → trail 距離 20
mk_bars([(100, 100, 100, 100),
         (100, 125, 99, 124),          # 升到 125, SL 推 breakeven 後 trail = 125-20=105
         (124, 126, 104, 105)])        # low 104 <= 105 → trail 出場 @105 = +0.5R
r = sd.simulate(E, E - K, K, "BUY", [(None, 1.0)], 0, trail_mult=2.0)
check(r is not None and abs(r - 0.5) < 0.05, "G1 trail 出場 ≈ +0.5R", f"(R={r})")

print("\n=== H. 到期 (冇 SL 冇 TP) ===")
rows = [(100, 100, 100, 100)] + [(100, 101, 99, 100)] * 200
mk_bars(rows)
r = sd.simulate(E, E - K, K, "BUY", [(99.0, 1.0)], 0)
check(r is not None and abs(r) < 1e-9, "H1 橫行到期 = 0R", f"(R={r})")

print("\n=== I. risk <= 0 安全 ===")
mk_bars([(100, 100, 100, 100), (100, 101, 99, 100)])
check(sd.simulate(E, E, K, "BUY", [(1.0, 1.0)], 0) is None, "I1 risk=0 → None (唔 crash)")
check(sd.simulate(E, E - K, K, "BUY", [(1.0, 1.0)], len(sd.O) - 1) is None,
      "I2 fill 喺最尾 → None")

print("\n" + "=" * 62)
print(f"結果: {N - len(FAILS)} PASS / {len(FAILS)} FAIL")
if FAILS:
    for f in FAILS:
        print(f"  ❌ {f}")
    sys.exit(1)
print("✅ 全部通過")
