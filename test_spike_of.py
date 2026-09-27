#!/usr/bin/env python3
"""test_spike_of.py — 驗證 btc_xauusd_gate.spike_of 同 XAUUSD 官方公式一致

背景: BTC repo 嘅 analyze_v3.py 係舊版, 冇 `_post_spike_state`
(post-spike gate 係 XAUUSD repo 2026-09-04 加, 未同步) → 我哋自己實作。
本 test 直接攞 XAUUSD repo 嘅**原始**函數做 ground truth 對比。

⚠️ XAUUSD repo 唯讀 — 只 import, 唔改任何檔。
"""
import importlib.util
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
XAU = os.path.expanduser("~/repos/xauusd-analyze-v3")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sys.path.insert(0, HERE)
import btc_xauusd_gate as G  # noqa: E402

av3x = _load("av3_xauusd_ref", os.path.join(XAU, "analyze_v3.py"))

PASS = FAIL = 0


def chk(desc, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ❌ {desc}\n     got={got!r} want={want!r}")


def official(closes, atr):
    """XAUUSD 原函數 → direction 字串 / None."""
    r = av3x._post_spike_state(closes, atr)
    return None if r is None else r["direction"]


print("=== test_spike_of: spike_of vs XAUUSD analyze_v3._post_spike_state ===\n")

# 0. 常數對齊 (官方 09-08 定 3.0; window 4)
print(f"官方常數: SPIKE_ATR_MULT={av3x.SPIKE_ATR_MULT}  "
      f"SPIKE_WINDOW_BARS={av3x.SPIKE_WINDOW_BARS}")
print(f"我哋常數: SPIKE_ATR_MULT={G.SPIKE_ATR_MULT}  "
      f"SPIKE_WINDOW_BARS={G.SPIKE_WINDOW_BARS}")
chk("SPIKE_ATR_MULT 對齊", G.SPIKE_ATR_MULT, av3x.SPIKE_ATR_MULT)
chk("SPIKE_WINDOW_BARS 對齊", G.SPIKE_WINDOW_BARS, av3x.SPIKE_WINDOW_BARS)

# 1. Guard cases
print("\n--- Guard ---")
chk("closes=None → None", G.spike_of(None, 1.0), None)
chk("atr=None → None", G.spike_of([1.0] * 9, None), None)
chk("atr=0 → None", G.spike_of([1.0] * 9, 0), None)
chk("atr<0 → None", G.spike_of([1.0] * 9, -1), None)
chk("closes 太短 → None", G.spike_of([1.0, 2.0, 3.0], 1.0), None)
chk("官方: closes 太短 → None",
    official([1.0, 2.0, 3.0], 1.0), None)

# 2. 邊界: |move| 剛好 = mult*ATR → 官方用 `<=` → 放行 (None)
print("\n--- 邊界 (= 門檻 → 放行) ---")
atr = 10.0
ct_eq = [100.0] * 9
ct_eq[-2] = 100.0 + 3.0 * atr          # move = 120-100 = 30 = 3.0*10 剛好
ct_eq[-2 - 4] = 100.0
chk("|move| == mult×ATR → None (我哋)", G.spike_of(ct_eq, atr), None)
chk("|move| == mult×ATR → None (官方)", official(ct_eq, atr), None)

# 3. 明確 spike
print("\n--- 明確 spike ---")
ct_up = [100.0] * 9
ct_up[-2 - 4] = 100.0
ct_up[-2] = 100.0 + 4.0 * atr          # move = +40 > 30 → up
chk("向上 spike → 'up' (我哋)", G.spike_of(ct_up, atr), "up")
chk("向上 spike → 'up' (官方)", official(ct_up, atr), "up")

ct_dn = [100.0] * 9
ct_dn[-2 - 4] = 100.0
ct_dn[-2] = 100.0 - 4.0 * atr
chk("向下 spike → 'down' (我哋)", G.spike_of(ct_dn, atr), "down")
chk("向下 spike → 'down' (官方)", official(ct_dn, atr), "down")

# 4. 隨機對比 300 組 (含 forming bar, 即 closes[-1] 唔參與計算)
print("\n--- 隨機對比 300 組 ---")
random.seed(20260927)
bad = 0
for k in range(300):
    n = random.randint(6, 12)
    closes = [round(random.uniform(50, 150), 2) for _ in range(n)]
    # 隨機注入 spike 令事件率合理 (均勻分佈太難 fire)
    if k % 3 == 0:
        closes[-2] = closes[-2 - 4] + random.choice([-1, 1]) * random.uniform(1, 6) * atr
    a = round(random.uniform(0.5, 12.0), 3)
    g, o = G.spike_of(closes, a), official(closes, a)
    if g != o:
        bad += 1
        if bad <= 3:
            print(f"  ❌ #{k}: 我哋={g!r} 官方={o!r}\n     closes={closes} atr={a}")
chk("300 組隨機全部一致", bad, 0)

# 5. 唔同 (mult, win) 組合 — 官方函數靠 monkey-patch 常數
print("\n--- 參數化掃描 (只测我哋實作, 對照公式) ---")
for mult, win in ((1.5, 2), (2.0, 4), (3.0, 4), (5.0, 6)):
    av3x.SPIKE_ATR_MULT, av3x.SPIKE_WINDOW_BARS = mult, win
    n_fire = 0
    for k in range(120):
        closes = [round(random.uniform(50, 150), 2) for _ in range(12)]
        a = round(random.uniform(1.0, 8.0), 3)
        o = official(closes, a)
        g = G.spike_of(closes, a, mult=mult, win=win)
        if g != o:
            FAIL += 1
            print(f"  ❌ mult={mult} win={win}: 我哋={g!r} 官方={o!r}")
        n_fire += o is not None
    print(f"  mult={mult:<4} win={win}  → fire {n_fire}/120  一致")
    PASS += 1

print(f"\n{'='*50}\nPASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
