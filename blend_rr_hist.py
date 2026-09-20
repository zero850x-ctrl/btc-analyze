#!/usr/bin/env python3
"""blend_rr_hist.py — 用歷史 25 單驗證 blended RR (配合 3 段 exit) vs TP1-only RR."""
import json
import os

HIST = os.path.expanduser("~/.hermes/reports/btc_testnet_closed_trades.json")
hist = json.load(open(HIST))

rows = []
for t in hist["trades"]:
    e, sl = t.get("entry_fill"), t.get("planned_stop")
    tp1, tp2 = t.get("planned_tp1"), t.get("planned_tp2")
    r = t.get("r_multiple")
    pnl = t.get("pnl_usdt")
    if not (e and sl and tp1) or r is None:
        continue
    risk = abs(e - sl)
    if risk <= 0:
        continue
    rr1 = abs(tp1 - e) / risk
    if tp2:
        # 保守 blended: 尾倉當 0 (只計 1/3 TP1 + 1/3 TP2)
        blend_cons = (abs(tp1 - e) / 3 + abs(tp2 - e) / 3) / risk
        # 進取 blended: 尾倉當食到 TP2 距離
        blend_aggr = (abs(tp1 - e) / 3 + abs(tp2 - e) * 2 / 3) / risk
    else:
        blend_cons = blend_aggr = rr1
    rows.append({"rr1": rr1, "bc": blend_cons, "ba": blend_aggr, "R": r,
                 "pnl": pnl or 0, "tp2": bool(tp2)})

print(f"有效樣本: {len(rows)}/{len(hist['trades'])} (有 TP2: {sum(1 for x in rows if x['tp2'])})\n")


def stat(name, g):
    if not g:
        print(f"{name}: 0 單")
        return
    sr = sum(x["R"] for x in g)
    w = sum(1 for x in g if x["R"] > 0)
    print(f"{name}: {len(g)} 單 sumR={sr:+.2f} pnl=${sum(x['pnl'] for x in g):+.2f} 勝={w}/{len(g)}")
    wy = [x["R"] for x in g if x["R"] > 0]
    ls = [x["R"] for x in g if x["R"] <= 0]
    if wy:
        print(f"    贏 {len(wy)} 個 +{sum(wy):.2f}R (平均 +{sum(wy)/len(wy):.2f})")
    if ls:
        print(f"    輸 {len(ls)} 個 {sum(ls):.2f}R (平均 {sum(ls)/len(ls):.2f})")


for key, label in (("rr1", "TP1-only RR (現行 gate)"),
                   ("bc", "blended RR 保守 (1/3 TP1 + 1/3 TP2)"),
                   ("ba", "blended RR 進取 (1/3 TP1 + 2/3 TP2)")):
    print(f"═══ {label} ═══")
    stat("  ✅ 過 gate (>=1.2)", [x for x in rows if x[key] >= 1.2])
    stat("  ❌ 唔過 (<1.2)", [x for x in rows if x[key] < 1.2])
    print()

print("=== 現行 gate (TP1-only >=1.2) 擋走咗幾多 ===")
blocked = [x for x in rows if x["rr1"] < 1.2]
print(f"擋 {len(blocked)}/{len(rows)} 單, 佢哋原本 sumR={sum(x['R'] for x in blocked):+.2f} "
      f"(贏 {sum(1 for x in blocked if x['R']>0)} 個)")
