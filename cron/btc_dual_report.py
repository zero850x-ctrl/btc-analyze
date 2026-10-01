#!/usr/bin/env python3
"""btc_dual_report.py — 主系統 + 馬丁格爾 合併狀態報告 (no_agent).

讀兩個 system log + live testnet orders + BTC 價, print 合併 summary.
設計: 工作日 08:00/12:00/16:00/20:00 HKT cron 行, deliver telegram.
靜默規則: 冇任何 active 倉 + 今日冇平倉 → print ⏳ 一句, 唔好太嘈.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

# 2026-09-13: clone 由 /tmp 搬入 ~/repos (macOS clean-tmps 會靜默清 tracked file)
BTC_REPO = os.environ.get("BTC_REPO") or os.path.expanduser("~/repos/btc-analyze")
sys.path.insert(0, BTC_REPO)
from binance_testnet_paper import current_price, _signed_request, _load_keys  # noqa

HKT = timezone(timedelta(hours=8))
ORDERS = os.path.expanduser("~/.hermes/reports/btc_testnet_orders.json")
HIST = os.path.expanduser("~/.hermes/reports/btc_testnet_closed_trades.json")
MART = os.path.expanduser("~/.hermes/reports/btc_martingale_log.json")
PAUSED_MARKER = os.path.expanduser("~/.hermes/reports/btc_main_system_paused.txt")
HIST_MARKER = os.path.expanduser("~/.hermes/reports/btc_main_system_history.txt")


def now_hkt():
    return datetime.now(HKT)


def _hkt(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(HKT)
    except Exception:
        return None


def main():
    n = now_hkt()
    out = [f"📊 雙系統報告 {n.strftime('%H:%M')} HKT"]

    # ── BTC 價 ──
    try:
        px = current_price()
        out[0] += f" — BTC ${px:,.0f}"
    except Exception:
        pass

    # ── 主系統 ──
    try:
        log = json.load(open(ORDERS))
    except Exception as e:
        log = {"orders": []}
    try:
        hist = json.load(open(HIST))
    except Exception:
        hist = {"trades": []}
    live = [o for o in log.get("orders", []) if o.get("status") == "OCO_PLACED"]
    pending = [o for o in log.get("orders", []) if o.get("status") == "LIMIT_PENDING"]
    today = n.strftime("%Y-%m-%d")
    closed_today = [t for t in hist.get("trades", [])
                    if (_hkt(t.get("closed_time") or "") or datetime.min).strftime("%Y-%m-%d") == today]
    sr_today = sum((t.get("r_multiple") or 0) for t in closed_today)
    # 全部 closed 統計
    allc = hist.get("trades", [])
    w = sum(1 for t in allc if (t.get("pnl_usdt") or 0) > 0)
    all_r = sum((t.get("r_multiple") or 0) for t in allc)

    if live:
        for o in live:
            d = _hkt(o.get("seeded_time"))
            ts = d.strftime("%m-%d %H:%M") if d else "?"
            out.append(f"  🎯 主: {o['side']} {o.get('pattern','')[:12]} @{o.get('entry_fill')} "
                       f"(開{ts}) ocoA={o.get('oco_a_id')} l3={o.get('l3_id')}")
    elif os.path.exists(PAUSED_MARKER):
        out.append("  ⏸️ 主: 已停用 (無可證實 edge)")
    elif os.path.exists(HIST_MARKER):
        out.append(f"  📡 主: 0 live 倉 (09-18 曾停用, 09-20 重開)")
    else:
        out.append(f"  📡 主: 0 live 倉")
    for o in pending:
        d = _hkt(o.get("seeded_time"))
        ts = d.strftime("%m-%d %H:%M") if d else "?"
        out.append(f"  📌 掛單 {o['side']} LIMIT @{o.get('limit_px')} (市價差 "
                   f"{((float(o.get('px_at_place') or 0) / float(o.get('limit_px') or 1)) - 1) * 100:+.2f}%, "
                   f"掛於 {ts}) RR {o.get('rr_limit')}")
    out.append(f"     已平 {len(allc)} 單 ({w}W/{len(allc)-w}L) sumR {all_r:+.2f}"
               + (f" ｜今日 {len(closed_today)} 單 sumR {sr_today:+.2f}" if closed_today else " ｜今日未平倉"))

    # ── gate 擋單 (令「0 單」可解釋) ──
    try:
        sys.path.insert(0, BTC_REPO)
        import btc_gate_log
        txt = btc_gate_log.fmt_summary(btc_gate_log.today_summary())
        if txt:
            out.append(f"     {txt}")
        elif len(live) == 0 and len(pending) == 0:
            out.append("     ✅ 今日冇 gate 擋記錄 (冇 setup 產生)")
    except Exception as e:
        out.append(f"     🚧 gate log 讀取失敗 ({e})")

    # ── 再平衡 60/40 ──
    try:
        rb_path = os.path.expanduser("~/.hermes/reports/btc_rebalance_log.json")
        led_path = os.path.expanduser("~/.hermes/reports/btc_rebalance_ledger.json")
        if os.path.exists(rb_path):
            rlg = json.load(open(rb_path))
            snaps = [s for s in (rlg.get("snapshots") or []) if s.get("post_trade")]
            evs = rlg.get("events") or []
            led = json.load(open(led_path)) if os.path.exists(led_path) else None
            if led:
                px = current_price()
                tot = led["btc"] * px + led["usdt"]
                pct = (led["btc"] * px) / tot * 100
                a_btc, a_usdt = _load_keys()
                acct = _signed_request("GET", "/api/v3/account", {}, a_btc, a_usdt)
                bal = {b["asset"]: float(b["free"]) + float(b["locked"])
                       for b in acct["balances"]}
                gap = bal.get("BTC", 0) - led["btc"]
                ev_txt = ""
                if evs:
                    d = _hkt(evs[-1].get("ts"))
                    ev_txt = (f"  ｜上次再平衡 {d.strftime('%m-%d') if d else '?'} "
                              f"{evs[-1].get('action')}")
                out.append(f"  ⚖️ 再平衡 60/40 (帳本): BTC {pct:.1f}% / {100 - pct:.1f}% USDT"
                           f" ｜偏離 {pct - 60:+.1f}% ｜總值 ${tot:,.0f}{ev_txt}")
                out.append(f"     與實際帳戶差 {gap:+.6f} BTC (馬丁格爾佔用)")
    except Exception as e:
        out.append(f"  ⚖️ 再平衡: 讀 log 失敗 ({e})")

    # ── 馬丁格爾 ──
    try:
        m = json.load(open(MART))
    except Exception as e:
        m = {}
    a = m.get("active")
    if a:
        entries = a.get("entries") or []
        tot_qty = sum(e["qty"] for e in entries)
        tot_cost = sum(e["qty"] * e["px"] for e in entries)
        avg = tot_cost / tot_qty if tot_qty else 0
        # 現價浮動
        try:
            px2 = current_price()
            fl = sum((px2 - e["px"]) * e["qty"] for e in entries) if a.get("side") == "BUY" \
                else sum((e["px"] - px2) * e["qty"] for e in entries)
            fl_txt = f"浮動 {fl:+.2f} (現價 {px2:,.0f})"
        except Exception:
            fl_txt = f"avg {avg:,.0f}"
        out.append(f"  🔵 馬丁: chain {a['id']} {a['side']} level {a['level']} "
                   f"({len(entries)}注) {fl_txt} 目標 +${a.get('target_usd')}")
        out.append(f"     開倉 {a.get('opened','?')[:16]}Z 已掛 {int((n - _hkt(a['opened'])).total_seconds()//3600)}h")
    else:
        out.append(f"  💤 馬丁: 無 active chain")
    dly = m.get("daily") or {}
    dd = dly.get(today) or {}
    out.append(f"     今日 {dd.get('wins',0)}W/{dd.get('losses',0)}L 蝕${dd.get('loss_usd',0):.2f} ｜ "
               f"累計 {sum(d.get('wins',0) for d in dly.values())}W/{sum(d.get('losses',0) for d in dly.values())}L")

    print("\n".join(out))


if __name__ == "__main__":
    main()
