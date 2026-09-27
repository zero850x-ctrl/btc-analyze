#!/usr/bin/env python3
"""一次性 integration check: 限價掛單 → 對帳 → cancel (真 testnet, 即掛即 cancel).

掛 BUY LIMIT @ 市價 ×0.75 (遠離市價, 一定唔成交), 驗證:
  1. place_signal_order(mode="limit") 真係掛到 LIMIT 單 (唔係 MARKET)
  2. binance 認得張單 (openOrders 見到)
  3. reconcile_cycle 唔會亂動未過期掛單
  4. cancel 之後 reconcile 見到 CANCELED → LIMIT_CANCELLED
最後清理 log (外科式: 移除測試 record、保留同期生產寫入), 唔污染統計。
"""
import json
import os
import sys
import time
import copy
import atexit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_testnet_paper as btp
import btc_auto_trade_cycle as cyc

LOG_PATH = btp.LOG_PATH if hasattr(btp, "LOG_PATH") else os.path.expanduser(
    "~/.hermes/reports/btc_testnet_orders.json")
backup = LOG_PATH + ".prelimitcheck"

key, secret = btp._load_keys()
px = btp.current_price()
print(f"BTC ${px:,.0f}")

# ── 0. backup log ──
with open(LOG_PATH) as f:
    orig_log = json.load(f)
with open(backup, "w") as f:
    json.dump(orig_log, f, ensure_ascii=False, indent=2)
n_before = len(orig_log["orders"])
orig_ids = {r.get("order_id") for r in orig_log["orders"]}
_test_ids = set()          # 測試期間建立嘅訂單 id (還原時外科式移除)
print(f"log backup → {backup} ({n_before} orders)")

# 2026-09-27: 呢個 script 本質上會暫時改生產 log (integration check) —— register
# atexit 還原: 中途 assert 爆 / KeyboardInterrupt 都會還原 + 清 backup。
# (之前寫法只有行到最尾先還原; 中途 crash 會留低測試記錄喺生產 log。)
_restored = {"done": False}


def _restore_log():
    if _restored["done"]:
        return
    _restored["done"] = True
    try:
        # 2026-09-27 GLM #7: 唔可以攞 script 心目中嘅 orig_log 直接覆寫 ——
        # 執行期間生產 cron 隨時會寫入新記錄/更新狀態, 覆寫會抹走佢哋。
        # 外科式還原: 讀返當前 log → 移除測試記錄 → 補回唔見咗嘅生產記錄。
        with open(LOG_PATH) as f:
            cur = json.load(f)
        cur_orders = cur.get("orders", [])
        kept = [r for r in cur_orders if r.get("order_id") not in _test_ids]
        have = {r.get("order_id") for r in kept}
        # 極端情況先會補回 (cron 唔會刪記錄); 若補返嘅係已完成記錄, 下輪對帳會再處理
        # (GLM round-2 #7 註記)。
        readded = [r for r in orig_log["orders"] if r.get("order_id") not in have]
        cur["orders"] = kept + readded
        with open(LOG_PATH, "w") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        if os.path.exists(backup):
            os.unlink(backup)
        print(f"\n[cleanup] 生產 log 外科式還原: 移除測試 {len(cur_orders) - len(kept)} 筆, "
              f"補回 {len(readded)} 筆, 現 {len(cur['orders'])} orders")
    except Exception as e:                          # noqa: BLE001
        try:
            with open(LOG_PATH, "w") as f:
                json.dump(orig_log, f, ensure_ascii=False, indent=2)
            print(f"\n[cleanup] ⚠️ 外科式還原失敗 ({e}), 已用 backup 覆寫還原")
        except Exception as e2:                     # noqa: BLE001
            print(f"\n[cleanup] ⚠️ 還原失敗: {e2} — backup 喺 {backup}")


atexit.register(_restore_log)

# ── 1. 掛一張唔會成交嘅 LIMIT (距市價 1.5%: > 唔會即刻成交, < 3% 唔會被偏離保護 cancel) ──
limit_px = round(px * 0.985, 2)
setup = {
    "btc_side": "BUY", "btc_entry": limit_px, "btc_limit_px": limit_px,
    "btc_stop": round(limit_px * 0.97, 2), "btc_tp1": round(limit_px * 1.06, 2),
    "btc_tp2": round(limit_px * 1.12, 2),
    "pattern": "🧪 INTEGRATION-CHECK (即掛即cancel)",
}
rec, err = btp.place_signal_order(setup, key, secret, atr=800.0, mode="limit")
print(f"\n1) place_signal_order → err={err}")
assert err is None and rec, "掛單失敗"
oid = rec["order_id"]
_test_ids.add(oid)
print(f"   status={rec['status']} orderId={oid} limit_px={rec['limit_px']:,.2f}")
assert rec["status"] == "LIMIT_PENDING"

# ── 2. Binance 認得張單 + 類型係 LIMIT ──
print("\n2) 查 binance 真實狀態")
o = btp._signed_request("GET", "/api/v3/order", {"symbol": "BTCUSDT", "orderId": oid}, key, secret)
print(f"   binance: type={o['type']} side={o['side']} price={o['price']} status={o['status']}")
assert o["type"] == "LIMIT" and o["side"] == "BUY"
opens = btp._signed_request("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"}, key, secret)
assert any(x["orderId"] == oid for x in opens), "openOrders 見唔到張單"
print(f"   openOrders: 見到 orderId={oid} ✓ (共 {len(opens)} 張)")

# ── 3. reconcile 唔會亂動未過期掛單 ──
print("\n3) reconcile_cycle (未過期掛單)")
ch, wp = cyc.reconcile_cycle(key, secret)
moved = [c for c in ch if c.get("order_id") == oid]
print(f"   changed={len(ch)} wiped={len(wp)} 其中涉及測試單={len(moved)}")
assert not moved, "未過期掛單唔應該被動"
with open(LOG_PATH) as f:
    after3 = json.load(f)
r3 = [r for r in after3["orders"] if r.get("order_id") == oid][0]
print(f"   測試單 status 仍然 = {r3['status']} ✓")
assert r3["status"] == "LIMIT_PENDING"

# ── 4. cancel → reconcile 見到 CANCELED ──
print("\n4) cancel + reconcile")
btp._signed_request("DELETE", "/api/v3/order", {"symbol": "BTCUSDT", "orderId": oid}, key, secret)
ch, wp = cyc.reconcile_cycle(key, secret)
with open(LOG_PATH) as f:
    after4 = json.load(f)
r4 = [r for r in after4["orders"] if r.get("order_id") == oid][0]
print(f"   測試單 status={r4['status']} note={r4.get('closed_note')}")
assert r4["status"] == "LIMIT_CANCELLED", f"expected LIMIT_CANCELLED, got {r4['status']}"

# ── 4b. 偏離保護: 掛遠 (距市價 25%) → reconcile 自動 cancel ──
print("\n4b) 偏離保護 (>3% 自動 cancel)")
far_px = round(px * 0.75, 2)
setup_far = dict(setup)
setup_far.update({"btc_limit_px": far_px, "btc_entry": far_px,
                  "btc_stop": round(far_px * 0.97, 2),
                  "btc_tp1": round(far_px * 1.06, 2),
                  "btc_tp2": round(far_px * 1.12, 2)})
rec_f, err_f = btp.place_signal_order(setup_far, key, secret, atr=800.0, mode="limit")
assert err_f is None and rec_f, f"遠價掛單失敗: {err_f}"
oid_f = rec_f["order_id"]
_test_ids.add(oid_f)
print(f"   掛 {far_px:,.2f} (市價 {px:,.0f}, 距 {abs(px - far_px) / far_px * 100:.1f}%)")
ch, wp = cyc.reconcile_cycle(key, secret)
with open(LOG_PATH) as f:
    after4b = json.load(f)
r4b = [r for r in after4b["orders"] if r.get("order_id") == oid_f][0]
print(f"   reconcile → status={r4b['status']} note={r4b.get('closed_note')}")
assert r4b["status"] == "LIMIT_EXPIRED", f"expected LIMIT_EXPIRED, got {r4b['status']}"
opens_f = btp._signed_request("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"}, key, secret)
assert not any(x["orderId"] == oid_f for x in opens_f), "偏離單應該已被 cancel"
print("   binance 確認已 cancel ✓")

# ── 5. HISTORY 冇被污染 ──
hist = json.load(open(cyc.HISTORY))
bad = [t for t in hist["trades"] if t.get("pattern", "").startswith("🧪")]
print(f"\n5) HISTORY: {len(hist['trades'])} 單, 其中測試單 = {len(bad)}")
assert not bad, "測試單污染咗 HISTORY"

# ── 6. 清理 log (外科式: 移除測試記錄, 保留同期生產寫入) ──
_restore_log()
with open(LOG_PATH) as f:
    restored = json.load(f)
_left = [r.get("order_id") for r in restored["orders"] if r.get("order_id") in _test_ids]
_missing = [i for i in orig_ids
            if not any(r.get("order_id") == i for r in restored["orders"])]
print(f"\n6) log 還原: {len(restored['orders'])} orders (測試前 {n_before}; "
      f"測試記錄殘留 {len(_left)}; 生產記錄唔見 {len(_missing)})")
assert not _left, f"測試記錄殘留: {_left}"
assert not _missing, f"生產記錄唔見咗: {_missing}"

opens_end = btp._signed_request("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"}, key, secret)
print(f"   最後 openOrders = {len(opens_end)} 張 (冇殘留)")
print("\n🎉 INTEGRATION CHECK ALL PASS")
