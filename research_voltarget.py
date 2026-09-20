#!/usr/bin/env python3
"""research_voltarget.py — A: BTC 曝險按波動率目標化 (vol targeting) 值唔值? (2026-09-19)

問題: 60/40 固定權重嘅 Sharpe 1.18 係 BTC 唯一證實有結構優勢嘅嘢。
      可唔可以再進一步 —— 唔用固定 60%, 而係按 realised vol 動態調曝險
      (高波動減倉、低波動加倉)?

⚠️ 最重要嘅方法論陷阱 (呢個 script 就係為咗避開佢):
   vol targeting **機械性降低平均曝險**。低曝險本身就會令 Sharpe 睇落好啲、
   maxDD 細啲。所以「vol target Sharpe 高過 60/40」**唔等於**有 edge —— 可能純粹係減倉。
   ⇒ 每個 vol-target 變體都配一個「同平均曝險嘅固定權重孖生對照」(matched twin):
       vol-target 嘅平均倉位 = ã  →  另外跑 sim_weight(target=ã)
     只有 vol-target 贏**自己嗰個孖生對照**, 先算係真嘅時變曝險 edge。

可否證標準 (跑完睇呢三條):
   (a) Sharpe 要贏固定 60/40
   (b) Sharpe 要贏自己嘅孖生對照 (即係唔可以純粹靠減倉)
   (c) 分段一致 (唔可以只靠一段)
   三條全部成立 = 真 edge; 只成立 (a) = 純粹減倉; 全部唔成立 = 冇用。

無 look-ahead: day i 嘅決定只用 i-1 或之前嘅回報 (rv 有 shift(1))。

用法:
  python3 research_voltarget.py              # 主實驗
  python3 research_voltarget.py --selfcheck  # 不變式檢查 (改呢個檔之前跑)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from research_dca import FEE, YEARS, load_daily, metrics, sim_bh, sim_weight

FREQ = 90          # 每季 (同 live 一致)
BAND = 0.05        # 偏離 5% 即做 (同 live 一致)
LOOKBACK = 30      # realised vol 回望日數


def realised_vol(px, lookback=LOOKBACK):
    """年化 realised vol, **shift(1)** 保證 day i 只用 i-1 或之前嘅回報 (無 look-ahead)。"""
    px = np.asarray(px, dtype=float)
    logret = np.empty(len(px))
    logret[0] = np.nan
    logret[1:] = np.log(px[1:] / px[:-1])
    rv = np.full(len(px), np.nan)
    for i in range(len(px)):
        lo = max(0, i - lookback)
        w = logret[lo:i]                      # 嚴格 slice 到 i-1
        w = w[np.isfinite(w)]
        if len(w) >= max(5, lookback // 2):
            rv[i] = w.std(ddof=1) * np.sqrt(365)
    return rv


def block_boot_ci(x, block=90, n=2000, seed=42):
    """Moving-block bootstrap 嘅 95% CI (回報有自相關, iid 會假顯著)。

    block 長度 = 一個再平衡週期 (90 日), 令重抽保留倉位期間嘅持續性。
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    T = len(x)
    if T <= block + 1:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(T / block))
    hi = T - block
    means = np.empty(n)
    for i in range(n):
        st = rng.integers(0, hi + 1, size=nb)
        s = np.concatenate([x[j:j + block] for j in st])[:T]
        means[i] = s.mean()
    return tuple(np.percentile(means, [2.5, 97.5]))


def sim_voltarget(d, target_vol=0.60, cap=1.0, base=0.6, freq_days=FREQ, band=BAND,
                  lookback=LOOKBACK, fee=FEE):
    """按 realised vol 調 BTC 權重: w = clip(target_vol / rv, 0, cap)。

    機制同 research_dca.sim_weight **逐行一致** (同一 band / freq / fee / 成交方式),
    唯一分別係 target 逐日變。rv 缺失 (開頭 warmup) → 用 base。
    """
    px = np.asarray(d["close"], dtype=float)
    n = len(px)
    rv = realised_vol(px, lookback)
    wt = np.full(n, np.nan)
    for i in range(n):
        r = rv[i]
        wt[i] = base if not np.isfinite(r) or r <= 1e-9 else min(cap, max(0.0, target_vol / r))

    btc = wt[0] / px[0]
    cash = 1.0 - wt[0]
    eq = []
    last = 0
    for i in range(n):
        p = px[i]
        tot = btc * p + cash
        if tot > 0:
            drift = abs(btc * p / tot - wt[i])
            if (i - last >= freq_days) or drift > band:
                diff = tot * wt[i] - btc * p
                if abs(diff) > tot * 0.01:
                    if diff > 0:
                        buy = min(diff, cash)
                        btc += buy * (1 - fee) / p
                        cash -= buy
                    else:
                        sell = min(-diff, btc * p)
                        btc -= sell / p
                        cash += sell * (1 - fee)
                last = i
        eq.append(btc * p + cash)
    return np.array(eq), wt


def show(rows, title, extra=None):
    print(f"\n{'=' * 104}\n{title}\n{'=' * 104}")
    head = f"{'策略':<34} {'淨值':>8} {'CAGR%':>7} {'maxDD%':>8} {'Sharpe':>7} {'Calmar':>7} {'vs B&H':>7}"
    if extra:
        head += f" {extra:>10}"
    print(head)
    print("-" * 104)
    for r in rows:
        line = (f"{r['label']:<34} {r['final']:>8.3f} {r['CAGR%']:>7.1f} {r['maxDD%']:>8.1f} "
                f"{r['Sharpe']:>7.2f} {r['Calmar']:>7.2f} {r['vs_BH']:>7.2f}")
        if extra:
            line += f" {r.get('avg_w', float('nan')):>10.3f}"
        print(line)


def _selfcheck():
    """不變式: (1) 恆價 → 冇賺蝕; (2) 恆定 rv → vol-target 必須等於同權重嘅 sim_weight。"""
    import pandas as pd

    ok = True

    # 1. 恆價: 任何策略淨值都要係 1.0 (冇價差可賺, fee 都唔應該觸發)
    n = 400
    d = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="D"),
                      "close": np.full(n, 100.0)})
    for name, fn in (("bh", lambda x: sim_bh(x)),
                     ("w60", lambda x: sim_weight(x, 0.6, FREQ, BAND, 1, FEE)),
                     ("vt", lambda x: sim_voltarget(x, 0.6)[0])):
        v = fn(d)
        good = np.allclose(v, 1.0, atol=1e-9)
        ok &= good
        print(f"  {'OK ' if good else 'FAIL'} 恆價 {name}: 淨值 {v[-1]:.10f} (要 1.0)")

    # 2. **真正強制 rv 恆定** → vol-target 必須逐日等於 sim_weight(target=target_vol/rv)
    #    ⚠️ 第一版呢個測試冇牙: 佢冇 patch rv, 所以 test 實際上仍然用真實 rv。
    #       撞 cap 嗰個 case 之所以「過」, 係因為 min(1.0, tv/rv) 幾乎永遠 = 1.0
    #       (對 rv 不敏感) 而好彩同 sim_weight(1.0) 撞中, 唔係機制真嘅對齊。
    #       內部權重 case 即刻暴露出嚟。所以必須 monkey-patch realised_vol。
    rng = np.random.default_rng(7)
    px = [100.0]
    daily_vol = 0.02
    for _ in range(600):
        px.append(px[-1] * float(np.exp(rng.normal(0, daily_vol))))
    px = np.array(px)
    d2 = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=len(px), freq="D"),
                       "close": px})
    rv_real = realised_vol(px, LOOKBACK)
    for rv_const in (0.20, 0.35, 0.60):
        for tv in (0.10, 0.25, 0.90):
            w_const = min(1.0, tv / rv_const)
            orig = globals()["realised_vol"]
            try:
                globals()["realised_vol"] = (
                    lambda p, lookback=LOOKBACK, rc=rv_const: np.full(len(p), rc))
                a, wt = sim_voltarget(d2, tv, 1.0, w_const, FREQ, BAND, LOOKBACK, FEE)
            finally:
                globals()["realised_vol"] = orig
            b = sim_weight(d2, w_const, FREQ, BAND, 1, FEE)
            aligned = np.allclose(wt, w_const, rtol=1e-12)
            good = np.allclose(a, b, rtol=1e-9, atol=1e-9) and aligned
            ok &= good
            kind = "內部" if w_const < 1.0 else "cap"
            print(f"  {'OK ' if good else 'FAIL'} rv={rv_const:.2f} target={tv:.2f} → "
                  f"w={w_const:.4f} ({kind}): vt {a[-1]:.6f} vs w {b[-1]:.6f}"
                  + ("" if aligned else "  [權重冇對齊!]"))

    # 3. 無 look-ahead: 改動最後一日價格, 之前所有 w 都唔可以變
    d3 = d2.copy()
    wt_before = sim_voltarget(d3, 0.60)[1]
    d3.loc[len(d3) - 1, "close"] = float(px[-2]) * 3
    wt_after = sim_voltarget(d3, 0.60)[1]
    good = np.allclose(wt_before[:-1], wt_after[:-1], equal_nan=True)
    ok &= good
    print(f"  {'OK ' if good else 'FAIL'} 無 look-ahead: 改最後一日價 → 之前權重不變")

    print(f"\n{'✅ 全部通過' if ok else '❌ 有 FAIL'}")
    return 0 if ok else 1


def main():
    d = load_daily(YEARS)
    print(f"數據: {len(d)} 日  {d['date'].iloc[0].date()} → {d['date'].iloc[-1].date()}")
    print(f"BTC: ${d['close'].iloc[0]:,.0f} → ${d['close'].iloc[-1]:,.0f}  "
          f"({(d['close'].iloc[-1] / d['close'].iloc[0] - 1) * 100:+.0f}%)")
    rv = realised_vol(d["close"].values, LOOKBACK)
    rvf = rv[np.isfinite(rv)]
    print(f" realised vol ({LOOKBACK}日, 年化): 中位 {np.median(rvf)*100:.0f}%  "
          f"p10 {np.percentile(rvf,10)*100:.0f}%  p90 {np.percentile(rvf,90)*100:.0f}%")

    bench = d["close"].values

    # ---- 主表: 每個 vol-target 變體 + 自己嘅同曝險孖生對照 ----
    rows = [metrics(sim_bh(d), bench, "B&H (100/0)"),
            metrics(sim_weight(d, 0.6, FREQ, BAND, 1, FEE), bench, "固定 60/40 (live 設定)")]
    for tv in (0.30, 0.40, 0.50, 0.60, 0.80):
        eq, wt = sim_voltarget(d, tv)
        r = metrics(eq, bench, f"volTarget {tv:.0%}")
        r["avg_w"] = float(np.mean(wt))
        rows.append(r)
    show(rows, f"A1. vol targeting vs 固定權重  (11 年, 每季+band{BAND:.0%}, fee {FEE:.1%})")
    print("  ⚠️ 上面 Sharpe 高唔等於有 edge —— 睇下一個表嘅孖生對照。")

    # ---- 決定性表: 逐個 vol-target 對自己嘅同平均曝險固定權重 ----
    print(f"\n{'=' * 104}")
    print("A2. 決定性對照: vol-target vs 「同平均曝險」嘅固定權重 (matched twin)")
    print("    孖生贏唔到 = 改善純粹來自減倉 (冇時變曝險 edge)")
    print('=' * 104)
    print(f"{'變體':<22} {'平均倉':>7} | {'vt Sharpe':>9} {'twin Sharpe':>11} {'Δ':>7} | "
          f"{'vt maxDD':>9} {'twin maxDD':>10} | 判定")
    print("-" * 104)
    for tv in (0.30, 0.40, 0.50, 0.60, 0.80):
        eq, wt = sim_voltarget(d, tv)
        aw = float(np.mean(wt))
        a = metrics(eq, bench, "")
        b = metrics(sim_weight(d, aw, FREQ, BAND, 1, FEE), bench, "")
        ds = a["Sharpe"] - b["Sharpe"]
        verdict = ("✅ 有 edge" if ds > 0.05 and a["maxDD%"] <= b["maxDD%"] + 0.5
                   else ("~ 平手" if abs(ds) <= 0.05 else "❌ 純減倉"))
        print(f"volTarget {tv:<12.0%} {aw:>7.3f} | {a['Sharpe']:>9.2f} {b['Sharpe']:>11.2f} "
              f"{ds:>+7.2f} | {a['maxDD%']:>9.1f} {b['maxDD%']:>10.1f} | {verdict}")

    # ---- 決定性: 專門審「唯一贏嘅格」(tA2 顯示 vt30 Δ+0.08), 睇係真 plateau 定單點噪音 ----
    print(f"\n{'=' * 104}")
    print("A3. 贏家審查 — vt 30% vs 自己孖生對照, 逐段睇 (6 段)")
    print('=' * 104)
    n = len(d)
    K = 6
    print(f"{'期間':<24} {'BTC':>9} | {'vt30':>7} {'twin30':>7} {'vt60':>7} {'60/40':>7} | "
          f"{'vt30 DD':>8} {'60/40 DD':>9}")
    print("-" * 104)
    wins = 0
    tot_seg = 0
    for k in range(K):
        i0, i1 = k * n // K, (k + 1) * n // K
        sub = d.iloc[i0:i1].reset_index(drop=True)
        if len(sub) < 120:
            continue
        bch = sub["close"].values
        e30, w30 = sim_voltarget(sub, 0.30)
        aw30 = float(np.mean(w30))
        m30 = metrics(e30, bch, "")
        mt30 = metrics(sim_weight(sub, aw30, FREQ, BAND, 1, FEE), bch, "")
        e60, _ = sim_voltarget(sub, 0.60)
        m60 = metrics(e60, bch, "")
        mfix = metrics(sim_weight(sub, 0.6, FREQ, BAND, 1, FEE), bch, "")
        chg = (bch[-1] / bch[0] - 1) * 100
        tot_seg += 1
        if m30["Sharpe"] > mt30["Sharpe"]:
            wins += 1
        print(f"{str(sub['date'].iloc[0].date())}→{str(sub['date'].iloc[-1].date()):<11} "
              f"{chg:>+8.0f}% | {m30['Sharpe']:>7.2f} {mt30['Sharpe']:>7.2f} "
              f"{m60['Sharpe']:>7.2f} {mfix['Sharpe']:>7.2f} | "
              f"{m30['maxDD%']:>8.1f} {mfix['maxDD%']:>9.1f}")
    print(f"  → vt30 贏自己孖生對照: {wins}/{tot_seg} 段 "
          f"({'段段一致 = 可能係真' if wins == tot_seg else '分段唔一致 = 唔可以信呢個 Δ'})")

    # ---- 多重比較檢查: 喺贏家附近細掃, 睇係 plateau 定單點 ----
    print(f"\n{'=' * 104}")
    print("A4. 多重比較檢查 — 細掃目標波動率 × lookback, 計有幾多格真過門檻")
    print("    (1-2 格贏 = 雜訊; 整片區域贏 = 真 edge)")
    print('=' * 104)
    targets = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45)
    lookbacks = (20, 30, 60, 90)
    print(f"{'lookback':<10}" + "".join(f"{t:>9.0%}" for t in targets))
    print("-" * 104)
    deltas = {}
    for lb in lookbacks:
        cells = []
        for tv in targets:
            eq, wt = sim_voltarget(d, tv, lookback=lb)
            aw = float(np.mean(wt))
            a = metrics(eq, bench, "")
            b = metrics(sim_weight(d, aw, FREQ, BAND, 1, FEE), bench, "")
            dd = a["Sharpe"] - b["Sharpe"]
            deltas[(lb, tv)] = dd
            mark = "*" if dd > 0.05 else " "
            cells.append(f"{dd:>+8.2f}{mark}")
        print(f"lb={lb:<7}" + "".join(cells))
    hit = sum(1 for v in deltas.values() if v > 0.05)
    print("-" * 104)
    print(f"  * = ΔSharpe > +0.05 (門檻)。命中 {hit}/{len(deltas)} 格 "
          f"({hit/len(deltas)*100:.0f}%)")
    print(f"  Δ 範圍 [{min(deltas.values()):+.2f}, {max(deltas.values()):+.2f}]  "
          f"中位 {np.median(list(deltas.values())):+.3f}")

    # ---- A5. 配對信賴區間: +0.08 Sharpe 係真定噪音? ----
    # 兩條 equity curve 高度重疊, 所以唔可以用「Sharpe 大咗 0.08」就當贏。
    # 用配對日回報差 + block bootstrap (block = 一個再平衡週期), 因為回報有自相關
    # (iid 檢定會假顯著 —— 同 overlapping-position 嗰個陷阱一樣)。
    print(f"\n{'=' * 104}")
    print("A5. 配對檢定 — vt vs 孖生對照嘅日回報差, block bootstrap 95% CI")
    print("    (CI 包含 0 = 未確立; iid p 只列出作對照, 唔可以引用)")
    print('=' * 104)
    print(f"{'變體':<14} {'平均倉':>7} {'ΔSharpe':>8} {'Δ年化%':>9} "
          f"{'block CI 95%':>26} {'iid p':>8} {'判定'}")
    print("-" * 104)
    for tv in (0.20, 0.30, 0.40):
        eqv, wtv = sim_voltarget(d, tv)
        aw = float(np.mean(wtv))
        eqt = sim_weight(d, aw, FREQ, BAND, 1, FEE)
        rv_ = np.diff(eqv) / eqv[:-1]
        rt_ = np.diff(eqt) / eqt[:-1]
        diff = rv_ - rt_
        diff = diff[np.isfinite(diff)]
        ci = block_boot_ci(diff, block=FREQ, n=2000)
        mv = metrics(eqv, bench, "")
        mt = metrics(eqt, bench, "")
        # iid t (只作對照)
        se = diff.std(ddof=1) / np.sqrt(len(diff))
        t_iid = diff.mean() / se if se > 0 else 0.0
        from math import erf, sqrt
        p_iid = 2 * (1 - 0.5 * (1 + erf(abs(t_iid) / sqrt(2))))
        real = ci[0] > 0
        print(f"volTarget {tv:<5.0%} {aw:>7.3f} {mv['Sharpe'] - mt['Sharpe']:>+8.2f} "
              f"{diff.mean() * 365 * 100:>+9.2f} "
              f"{f'[{ci[0]*365*100:+.2f}%, {ci[1]*365*100:+.2f}%]':>26} "
              f"{p_iid:>8.4f} {'✅ CI 排除 0' if real else '❌ CI 包含 0 (未確立)'}")
    print("\n  ⚠️ iid p 明顯細過 block CI 嘅結論 → 正正係自相關造成嘅假顯著 (唔可以引用)。")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        sys.exit(_selfcheck())
    main()
