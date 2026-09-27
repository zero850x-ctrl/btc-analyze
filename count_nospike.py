#!/usr/bin/env python3
"""count_nospike.py — 決定性測試: nospike gate 喺 backtest 實際 fire 幾多次?

背景 (2026-09-27):
  xauusd_gate_ablation.py 用 cooldown-based backtest 做逐層 ablation,
  但 TRADE_COOLDOWN=6 令「改任何一層 → 整條交易序列唔同」→ 數字唔可比
  (鐵證: 加 quality 反而多單; 移除 nospike 單數一樣但 PnL 差 $124)。

  所以改為直接量度: 喺真 backtest 裏面, `post_spike_blocked=True` 出現幾多次?
  若係 0 → 呢層從來冇 fire → 貢獻必然 = 0 (唔使爭論 cooldown)。

方法: monkey-patch `bt._inject_push_metadata` 包一層計數
  (backtest.py 用 `from analyze_v3 import _inject_push_metadata` → 要 patch
   bt 命名空間嘅 reference, patch av3 唔會生效)。

⚠️ XAUUSD repo 唯讀 — 只 import + patch runtime。
用法: python3 count_nospike.py [days]
"""
import importlib.util
import io
import contextlib
import os
import sys

XAU = os.path.expanduser("~/repos/xauusd-analyze-v3")
sys.path.insert(0, XAU)
os.chdir(XAU)

spec = importlib.util.spec_from_file_location("xbt", os.path.join(XAU, "backtest.py"))
bt = importlib.util.module_from_spec(spec)
sys.modules["xbt"] = bt
spec.loader.exec_module(bt)

import analyze_v3 as av3x  # noqa: E402  (backtest.py 用 from ... import, 冇 module ref)

_ORIG = bt._inject_push_metadata
STATS = {"calls": 0, "setups": 0, "blocked": 0, "spike_any": 0}


def counting_inject(setups, *a, **kw):
    out = _ORIG(setups, *a, **kw)
    STATS["calls"] += 1
    STATS["setups"] += len(setups)
    for s in setups:
        if s.get("post_spike_blocked"):
            STATS["blocked"] += 1
        if s.get("post_spike_note"):
            STATS["spike_any"] += 1
    return out


bt._inject_push_metadata = counting_inject

days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
iv = bt._pick_interval(days)
print(f"=== nospike fire 計數 ({days}d, yfinance interval={iv}) ===")
print(f"SPIKE_WINDOW_BARS={av3x.SPIKE_WINDOW_BARS}  "
      f"SPIKE_ATR_MULT={av3x.SPIKE_ATR_MULT}  "
      f"TRADE_COOLDOWN={bt.TRADE_COOLDOWN}")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    df_bars, df_day = bt.fetch_backtest_data(days=days)
print(f"bars: {len(df_bars)}  {df_bars.index[0]} → {df_bars.index[-1]}")

with contextlib.redirect_stdout(buf):
    trades = bt.run_backtest(df_bars, df_day)

print(f"\n_inject_push_metadata 被 call: {STATS['calls']} 次")
print(f"  處理 setup 總數:            {STATS['setups']}")
print(f"  有 spike note (任何方向):    {STATS['spike_any']}")
print(f"  post_spike_blocked=True:    {STATS['blocked']}  ← gate 真正 fire 次數")
print(f"  產出交易:                    {len(trades)} 單")
if STATS["setups"]:
    print(f"  fire 率: {STATS['blocked']/STATS['setups']*100:.2f}% of setups")
if STATS["blocked"] == 0:
    print("\n  → ⚠️ 呢層喺此數據集從未 fire → 對交易序列貢獻必然 = 0")
else:
    print(f"\n  → 有 fire, 但 cooldown 令序列效應存在, 需要逐 setup 乾淨評估")
