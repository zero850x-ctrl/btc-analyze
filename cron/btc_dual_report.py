#!/usr/bin/env python3
"""btc_dual_report.py — 主系統 + 馬丁格爾 合併狀態報告 (no_agent).

讀兩個 system log + live testnet orders + BTC 價, print 合併 summary.
設計: 工作日 08:00/12:00/16:00/20:00 HKT cron 行, deliver telegram.
靜默規則: 冇任何 active 倉 + 今日冇平倉 → print ⏳ 一句, 唔好太嘈.
"""
import json
import math
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


def _mart_net_btc():
    """馬丁 active chain 對共用帳戶 BTC 嘅**淨**佔用 (正 = 多咗 BTC)。

    ⚠️ 2026-10-01 查帳時嘅關鍵發現: **完成咗嘅 chain 淨影響 ≈ 0**
    (開倉買/賣幾多, 平倉就反向幾多, 只剩手續費), 所以只有 active chain
    先真正佔用 BTC。之前報告寫「與實際帳戶差 −0.30003 BTC (馬丁格爾佔用)」
    係錯嘅 —— 34 條完成 chain 累計 qty 0.047 BTC, 但佢哋開倉/平倉互相抵銷,
    實際淨佔用係 0。真兇係 09-20~09-27 phantom 999001 事故殘餘 (99.3%)。

    Spot 手續費由「收到嗰邊」資產扣 (買入扣 BTC), 所以完成 chain 嘅真實淨佔用
    係 −Σfee_BTC 而唔係絕對 0 —— 量級 ~1e-5 BTC (34 條累計 ~7e-5), 遠低於
    歸因門檻 5e-4, 唔影響結論。

    本函數**信任 log 嘅 active 狀態**: log 話完成但實際未平倉 (部分成交 / 平倉
    失敗) 嘅情況會被歸入「未對帳」殘餘, 唔會喺呢度反映。

    回 None = 讀唔到 / 唔知方向 → caller 唔可以亂歸因。
    """
    try:
        raw = json.load(open(MART)) or {}
        if not isinstance(raw, dict):
            return None
        a = raw.get("active")
        # 「冇 active chain」係**明確狀態** = 冇任何佔用 (0.0), 唔係「唔知」。
        # (同「讀唔到 log」唔同 —— 後者才係 None。)
        if not a:
            return 0.0
        if not isinstance(a, dict):
            return None
        # ⚠️ 唔可以「唔係 SELL 就當 BUY」。side 缺失 / "LONG" / 數字 都會靜靜
        #    當 BUY → 符號反轉 → 報告將「賣走 BTC」講成「買入 BTC」。
        #    呢個係 fail-*wrong*, 比 fail-safe 差得多 → 唔知方向就 return None。
        #    (大小寫/空白唔同例如 "sell" / " SELL " 係可正常化嘅, 唔算「唔知」。)
        side = str(a.get("side") or "").strip().upper()
        if side not in ("BUY", "SELL"):
            return None
        q = 0.0
        n_used = 0
        raw_entries = a.get("entries")
        # GLM R3 M4: entries 唔係 list/tuple 時 `for e in 5` 會 TypeError →
        # 跌入外層 except → 印 stderr 噪音。呢個係「唔知」, 直接回 None 乾淨啲。
        if raw_entries is None:
            raw_entries = []
        if not isinstance(raw_entries, (list, tuple)):
            return None
        for e in raw_entries:
            if not isinstance(e, dict):
                continue
            v = e.get("qty")
            # bool 係 int 嘅 subclass → 要明確排除; 字串 "0.001" 亦唔收
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if not math.isfinite(v):      # nan / inf → 傳落去報告會印 "nan"
                continue
            q += v
            n_used += 1
        # ⚠️ 兩種「冇數」唔可以混:
        #    entries 空 list   = 未成交, 明確冇佔用 → 0.0
        #    有 entries 但全部讀唔到 = 唔知有幾多 → None (同「無法辨識 side」同一個原則)
        #    GLM R2 M1 指出原本一律回 0.0 會低估佔用。
        if n_used == 0:
            return 0.0 if not raw_entries else None
        return -q if side == "SELL" else q
    except Exception as e:
        # 唔可以全靜音 — log 路徑錯咗會令報告永遠顯示「歸因不明」而冇人知
        print(f"  ⚠️ _mart_net_btc 讀唔到: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _gap_attribution(gap, mo):
    """帳本 vs 實際帳戶差額嘅歸因文字。

    抽成獨立函數嘅原因: 原本 inline 喺 main() 入面, 只能夠用 source-grep 測
    (「有冇『唔係馬丁』呢個字串」), 测唔到行為 —— 打錯分支邏輯都會 PASS。

    mo=None → 讀唔到馬丁 log, 唔知方向, 唔可以亂認。
    """
    if mo is None:
        return "馬丁 log 讀唔到 → 差額歸因不明 (檢查 MART log 路徑)"
    tol = max(0.0005, abs(gap) * 0.05)
    if gap == 0:
        # GLM R3 M3: 帳本同帳戶完全對齊, 但馬丁 log 話有佔用 → 呢個本身就係矛盾,
        # 唔可以照行落面出「佔 -0.002000 (0%)」咁自相矛盾嘅字。
        if abs(mo) <= tol:
            return f"馬丁格爾佔用 {mo:+.6f}"
        return (f"帳本零差額但馬丁 log 話佔用 {mo:+.6f} → ⚠️ 唔對帳")
    if abs(gap - mo) <= tol:
        return f"馬丁格爾佔用 {mo:+.6f}"
    # ⚠️ 一定要報比例: 馬丁佔 90% 但絕對差 > 門檻時, 只講「唔係馬丁」會誤導。
    frac = abs(mo / gap) if gap else 0.0
    # GLM R2 M2: frac 可以 >100% (gap 同 mo 符號相反 / mo 絕對值大過 gap) —
    # 呢個本身就係「有嘢唔對帳」嘅信號, 唔應該收埋, 要明示。
    if gap * mo < 0:
        note = " ⚠️ 方向同差額相反"
        frac = min(frac, 1.0)
    else:
        note, frac = "", min(frac, 1.0)
    return (f"馬丁格爾佔 {mo:+.6f} ({frac:.0%}{note})｜其餘 {gap - mo:+.6f} "
            f"唔係馬丁 (事故殘餘 / 未對帳)")


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
# ⚠️ 呢個係停用標記嘅路徑, single source of truth 喺 `btc_pause.MARKER_PATH`。
#    呢度刻意寫 literal (報告唔想因為 repo 載入失敗就連標記都睇唔到),
#    靠 test_btc_pause_gate.py 嘅相等斷言綁住兩邊 —— 改咗一邊而冇改另一邊會 FAIL。
HIST_MARKER = os.path.expanduser("~/.hermes/reports/btc_main_system_history.txt")


def now_hkt():
    return datetime.now(HKT)


def _hkt(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(HKT)
    except Exception:
        return None


def _pause_gate_status():
    """回 (enforced: bool, msg: str) — 停用硬閘係唔係**真**裝好。

    ⚠️ 報告唔可以見到 marker 存在就寫「已封住落單」—— 要真驗過
    `btc_pause` 喺 repo 載得到 + 標記讀得到。否則就會出現「報告講封咗、
    實際冇封」嘅 fail-wrong (同 PR#10 個 `side` 符號反轉係同一類錯)。
    """
    if BTC_REPO not in sys.path:
        sys.path.insert(0, BTC_REPO)
    try:
        import btc_pause
    except Exception as e:
        return False, (f"     ⚠️ 停用硬閘**未生效**: 載入 btc_pause 失敗 "
                       f"({type(e).__name__}: {e}) — 落單路徑可能仍然開通")
    try:
        _, _, certain = btc_pause.check()
    except Exception as e:
        return False, (f"     ⚠️ 停用硬閘**未生效**: btc_pause.check() 出錯 "
                       f"({type(e).__name__}: {e})")
    if not certain:
        return False, ("     ⚠️ 停用標記讀唔到 → 硬閘會 fail-safe 擋落單 "
                       "（check 標記檔權限）")
    return True, "     ⛔ 落單路徑已硬閘封住（cycle step 2/3 跳過；reconcile 照跑）"


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
        # 停用要有日期 + 原因 + 幾耐冇落單，唔可以只講「已停用」
        # （09-18 停用 → 09-20 靜默重開 = 同一個決定做咗兩次嘅事故）
        last_seed = None
        for o in log.get("orders", []):
            t = _hkt(o.get("seeded_time") or "")
            if t and (last_seed is None or t > last_seed):
                last_seed = t
        if last_seed:
            days = (n - last_seed).days
            out.append(f"  ⏸️ 主: 已正式停用（{days} 日無新單；無可證實 edge）")
        else:
            out.append("  ⏸️ 主: 已正式停用（無可證實 edge）")
        # ⚠️ 唔可以見 marker 就寫「已封住」—— 要真驗過閘裝好 (見 _pause_gate_status)
        out.append(_pause_gate_status()[1])
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
                # ⚠️ 唔可以一口咬定 gap = 馬丁佔用。2026-10-01 實測: gap −0.29865
                # 之中馬丁只佔 −0.00052 (0.2%), 其餘 −0.29670 係 phantom 事故殘餘。
                # 歸因邏輯抽做 _gap_attribution() 以便行為測試。
                gap_txt = _gap_attribution(gap, _mart_net_btc())
                out.append(f"     與實際帳戶差 {gap:+.6f} BTC — {gap_txt}")
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
