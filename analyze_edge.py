#!/usr/bin/env python3
"""analyze_edge.py — 25 單 live 數據統計上夠唔夠力下結論?

問題 (2026-09-27 用戶問「edge如何改善」):
  sumR -7.94 睇落似「冇 edge」, 但 25 單樣本細。
  若統計上唔顯著 → 答案係「收集更多樣本」而唔係「換策略」。
  若顯著負 → 可以確認策略有害, 應該停。

方法: t-test + bootstrap CI + 統計力分析 (要幾多單才夠)
"""
import json
import os
import random
import statistics

H = os.path.expanduser("~/.hermes/reports")


def t_stat(x):
    n = len(x)
    if n < 2:
        return 0.0
    sd = statistics.stdev(x)
    return statistics.mean(x) / (sd / (n ** 0.5)) if sd else 0.0


def boot_ci(x, n_iter=20000, lo=2.5, hi=97.5, seed=42):
    rng = random.Random(seed)
    n = len(x)
    ms = sorted(statistics.mean(rng.choices(x, k=n)) for _ in range(n_iter))
    return ms[int(n_iter * lo / 100)], ms[int(n_iter * hi / 100)]


def main():
    o = json.load(open(f"{H}/btc_testnet_orders.json"))["orders"]
    c = [x for x in o if x.get("status") == "CLOSED"]
    rs = [x.get("r_multiple") or 0 for x in c]

    n = len(rs)
    mean = statistics.mean(rs)
    sd = statistics.stdev(rs)
    t = t_stat(rs)
    se = sd / (n ** 0.5)
    ci_lo, ci_hi = boot_ci(rs)

    print(f"=== Live 樣本統計 ({n} 單) ===")
    print(f"  meanR      {mean:+.3f}")
    print(f"  std        {sd:.3f}")
    print(f"  SE         {se:.3f}")
    print(f"  t 統計量   {t:+.2f}   (|t|>2.06 才 p<0.05, df={n-1})")
    print(f"  95% bootstrap CI  [{ci_lo:+.3f}, {ci_hi:+.3f}]")
    sig = abs(t) > 2.06
    print(f"  → {'統計顯著 (負 EV 確立)' if sig else '唔顯著 — 樣本不足以斷定'}")
    print(f"  勝率 {sum(1 for r in rs if r > 0)}/{n} = "
          f"{sum(1 for r in rs if r > 0)/n*100:.1f}%")
    print()

    # 統計力: 要幾多單才驗證到 -0.317R?
    print("=== 統計力分析 ===")
    print(f"  觀測效應量 -0.317R, std {sd:.2f}")
    for target_t in (2.06,):
        need = (target_t * sd / abs(mean)) ** 2
        print(f"  要在 |t|>{target_t} 下確認負 EV → 需要約 {need:.0f} 單")
    print(f"  現時 {n} 單 → 達成度 {n/((2.06*sd/abs(mean))**2)*100:.0f}%")
    print()

    # 若真 meanR 為 0 (冇 edge), 觀測到咁差嘅機率?
    print("=== 虛無假設檢定 (H0: 真 meanR = 0) ===")
    se0 = sd / (n ** 0.5)
    if se0:
        z = mean / se0
        print(f"  z = {z:+.2f}")
        print(f"  正態近似單尾 p ≈ {0.5 * (1 + __import__('math').erf(z / 2**0.5)):.3f}")
        print(f"  → {'否決 H0' if abs(z) > 1.96 else '無法否決 H0 — 呢 25 單可能只係運氣差'}")
    print()

    # 分方向
    print("=== 分方向 ===")
    for s in ("BUY", "SELL"):
        v = [x.get("r_multiple") or 0 for x in c if x.get("side") == s]
        if len(v) >= 2:
            print(f"  {s:4} n={len(v):2}  meanR {statistics.mean(v):+.3f}  "
                  f"t {t_stat(v):+.2f}  ${'顯著' if abs(t_stat(v))>2.06 else '唔顯著'}")
    print()

    # 每單 fee vs R (關鍵: R 太細時 fee 會食晒 edge)
    print("=== 交易成本 / R 比例 (最關鍵) ===")
    per = []
    for x in c:
        e = x.get("entry_fill") or 0
        q = x.get("qty") or 0
        s = x.get("planned_stop") or 0
        notional = e * q
        risk_usd = abs(e - s) * q
        if risk_usd > 0:
            per.append({"notional": notional, "risk": risk_usd,
                        "fee": notional * 0.001 * 2})
    if per:
        m_n = statistics.mean(p["notional"] for p in per)
        m_r = statistics.mean(p["risk"] for p in per)
        m_f = statistics.mean(p["fee"] for p in per)
        print(f"  平均名義成交額 ${m_n:,.2f}/單")
        print(f"  平均 risk        ${m_r:,.2f}/單  ({m_r/m_n*100:.2f}% of notional)")
        print(f"  平均來回 fee     ${m_f:,.2f}/單  (0.1%×2)")
        print(f"  → fee 佔 R 比例  {m_f/m_r:.2f}R/單  <-- 每單要先賺呢個數才夠付費")
        print()
        fee_r = sum(p["fee"] / p["risk"] for p in per)
        print(f"  {len(per)} 單免 fee sumR : {sum(rs):+.2f}")
        print(f"  實盤 fee 調整後 sumR : {sum(rs) - fee_r:+.2f}  "
              f"(減 {fee_r:.2f}R)")
        print(f"  實盤 meanR           : {(sum(rs) - fee_r)/len(per):+.3f}/單")
        print()
        print("  ⚠️ testnet log 記 fee 0.0 (testnet 唔收費) → 上面 25 單數字"
              "係『免手續費』成績。實盤會顯著更差。")
        print("  ⚠️ 根因: R 只有 ${:.2f} 但 fee 按名義額 ${:,.0f} 收 → "
              "R 越細, fee 佔比越高。".format(m_r, m_n))


if __name__ == "__main__":
    main()
