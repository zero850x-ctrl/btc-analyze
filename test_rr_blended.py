#!/usr/bin/env python3
"""test_rr_blended.py — 3 段出場 blended R gate 測試 (feat/rr-blended-gate, 2026-09-28).

背景 (為什麼要換 metric):
    實際出場結構 = 1/3 @ TP1 + 1/3 @ TP2 + 1/3 尾倉 (trail)。
    舊 gate 只用 TP1 單段 RR, 但引擎 TP1 按設計擺 ~1:1
    (`analyze_v3._staged_targets`: tp1 = min(fib_tp, entry + risk))
    → RR(TP1) 中位數 0.83, 2 年 274 個 Flag setup 只有 3.7% >= 1.2
    → gate 幾乎永遠擋, 主系統結構性唔開單 (2026-09-13~09-30 連續 17 日 0 單)。

    新 metric: blended = (1/3)·rr1 + (1/3)·rr2 + (1/3)·尾倉R, 尾倉保守假設 0R。

測試覆蓋:
    A. _blended_r 公式 (含冇 TP2 嘅退化情況)
    B. _rr_at 一致性
    C. _compute_rr 入口 (limit_px / entry_override)
    D. btc_engine.btc_filter_setups gate 行為 (含新舊 metric 分界)
    E. 回歸: 冇 TP1 唔會被 RR gate 擋 (行為同舊版一致)
"""
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import binance_testnet_paper as btp

spec = importlib.util.spec_from_file_location("btc_engine", os.path.join(REPO, "btc_engine.py"))
be = importlib.util.module_from_spec(spec)
spec.loader.exec_module(be)

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


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


# ══════════════════════════════════════════════════════════════════════
print("=== A. _blended_r 公式 ===")

check(close(btp._blended_r(1.0, 2.0, 0.0), 1.0), "A1 (1.0, 2.0, tail=0) = 1.0")
check(close(btp._blended_r(1.0, 2.0), 1.0), "A1b 默認 tail = TAIL_R_ASSUMED (0.0)")
check(close(btp._blended_r(1.0, 2.0, 1.0), 4.0 / 3.0), "A2 tail=1.0 → 4/3")
check(close(btp._blended_r(1.5, None, 0.0), 0.5), "A3 冇 TP2 → (1.5+0+0)/3 = 0.5")
check(close(btp._blended_r(1.5, None, 0.9), 1.1), "A4 冇 TP2 + tail=0.9 → 1.1")
check(btp._blended_r(0.0, 0.0, 0.0) == 0.0, "A5 全 0 → 0")
check(btp._blended_r(-1.0, -1.0, 0.0) < 0, "A6 負值原樣傳遞 (唔會 clamp 到 0)")

# 單調性: rr1 / rr2 各自增加, blended 一定唔會跌
mono_ok = True
prev = -1e9
for r1 in [x / 10 for x in range(0, 41)]:
    v = btp._blended_r(r1, 2.0, 0.0)
    if v < prev - 1e-12:
        mono_ok = False
    prev = v
check(mono_ok, "A7 blended 對 rr1 單調不減")
mono_ok2 = True
prev = -1e9
for r2 in [x / 10 for x in range(0, 41)]:
    v = btp._blended_r(1.0, r2, 0.0)
    if v < prev - 1e-12:
        mono_ok2 = False
    prev = v
check(mono_ok2, "A8 blended 對 rr2 單調不減")

# ══════════════════════════════════════════════════════════════════════
print("\n=== B. _rr_at 一致性 ===")

S = {"btc_side": "BUY", "btc_entry": 100000.0, "btc_stop": 99000.0,
     "btc_tp1": 101000.0, "btc_tp2": 102700.0}
r = btp._rr_at(S, 100000.0)
check(close(r["rr1"], 1.0), "B1 rr1 = 1.0")
check(close(r["rr2"], 2.7), "B2 rr2 = 2.7")
check(close(r["blended"], (1.0 + 2.7) / 3.0), "B3 blended = (rr1+rr2+0)/3")
check(close(r["blended"], 1.2333333333333334), "B3b blended 數值 1.2333")

S_no2 = dict(S)
S_no2["btc_tp2"] = None
r2 = btp._rr_at(S_no2, 100000.0)
check(r2["rr2"] is None and close(r2["blended"], 1.0 / 3.0), "B4 冇 TP2 → blended = rr1/3")

check(btp._rr_at({"btc_stop": 1.0}, 1.0) is None, "B5 冇 TP1 → None")
check(btp._rr_at({"btc_stop": 100.0, "btc_tp1": 110.0}, 100.0) is None,
      "B6 risk = 0 → None (唔會 ZeroDivision)")
check(btp._rr_at({"btc_stop": None, "btc_tp1": 110.0}, 100.0) is None, "B7 stop 非數值 → None")

# ══════════════════════════════════════════════════════════════════════
print("\n=== C. _compute_rr 入口 ===")

S2 = dict(S)
S2["btc_limit_px"] = 100000.0
check(close(btp._compute_rr(S2), 1.2333333333333334), "C1 用 btc_limit_px 計")
check(close(btp._compute_rr(S), 1.2333333333333334), "C2 冇 limit_px → 用 btc_entry")
check(close(btp._compute_rr(S2, entry_override=100000.0), 1.2333333333333334),
      "C3 entry_override 生效")
# 追高 → blended 跌
r_hi = btp._compute_rr(S2, entry_override=100900.0)
check(r_hi < 1.2, "C4 追高 0.9% → blended 跌到 < 1.2", f"({r_hi:.3f})")
check(btp._compute_rr({"btc_stop": 1.0}) is None, "C5 冇 TP1 → None")

# ══════════════════════════════════════════════════════════════════════
print("\n=== D. btc_engine.btc_filter_setups gate ===")

DIFF_OK = {"status": "OK"}


def mk(entry="限價買入 @ $100,000", zone="$99,900 - $100,100", stop="$99,000",
       tp1="$101,000", tp2="$102,700", pattern="🚩 Bull Flag (牛旗)",
       mode="breakout", direction="BUY"):
    return {"direction": direction, "pattern": pattern, "entry_mode": mode,
            "entry_zone": zone, "entry_trigger": entry, "stop_loss": stop,
            "tp1": tp1, "tp2": tp2}


# D1: 舊 metric 會擋 (rr1=1.0 < 1.2) 但新 metric 過關 (blended 1.2333) ← 呢個就係 fix 目的
out = be.btc_filter_setups([mk()], atr=500.0, px=100000.0, diff_check=DIFF_OK, ma50=None)
check(len(out) == 1, "D1 rr1=1.0 但 blended=1.233 → 放行 (舊版會擋)", f"(kept={len(out)})")
if out:
    s = out[0]
    check(close(s.get("rr_tp1"), 1.0), "D1a 記低 rr_tp1", f"({s.get('rr_tp1')})")
    check(close(s.get("rr_tp2"), 2.7), "D1b 記低 rr_tp2", f"({s.get('rr_tp2')})")
    check(close(s.get("rr_blended"), 1.23), "D1c 記低 rr_blended", f"({s.get('rr_blended')})")

# D2: blended < 1.2 → 擋, 且原因係新格式
out2 = be.btc_filter_setups([mk(tp2="$102,000")], atr=500.0, px=100000.0,
                            diff_check=DIFF_OK, ma50=None)
check(len(out2) == 0, "D2 典型 1:1 + 2:1 結構 (blended=1.0) → 仍然擋")
raw = mk(tp2="$102,000")
be.btc_filter_setups([raw], atr=500.0, px=100000.0, diff_check=DIFF_OK, ma50=None)
check(str(raw.get("_gate_skip", "")).startswith("rr_blended_"),
      "D2a _gate_skip 用新格式 rr_blended_*", f"({raw.get('_gate_skip')})")

# D3: 冇 TP2 → blended = rr1/3 → 保守擋
out3 = be.btc_filter_setups([mk(tp2=None)], atr=500.0, px=100000.0,
                            diff_check=DIFF_OK, ma50=None)
check(len(out3) == 0, "D3 冇 TP2 → blended = rr1/3 = 0.33 → 擋 (保守)")

# D4: 邊界 — blended 啱啱好 1.2
# rr1 = 1.0, 要 blended = 1.2 → rr2 = 2.6
out4 = be.btc_filter_setups([mk(tp2="$102,600")], atr=500.0, px=100000.0,
                            diff_check=DIFF_OK, ma50=None)
check(len(out4) == 1, "D4 blended = 1.2 啱啱好 → 放行 (>= 唔係 >)")
# 差 1 蚊: rr2 = 2.599, blended = 1.19967 → 擋
out5 = be.btc_filter_setups([mk(tp2="$102,599")], atr=500.0, px=100000.0,
                            diff_check=DIFF_OK, ma50=None)
check(len(out5) == 0, "D5 blended = 1.1997 (差 1 蚊) → 擋")

# D6: SELL 對稱
s_sell = be.btc_filter_setups(
    [{"direction": "SELL", "pattern": "🚩 Bear Flag (熊旗)", "entry_mode": "breakout",
      "entry_zone": "$100,000 - $100,300", "entry_trigger": "限價賣出 @ $100,000",
      "stop_loss": "$101,000", "tp1": "$99,000", "tp2": "$97,300"}],
    atr=500.0, px=100000.0, diff_check=DIFF_OK, ma50=None)
check(len(s_sell) == 1, "D6 SELL 對稱 (blended 1.233) → 放行", f"(kept={len(s_sell)})")
if s_sell:
    check(close(s_sell[0].get("rr_blended"), 1.23), "D6a SELL rr_blended",
          f"({s_sell[0].get('rr_blended')})")

# D7: rr2 比 rr1 細 (TP2 近過 TP1) → blended 拉低
out7 = be.btc_filter_setups([mk(tp1="$101,600", tp2="$101,700")],
                            atr=500.0, px=100000.0, diff_check=DIFF_OK, ma50=None)
check(len(out7) == 0, "D7 rr2 貼近 rr1 → blended 唔夠 → 擋")

# ══════════════════════════════════════════════════════════════════════
print("\n=== E. 回歸: 冇 TP1 行為不變 ===")

raw_np = mk(tp1=None, tp2=None)
out_np = be.btc_filter_setups([raw_np], atr=500.0, px=100000.0,
                              diff_check=DIFF_OK, ma50=None)
skip_np = str(raw_np.get("_gate_skip", ""))
check(not skip_np.startswith("rr_"), "E1 冇 TP1 → 唔會被 RR gate 擋 (同舊版一致)",
      f"({skip_np or '冇 skip'})")
check(out_np and out_np[0].get("btc_tp1") is None, "E2 冇 TP1 仍可放行到下層 gate")
check(be.MIN_RR == 1.2, "E3 engine MIN_RR 仍然係 1.2 (門檻冇改)")
check(btp.MIN_RR_EXEC == 1.2, "E4 exec layer MIN_RR_EXEC 仍然係 1.2 (門檻冇改)")

# E5: engine 同 exec layer 兩邊公式一致
eng_setup = be.btc_filter_setups([mk()], atr=500.0, px=100000.0,
                                 diff_check=DIFF_OK, ma50=None)
if eng_setup:
    s = eng_setup[0]
    exec_rr = btp._compute_rr({"btc_side": s["btc_side"], "btc_entry": s["btc_entry"],
                               "btc_stop": s["btc_stop"], "btc_tp1": s["btc_tp1"],
                               "btc_tp2": s["btc_tp2"], "btc_limit_px": s["btc_limit_px"]})
    check(close(s["rr_blended"], round(exec_rr, 2), tol=0.011),
          "E5 engine rr_blended == exec layer blended (同一條公式)",
          f"(engine={s['rr_blended']}, exec={exec_rr:.4f})")

print("\n" + "=" * 62)
print(f"結果: {N - len(FAILS)} PASS / {len(FAILS)} FAIL")
if FAILS:
    for f in FAILS:
        print(f"  ❌ {f}")
    sys.exit(1)
print("✅ 全部通過")
