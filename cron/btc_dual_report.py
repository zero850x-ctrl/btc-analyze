#!/usr/bin/env python3
"""btc_dual_report.py — 主系統 + 馬丁格爾 合併狀態報告 (no_agent).

讀兩個 system log + live testnet orders + BTC 價, print 合併 summary.
設計: 工作日 08:00/12:00/16:00/20:00 HKT cron 行, deliver telegram.
靜默規則: 冇任何 active 倉 + 今日冇平倉 → print ⏳ 一句, 唔好太嘈.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

# 2026-09-13: clone 由 /tmp 搬入 ~/repos (macOS clean-tmps 會靜默清 tracked file)
BTC_REPO = os.environ.get("BTC_REPO") or os.path.expanduser("~/repos/btc-analyze")
# 呢個 script 由**共用** repo 讀 code (binance_testnet_paper)。同其他 cron 一樣
# 要知 repo 應該喺邊個 branch — 否則 repo 被留喺錯版本時, 報告數字會誤導
# (只讀, 所以只出聲唔修; 修嘅責任喺 btc_weekend_cron / btc_rebalance_cron)。
BRANCH_PIN = os.environ.get("BTC_REPO_BRANCH", "main")


def _bt():
    """延遲 import binance_testnet_paper (住喺 BTC_REPO)。

    2026-10-01 GLM review: 原本係 module-level import → 任何人 import 呢個
    module (包括測試 / py_compile 環境) 都會即刻拉真 repo 嘅 code 入嚟。
    改做用到先 import。
    """
    if BTC_REPO not in sys.path:
        sys.path.insert(0, BTC_REPO)
    try:
        import binance_testnet_paper as m
    except Exception as e:
        # 唔好靜默: 講清楚係「repo code 載入失敗」而唔係普通 import error
        # (lazy import 令 import error 由啟動時變成用到先爆, 更加要講清)
        raise RuntimeError(
            f"載入 {BTC_REPO} 嘅 binance_testnet_paper 失敗: {type(e).__name__}: {e}"
            f" — 檢查 repo 狀態 / branch (應該係 {BRANCH_PIN})") from e
    return m


def current_price():
    return _bt().current_price()


def _load_keys():
    return _bt()._load_keys()


def _signed_request(*a, **kw):
    return _bt()._signed_request(*a, **kw)


def _qty_label(entries, usable):
    """注數標籤 — 缺 qty/px 嘅注唔會計入浮動, 所以要標明口徑。

    2026-10-01 GLM review LOW: 原本寫死 `({len(entries)}注)` 但浮動/avg 用
    `usable` 計 → 有一注缺 px 時會顯示「(2注)」但浮動只計 1 注 = 誤導。
    """
    if len(usable) == len(entries):
        return f"{len(entries)}注"
    return f"{len(entries)}注/{len(usable)}可計"


def branch_warning():
    """repo 唔喺 BRANCH_PIN → 回一句警告 (只讀 script: 唔修, 只提醒)。"""
    try:
        r = subprocess.run(["git", "-C", BTC_REPO, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        cur = (r.stdout or "").strip()
        if r.returncode == 0 and cur and cur != BRANCH_PIN:
            return f"⚠️ 主 repo 喺 {cur} (應該係 {BRANCH_PIN}) — 報告數字可能嚟自錯版本"
    except Exception:
        pass
    return None

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

    # repo 唔喺預期 branch → 出聲 (報告數字可能嚟自錯版本)
    w = branch_warning()
    if w:
        out.append(w)

    # ── BTC 價 ──
    try:
        px = current_price()
        out[0] += f" — BTC ${px:,.0f}"
    except Exception as e:
        # 原本靜默 pass → repo code 載入唔到時報告會「少咗 BTC 價」而冇人知
        out.append(f"⚠️ 攞 BTC 價失敗: {e}")

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
    # 2026-10-01 GLM review LOW: 原本所有失敗都寫「讀 log 失敗」— 但呢段同時做
    # 攞價 (current_price) 同簽名 API 查詢, 失敗原因可以完全唔同 → 訊息要講清楚,
    # 否則查問題時會去錯方向。
    try:
        rb_path = os.path.expanduser("~/.hermes/reports/btc_rebalance_log.json")
        led_path = os.path.expanduser("~/.hermes/reports/btc_rebalance_ledger.json")
        if os.path.exists(rb_path):
            rlg = json.load(open(rb_path))
            snaps = [s for s in (rlg.get("snapshots") or []) if s.get("post_trade")]
            evs = rlg.get("events") or []
            led = json.load(open(led_path)) if os.path.exists(led_path) else None
            if led:
                try:
                    px = current_price()
                except Exception as e:
                    raise RuntimeError(f"攞 BTC 價失敗: {e}") from e
                tot = led["btc"] * px + led["usdt"]
                pct = (led["btc"] * px) / tot * 100
                try:
                    a_btc, a_usdt = _load_keys()
                    acct = _signed_request("GET", "/api/v3/account", {}, a_btc, a_usdt)
                    bal = {b["asset"]: float(b["free"]) + float(b["locked"])
                           for b in acct["balances"]}
                    gap = bal.get("BTC", 0) - led["btc"]
                except Exception as e:
                    raise RuntimeError(f"查帳戶失敗: {e}") from e
                ev_txt = ""
                if evs:
                    # 2026-10-01 GLM review LOW: _hkt 之前冇包 — 最後一個 event
                    # 缺 ts / 格式奇怪會爆, 跌入外層 except 出 raw TypeError。
                    try:
                        d = _hkt(evs[-1].get("ts"))
                        ev_txt = (f"  ｜上次再平衡 {d.strftime('%m-%d') if d else '?'} "
                                  f"{evs[-1].get('action')}")
                    except Exception:
                        ev_txt = "  ｜上次再平衡 ? (event 格式有問題)"
                out.append(f"  ⚖️ 再平衡 60/40 (帳本): BTC {pct:.1f}% / {100 - pct:.1f}% USDT"
                           f" ｜偏離 {pct - 60:+.1f}% ｜總值 ${tot:,.0f}{ev_txt}")
                out.append(f"     與實際帳戶差 {gap:+.6f} BTC (馬丁格爾佔用)")
    except Exception as e:
        out.append(f"  ⚖️ 再平衡: {e}")

    # ── 馬丁格爾 ──
    # 2026-10-01 GLM review LOW: 呢段原本**完全冇 try 包住** — 而且用
    # `a['id']` / `a['side']` / `a['level']` / `e['qty']` / `e['px']` 直接 index。
    # log 欄位一缺 (舊版本寫落嘅 chain、人手改過) 就 KeyError →
    # **成個報告 crash**, 連主系統嗰段都出唔到。只讀 script 唔影響落單,
    # 但報告本身就係佢存在嘅意義 → 一定要出得到。
    log_ok = True
    try:
        try:
            m = json.load(open(MART))
        except Exception as e:
            m = {}
            log_ok = False
            out.append(f"  ⚠️ 馬丁 log 讀唔到: {e}")
        a = m.get("active")
        if a:
            entries = [e for e in (a.get("entries") or []) if isinstance(e, dict)]
            # 缺 qty/px 嘅 entry 剔走 (唔好當 0, 否則 average 會計錯)
            usable = [e for e in entries if e.get("qty") is not None and e.get("px") is not None]
            tot_qty = sum(e["qty"] for e in usable)
            tot_cost = sum(e["qty"] * e["px"] for e in usable)
            avg = tot_cost / tot_qty if tot_qty else 0
            # 現價浮動
            try:
                px2 = current_price()
                fl = sum((px2 - e["px"]) * e["qty"] for e in usable) if a.get("side") == "BUY" \
                    else sum((e["px"] - px2) * e["qty"] for e in usable)
                fl_txt = f"浮動 {fl:+.2f} (現價 {px2:,.0f})"
            except Exception:
                fl_txt = f"avg {avg:,.0f}"
            out.append(f"  🔵 馬丁: chain {a.get('id', '?')} {a.get('side', '?')} "
                       f"level {a.get('level', '?')} "
                       f"({_qty_label(entries, usable)}) {fl_txt} "
                       f"目標 +${a.get('target_usd')}")
            opened = a.get("opened")
            t_open = _hkt(opened) if opened else None
            if t_open:
                held = f"已掛 {int((n - t_open).total_seconds() // 3600)}h"
            else:
                held = "已掛 ? (log 冇 opened 欄)"
            out.append(f"     開倉 {str(opened or '?')[:16]}Z {held}")
        else:
            # 讀唔到 log ≠ 冇 chain — 唔好兩句並存令人以為真係冇倉
            if log_ok:
                out.append("  💤 馬丁: 無 active chain")
        dly = m.get("daily") or {}
        dd = dly.get(today) or {}
        if log_ok:
            out.append(f"     今日 {dd.get('wins',0)}W/{dd.get('losses',0)}L "
                       f"蝕${(dd.get('loss_usd') or 0):.2f} ｜ "
                       f"累計 {sum(d.get('wins',0) for d in dly.values())}W/"
                       f"{sum(d.get('losses',0) for d in dly.values())}L")
    except Exception as e:
        # 報告永遠要出得到 — 馬丁段爆都要出主系統嗰段
        out.append(f"  ⚠️ 馬丁段計唔到 ({type(e).__name__}: {e})")

    print("\n".join(out))


if __name__ == "__main__":
    main()
