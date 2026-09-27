#!/usr/bin/env python3
"""analyze_mfe.py — 入場錯 vs 出場錯? (MFE/MAE 歸因分析)

問題 (2026-09-27 用戶問「edge如何改善」):
  25 單 sumR -7.94。究竟係「入場揀錯方向」定「入場對但出場差」?
  兩者改善方向完全唔同:
    - 入場錯 → 要換信號源
    - 出場錯 → 改 exit 結構就有 edge

方法:
  用 log 嘅 max_favorable_px (MFE) 計每單「曾經去到幾多 R」,
  對比最終 r_multiple。若 MFE 高但 final 負 → 出場問題。
"""
import json
import os
import statistics
from collections import Counter, defaultdict

H = os.path.expanduser("~/.hermes/reports")


def mfe_r(o):
    """最大有利偏移 (以 R 為單位)."""
    e, m = o.get("entry_fill"), o.get("max_favorable_px")
    s = o.get("planned_stop")
    if not (e and m and s):
        return None
    risk = abs(e - s)
    if risk <= 0:
        return None
    d = -1 if o.get("side") == "SELL" else 1
    return (m - e) * d / risk


def main():
    o = json.load(open(f"{H}/btc_testnet_orders.json"))["orders"]
    c = [x for x in o if x.get("status") == "CLOSED"]

    rows = []
    for x in c:
        r = mfe_r(x)
        if r is None:
            continue
        rows.append({
            "mfe": r,
            "final": x.get("r_multiple") or 0,
            "side": x.get("side"),
            "pattern": (x.get("pattern") or "?")[:16],
            "t": (x.get("closed_time") or "")[:10],
        })

    print(f"=== MFE 歸因分析 ({len(rows)}/{len(c)} 單有 MFE 數據) ===\n")
    if not rows:
        return

    # 1) 整體
    mfes = [r["mfe"] for r in rows]
    fin = [r["final"] for r in rows]
    print(f"MFE  (曾去到): mean {statistics.mean(mfes):+.2f}R  "
          f"median {statistics.median(mfes):+.2f}R  max {max(mfes):+.2f}R")
    print(f"最終 (實收):   mean {statistics.mean(fin):+.2f}R  "
          f"median {statistics.median(fin):+.2f}R")
    print(f"→ 蒸發: {statistics.mean(mfes) - statistics.mean(fin):.2f}R/單\n")

    # 2) 關鍵分組: MFE 好但最終蝕
    good_start_bad_end = [r for r in rows if r["mfe"] >= 1.0 and r["final"] < 0]
    bad_start = [r for r in rows if r["mfe"] < 0.5]
    good = [r for r in rows if r["final"] > 0]

    print("=== 歸因分組 ===")
    print(f"A. 入場好但輸 (MFE>=1.0R, final<0): {len(good_start_bad_end):2} 單 "
          f"({len(good_start_bad_end)/len(rows)*100:.0f}%)  "
          f"合計 {sum(r['final'] for r in good_start_bad_end):+.2f}R")
    print(f"B. 入場差 (MFE<0.5R):               {len(bad_start):2} 單 "
          f"({len(bad_start)/len(rows)*100:.0f}%)  "
          f"合計 {sum(r['final'] for r in bad_start):+.2f}R")
    print(f"C. 贏單 (final>0):                  {len(good):2} 單 "
          f"({len(good)/len(rows)*100:.0f}%)  合計 {sum(r['final'] for r in good):+.2f}R\n")

    # 3) 反事實: 若 MFE>=1R 就 1R 止賺走人
    print("=== 反事實測試 ===")
    for thr in (0.5, 1.0, 1.5, 2.0):
        sim = [min(r["mfe"], thr) if r["mfe"] > 0 else r["final"] for r in rows]
        # 更簡單: 到達 thr 就食 thr, 否則食實際 final
        sim2 = [thr if r["mfe"] >= thr else r["final"] for r in rows]
        print(f"  到 {thr:.1f}R 即止賺: sumR {sum(sim2):+.2f}  "
              f"(實際 {sum(fin):+.2f})  差 {sum(sim2)-sum(fin):+.2f}")
    print()

    # 4) 按 pattern
    print("=== 按形態 ===")
    g = defaultdict(list)
    for r in rows:
        g[r["pattern"]].append(r)
    for k, v in sorted(g.items(), key=lambda x: -len(x[1])):
        print(f"  {k:18} n={len(v):2}  MFE {statistics.mean([x['mfe'] for x in v]):+.2f}R  "
              f"final {sum(x['final'] for x in v):+.2f}R")
    print()

    # 5) 按 side
    print("=== 按方向 ===")
    for s in ("BUY", "SELL"):
        v = [r for r in rows if r["side"] == s]
        if v:
            print(f"  {s:4} n={len(v):2}  MFE {statistics.mean([x['mfe'] for x in v]):+.2f}R  "
                  f"final {sum(x['final'] for x in v):+.2f}R  "
                  f"({sum(x['final'] for x in v)/len(v):+.2f}R/單)")


if __name__ == "__main__":
    main()
