#!/usr/bin/env python3
"""xauusd_nospike_deep.py — 深入查 post-spike chase gate (nospike)

用戶 2026-09-27 要求: XAUUSD 180d ablation 顯示移除 nospike 反而多賺
(+317.51 → +441.31, cooldown confound 下嘅數字) → 要乾淨版驗證。

機制 (analyze_v3._post_spike_state):
    move = close[-2] - close[-2-W]      (W = SPIKE_WINDOW_BARS, 已收市 bar)
    若 |move| > MULT × ATR  → spike, 方向 = move 符號
    setup 方向同 spike 方向一致 → post_spike_blocked = True (唔推送)

官方默認: MULT=3.0 (09-08 由 2.0 調高, 因 2.0 喺普通趨勢延續都 fire),
          W=4 (最近 4 條已收市 M30 bar)

方法: btc_xauusd_gate.py 已記錄每筆 setup 嘅 closes_tail + ATR
      → 本 script 離線重算任何 (MULT, W) 組合, 唔使重跑 backtest。

⚠️ 逐 setup 獨立評估 (唔經 portfolio cooldown) → 乾淨子集比較。

用法:
  GATE_RESULTS=~/.hermes/reports/xxx.json python3 xauusd_nospike_deep.py
"""
import json
import os
import sys

P = os.environ.get("GATE_RESULTS") or os.path.expanduser(
    "~/.hermes/reports/btc_xauusd_gate_results_GCF_1h.json")


def load():
    with open(os.path.expanduser(P)) as f:
        return json.load(f)


def st(rs):
    n = len(rs)
    if n == 0:
        return None
    m = sum(rs) / n
    wr = sum(1 for r in rs if r > 0) / n * 100
    if n > 1:
        var = sum((r - m) ** 2 for r in rs) / (n - 1)
        sd = var ** 0.5
        t = m / (sd / n ** 0.5) if sd else 0.0
    else:
        sd = t = 0.0
    wins = [r for r in rs if r > 0]
    loss = [r for r in rs if r <= 0]
    pf = (sum(wins) / abs(sum(loss))) if loss and sum(loss) != 0 else float("inf")
    return {"n": n, "mean": m, "wr": wr, "t": t, "sum": sum(rs), "pf": round(pf, 2)}


def ln(name, s, extra=""):
    if not s:
        print(f"  {name:26} 冇樣本")
        return
    print(f"  {name:26} n={s['n']:4}  勝率 {s['wr']:5.1f}%  meanR {s['mean']:+.3f}  "
          f"t {s['t']:+5.2f}  PF {s['pf']:>5}  sumR {s['sum']:+7.2f}  {extra}")


def spike_of(row, mult, win):
    """複製 av3._post_spike_state 邏輯 (參數化). 回傳 'up'/'down'/None."""
    ct = row.get("ct") or []
    atr = row.get("atr") or 0
    need = win + 2
    if len(ct) < need or atr <= 0:
        return None
    ref = ct[-2]                    # 最後已收市 bar
    base = ct[-2 - win]
    move = ref - base
    if abs(move) <= mult * atr:
        return None
    return "down" if move < 0 else "up"


def would_block(row, mult, win):
    sp = spike_of(row, mult, win)
    if sp is None:
        return False
    return (sp == "down" and row["side"] == "SELL") or (sp == "up" and row["side"] == "BUY")


def main():
    rows = load()
    print(f"=== nospike 深入分析 ({os.path.basename(P)}, {len(rows)} setup) ===\n")

    for seg in ("TRAIN", "TEST", "ALL"):
        rs = rows if seg == "ALL" else [r for r in rows if r["label"] == seg]
        if not rs:
            continue
        print(f"--- {seg} ---")
        blk = [r["pnl_r"] for r in rs if r.get("post_spike")]
        pss = [r["pnl_r"] for r in rs if not r.get("post_spike")]
        ln("官方 gate: 被 block", st(blk))
        ln("官方 gate: 通過", st(pss))
        ln("  (全部)", st([r["pnl_r"] for r in rs]))
        if blk and pss:
            d = st(pss)["mean"] - st(blk)["mean"]
            print(f"  → 通過 - 被擋 meanR 差 = {d:+.3f}  "
                  f"({'gate 有效 (block 嘅較差)' if d > 0 else '❌ gate 反向 (block 嘅更好)'})")
        print()

    # spike 方向分佈
    print("--- spike 狀態分佈 (全期) ---")
    for label, fn in (
        ("官方 spike (3.0×ATR,4bar) 一致 → block", lambda r: r.get("post_spike")),
        ("spike 但反方向 (搏反彈, 放行)", lambda r: spike_of(r, 3.0, 4) is not None and not r.get("post_spike")),
        ("冇 spike", lambda r: spike_of(r, 3.0, 4) is None),
    ):
        ln(label, st([r["pnl_r"] for r in rows if fn(r)]))

    # 敏感度
    print("\n--- 敏感度: MULT × WINDOW (全期) ---")
    print("  (cell = 被 block 嘅 n / meanR || 通過嘅 meanR)")
    mults = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    wins_ = [2, 3, 4, 6]
    hdr = "  MULT\\W  " + "".join(f"{w:>22}" for w in wins_)
    print(hdr)
    grid = {}
    for m in mults:
        cells = []
        for w in wins_:
            b = [r["pnl_r"] for r in rows if would_block(r, m, w)]
            p = [r["pnl_r"] for r in rows if not would_block(r, m, w)]
            sb, sp = st(b), st(p)
            grid[(m, w)] = (sb, sp)
            cells.append(f"{sb['n'] if sb else 0:>3}/{(sb['mean'] if sb else 0):+.2f} |"
                         f"{(sp['mean'] if sp else 0):+.2f}")
        mark = "  ←官方" if m == 3.0 else ""
        print(f"  {m:>5}  " + "".join(f"{c:>22}" for c in cells) + mark)

    # 每個組合「加呢層之後整體 meanR」
    print("\n--- 加 nospike 之後整體 meanR (對照: 唔加 = "
          f"{st([r['pnl_r'] for r in rows])['mean']:+.3f}) ---")
    base = st([r["pnl_r"] for r in rows])["mean"]
    scored = []
    for m in mults:
        for w in wins_:
            p = [r["pnl_r"] for r in rows if not would_block(r, m, w)]
            sp = st(p)
            if sp:
                scored.append((sp["mean"], m, w, sp))
    scored.sort(reverse=True)
    for mean, m, w, sp in scored[:6]:
        mark = "  ←官方" if (m, w) == (3.0, 4) else ""
        print(f"  MULT={m:<4} W={w}  n={sp['n']:4}  meanR {mean:+.3f}  "
              f"t {sp['t']:+.2f}  (vs 唔加 {base:+.3f}, 差 {mean - base:+.3f}){mark}")
    print("  ...")
    for mean, m, w, sp in scored[-3:]:
        print(f"  MULT={m:<4} W={w}  n={sp['n']:4}  meanR {mean:+.3f}  "
              f"t {sp['t']:+.2f}  (vs 唔加 {base:+.3f}, 差 {mean - base:+.3f})")

    n_tests = len(mults) * len(wins_)
    print(f"\n  ⚠️ 測咗 {n_tests} 個組合 → Bonferroni 門檻 |t| > "
          f"{2.576:.1f} (0.05/{n_tests} 雙尾)")
    best_t = max(abs(sp["t"]) for _, _, _, sp in scored)
    print(f"  最佳 |t| = {best_t:.2f} → {'過' if best_t > 2.576 else '唔過'}")

    # 邊際: 官方 gate block 嘅 setup 若放行會點
    print("\n--- 決策: 官方 nospike 層值唔值 ---")
    blk = [r["pnl_r"] for r in rows if r.get("post_spike")]
    sb = st(blk)
    if sb:
        print(f"  被擋 {sb['n']} 單 ({sb['n']/len(rows)*100:.1f}%), meanR {sb['mean']:+.3f}, "
              f"t {sb['t']:+.2f}, 合計 {sb['sum']:+.2f}R")
        print(f"  即係: 擋走嘅係 {'虧損' if sb['mean'] < 0 else '賺錢'} 單 "
              f"→ gate {'正確' if sb['mean'] < 0 else '❌ 誤殺'}")
        if sb["mean"] >= 0:
            print(f"  ⚠️ 若放行呢 {sb['n']} 單 → 整體 sumR 由 "
                  f"{sum(r['pnl_r'] for r in rows):+.2f} → "
                  f"{sum(r['pnl_r'] for r in rows) + sb['sum']:+.2f}")


if __name__ == "__main__":
    main()
