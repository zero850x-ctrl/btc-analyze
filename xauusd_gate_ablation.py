#!/usr/bin/env python3
"""xauusd_gate_ablation.py — XAUUSD 逐層 gate ablation

問題 (2026-09-27 用戶問): XAUUSD 邊啲 gate 層真正有貢獻?

背景:
  喺 BTC 上測 XAUUSD 主力 gate 發現「越加越差」(見 btc_xauusd_gate.py)。
  咁 XAUUSD 自己呢? 佢 cron_push_eligible 有 9 個條件串成 AND:
    BASIS / kline_confirmed / quality(OK,GOOD) / severity==ALIGNED
    / time_quality!=danger / !post_spike / 有TP1 / 有SL / priority
  從來冇人逐層拆開睇邊層真正有貢獻。

方法:
  用 XAUUSD 自己嘅 backtest.py harness, 只換 `push_eligible` 一個函數,
  跑 N 次 (數據同代碼完全不變) → 比較各層。因為係 deterministic,
  差異 100% 來自 gate 本身。

  ⚠️ 唯讀: 唔會改 XAUUSD repo 任何檔案, 只 import + monkey-patch。

用法: python3 xauusd_gate_ablation.py [days]
"""
import contextlib
import importlib.util
import io
import os
import sys

XAU = os.path.expanduser("~/repos/xauusd-analyze-v3")
sys.path.insert(0, XAU)
os.chdir(XAU)

spec = importlib.util.spec_from_file_location("xbt", os.path.join(XAU, "backtest.py"))
bt = importlib.util.module_from_spec(spec)
sys.modules["xbt"] = bt
spec.loader.exec_module(bt)

import analyze_v3 as av3  # noqa: E402


def _has_level(v):
    s = str(v or "")
    return bool(s) and s != "0" and "$0" not in s


def layer_flags(s):
    """逐層條件 (同 cron_push_eligible 一致)."""
    return {
        "L_tp_sl": _has_level(s.get("tp1")) and _has_level(s.get("stop_loss")),
        "L_quality": s.get("quality") in ("OK", "GOOD"),
        "L_aligned": s.get("counter_trend_severity") == "ALIGNED",
        "L_priority": _prio_ok(s),
        "L_kline": bool(s.get("kline_confirmed")),
        "L_session": s.get("time_quality") != "danger",
        "L_nospike": not s.get("post_spike_blocked"),
    }


def _prio_ok(s):
    mode = s.get("entry_mode", "breakout")
    p = s.get("priority", 99)
    return p <= (3 if mode in ("pullback", "boundary", "fib", "fib0786") else 2)


# 累積層 (由最寬到最嚴) — 順序跟 cron_push_eligible 嘅檢查次序
CUM = [
    ("G0 無 gate", lambda s: True),
    ("G1 +TP/SL", lambda s: layer_flags(s)["L_tp_sl"]),
    ("G2 +quality", lambda s: layer_flags(s)["L_tp_sl"]
     and layer_flags(s)["L_quality"]),
    ("G3 +ALIGNED", lambda s: all(layer_flags(s)[k] for k in
                                  ("L_tp_sl", "L_quality", "L_aligned"))),
    ("G4 +priority", lambda s: all(layer_flags(s)[k] for k in
                                   ("L_tp_sl", "L_quality", "L_aligned", "L_priority"))),
    ("G5 +kline", lambda s: all(layer_flags(s)[k] for k in
                                ("L_tp_sl", "L_quality", "L_aligned",
                                 "L_priority", "L_kline"))),
    ("G6 =cron_push", lambda s: all(layer_flags(s)[k] for k in
                                    ("L_tp_sl", "L_quality", "L_aligned", "L_priority",
                                     "L_kline", "L_session", "L_nospike"))),
    ("G7 =push(現行)", None),   # 用返真 push_eligible
]

# 單層測試 — 只加呢一層 (對照 G0)
SINGLE = [
    ("只 quality", "L_quality"),
    ("只 ALIGNED", "L_aligned"),
    ("只 priority", "L_priority"),
    ("只 kline", "L_kline"),
    ("只 session", "L_session"),
    ("只 nospike", "L_nospike"),
]

_ORIG_PUSH = bt.push_eligible


def run_gate(df_bars, df_day, gate_fn, label, quiet=True):
    """用指定 gate 跑一次 backtest. 只換 push_eligible, 其餘完全不變."""
    import time
    bt.push_eligible = gate_fn if gate_fn else _ORIG_PUSH
    buf = io.StringIO()
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(buf) if quiet else contextlib.nullcontext():
            trades = bt.run_backtest(df_bars, df_day)
    finally:
        bt.push_eligible = _ORIG_PUSH
    print(f"    · {label} → {len(trades)} 單 ({time.time()-t0:.0f}s)", flush=True)
    return trades


def stats(trades):
    if not trades:
        return {"n": 0}
    p = [float(t.total_pnl) for t in trades]
    n = len(p)
    m = sum(p) / n
    wr = sum(1 for x in p if x > 0) / n * 100
    gp = sum(x for x in p if x > 0)
    gl = abs(sum(x for x in p if x < 0))
    pf = gp / gl if gl else float("inf")
    if n > 1:
        sd = (sum((x - m) ** 2 for x in p) / (n - 1)) ** 0.5
        t = m / (sd / n ** 0.5) if sd else 0.0
    else:
        sd = t = 0.0
    # max DD
    eq, peak, mdd = 0.0, 0.0, 0.0
    for x in p:
        eq += x
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return {"n": n, "mean": m, "wr": wr, "pnl": sum(p), "pf": pf,
            "t": t, "mdd": mdd, "sd": sd}


def line(label, s):
    if not s or not s.get("n"):
        return f"  {label:20} 冇交易"
    flag = "✅" if s["mean"] > 0 and s["t"] > 2.0 else ("❌" if s["mean"] < 0 and s["t"] < -2.0 else "·")
    return (f"  {label:20} n={s['n']:4}  勝率 {s['wr']:5.1f}%  "
            f"總PnL {s['pnl']:+9.2f}  每單 {s['mean']:+7.2f}  "
            f"PF {s['pf']:>5}  t {s['t']:+5.2f}  maxDD {s['mdd']:7.2f}  {flag}")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    mode = sys.argv[2] if len(sys.argv) > 2 else "all"
    print(f"=== XAUUSD gate ablation ({days} 日, mode={mode}) ===\n", flush=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        df_bars, df_day = bt.fetch_backtest_data(days=days)
    print(f"bars: {len(df_bars)}  {df_bars.index[0]} → {df_bars.index[-1]}", flush=True)
    print(f"TRADE_COOLDOWN = {bt.TRADE_COOLDOWN} bars "
          f"(⚠️ gate 改動會改變交易序列 → 唔係乾淨子集比較)", flush=True)
    print(flush=True)

    base = run_gate(df_bars, df_day, None, "baseline")
    print(f"基準 (現行 push_eligible): {len(base)} 單\n", flush=True)

    if mode == "key":
        # 只跑關鍵層 (長時段用 — 730d H1 每 run 約 2-3 分鐘)
        pick = [CUM[0], CUM[2], CUM[3], CUM[5], CUM[7]]
        keys = ["L_quality", "L_aligned", "L_kline"]
    else:
        pick = CUM
        keys = [k for _, k in SINGLE]

    print("--- 累積層 ---", flush=True)
    for label, fn in pick:
        tr = run_gate(df_bars, df_day, fn, label)
        print(line(label, stats(tr)), flush=True)
    print(flush=True)

    print("--- 單層 (對照 G0) ---", flush=True)
    g0 = run_gate(df_bars, df_day, CUM[0][1], "G0")
    print(line("G0 無 gate (基準)", stats(g0)), flush=True)
    for key in keys:
        fn = (lambda k: (lambda s: layer_flags(s)[k]))(key)
        tr = run_gate(df_bars, df_day, fn, key)
        print(line(f"只 {key}", stats(tr)), flush=True)

    print("--- 逐層移除 (由現行 gate 開始, 每次攞走一層) ---")
    full_keys = ("L_tp_sl", "L_quality", "L_aligned", "L_priority",
                 "L_kline", "L_session", "L_nospike")
    for drop in full_keys:
        keys = tuple(k for k in full_keys if k != drop)
        fn = (lambda ks: (lambda s: all(layer_flags(s)[k] for k in ks)))(keys)
        tr = run_gate(df_bars, df_day, fn, f"-{drop}")
        print(line(f"移除 {drop}", stats(tr)))


if __name__ == "__main__":
    main()
