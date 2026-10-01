#!/usr/bin/env python3
"""btc_auto_trade_cycle.py — 週末 auto-trade 一個完整循環 (5min cron 入口)

1. reconcile 上次 OCO 成交 → 計 R → close log
2. btc_engine.py 重新掃描 (真 BTC 數據)
3. 落新單 (dedup + 風控)
4. 心跳 log
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
from datetime import datetime, timezone

# 2026-09-13: 自我定位 (clone 已由 /tmp 搬入 ~/repos)；BTC_REPO env 可覆寫
REPO = os.environ.get("BTC_REPO") or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
LOG_PATH = os.path.expanduser("~/.hermes/reports/btc_testnet_orders.json")
HEARTBEAT = os.path.expanduser("~/.hermes/reports/btc_auto_trade_heartbeat.txt")
HISTORY = os.path.expanduser("~/.hermes/reports/btc_testnet_closed_trades.json")


def sh(cmd):
    # 用同一個 interpreter (cron 環境 python3 可能冇 numpy/yfinance)
    cmd = cmd.replace("python3 ", f'"{sys.executable}" ', 1)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=REPO, timeout=280)
    return (r.stdout + r.stderr).strip()


def log(msg):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | {msg}"
    print(line)
    os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
    with open(HEARTBEAT, "a") as f:
        f.write(line + "\n")
    # heartbeat file 只保留最後 500 行
    with open(HEARTBEAT) as f:
        lines = f.readlines()
    if len(lines) > 500:
        with open(HEARTBEAT, "w") as f:
            f.writelines(lines[-500:])


RECONCILE_STATE = os.environ.get("BTC_RECONCILE_STATE") or os.path.expanduser(
    "~/.hermes/reports/btc_reconcile_state.json")
BALANCE_DROP_ALERT_BTC = float(os.environ.get("BTC_BALANCE_DROP_ALERT", "0.05"))
# 孤兒狀態長期唔變都要定期重提 (GLM 09-27 #3): 一次性警報冇人理 = 永遠靜默,
# 正正係 7 日 phantom 事故嘅教訓。每 N 小時重提未處理嘅孤兒對帳。
ALERT_REMIND_SEC = int(os.environ.get("BTC_ALERT_REMIND_HOURS", "6")) * 3600
# 異常賣出偵測 (2026-09-27 GLM round-2 P2): 24h 窗內「賣出成交唔對應任何本地記錄」
# ≥ N 筆 → ⚠️ (dedupe)。事故當時靠餘額跌 0.05 要 ~5h 先響; 呢個 detector 幾分鐘內揪到。
UNMATCHED_SELL_MIN = int(os.environ.get("BTC_UNMATCHED_SELL_MIN", "2"))
UNMATCHED_SELL_DEDUPE = int(os.environ.get("BTC_UNMATCHED_SELL_DEDUPE_H", "12")) * 3600


def reconcile_alerts(summary, acct_btc):
    """對帳狀態變化 / 餘額異常警報 —— 只喺狀態變化先出聲 (唔會每 tick spam)。

    2026-09-27 phantom loop 教訓: 孤兒狀態連續 7 日每 tick 重複 rebuild 失敗
    (needs_legs 長期 10+) 全程冇任何警報 → 冇人知。所以:
      - orphan summary (per-tick 計數) 有變化 → ⚠️ 出一條, 之後靜默直到再變。
      - 狀態長期唔變 (例如 freeze 後) → 每 BTC_ALERT_REMIND_HOURS (default 6h)
        重提一次 (GLM 09-27 #3: 一次性警報冇人理 = 永遠靜默, 就係事故嘅核心教訓)。
      - 餘額 high-water mark: 由高位累計跌 ≥ BALANCE_DROP_ALERT_BTC 出一次聲
        (正常單筆規模 0.002-0.003 BTC, 跌 0.05 = 唔可能係正常操作), 破新高先重設。
    回傳 list[str]; caller 用 log() 輸出 (⚠️ 喺 cron NOTABLE_KEYS = 會推送 TG)。
    """
    msgs = []
    try:
        st = {}
        if os.path.exists(RECONCILE_STATE):
            with open(RECONCILE_STATE) as f:
                st = json.load(f) or {}
        cur = {k: v for k, v in (summary or {}).items() if v}
        prev = st.get("orphan_summary") or {}
        now_ts = time.time()

        def _fmt(d):
            return ", ".join(f"{k}={v}" for k, v in sorted(d.items()))

        _fired = False
        if cur != prev:
            if cur and prev:
                msgs.append(f"⚠️ 孤兒對帳狀態變化: {_fmt(cur)} (之前: {_fmt(prev)})")
            elif cur:
                msgs.append(f"⚠️ 孤兒對帳狀態: {_fmt(cur)}")
            elif prev:
                msgs.append(f"✅ 孤兒對帳已清空 (之前: {_fmt(prev)})")
            st["alert_ts"] = now_ts
            _fired = True
        elif cur and now_ts - float(st.get("alert_ts") or 0.0) >= ALERT_REMIND_SEC:
            # GLM 09-27 #3: 狀態長期唔變 (e.g. freeze 後) 都要定期重提 ——
            # 一次性警報冇人理 = 永遠靜默 (7 日事故核心教訓)。
            msgs.append(f"⏰ 孤兒對帳仍未處理 (每 {ALERT_REMIND_SEC // 3600}h 重提): {_fmt(cur)}")
            st["alert_ts"] = now_ts
            _fired = True
        # 2B (office R1-B, 2026-09-27): freeze 要有專屬醒目訊號 —— 唔可以只靠
        # summary 數字變化。有警報出 (變化/重提) 時, freeze 記錄附加 🚨 一行。
        _frozen = int(cur.get("frozen_rebuild") or 0)
        if _frozen and _fired:
            msgs.append(f"🚨 對帳重建已凍結 (需人手): {_frozen} 筆 — 倉位可能冇止損, "
                        f"檢查 rebuild_frozen_note (清 rebuild_frozen 可恢復)")
        st["orphan_summary"] = cur
        if acct_btc is not None:
            hw = st.get("balance_hw")
            if hw is None or acct_btc > hw:
                st["balance_hw"], st["balance_alerted"] = acct_btc, False
            drop = float(st["balance_hw"]) - float(acct_btc)
            if drop >= BALANCE_DROP_ALERT_BTC and not st.get("balance_alerted"):
                msgs.append(f"⚠️ testnet BTC 餘額異常: 高位 {st['balance_hw']:.5f} → "
                            f"現 {acct_btc:.5f} (跌 {drop:.5f} BTC) — 查下有冇未對帳嘅賣出")
                st["balance_alerted"] = True
        st["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(RECONCILE_STATE, "w") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception as e:                    # noqa: BLE001
        msgs.append(f"⚠️ reconcile alert 出錯 (唔影響交易): {type(e).__name__}: {e}")
    return msgs


def find_unmatched_sells(trades, log_d, since_ms=None):
    """賣出成交 (isBuyer=False) 但 orderId 唔對應任何本地記錄 → list[orderId]。

    2026-09-27 GLM round-2 P2 (phantom 同類偵測): 本地「已知 order」= 所有記錄嘅
    order_id / oco_id / l3_id / flatten_order_id + 全部 leg id 欄位。任何其他賣出
    成交都係未對帳賣出 —— 今次事故嘅 270 筆市價平倉正正全部係咁 (冇入 log)。

    假設 (GLM round-3 LOW 註明): 已完成記錄會一直留喺 log_d["orders"] (現行
    不變量, 冇 archive/rotation)。若日後加 log rotation, 要改為掃埋 archive,
    否則舊記錄嘅 leg 成交會誤報。
    """
    from binance_testnet_paper import record_known_ids   # 單一來源 (btp), 唔重複維護
    known = set()
    for r in (log_d or {}).get("orders", []):
        known |= record_known_ids(r)
    out = []
    for t in trades or []:
        if t.get("isBuyer"):
            continue
        if since_ms is not None and int(t.get("time") or 0) < int(since_ms):
            continue
        oid = t.get("orderId")
        if str(oid) not in known:
            out.append(oid)
    return out


def sell_reconcile_alerts(trades, log_d):
    """異常賣出警報 (24h 窗 ≥ UNMATCHED_SELL_MIN, dedupe UNMATCHED_SELL_DEDUPE)。

    首次執行只掃描之後嘅賣出 (sell_scan_since = now) —— 唔會一 deploy 就為歷史
    賣出 (含事故遺留) 響。sell_scan_since 係「deploy 錨點」唔係 cursor: 佢唔會
    前推, window = max(錨點, now−24h) (GLM round-3 LOW)。
    trades=None (myTrades 查詢失敗) → 盲點提醒, 6h dedupe (GLM round-3 LOW)。
    回傳 list[str]; caller 用 log() 輸出 (⚠️ → TG)。
    """
    msgs = []
    try:
        st = {}
        if os.path.exists(RECONCILE_STATE):
            with open(RECONCILE_STATE) as f:
                st = json.load(f) or {}
        now = time.time()
        since = st.get("sell_scan_since")
        if since is None:
            since = now
        if trades is None:
            last_f = float(st.get("trades_fail_alert_ts") or 0.0)
            if now - last_f >= 6 * 3600:
                msgs.append("⚠️ myTrades 查詢失敗 — 賣出對帳今次跳過 (連續失敗每 6h 提醒)")
                st["trades_fail_alert_ts"] = now
            st["sell_scan_since"] = since
            with open(RECONCILE_STATE, "w") as f:
                json.dump(st, f, ensure_ascii=False, indent=1)
            return msgs
        window_start_ms = int(max(float(since), now - 24 * 3600) * 1000)
        unmatched = find_unmatched_sells(trades, log_d, since_ms=window_start_ms)
        if len(unmatched) >= UNMATCHED_SELL_MIN:
            last = float(st.get("unmatched_sell_alert_ts") or 0.0)
            if now - last >= UNMATCHED_SELL_DEDUPE:
                msgs.append(
                    f"⚠️ 24h 內 {len(unmatched)} 筆賣出成交唔對應任何本地記錄 "
                    f"(orderIds={unmatched[:8]}) — 可能有未對帳賣出 (phantom 同類), 查 myTrades")
                st["unmatched_sell_alert_ts"] = now
        st["sell_scan_since"] = since
        with open(RECONCILE_STATE, "w") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception as e:                    # noqa: BLE001
        msgs.append(f"⚠️ sell alert 出錯 (唔影響交易): {type(e).__name__}: {e}")
    return msgs


def _pnl_of(rec, exit_px, qty, fee, fee_asset):
    """單一段 exit 嘅 pnl (扣 fee).

    Binance spot: seller 付 USDT (quote)、buyer 付 BTC (base) —
    用 myTrades 嘅 commissionAsset 判斷 (GLM review #A1, 真數據驗證):
      USDT → 直接扣; BTC → ×price 轉 USD.
    """
    direction = -1 if rec["side"] == "SELL" else 1
    fee_usdt = fee if str(fee_asset).upper() == "USDT" else fee * exit_px
    return (exit_px - rec["entry_fill"]) * qty * direction - fee_usdt


def _trail_leg(rec, open_map, key, secret):
    """尾倉 SL (l3) 按 ATR step 推 — 每行 +1 ATR 利潤 → SL 追 (steps-1)×ATR.

    steps=1 時 SL 推到 breakeven (鎖打和); steps=2 鎖 +1 ATR, 如此類推.
    先 POST 新 SL order 成功先 DELETE 舊 (避免 naked 窗口); DELETE 失敗 →
    即刻 DELETE 剛 POST 嘅新 SL (rollback), 防止雙 SL live 開反向裸倉
    (GLM review #B3).
    """
    from binance_testnet_paper import _signed_request, current_price

    l3_id = rec.get("l3_id")
    if not l3_id or l3_id not in open_map:
        return
    # trail_stuck cleanup (DSv4 review #C): 上 tick rollback 失敗留低雙 SL →
    # 掃 open_map 清走同向獨立 stop-limit (orderListId=-1 = 唔係 OCO leg)
    if rec.get("trail_stuck"):
        exit_side = "SELL" if rec["side"] == "BUY" else "BUY"
        for o in list(open_map.values()):
            if (o.get("type") == "STOP_LOSS_LIMIT" and o.get("symbol") == "BTCUSDT"
                    and o.get("side") == exit_side
                    and int(o.get("orderListId") or -1) == -1
                    and o["orderId"] != l3_id):
                try:
                    _signed_request("DELETE", "/api/v3/order",
                                    {"symbol": "BTCUSDT", "orderId": o["orderId"]}, key, secret)
                    rec.setdefault("trail_errors", []).append(
                        f"stuck cleanup del {o['orderId']}")
                except Exception as e:
                    rec.setdefault("trail_errors", []).append(
                        f"stuck cleanup FAIL {o['orderId']}: {str(e)[:60]}")
        rec["trail_stuck"] = False
    atr = rec.get("atr")
    if not atr or atr <= 0:
        return
    entry = rec["entry_fill"]
    side = rec["side"]
    # 高水位: 用本 tick 見過嘅最優價計 steps (spike 回落都唔會錯過追蹤)
    px = current_price()
    best = max(rec.get("max_favorable_px") or 0, px) if side == "BUY" \
        else min(rec.get("max_favorable_px") or px, px)
    rec["max_favorable_px"] = best
    old_stop = float(open_map[l3_id]["stopPrice"])
    if side == "BUY":
        steps = int((best - entry) / atr)
        new_stop = entry + max(0, steps - 1) * atr
    else:
        steps = int((entry - best) / atr)
        new_stop = entry - max(0, steps - 1) * atr
    if steps < 1:
        return  # 未行夠 1 ATR
    new_stop = round(new_stop, 2)
    if (side == "BUY" and new_stop <= old_stop) or (side == "SELL" and new_stop >= old_stop):
        return  # 唔更有利
    qty = float(open_map[l3_id]["origQty"])
    try:
        if side == "BUY":
            r = _signed_request("POST", "/api/v3/order", {
                "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_LOSS_LIMIT",
                "quantity": f"{qty:.5f}", "stopPrice": f"{new_stop:.2f}",
                "price": f"{new_stop * 0.9985:.2f}", "timeInForce": "GTC"}, key, secret)
        else:
            r = _signed_request("POST", "/api/v3/order", {
                "symbol": "BTCUSDT", "side": "BUY", "type": "STOP_LOSS_LIMIT",
                "quantity": f"{qty:.5f}", "stopPrice": f"{new_stop:.2f}",
                "price": f"{new_stop * 1.0015:.2f}", "timeInForce": "GTC"}, key, secret)
    except Exception as e:
        rec.setdefault("trail_errors", []).append(f"POST fail: {str(e)[:100]}")
        return
    # POST 成功先 DELETE 舊 SL; DELETE 失敗 → rollback 新 SL (雙 SL = 反向裸倉風險)
    try:
        _signed_request("DELETE", "/api/v3/order",
                        {"symbol": "BTCUSDT", "orderId": l3_id}, key, secret)
    except Exception as e:
        try:
            _signed_request("DELETE", "/api/v3/order",
                            {"symbol": "BTCUSDT", "orderId": r["orderId"]}, key, secret)
            rec.setdefault("trail_errors", []).append(
                f"old-DELETE fail → new SL rolled back: {str(e)[:80]}")
        except Exception as e2:
            rec["trail_stuck"] = True   # 雙 SL 都剷唔走 → 標記, 下 tick 優先處理
            rec.setdefault("trail_errors", []).append(
                f"ROLLBACK FAIL 雙SL live old={l3_id} new={r['orderId']}: {str(e2)[:80]}")
        return
    rec["l3_id"] = r["orderId"]
    rec.setdefault("exit_leg_ids", []).append(r["orderId"])   # 新 SL leg 都要 reconcile
    rec["trail_stop"] = new_stop
    rec.setdefault("trails", []).append({
        "to": new_stop,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


def _breakeven_remaining(rec, open_map, key, secret):
    """TP1 (OCO_A) 成交後 → 只推尾倉 l3 SL 去 breakeven.

    3 注結構 (用戶確認 2026-09-03):
      1. OCO_A @TP1 成交 = 賣第 1 份 ✅
      2. OCO_B 保持原狀 → 照等 TP2 = 賣第 2 份 (唔郁!)
      3. 尾倉 l3 → 推 breakeven, 之後 ATR trail = 食大趨勢
    之前錯誤地將 OCO_B 都取消重建去 breakeven → 第 2 份冇等 TP2,
    彈返 breakeven 就 2/3 全走 (BTC 爆上 $81k 得 0 手).

    l3 係獨立 SL → 直接 POST 新 (breakeven) + DELETE 舊, 失敗 rollback.
    只喺 OCO_A 嘅 **TP leg 成交** 先 trigger (SL leg 成交 = 價格已反轉,
    推 breakeven 會令剩倉 SL 高過市價即時觸發 — 唔可以做).
    """
    from binance_testnet_paper import _signed_request

    if not rec.get("oco_a_tp_hit"):
        return
    if rec.get("be_done"):
        return
    entry = rec["entry_fill"]
    side = rec["side"]
    be = round(entry, 2)

    l3_id = rec.get("l3_id")
    if not l3_id or l3_id not in open_map:
        return  # 冇尾倉 (無 TP1 冇 TP2 時 q3 先有) — 冇嘢要郁
    o = open_map[l3_id]
    if abs(float(o.get("stopPrice", 0)) - be) < 1e-6:
        rec["be_done"] = True   # 已係 breakeven
        return
    qty = float(o["origQty"])
    try:
        if side == "BUY":
            r = _signed_request("POST", "/api/v3/order", {
                "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_LOSS_LIMIT",
                "quantity": f"{qty:.5f}", "stopPrice": f"{be:.2f}",
                "price": f"{be * 0.9985:.2f}", "timeInForce": "GTC"}, key, secret)
        else:
            r = _signed_request("POST", "/api/v3/order", {
                "symbol": "BTCUSDT", "side": "BUY", "type": "STOP_LOSS_LIMIT",
                "quantity": f"{qty:.5f}", "stopPrice": f"{be:.2f}",
                "price": f"{be * 1.0015:.2f}", "timeInForce": "GTC"}, key, secret)
    except Exception as e:
        rec.setdefault("trail_errors", []).append(f"BE l3 POST fail: {str(e)[:80]}")
        return
    try:
        _signed_request("DELETE", "/api/v3/order",
                        {"symbol": "BTCUSDT", "orderId": l3_id}, key, secret)
    except Exception as e:
        try:
            _signed_request("DELETE", "/api/v3/order",
                            {"symbol": "BTCUSDT", "orderId": r["orderId"]}, key, secret)
            rec.setdefault("trail_errors", []).append(f"BE l3 rollback: {str(e)[:80]}")
        except Exception as e2:
            rec["trail_stuck"] = True
            rec.setdefault("trail_errors", []).append(f"BE l3 ROLLBACK FAIL: {str(e2)[:80]}")
        return
    rec["l3_id"] = r["orderId"]
    rec["exit_leg_ids"] = [i for i in rec.get("exit_leg_ids", []) if i != l3_id] + [r["orderId"]]
    rec["breakeven_done_l3"] = True
    rec["be_done"] = True
    rec.setdefault("breakeven_moves", []).append({"l3": l3_id, "new": r["orderId"], "be": be})


def reconcile_cycle(key, secret):
    """3 段 exit reconcile + 尾倉 trailing.

    1. exit leg 消失 → myTrades 對應 fill → 累計 realized (已計 legs 唔重複)
    2. 全部 qty 已實現 → CLOSED, 計 R (扣 fee)
    3. 尾倉 SL 仲開住 + 價格行咗 >=1 ATR → 推 SL (trailing)
    """
    from binance_testnet_paper import (_signed_request, load_log, save_log,
                                       build_exit_legs, exchange_filters,
                                       current_price, LIMIT_TTL_HOURS,
                                       resolve_orphan_states, is_live_rec,
                                       DONE_STATUS, ORPHAN_STATUS, estimate_held_for,
                                       HOLDING_STATUS)

    log_d = load_log()
    changed = []
    wiped = []      # testnet 帳戶重置 → 幻影倉 (唔入 HISTORY, 唔污染 sumR)
    dirty = False   # partial fill / breakeven / trail rebuild 後必須 save (bug fix 09-03)
    opens = _signed_request("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"}, key, secret)
    open_ids = {o["orderId"] for o in opens}
    open_map = {o["orderId"]: o for o in opens}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── fail-closed 復原 (2026-09-20 GLM review #1 / A / B) ────────────────
    # is_live_rec() 係 fail-closed: 唔喺 DONE_STATUS 就當 live。好處係唔會漏 guard,
    # 代價係「實際已經冇倉」嘅孤兒狀態會永久鎖死引擎 (所有新單被 same_side 擋)。
    # 所以每次 reconcile 都對帳:
    #   冇倉 (低於 dust) → 歸 CLOSED (解鎖)
    #   有倉             → **真正補建 exit legs** (唔可以只標 needs_legs —— 冇消費者
    #                      = 死巷, 倉會永久裸掛冇止損, 比原問題更危險)
    # 用 per-record 持倉判斷 (GLM B/F1): 帳戶總額對 N 個孤兒係語意錯配。
    # F1 修正: 之前 _held_for 完全冇讀 rec → 假 per-record。而家:
    #   (1) 帳戶餘額喺 loop 外查**一次** (之前 N 個孤兒 = N 次 API call, rate limit 風險)
    #   (2) 每筆扣除「其他仍 live 記錄」嘅 qty → 得出該筆自己嘅估算持倉
    try:
        lot_step, _lm, _mn = exchange_filters(key, secret)
    except Exception:                                   # noqa: BLE001
        lot_step = None
    dust_eps = (lot_step * 10) if lot_step else 0.0001

    try:
        _acct = _signed_request("GET", "/api/v3/account", {}, key, secret)
        _acct_btc = next((float(b["free"]) + float(b["locked"])
                          for b in _acct.get("balances", []) if b.get("asset") == "BTC"), 0.0)
    except Exception:                                   # noqa: BLE001
        _acct_btc = None                                # 查唔到 → resolve 會 skip
    # LOW-4 (GLM 第五輪): 只扣「持貨類」status 嘅 qty。
    # LOW-I (第六輪): tuple 由 binance_testnet_paper 定義 (HOLDING_STATUS),
    #   唔喺呢度重複一份 —— 兩份會 drift, 而 LOW-4 正正就係食過呢個虧。
    _live_recs = [r for r in log_d["orders"]
                  if is_live_rec(r) and str(r.get("status") or "") in HOLDING_STATUS]

    def _held_for(rec):
        """該筆自己嘅估算持倉 —— 邏輯喺 estimate_held_for() (可測)。"""
        return estimate_held_for(rec, _acct_btc, _live_recs)

    _trades_cache = {"data": None}

    def _get_trades():
        """每 tick 共用嘅 myTrades (limit 1000) —— rebuild entry 驗證 + 賣出對帳共用,
        唔使每個孤兒各查一次 (GLM round-2 #2 建議)。帳戶 lifetime ~360 筆, 遠離窗口上限。
        """
        if _trades_cache["data"] is None:
            _trades_cache["data"] = _signed_request(
                "GET", "/api/v3/myTrades", {"symbol": "BTCUSDT", "limit": 1000}, key, secret)
        return _trades_cache["data"]

    def _rebuild(rec):
        if lot_step is None:
            raise RuntimeError("冇 lot_step, 唔敢建 legs")
        # 2026-09-27 GLM review #2: 建 legs 前必須確認 entry 成交真係存在於交易所
        # (myTrades)。幽靈記錄 (test 污染/人手改 log) 價位合理時會照樣掛真 OCO/SL
        # legs → SL 遲啲觸發 = 延遲版市價平倉。驗證唔到 entry → 拒建, 交人手
        # (行同一條 rebuild-fail → freeze 路徑, 3 tick 後停手; 全程唔會落任何單)。
        entry_id = rec.get("order_id")
        trades_all = _get_trades()
        if not entry_id or not any(str(x.get("orderId")) == str(entry_id)
                                   for x in trades_all):
            raise RuntimeError(
                f"reconcile rebuild 拒絕: entry order {entry_id!r} 唔喺 myTrades — "
                f"疑似幽靈記錄, 唔建 legs (交人手對帳)")
        # 2026-09-27 phantom loop: 對帳 rebuild 唔准市價平倉 (持倉只係估算,
        # 平倉會賣走唔屬於呢筆嘅幣) —— 見 build_exit_legs docstring。
        return build_exit_legs(rec, key, secret, lot_step, allow_flatten=False)

    orph, orph_summ = resolve_orphan_states(
        log_d, _held_for, dust_eps=dust_eps,
        rebuild=_rebuild if lot_step is not None else None,
        acct_btc=_acct_btc,          # HIGH-1: freeze-on-ambiguity 要用帳戶總額
        trades_getter=_get_trades)   # P1: 歸零判 CLOSED 前覆核賣出證據 (共用 fetch)
    if orph:
        changed.extend(orph)
        dirty = True
        log("🩹 孤兒狀態對帳: " + ", ".join(f"{k}={v}" for k, v in orph_summ.items() if v)
            + f" (dust_eps={dust_eps:.5f}, acct_btc={_acct_btc})")
    # 2026-09-27 phantom loop: 對帳狀態變化 / 餘額異常警報 (⚠️ = cron 會推送 TG;
    # 狀態唔變 = 完全靜默)。呼叫喺 orphan 處理之後, 攞到最新 summary + 餘額。
    for _m in reconcile_alerts(orph_summ, _acct_btc):
        log(_m)
    # 2026-09-27 GLM round-2 P2: 異常賣出偵測 (每 tick; 同 _rebuild 共用 _get_trades fetch)。
    # myTrades 失敗 → trades=None → sell_reconcile_alerts 出盲點提醒 (6h dedupe)。
    try:
        _trades_now = _get_trades()
    except Exception:                           # noqa: BLE001
        _trades_now = None
    for _m in sell_reconcile_alerts(_trades_now, log_d):
        log(_m)

    # ── F4: 未知 status 要出聲 (之前會永久 live 但零 log) ──────────────────
    _known = set(DONE_STATUS) | set(ORPHAN_STATUS) | {"LIMIT_PENDING", "OCO_PLACED"}
    _unknown = {}
    for r in log_d["orders"]:
        st = str(r.get("status") or "")
        if st and st not in _known:
            _unknown[st] = _unknown.get(st, 0) + 1
    if _unknown:
        log(f"⚠️ 未見過嘅 status (會被當 live, 永久鎖引擎): {_unknown} — 需人手確認")

    # ── feat/limit-entry: 限價掛單對帳 (09-16) ─────────────────────────
    # 掛單喺 openOrders → 仲等緊；唔喺 → 查最終狀態 (FILLED → 建 3 段 exit /
    # CANCELED → 釋放 cap / 部分成交 → cancel 剩餘 + 用已成交 qty 建 exit)。
    # 兩道 cancel 條件: TTL 過期 (engine 每 15min 重算, 形態會過期) 或價格偏離太遠
    # (形態已失效, 掛單永遠唔會成交, 但霸住 cap 1)。
    for rec in log_d["orders"]:
        if rec.get("status") != "LIMIT_PENDING":
            continue
        oid = rec.get("order_id")
        if oid in open_ids:
            age_h = (time.time() - (rec.get("limit_ts") or time.time())) / 3600.0
            o = open_map.get(oid) or {}
            far = False
            try:
                lpx = float(rec.get("limit_px") or 0)
                if lpx:
                    far = abs(current_price() - lpx) / lpx > 0.03   # 離掛單價 > 3%
            except Exception:
                far = False
            if age_h >= LIMIT_TTL_HOURS or far:
                why = (f"掛單 {age_h:.1f}h 未成交 (TTL {LIMIT_TTL_HOURS:g}h)"
                       if age_h >= LIMIT_TTL_HOURS else
                       f"價格已偏離掛單價 >3% (形態失效)")
                try:
                    _signed_request("DELETE", "/api/v3/order",
                                    {"symbol": "BTCUSDT", "orderId": oid}, key, secret)
                    rec["status"] = "LIMIT_EXPIRED"
                    rec["closed_note"] = f"{why} → cancel (釋放 cap)"
                    rec["closed_time"] = now_iso
                    rec["closed_via"] = "limit_ttl"
                    changed.append(rec)
                    dirty = True
                except urllib.error.HTTPError as e:
                    rec["limit_cancel_error"] = e.read().decode()[:200]
                    dirty = True
            continue

        # 唔喺 openOrders → 攞最終狀態
        try:
            o = _signed_request("GET", "/api/v3/order",
                                {"symbol": "BTCUSDT", "orderId": oid}, key, secret)
        except urllib.error.HTTPError:
            o = None
        st = (o or {}).get("status", "")
        exec_qty = float((o or {}).get("executedQty") or 0)
        if st == "FILLED" or exec_qty > 0:
            # 限價成交 (可能部分) → 建 3 段 exit
            if st not in ("FILLED",):
                # 部分成交: cancel 剩餘, 用已成交 qty 建 exit
                try:
                    _signed_request("DELETE", "/api/v3/order",
                                    {"symbol": "BTCUSDT", "orderId": oid}, key, secret)
                except urllib.error.HTTPError:
                    pass
            cum_quote = float((o or {}).get("cummulativeQuoteQty") or 0)
            fill_px = (cum_quote / exec_qty) if exec_qty else float(rec.get("limit_px") or 0)
            fee_paid = 0.0
            try:
                for t in _signed_request("GET", "/api/v3/myTrades",
                                         {"symbol": "BTCUSDT", "limit": 100}, key, secret):
                    if t.get("orderId") == oid:
                        fee_paid += float(t.get("commission") or 0)
            except urllib.error.HTTPError:
                pass
            rec["qty"] = exec_qty
            rec["entry_fill"] = round(fill_px, 2)
            rec["fee"] = fee_paid
            rec["filled_time"] = now_iso
            rec["status"] = "LIMIT_FILLED"
            rec["was_limit_fill"] = True
            rec["rr_fill"] = (round(abs(rec["planned_tp1"] - fill_px)
                                    / abs(fill_px - rec["planned_stop"]), 2)
                              if rec.get("planned_tp1") and fill_px != rec.get("planned_stop")
                              else None)
            # ── 2026-09-20 fix: 成交即刻寫 log, 唔等成個 loop 行完 ──────────────
            # 舊寫法: 呢個 for 迴圈行完才一次過 save_log。迴圈內每個成交都要
            # build_exit_legs (2-3 次 API, 實測 1-2 秒) → 期間 log 完全冇記錄,
            # 令同 tick 之後嘅 setup (同 tick 之後嘅 reconcile) 見到「冇 live 倉」
            # 而繞過 same_pattern / same_side / opp_side guard。
            # 實證 (2026-08-29~08-31): 4 對同 pattern 重疊倉, seeded_time 相隔 1-2 秒,
            # log 由頭到尾冇一筆 same_pattern skip 記錄。
            rec["status"] = "ENTRY_FILLED_PENDING_EXITS"
            save_log(log_d)
            lot_step, _lm, _mn = exchange_filters(key, secret)
            rec, _ = build_exit_legs(rec, key, secret, lot_step)
            changed.append(rec)
            dirty = True
            continue
        if st in ("CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"):
            rec["status"] = "LIMIT_CANCELLED"
            rec["closed_note"] = f"限價單 {st} (未成交)"
            rec["closed_time"] = now_iso
            rec["closed_via"] = "limit_" + st.lower()
            changed.append(rec)
            dirty = True
            continue
        # 狀態不明 (API 冇回應等) → 下個 tick 再試

    for rec in log_d["orders"]:
        # 舊格式 (oco_leg_ids) → migration 到 exit_leg_ids (GLM review #C1:
        # 唔 migrate 嘅舊單會永久佔住 cap 1 名額, 癱瘓新開倉)
        if rec.get("status") == "OCO_PLACED" and not rec.get("exit_leg_ids") and rec.get("oco_leg_ids"):
            rec["exit_leg_ids"] = list(rec["oco_leg_ids"])
            # 舊格式 = 單一 OCO 全倉 = OCO_A (DSv4 review #E: 舊單 breakeven 保護)
            rec.setdefault("oco_a_leg_ids", list(rec["oco_leg_ids"]))
            dirty = True
        if rec.get("status") != "OCO_PLACED" or not rec.get("exit_leg_ids"):
            continue
        legs_gone = [i for i in rec["exit_leg_ids"] if i not in open_ids]
        done_ids = set(rec.get("closed_leg_ids") or [])
        new_gone = [i for i in legs_gone if i not in done_ids]
        # ── testnet 帳戶重置偵測 (2026-09-09 實證) ──────────────────────
        # 全部 exit legs 唔喺 openOrders + 完全冇 open orders + entry 成交都喺
        # myTrades 消失 → Binance testnet 維護重置咗帳戶 (成交歷史被清空)。
        # 唔處理嘅後果: log 永遠當佢 live → cap 1 名額被幻影倉永久佔住,
        # 系統之後永遠唔開新倉, 整點報告出假浮動 (09-10 實測 -1.19 假數)。
        if rec["exit_leg_ids"] and len(legs_gone) == len(rec["exit_leg_ids"]) and not opens:
            trades_all = _signed_request("GET", "/api/v3/myTrades",
                                         {"symbol": "BTCUSDT", "limit": 1000}, key, secret)
            entry_id = rec.get("order_id")
            entry_gone = (not entry_id) or not any(x["orderId"] == entry_id for x in trades_all)
            remaining_chk = round(rec.get("qty", 0) - (rec.get("realized_qty") or 0.0), 8)
            if entry_gone and remaining_chk > 1e-8:
                rec["status"] = "WIPED"
                rec["closed_via"] = "testnet_account_reset"
                rec["wiped_remaining_qty"] = remaining_chk
                rec["wiped_note"] = "exit legs + entry 成交全部消失 (testnet 帳戶重置)"
                rec["closed_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                # 唔 append 入 HISTORY → 唔計入 sumR / 勝率 (realized_parts 保留做記錄)
                dirty = True
                wiped.append(rec)
                continue
        if new_gone:
            # OCO_A legs 消失 → 判斷係 TP 定 SL 成交 (TP 成交先推 breakeven)
            oco_a_ids = rec.get("oco_a_leg_ids") or []
            if oco_a_ids and all(i in set(new_gone) | done_ids for i in oco_a_ids):
                rec["oco_a_gone"] = True
            trades = _signed_request("GET", "/api/v3/myTrades", {"symbol": "BTCUSDT"}, key, secret)
            t = sorted(trades, key=lambda x: x["time"])
            exit_trades = [x for x in t if x["orderId"] in new_gone]
            if exit_trades:
                exit_qty = sum(float(x["qty"]) for x in exit_trades)
                exit_px = sum(float(x["price"]) * float(x["qty"]) for x in exit_trades) / exit_qty
                # fee 逐筆按 commissionAsset 換算 (MIXED 都啱 — DSv4 review #B)
                fee_usdt = sum(
                    (float(x["commission"])
                     if str(x.get("commissionAsset", "BTC")).upper() == "USDT"
                     else float(x["commission"]) * float(x["price"]))
                    for x in exit_trades)
                # OCO_A 成交判定: exit price 向 TP 方向行 = TP hit, 向 SL = stop hit
                tp1 = rec.get("planned_tp1")
                if rec.get("oco_a_gone") and tp1:
                    if (rec["side"] == "BUY" and exit_px >= float(tp1) * 0.9985) or \
                       (rec["side"] == "SELL" and exit_px <= float(tp1) * 1.0015):
                        rec["oco_a_tp_hit"] = True
                rec["closed_leg_ids"] = sorted(done_ids | set(new_gone))
                rec["realized_qty"] = round((rec.get("realized_qty") or 0.0) + exit_qty, 8)
                rec["realized_pnl"] = round((rec.get("realized_pnl") or 0.0)
                                            + _pnl_of(rec, exit_px, exit_qty, fee_usdt, "USDT"), 2)
                rec["exit_fee"] = round((rec.get("exit_fee") or 0.0) + fee_usdt, 8)
                rec.setdefault("realized_parts", []).append({
                    "qty": exit_qty, "price": round(exit_px, 2), "fee": fee_usdt,
                    "fee_asset": "USDT(EQV)",
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
                dirty = True
            else:
                # leg 消失但搵唔到 trade (例如被 cancel) — mark done 避免每 tick 重查
                rec["closed_leg_ids"] = sorted(done_ids | set(new_gone))
                dirty = True
        remaining = round(rec.get("qty", 0) - (rec.get("realized_qty") or 0.0), 8)
        if remaining <= 1e-8:
            rec["status"] = "CLOSED"
            rec["exit_qty"] = rec.get("realized_qty")
            # exit_fill = realized_parts 加權平均 exit price (bug fix 09-04:
            # 之前冇 set → main() c['exit_fill'] KeyError crash)
            parts = rec.get("realized_parts") or []
            if parts:
                pq = sum(float(p["qty"]) for p in parts)
                if pq > 0:
                    rec["exit_fill"] = round(sum(float(p["price"]) * float(p["qty"]) for p in parts) / pq, 2)
            else:
                rec["exit_fill"] = rec.get("exit_fill") or rec.get("entry_fill")
            risk = abs(rec["entry_fill"] - float(rec["planned_stop"])) * rec["qty"]
            rec["pnl_usdt"] = round((rec.get("realized_pnl") or 0.0)
                                    - (rec.get("fee") or 0.0) * rec["entry_fill"], 2)
            rec["r_multiple"] = round(rec["pnl_usdt"] / risk, 3) if risk > 0 else 0.0
            rec["closed_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["closed_via"] = "oco_legs_all_gone"
            changed.append(rec)
        else:
            before = json.dumps(rec, sort_keys=True, default=str)
            _breakeven_remaining(rec, open_map, key, secret)
            _trail_leg(rec, open_map, key, secret)
            if json.dumps(rec, sort_keys=True, default=str) != before:
                dirty = True
    if changed or dirty:
        save_log(log_d)
    if changed:
        # 追加 closed history — **只計真正平倉嘅單**。
        # feat/limit-entry: LIMIT_EXPIRED / LIMIT_CANCELLED 係「掛單未成交就取消」,
        # 冇 trade 過、冇 PnL, 寫入 HISTORY 會污染 sumR / 勝率 (佢哋冇 r_multiple)。
        # 2026-09-27: 對帳判 CLOSED 嘅孤兒 (冇 exit 成交、冇 r_multiple) 亦唔可以入
        # HISTORY —— 佢哋唔係完成嘅 trade, 只係對帳結論 (入咗會令統計 KeyError/失真)。
        closed_recs = [r for r in changed if r.get("status") == "CLOSED"
                       and r.get("r_multiple") is not None]
        if closed_recs:
            hist = json.load(open(HISTORY)) if os.path.exists(HISTORY) else {"trades": []}
            hist["trades"].extend(closed_recs)
            with open(HISTORY, "w") as f:
                json.dump(hist, f, ensure_ascii=False, indent=2)
    return changed, wiped


def main():
    from btc_pause import check as _pause_check
    from binance_testnet_paper import _load_keys

    # 0. 停用硬閘 (2026-10-01)
    #    呢個係**唯一**落新單嘅路徑 (step 3), 所以閘擺呢度就夠 cover cron + 手動。
    #    ⚠️ 唔確定 (讀唔到 marker) 會 fail-safe 連 reconcile 都唔跑 ——
    #       連「有冇倉」都講唔準嘅話, 落任何單都係錯。
    #    閘擺喺 `_load_keys()` **之前**: 停用期間唔應該掂 credentials。
    paused, pause_reason, pause_certain = _pause_check()
    if paused and not pause_certain:
        log(pause_reason)
        return

    key, secret = _load_keys()
    if not key or not secret:
        log("❌ 冇 testnet keys")
        return

    # 1. reconcile
    #    ⚠️ 停用期間**照跑**: 佢係管理/平掉**現有**倉 (exit leg / trailing /
    #       breakeven) 嘅唯一路徑。停用 = 唔開新倉, 唔係「唔理已開嘅倉」。
    closed, wiped = reconcile_cycle(key, secret)
    for c in closed:
        st = c.get("status")
        # .get() 防禦 — 任何缺 field 都唔可以 crash 個 watchdog (bug fix 09-04)
        if st == "CLOSED":
            log(f"🔒 CLOSED {c.get('side','?')} {(c.get('pattern') or '?')[:18]} "
                f"exit={c.get('exit_fill')} pnl={c.get('pnl_usdt')}USDT R={c.get('r_multiple')}")
        elif c.get("was_limit_fill"):
            log(f"🎯 限價成交 {c.get('side','?')} {(c.get('pattern') or '?')[:18]} "
                f"@ {c.get('entry_fill')} (掛單 {c.get('limit_px')}) → "
                f"3 段 exit 已建 (RR {c.get('rr_fill')})")
        elif st == "LIMIT_EXPIRED":
            log(f"⌛ 限價過期 cancel {c.get('side','?')} {(c.get('pattern') or '?')[:18]} "
                f"@ {c.get('limit_px')} — {c.get('closed_note')}")
        elif st == "LIMIT_CANCELLED":
            log(f"🚫 限價單取消 {c.get('side','?')} {(c.get('pattern') or '?')[:18]} "
                f"@ {c.get('limit_px')} — {c.get('closed_note')}")
    for w in wiped:
        log(f"🧹 WIPED {w.get('side','?')} {(w.get('pattern') or '?')[:18]} "
            f"entry={w.get('entry_fill')} remaining={w.get('wiped_remaining_qty')} "
            f"(testnet 帳戶重置 — 終態 WIPED, 唔計入 sumR, cap 已釋放)")

    # 0b. 停用 → 喺呢度收工: 已經跑完 reconcile (管理現有倉), 但**唔開新倉**。
    #     訊息用 "⏸️" 而唔係 "⚠️"/"❌" —— 常規停用每 15 分鐘出現一次,
    #     落 cron wrapper 嘅 NOTABLE_KEYS 會變 TG spam。用 "⏸️" 走靜默,
    #     狀態由 btc_dual_report 每日 4 次報。
    if paused:
        log(f"{pause_reason}（reconcile 照跑, 統計照出）")

    # 2. 引擎掃描 —— ⛔ 停用期間跳過 (連帶跳過 step 3 落單)
    if not paused:
        out = sh("python3 btc_engine.py 2>&1 | tail -30")
        if "Trade Setups" not in out:
            log(f"⚠️ 引擎冇 setups (可能數據問題): {out[-120:]}")
            return

        # 3. 落單 (dedup + 風控內建)
        out2 = sh("python3 binance_testnet_paper.py 2>&1 | tail -10")
        if out2.strip():
            log(f"掃描結果: {out2.strip().splitlines()[-1]}")
        for line in out2.splitlines():
            if any(k in line for k in ("✅", "🚫", "❌", "[DRY]", "📌", "⚠️")):
                log(line.strip())

    # 4. 每日統計
    if closed:
        hist = json.load(open(HISTORY))
        # .get() 防禦 (09-27): 統計唔可以因任何缺 field 嘅記錄 crash。
        # GLM 09-27 #10: 缺 r_multiple 嘅記錄唔可以當 0R 計入勝率分母 ——
        # 同 HISTORY 寫入 filter (r_multiple is not None) 對齊。
        rs = [t["r_multiple"] for t in hist["trades"] if t.get("r_multiple") is not None]
        log(f"📊 累計 {len(rs)} 平倉: sumR={sum(rs):+.2f} 勝率={sum(1 for r in rs if r > 0)}/{len(rs)}")


if __name__ == "__main__":
    main()
