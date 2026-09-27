#!/usr/bin/env python3
"""xauusd_gate_breakdown.py — 讀 btc_xauusd_gate_results.json 做穩定性細分."""
import json
import os

P = os.environ.get("GATE_RESULTS") or os.path.expanduser(
    "~/.hermes/reports/btc_xauusd_gate_results_1h.json")
rows = json.load(open(P))
print(f"(讀 {P})\n")


def st(rs):
    n = len(rs)
    if n < 2:
        return None
    m = sum(rs) / n
    sd = (sum((r - m) ** 2 for r in rs) / (n - 1)) ** 0.5
    t = m / (sd / n ** 0.5) if sd else 0.0
    return (n, m, sum(1 for r in rs if r > 0) / n * 100, t)


def ln(tag, rs):
    s = st(rs)
    if not s:
        return f"  {tag:34} n<2"
    return f"  {tag:34} n={s[0]:4}  meanR {s[1]:+.3f}  勝率 {s[2]:4.1f}%  t {s[3]:+5.2f}"


print("=== TRAIN / TEST 符號一致性 (穩定性檢查) ===")
print("  得一段正 = 唔可靠; 兩段同號 = 較可信\n")
GATES = ["GATE_BASE", "GATE_RR", "GATE_ALIGN", "GATE_FULL"]
for g in GATES:
    tr = [r["pnl_r"] for r in rows if r["label"] == "TRAIN" and r[g]]
    te = [r["pnl_r"] for r in rows if r["label"] == "TEST" and r[g]]
    a, b = st(tr), st(te)
    if not (a and b):
        print(f"  {g:12} 樣本不足")
        continue
    same = "✅ 同號" if (a[1] > 0) == (b[1] > 0) else "❌ 反號 (唔可靠)"
    print(f"  {g:12} TRAIN {a[1]:+.3f} (n={a[0]:3})  TEST {b[1]:+.3f} (n={b[0]:3})   {same}")

print("\n=== 按方向 × gate ===")
for sd in ("BUY", "SELL"):
    print(f"  [{sd}]")
    for g in ["GATE_BASE"] + GATES[1:]:
        print(ln(f"    {g}", [r["pnl_r"] for r in rows if r["side"] == sd and r[g]]))

print("\n=== SELL 單獨 TRAIN/TEST ===")
for lab in ("TRAIN", "TEST"):
    print(ln(f"    SELL {lab}", [r["pnl_r"] for r in rows if r["side"] == "SELL" and r["label"] == lab]))
    print(ln(f"    BUY  {lab}", [r["pnl_r"] for r in rows if r["side"] == "BUY" and r["label"] == lab]))

print("\n=== 樣本密度 ===")
n, days = len(rows), 730
print(f"  共 {n} 個 setup / {days} 日 = {n/days:.2f} 個/日")
print(f"  完整 gate 只有 {sum(1 for r in rows if r['GATE_FULL'])} 個 "
      f"= {sum(1 for r in rows if r['GATE_FULL'])/days*2:.2f} 個/週")

print("\n=== 對照 baseline: 純 buy&hold (同期 BTC) ===")
print("  (見 research_premium.py: 2 年 BTC 大致倍升; 559 setup sumR +34R)")

print("\n=== Bonferroni 修正 ===")
print(f"  測咗 {len(GATES)} 個 gate 層 → 門檻 |t| > 2.5 (0.05/4 雙尾近似)")
best = max(st([r["pnl_r"] for r in rows if r[g]]) for g in GATES)
print(f"  最佳層 |t| = {abs(best[3]):.2f} → {'過' if abs(best[3]) > 2.5 else '唔過'}")
