#!/usr/bin/env python3
"""費後真相回放 —— 主引擎 + 馬丁格爾。

目的：量化「手續費計唔計入贏門檻」同「TP/停損距離」對期望值嘅影響。
方法：真過去 bar（Binance mainnet 15m，testnet 價差 ~0.07%）+ 真成交記錄。

⚠️ 方法學紀律（跟 MARTINGALE_CAP_FINDINGS.md 嘅教訓）：
   - 每個「現行」變體都要有 anti-vacuous 檢查（唔可以「兩邊乜都冇做」而當一致）
   - 同一 bar 內同時觸及 TP 同 SL → 當 SL（最壞情況，唔准樂觀假設）
   - 全部變體都扣 taker 0.1%×2
   - 睇**相對**比較，唔好當絕對數字係真
   - 序列效應：每個變體獨立重跑同一批交易（唔模擬「早平倉→新倉提早開」）
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

FEE = 0.001                      # Binance taker 0.1%（兩邊都收）
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.expanduser("~/.hermes/cache/btc_klines_15m.json")


# ── bar 抓取（paged + cache）───────────────────────────────────────────────

def _fetch(interval: str, start_ms: int, end_ms: int) -> list:
    u = (f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT"
         f"&interval={interval}&startTime={start_ms}&endTime={end_ms}&limit=1000")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(u, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 3:
                raise
            print(f"    retry {attempt+1}: {type(e).__name__}", file=sys.stderr)
            time.sleep(2)


def get_bars(start_iso: str, end_iso: str, interval: str = "15m",
             use_cache: bool = True) -> list[dict]:
    """回 list[dict] {t: ms, o,h,l,c}。按 user 過濾，唔用 vwap。"""
    s = int(datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
    e = int(datetime.fromisoformat(end_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
    if use_cache and os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE))
            if c.get("start") == s and c.get("end") == e:
                return c["bars"]
        except Exception:
            pass
    out, cur = [], s
    while cur < e:
        k = _fetch(interval, cur, e)
        if not k:
            break
        out += k
        nxt = k[-1][0] + 1
        if nxt <= cur:
            break
        cur = nxt
        if len(k) < 1000:
            break
    bars = [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4])} for x in out]
    bars.sort(key=lambda b: b["t"])
    # 去重
    seen, ded = set(), []
    for b in bars:
        if b["t"] not in seen:
            seen.add(b["t"])
            ded.append(b)
    if use_cache:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump({"start": s, "end": e, "interval": interval, "bars": ded},
                  open(CACHE, "w"))
    return ded


def atr_from(bars: list[dict], n: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        trs.append(max(bars[i]["h"] - bars[i]["l"],
                       abs(bars[i]["h"] - bars[i - 1]["c"]),
                       abs(bars[i]["l"] - bars[i - 1]["c"])))
    if len(trs) < n:
        return sum(trs) / len(trs) if trs else 0.0
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


# ── 通用：單筆交易模擬（同一 SL，可變 TP 距離）──────────────────────────────

def sim_one(bars: list[dict], t0_ms: int, side: str, entry: float,
            sl: float, tp: float, qty: float, max_hours: float = 72.0) -> dict:
    """由 t0 起逐支 15m bar 走，先 SL 後 TP（同 bar 兩者都中 → 當 SL）。

    回 {outcome, exit_px, gross, fee, net, bars_held, t_exit}
    """
    risk_per_unit = abs(entry - sl)
    end_ms = t0_ms + int(max_hours * 3600 * 1000)
    d = 1 if side == "BUY" else -1
    n = 0
    for b in bars:
        if b["t"] <= t0_ms:
            continue
        if b["t"] > end_ms:
            break
        n += 1
        # 最壞情況先檢查 SL
        if d == 1:
            if b["l"] <= sl:
                ex, oc = sl, "SL"
            elif b["h"] >= tp:
                ex, oc = tp, "TP"
            else:
                continue
        else:
            if b["h"] >= sl:
                ex, oc = sl, "SL"
            elif b["l"] <= tp:
                ex, oc = tp, "TP"
            else:
                continue
        gross = (ex - entry) * qty * d
        fee = (entry + ex) * qty * FEE
        return {"outcome": oc, "exit_px": ex, "gross": gross, "fee": fee,
                "net": gross - fee, "bars": n, "t_exit": b["t"],
                "risk_usd": risk_per_unit * qty}
    # timeout
    last = None
    for b in bars:
        if t0_ms < b["t"] <= end_ms:
            last = b
    if last is None:
        return {"outcome": "NODATA", "exit_px": None, "gross": 0.0,
                "fee": 0.0, "net": 0.0, "bars": 0, "t_exit": None,
                "risk_usd": risk_per_unit * qty}
    gross = (last["c"] - entry) * qty * d
    fee = (entry + last["c"]) * qty * FEE
    return {"outcome": "TIMEOUT", "exit_px": last["c"], "gross": gross,
            "fee": fee, "net": gross - fee, "bars": n, "t_exit": last["t"],
            "risk_usd": risk_per_unit * qty}


def stats(rows: list[dict], key: str = "net") -> dict:
    v = [r[key] for r in rows if r[key] is not None]
    if not v:
        return {}
    w = [x for x in v if x > 0]
    l = [x for x in v if x <= 0]
    d = {"n": len(v), "sum": sum(v), "mean": sum(v) / len(v),
         "wr": len(w) / len(v) * 100}
    if w and l:
        aw, al = sum(w) / len(w), sum(l) / len(l)
        d["avg_win"], d["avg_loss"] = aw, al
        d["payoff"] = aw / abs(al)
        d["be_wr"] = 100 / (1 + aw / abs(al))
    return d


def show(tag: str, s: dict, extra: str = ""):
    if not s:
        print(f"  {tag:26} （冇數據）")
        return
    print(f"  {tag:26} n={s['n']:2d}  WR={s['wr']:5.1f}%  "
          f"淨 ${s['sum']:+8.2f}  平均 ${s['mean']:+6.3f}"
          + (f"  payoff {s['payoff']:.3f}  需WR {s['be_wr']:.1f}%" if "payoff" in s else "")
          + extra)


# ── 主引擎 ─────────────────────────────────────────────────────────────────

def main_engine(bars: list[dict]):
    p = os.path.expanduser("~/.hermes/reports/btc_testnet_closed_trades.json")
    t = json.load(open(p))["trades"]
    print("\n  ══ 主引擎（Flag）══════════════════════════════════════")
    print(f"  真成交 {len(t)} 筆，08-29 → 09-13")

    def ms(iso):
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)

    # ── A. 記錄值 vs 費後真相（exact，唔靠模擬）──
    rec = [x.get("pnl_usdt") or 0 for x in t]
    true_net, r_true = [], []
    for x in t:
        e, sl = x["entry_fill"], x["planned_stop"]
        q, exq, exp_ = x["qty"], x.get("exit_qty") or x["qty"], x.get("exit_fill") or e
        fee = (e * q + exp_ * exq) * FEE
        net = (x.get("pnl_usdt") or 0) - fee
        true_net.append(net)
        risk = abs(e - sl) * q
        if risk > 0:
            r_true.append(net / risk)

    print("\n  ── A. 現行記錄 vs 費後真相（exact，同一批真成交）──")
    print(f"  {'記錄值':26} n={len(rec):2d}  WR={sum(1 for x in rec if x>0)/len(rec)*100:5.1f}%  "
          f"淨 ${sum(rec):+8.2f}  平均 ${sum(rec)/len(rec):+6.3f}  "
          f"（記錄 fee 全部 = 0）")
    print(f"  {'費後真相':26} n={len(true_net):2d}  "
          f"WR={sum(1 for x in true_net if x>0)/len(true_net)*100:5.1f}%  "
          f"淨 ${sum(true_net):+8.2f}  平均 ${sum(true_net)/len(true_net):+6.3f}")
    n_rec_w = sum(1 for x in rec if x > 0)
    n_true_w = sum(1 for x in true_net if x > 0)
    print(f"  → 手續費令 {n_rec_w - n_true_w} 筆「贏單」變成蝕單 "
          f"（{n_rec_w} → {n_true_w}）")
    print(f"  → 費後 R：sum {sum(r_true):+.2f}R  平均 {sum(r_true)/len(r_true):+.3f}R "
          f"｜記錄 R：sum -7.94  平均 -0.317")

    # ── B. 同一 SL、拉遠 TP（真 bar 回放）──
    print("\n  ── B. 同一止損、拉遠 TP1（真 15m bar 回放，費後）──")
    variants = [("實況(plan TP1)", None), ("TP=0.5R", 0.5), ("TP=1.0R", 1.0),
                ("TP=1.2R", 1.2), ("TP=1.5R", 1.5), ("TP=2.0R", 2.0),
                ("TP=3.0R", 3.0), ("唔設TP(只等SL)", 999)]
    results = {}
    for tag, mult in variants:
        rows = []
        for x in t:
            e, sl = x["entry_fill"], x["planned_stop"]
            q = x["qty"]
            risk = abs(e - sl)
            if risk <= 0:
                continue
            tp = x["planned_tp1"] if mult is None else (
                e + mult * risk if x["side"] == "BUY" else e - mult * risk)
            if mult is None and tp is None:
                continue
            rows.append(sim_one(bars, ms(x["seeded_time"] + "Z" if not x["seeded_time"].endswith("Z") else x["seeded_time"]),
                                x["side"], e, sl, tp, q))
        results[tag] = rows
        show(tag, stats(rows))
        outc = {}
        for r in rows:
            outc[r["outcome"]] = outc.get(r["outcome"], 0) + 1
        print(f"  {'':26} 出場: {outc}")

    # anti-vacuous：實況變體必須有真出場（唔可以全部 NODATA）
    asis = results["實況(plan TP1)"]
    live = [r for r in asis if r["outcome"] in ("TP", "SL", "TIMEOUT")]
    print(f"\n  🔍 anti-vacuous：實況變體真出場 {len(live)}/{len(asis)}"
          f"{'  ✅' if len(live) >= len(asis)*0.9 else '  ⛔ 回放無效！'}")
    return results


# ── 馬丁格爾 ───────────────────────────────────────────────────────────────

def martingale(bars: list[dict]):
    p = os.path.expanduser("~/.hermes/reports/btc_martingale_log.json")
    d = json.load(open(p))
    ch = [c for c in d["chains"] if isinstance(c.get("profit_usd"), (int, float))]
    print("\n  ══ 馬丁格爾 ══════════════════════════════════════════")
    print(f"  完成 chain {len(ch)}，09-04 → 10-01")

    def ms(iso):
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)

    # ── A. 記錄值 vs 費後真相（exact）──
    # ⚠️ 2026-10-01 實證：記錄 profit_usd == **純 gross**，兩邊手續費都冇扣
    #    （唔係「扣咗出場費」）。所以真相 = 記錄 − fee_in − fee_out。
    print("\n  ── A. 記錄值 vs 費後真相（exact）──")
    rec = [c["profit_usd"] for c in ch]
    true_net, by_lv_true, fee_tot = [], {}, 0.0
    for c in ch:
        ent = c.get("entries") or []
        qq = sum(e["qty"] for e in ent)
        ex = c.get("closed_px") or (ent[-1]["px"] if ent else 0)
        fee = (sum(e["qty"] * e["px"] for e in ent) + qq * ex) * FEE
        fee_tot += fee
        net = c["profit_usd"] - fee
        true_net.append(net)
        by_lv_true.setdefault(c.get("level"), []).append(net)
    show("記錄值（gross，零手續費）", stats([{"net": x} for x in rec]))
    show("費後真相", stats([{"net": x} for x in true_net]))
    print(f"  → 全程手續費 ${fee_tot:.2f}（記錄從未計入）"
          f"，佔記錄淨損 ${abs(sum(rec)):.2f} 嘅 {fee_tot/abs(sum(rec))*100:.0f}%")
    print(f"\n  {'level':8} {'n':>3} {'記錄淨':>10} {'費後淨':>10} {'費後勝':>7} {'每chain費':>10}")
    for lv in sorted(by_lv_true, key=lambda x: (x is None, x)):
        v = by_lv_true[lv]
        rec_lv = [c["profit_usd"] for c in ch if c.get("level") == lv]
        print(f"  L{lv:<7} {len(v):>3} {sum(rec_lv):>+10.3f} {sum(v):>+10.3f} "
              f"{sum(1 for x in v if x>0):>4}/{len(v)}")
    # ── B. L3 幾何：手續費 vs 贏門檻 ──
    print("\n  ── B. L3 幾何（呢個係「數學上冇可能贏」嘅證明）──")
    l3 = [c for c in ch if c.get("level") == 3 and c.get("entries")]
    if l3:
        notional = statistics.median([sum(e["qty"] * e["px"] for e in c["entries"]) for c in l3])
        S0 = statistics.median([c["entries"][0]["qty"] * c["entries"][0]["px"] for c in l3])
        TARGET = 0.20
        print(f"  L3 名義中位 ${notional:.2f}（S0 中位 ${S0:.2f} × 15）")
        print(f"  入場費 ${notional*FEE:.3f} + 出場費 ${notional*FEE:.3f} = "
              f"${notional*FEE*2:.3f}")
        print(f"  贏門檻（gross，code 實際用嘅）${TARGET:.2f}")
        print(f"  → 觸發 WIN 嘅真實淨額 = {TARGET:.2f} − {notional*FEE*2:.3f} = "
              f"${TARGET - notional*FEE*2:+.3f}  "
              f"{'⛔ 贏都係蝕' if TARGET - notional*FEE*2 < 0 else '✅'}")

    # ── C. 反事實：WIN 門檻改成淨額 + 真停損（真 bar）──
    print("\n  ── C. 反事實變體（真 15m bar 回放）──")
    print("     由「第 4 加入場」之後第一個 tick 起，逐支 15m bar 逐 tick 評估")

    def chain_pnl(c, px):
        """回 (gross, net, qty) —— gross = code 用嘅 chain_net（零手續費）。"""
        ent = c["entries"]
        d_ = 1 if c["side"] == "BUY" else -1
        gross = sum((px - e["px"]) * e["qty"] for e in ent) * d_
        q = sum(e["qty"] for e in ent)
        fee = (sum(e["qty"] * e["px"] for e in ent) + q * px) * FEE
        return gross, gross - fee, q

    def sim_mart(c, mode, stop_atr=None, max_bars=96):
        """mode:
             'live_timer'  = 現行 code：gross ≥ 0.20 就 WIN；否則下一個 tick 無條件平
             'fee_timer'   = 門檻改成淨額：net ≥ 0.20 就 WIN；否則下一個 tick 無條件平
             'fee_stop'    = 門檻淨額 + 逆向 x×ATR 停損（唔用計時器）
        """
        ent = c["entries"]
        if len(ent) < 4:
            return None
        t4 = ent[-1]["time"]
        note4 = ms(t4 if t4.endswith("Z") else t4 + "Z")
        sub = [b for b in bars if b["t"] > note4][:max_bars]
        if not sub:
            return None
        atr = atr_from([b for b in bars if b["t"] <= note4][-100:])
        if not atr:
            return None
        d_ = 1 if c["side"] == "BUY" else -1
        last_px = ent[-1]["px"]
        first = sub[0]
        g0, n0, _ = chain_pnl(c, first["o"])
        if mode == "live_timer":
            # code 事實：先睇 WIN（gross），否則 level==MAX → 即刻平
            if g0 >= 0.20:
                return {"oc": "WIN", "net": n0, "gross": g0, "t": first["t"]}
            return {"oc": "CAP(timer)", "net": n0, "gross": g0, "t": first["t"]}
        # fee_timer / fee_stop：逐 tick 搵 net ≥ 0.20
        for b in sub:
            g, n, _ = chain_pnl(c, b["o"])
            if n >= 0.20:
                return {"oc": "WIN", "net": n, "gross": g, "t": b["t"]}
            if mode == "fee_timer":
                # 下一個 tick 就平（只有第一個 tick 會被評估到）
                if b is first:
                    return {"oc": "CAP(timer)", "net": n, "gross": g, "t": b["t"]}
            elif stop_atr is not None:
                adv = -d_ * (b["o"] - last_px)
                if adv >= stop_atr * atr:
                    return {"oc": "STOP", "net": n, "gross": g, "t": b["t"]}
        b = sub[-1]
        g, n, _ = chain_pnl(c, b["o"])
        return {"oc": "TIMEOUT", "net": n, "gross": g, "t": b["t"]}

    l4 = [c for c in ch if c.get("level") == 3 and len(c.get("entries") or []) == 4]
    print(f"\n  L3 chain（有完整 4 注）: {len(l4)}")
    if not l4:
        print("  ⛔ 冇 L3 chain → 反事實無法做（anti-vacuous 檢查未過）")
        return

    # ── C0. PARITY 斷言：'live_timer' 必須重現實況（唔可以係裝飾）──
    par, mism = 0, []
    for c in l4:
        r = sim_mart(c, "live_timer")
        if r is None:
            continue
        actual_win = (c["profit_usd"] or 0) > 0
        sim_win = r["oc"] == "WIN"
        if actual_win == sim_win:
            par += 1
        else:
            mism.append((c["id"], c["profit_usd"], round(r["net"], 3), r["oc"]))
    print(f"  🔍 PARITY：live_timer 變體 vs 實際記錄 {par}/{len(l4)} 一致"
          f"{'  ✅ 回放可信' if par >= len(l4)*0.9 else '  ⛔ 回放無效 —— 以下數字唔可以信'}")
    for m in mism[:5]:
        print(f"       ✗ {m[0]}  實際 {m[1]:+.3f} / 回放 {m[2]:+.3f} ({m[3]})")

    for tag, mode, stop in [
            ("現行（gross門檻+計時器）", "live_timer", None),
            ("費後門檻+計時器", "fee_timer", None),
            ("費後門檻+真停損 0.5×ATR", "fee_stop", 0.5),
            ("費後門檻+真停損 1.0×ATR", "fee_stop", 1.0),
            ("費後門檻+真停損 1.5×ATR", "fee_stop", 1.5),
            ("費後門檻+真停損 2.0×ATR", "fee_stop", 2.0)]:
        rows = [r for r in (sim_mart(c, mode, stop) for c in l4) if r]
        if not rows:
            print(f"  {tag:26} （冇結果）")
            continue
        s = stats(rows)
        outc = {}
        for r in rows:
            outc[r["oc"]] = outc.get(r["oc"], 0) + 1
        print(f"  {tag:26} n={s['n']:2d} 淨 ${s['sum']:+7.3f} 平均 ${s['mean']:+6.3f} "
              f"勝 {sum(1 for r in rows if r['net']>0):>2}/{len(rows)}  {outc}")

    # ── D. 全 34 chain（唔止 L3）兩個門檻版本對比 ──
    print("\n  ── D. 全部 34 chain：gross 門檻 vs 費後門檻（exact 重算，唔靠模擬）──")
    g_tot, n_tot = 0.0, 0.0
    for c in ch:
        ent = c.get("entries") or []
        if not ent:
            continue
        ex = c.get("closed_px") or ent[-1]["px"]
        g, n, q = chain_pnl(c, ex)
        g_tot += c["profit_usd"]
        n_tot += c["profit_usd"] - (
            sum(e["qty"] * e["px"] for e in ent) + sum(e["qty"] for e in ent) * ex) * FEE
    print(f"  {'現行記錄（gross）':26} 淨 ${g_tot:+7.3f}")
    print(f"  {'費後真相':26} 淨 ${n_tot:+7.3f}")
    print("\n  ⚠️ n 細 + in-sample + 序列效應未模擬 → 只可以睇方向，唔可以據此揀參數")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-28")
    ap.add_argument("--end", default="2026-10-03")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    print(f"  抓 BTCUSDT 15m bar {a.start} → {a.end} …")
    bars = get_bars(a.start, a.end, "15m", use_cache=not a.no_cache)
    print(f"  得到 {len(bars)} 支  "
          f"{datetime.fromtimestamp(bars[0]['t']/1000, timezone.utc):%Y-%m-%d %H:%M} → "
          f"{datetime.fromtimestamp(bars[-1]['t']/1000, timezone.utc):%Y-%m-%d %H:%M} UTC")
    main_engine(bars)
    martingale(bars)


if __name__ == "__main__":
    main()
