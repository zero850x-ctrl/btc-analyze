#!/usr/bin/env python3
"""btc_rebalance.py — BTC 60/40 定期再平衡系統 (spot testnet, 唔用槓桿/futures).

策略依據 (2026-09-18 研究, research_premium.py):
  11 年 (2015-2026) 回測: 再平衡 60/40 每季 CAGR 48.0% / maxDD 63.7% / Sharpe 1.18
  對比 Buy&Hold CAGR 69.6% / maxDD 83.4% / Sharpe 1.13
  → 絕對回報低啲, 但風險調整後更好 (maxDD 細 ~20pp), 唔靠預測方向。

機制:
  target = BTC 60% / USDT 40%
  1. 每逢季度月 (1/4/7/10) 固定再平衡
  2. 平時偏離超過 BAND (5%) 亦會即時再平衡
  3. 差額細過 MIN_TRADE_USD 唔做 (避免 fee 蠶食)

用法:
  python3 btc_rebalance.py --status      # 睇現時配置
  python3 btc_rebalance.py --dry-run     # 模擬 (唔落單)
  python3 btc_rebalance.py               # 真做 (有需要才落單)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from binance_testnet_paper import (  # noqa: E402
    _load_keys, _signed_request, current_price, exchange_filters, round_step,
    MIN_NOTIONAL,
)

HKT = timezone(timedelta(hours=8))
LOG_PATH = os.path.expanduser("~/.hermes/reports/btc_rebalance_log.json")

TARGET_BTC_PCT = float(os.environ.get("BTC_REBAL_TARGET", "0.60"))
BAND = float(os.environ.get("BTC_REBAL_BAND", "0.05"))          # 偏離 5% 即做
MIN_TRADE_USD = float(os.environ.get("BTC_REBAL_MIN_USD", "50"))
QUARTER_MONTHS = (1, 4, 7, 10)


def now_hkt():
    return datetime.now(HKT)


def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            return json.load(f)
    return {"created": now_hkt().isoformat(), "target_btc_pct": TARGET_BTC_PCT,
            "events": [], "snapshots": []}


def save_log(log):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    tmp = LOG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(log, f, indent=1, ensure_ascii=False)
    os.replace(tmp, LOG_PATH)


def read_account(key, secret):
    acct = _signed_request("GET", "/api/v3/account", {}, key, secret)
    bal = {b["asset"]: float(b["free"]) + float(b["locked"]) for b in acct["balances"]}
    return bal.get("BTC", 0.0), bal.get("USDT", 0.0)


def compute_state(btc, usdt, px):
    total = btc * px + usdt
    btc_pct = (btc * px) / total if total > 0 else 0.0
    return {"btc": btc, "usdt": usdt, "px": px, "total_usd": total,
            "btc_pct": btc_pct, "usdt_pct": 1 - btc_pct,
            "drift_pct": (btc_pct - TARGET_BTC_PCT) * 100,
            "target_btc": total * TARGET_BTC_PCT / px if px else 0,
            "target_usdt": total * (1 - TARGET_BTC_PCT)}


def is_quarter_month(dt=None):
    return (dt or now_hkt()).month in QUARTER_MONTHS


def rebalance(key, secret, dry=False, force=False, reason=""):
    """對外接口: 執行再平衡 + 記 log (唔理邊個 caller 都唔會漏記).

    返回 (action, detail). action ∈ {'none','buy','sell'}.
    """
    action, detail = _decide_and_execute(key, secret, dry=dry, force=force, reason=reason)
    if not dry:
        _record(action, detail)
    return action, detail


def _record(action, detail):
    log = load_log()
    log["snapshots"].append({"ts": now_hkt().isoformat(), **{
        k: detail["state"][k] for k in ("btc", "usdt", "px", "total_usd",
                                        "btc_pct", "drift_pct")}})
    log["snapshots"] = log["snapshots"][-500:]
    if action != "none":
        log["events"].append({"ts": now_hkt().isoformat(), "action": action,
                              "trigger": detail.get("trigger"),
                              **{k: v for k, v in detail.items()
                                 if k not in ("state", "msg", "trigger")}})
    save_log(log)


def _decide_and_execute(key, secret, dry=False, force=False, reason=""):
    """返回 (action, detail). action ∈ {'none','buy','sell'}."""
    px = current_price()
    btc, usdt = read_account(key, secret)
    st = compute_state(btc, usdt, px)

    qtr = is_quarter_month()
    drift = abs(st["btc_pct"] - TARGET_BTC_PCT)
    trigger = None
    if force:
        trigger = reason or "force"
    elif qtr:
        trigger = f"季度月 ({now_hkt().month} 月)"
    elif drift > BAND:
        trigger = f"偏離 {drift * 100:.1f}% > {BAND * 100:.0f}%"

    if not trigger:
        return "none", {"state": st, "msg": (
            f"唔需要再平衡 — BTC {st['btc_pct'] * 100:.1f}% (目標 {TARGET_BTC_PCT * 100:.0f}%, "
            f"偏離 {st['drift_pct']:+.1f}%), 下次季度月再檢")}

    need_btc = st["target_btc"] - st["btc"]       # >0 要買 BTC (單位: BTC)
    need_usd = need_btc * px                      # 對應金額 (單位: USDT)
    if abs(need_usd) < MIN_TRADE_USD:
        return "none", {"state": st, "msg": (
            f"{trigger} 但要調整嘅金額 ${abs(need_usd):.2f} < MIN ${MIN_TRADE_USD:.0f} — 唔做")}

    step, qty_min, min_notional = exchange_filters(key, secret)

    if need_btc > 0:
        # 買 BTC: 用 quoteOrderQty (USDT 金額)
        buy_usd = need_usd
        if buy_usd < min_notional:
            return "none", {"state": st, "msg": (
                f"買入額 ${buy_usd:.2f} < minNotional ${min_notional:.2f}")}
        if dry:
            return "buy", {"state": st, "trigger": trigger, "msg": (
                f"[DRY] {trigger} — 買入 BTC 用 ${buy_usd:.2f} (現 BTC {st['btc_pct'] * 100:.1f}% → "
                f"目標 {TARGET_BTC_PCT * 100:.0f}%)"),
                "quoteOrderQty": round(buy_usd, 2)}
        r = _signed_request("POST", "/api/v3/order",
                            {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
                             "quoteOrderQty": f"{buy_usd:.2f}", "newOrderRespType": "FULL"},
                            key, secret)
        fills = r.get("fills") or []
        got = sum(float(f["qty"]) for f in fills)
        spent = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        avg = spent / got if got else 0
        return "buy", {"state": st, "trigger": trigger, "order_id": r.get("orderId"),
                       "executed_qty": got, "quote_spent": spent, "avg_price": avg,
                       "msg": (f"{trigger} — 買入 {got:.6f} BTC @ ${avg:,.2f} "
                               f"(用 ${spent:,.2f})")}
    else:
        qty = round_step(abs(need_btc), step)
        if qty * px < min_notional:
            return "none", {"state": st, "msg": (
                f"賣出額 ${qty * px:.2f} < minNotional ${min_notional:.2f}")}
        if dry:
            return "sell", {"state": st, "trigger": trigger, "msg": (
                f"[DRY] {trigger} — 賣出 {qty:.6f} BTC (~${qty * px:,.2f}) "
                f"(現 BTC {st['btc_pct'] * 100:.1f}% → 目標 {TARGET_BTC_PCT * 100:.0f}%)"),
                "quantity": qty}
        r = _signed_request("POST", "/api/v3/order",
                            {"symbol": "BTCUSDT", "side": "SELL", "type": "MARKET",
                             "quantity": f"{qty:.6f}", "newOrderRespType": "FULL"},
                            key, secret)
        fills = r.get("fills") or []
        sold = sum(float(f["qty"]) for f in fills)
        gained = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        avg = gained / sold if sold else 0
        return "sell", {"state": st, "trigger": trigger, "order_id": r.get("orderId"),
                        "executed_qty": sold, "quote_gained": gained, "avg_price": avg,
                        "msg": (f"{trigger} — 賣出 {sold:.6f} BTC @ ${avg:,.2f} "
                                f"(得 ${gained:,.2f})")}


def fmt_status(st):
    return (f"BTC {st['btc']:.6f} (${st['btc'] * st['px']:,.2f}, {st['btc_pct'] * 100:.1f}%) | "
            f"USDT {st['usdt']:,.2f} ({st['usdt_pct'] * 100:.1f}%) | "
            f"總值 ${st['total_usd']:,.2f} @ ${st['px']:,.2f} | 偏離 {st['drift_pct']:+.1f}%")


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    force = "--force" in args
    status_only = "--status" in args

    key, secret = _load_keys()
    px = current_price()
    btc, usdt = read_account(key, secret)
    st = compute_state(btc, usdt, px)

    if status_only:
        need_qty = st["target_btc"] - st["btc"]
        need_usd = need_qty * px
        act = "買入" if need_qty > 0 else "賣出"
        print(f"📊 再平衡狀態 {now_hkt().strftime('%Y-%m-%d %H:%M')} HKT")
        print(f"  目標: BTC {TARGET_BTC_PCT * 100:.0f}% / USDT {(1 - TARGET_BTC_PCT) * 100:.0f}%"
              f"  偏離帶 ±{BAND * 100:.0f}%  季度月 {QUARTER_MONTHS}")
        print(f"  現時: {fmt_status(st)}")
        print(f"  目標倉: {st['target_btc']:.6f} BTC / ${st['target_usdt']:,.2f} USDT")
        print(f"  需要: {act} {abs(need_qty):.6f} BTC (~${abs(need_usd):,.2f})"
              f"{'  ⚠️ 未夠 MIN_TRADE_USD' if abs(need_usd) < MIN_TRADE_USD else ''}")
        trig = ("季度月，今次會做" if is_quarter_month()
                else f"非季度月，偏離 {abs(st['drift_pct']):.1f}% (需 > {BAND * 100:.0f}% 才做)")
        print(f"  觸發: {trig}")
        return

    action, detail = rebalance(key, secret, dry=dry, force=force)
    if "state" in detail:
        print(fmt_status(detail["state"]))
    print(detail.get("msg", ""))


if __name__ == "__main__":
    main()
