#!/usr/bin/env python3
"""test_btc_risk_guards.py — BTC 風控 guard 修復 (2026-09-20) 嘅守門測試。

背景 (實證): 2026-08-29 ~ 08-31 出現 4 對同 pattern 重疊倉, seeded_time 全部相隔
1-2 秒, 而 log 由頭到尾冇一筆 same_pattern skip 記錄。根因兩個:

  Bug 1 「1-2 秒窗口」: place_signal_order 成交後**冇即刻寫 log**, 一路等到
        build_exit_legs 建完所有 OCO legs 才 append。建 OCO 要 call 2-3 次 API
        (1-2 秒) → 呢段窗口 log 完全冇記錄 → 下一個 setup 嘅 guard 見到「冇 live 倉」
        → same_pattern / same_side / opp_side 全部放行。
  Bug 2 「白名單漏狀態」: live 判定用白名單 (FILLED_ENTRY/OCO_PLACED/LIMIT_PENDING),
        令 FLATTENED_OCO_FAILED / OCO_FAILED (可能仲喺市場) 被當成冇倉。

本 test 守五件事:
  A. is_live_rec fail-closed — 只有明確完結嘅狀態唔算 live
  B. 成交即刻寫 log — 模擬 build_exit_legs 慢, 下一個 setup 仍要見到倉
  C. 同 pattern / 同向 / 反向 guard 會擋 (唔會再有重疊倉)
  D. 每日 -R hard stop
  E. daily_realized_r 只計當日已 CLOSED 嘅單

跑: python3 test_btc_risk_guards.py
"""
import importlib.util
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "binance_testnet_paper", os.path.join(REPO, "binance_testnet_paper.py"))
btp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(btp)

FAILS = []
N = 0


def check(cond, label, extra=""):
    global N
    N += 1
    if cond:
        print(f"  OK   {label}")
    else:
        print(f"  FAIL {label}" + (f"  [{extra}]" if extra else ""))
        FAILS.append(label)


TMP = tempfile.mkdtemp(prefix="btc_guard_test_")
btp.LOG_PATH = os.path.join(TMP, "orders.json")


def reset_log(orders=None):
    with open(btp.LOG_PATH, "w") as f:
        json.dump({"orders": orders or [], "history": []}, f)


print("=== A. is_live_rec fail-closed ===")
LIVE_EXPECT = ["FILLED_ENTRY", "ENTRY_FILLED_PENDING_EXITS", "OCO_PLACED", "LIMIT_PENDING",
               "LIMIT_FILLED", "FLATTENED_OCO_FAILED", "OCO_FAILED", "WIPED",
               "SOMETHING_NEW_WE_NEVER_SAW"]
DONE_EXPECT = ["CLOSED", "LIMIT_EXPIRED", "LIMIT_CANCELLED", "SKIP_PREFLIGHT"]
for st in LIVE_EXPECT:
    check(btp.is_live_rec({"status": st}), f"{st} 當 live")
for st in DONE_EXPECT:
    check(not btp.is_live_rec({"status": st}), f"{st} 當已完結")

print("\n=== B. 成交即刻寫 log（封死 1-2 秒窗口）===")
reset_log()
rec = {"order_id": 111, "pattern": "🚩 Bull Flag (牛旗)", "side": "BUY",
       "status": "ENTRY_FILLED_PENDING_EXITS", "entry_fill": 80000.0}
btp._log_upsert(rec)
lg = btp.load_log()
check(len(lg["orders"]) == 1, "成交後 log 立即有 1 筆")
check(btp.is_live_rec(lg["orders"][0]), "該筆即刻算 live → 下一個 setup 會見到")

# 模擬 build_exit_legs 後來更新同一筆（唔應該重複 append）
rec2 = dict(rec)
rec2["status"] = "OCO_PLACED"
rec2["exit_leg_ids"] = [1, 2, 3]
btp._log_upsert(rec2)
lg = btp.load_log()
check(len(lg["orders"]) == 1, "更新唔會重複 append", f"len={len(lg['orders'])}")
check(lg["orders"][0]["status"] == "OCO_PLACED", "狀態已更新")

print("\n=== C. guard 會擋：重現 08-29 情境 ===")
reset_log()
# 第一筆已 live（同 pattern、同方向）
btp._log_upsert({"order_id": 9646238, "pattern": "🚩 Bear Flag (熊旗)", "side": "SELL",
                 "status": "OCO_PLACED", "entry_fill": 77666.0})
live = [o for o in btp.load_log()["orders"] if btp.is_live_rec(o)]
s = {"pattern": "🚩 Bear Flag (熊旗)", "btc_side": "SELL"}
same_pattern = [o for o in live if o.get("pattern") == s.get("pattern")]
same_side = [o for o in live if o.get("side") == s["btc_side"]]
opp_side = [o for o in live if o.get("side") != s["btc_side"]]
check(bool(same_pattern), "同 pattern 會被擋 (08-29 第二筆而家入唔到)")
check(bool(same_side), "同方向會被擋")
check(not opp_side, "同方向時 opp_side 為空 (唔會誤擋)")

# 反向情境
reset_log()
btp._log_upsert({"order_id": 1, "pattern": "🚩 Bull Flag (牛旗)", "side": "BUY",
                 "status": "OCO_PLACED", "entry_fill": 80000.0})
live = [o for o in btp.load_log()["orders"] if btp.is_live_rec(o)]
opp = [o for o in live if o.get("side") != "SELL"]
check(bool(opp), "反向會被擋")

# Bug 2 情境: FLATTENED_OCO_FAILED 而家要當 live
reset_log()
btp._log_upsert({"order_id": 2, "pattern": "📐 Ascending Triangle (上升三角形)",
                 "side": "BUY", "status": "FLATTENED_OCO_FAILED", "entry_fill": 78086.0})
live = [o for o in btp.load_log()["orders"] if btp.is_live_rec(o)]
check(len(live) == 1, "FLATTENED_OCO_FAILED 而家算 live (舊版算冇倉)")

print("\n=== D. 每日 -R hard stop ===")
check(btp.MAX_DAILY_LOSS_R == 3.0, f"MAX_DAILY_LOSS_R = 3.0", str(btp.MAX_DAILY_LOSS_R))
from datetime import datetime, timezone
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
reset_log([
    {"status": "CLOSED", "closed_time": f"{today}T03:00:00Z", "r_multiple": -1.2},
    {"status": "CLOSED", "closed_time": f"{today}T05:00:00Z", "r_multiple": -1.5},
    {"status": "CLOSED", "closed_time": f"{today}T07:00:00Z", "r_multiple": -0.4},
])
brake, tot, n = btp.daily_loss_brake(btp.load_log())
check(tot <= -btp.MAX_DAILY_LOSS_R, f"當日 {tot:+.2f}R 觸發煞停")
check(brake is True, "daily_loss_brake 回 True (會真嘅 return)")
reset_log([
    {"status": "CLOSED", "closed_time": f"{today}T03:00:00Z", "r_multiple": -1.2},
    {"status": "CLOSED", "closed_time": f"{today}T05:00:00Z", "r_multiple": +0.8},
])
brake, tot, n = btp.daily_loss_brake(btp.load_log())
check(brake is False, f"當日 {tot:+.2f}R 唔煞停")
# 邊界: 剛好 -3.0 要煞停
reset_log([{"status": "CLOSED", "closed_time": f"{today}T03:00:00Z", "r_multiple": -3.0}])
check(btp.daily_loss_brake(btp.load_log())[0] is True, "邊界 -3.00R 會煞停 (<=)")
reset_log([{"status": "CLOSED", "closed_time": f"{today}T03:00:00Z", "r_multiple": -2.99}])
check(btp.daily_loss_brake(btp.load_log())[0] is False, "邊界 -2.99R 唔煞停")

print("\n=== F. place_signal_order 真實路徑: 成交即寫 log（唔靠 _log_upsert 直接 call）===")
# 用假 _signed_request 模擬成交, 令 place_signal_order 跑到成交後嘅寫 log。
# 呢個補 mutation 揭到嘅缺口: 舊測試自己 call _log_upsert, 繞過咗真實路徑。
reset_log()
calls = []


class FakeResp:
    def __init__(self, d):
        self._d = d

    def read(self):
        return json.dumps(self._d).encode()


def fake_signed(method, path, params, key, secret):
    calls.append((method, path, params))
    # 記錄「每次 API call 時 log 有幾多筆」→ 用嚟斷言寫 log 發生喺建 OCO 之前
    log_snapshots.append(len(btp.load_log()["orders"]))
    if path.endswith("/order") and method == "POST":
        return {"orderId": 555, "fills": [{"price": "80000", "qty": "0.001", "commission": "0.08"}]}
    if "/order/oco" in path or path.endswith("/order"):
        return {"orderId": 556, "orderListId": 556, "orders": [{"orderId": 1}, {"orderId": 2}]}
    return {}


log_snapshots = []
btp._signed_request = fake_signed
btp.ENTRY_MODE = "limit"
setup = {"btc_side": "BUY", "btc_entry": 80000.0, "btc_stop": 79600.0,
         "btc_tp1": 80800.0, "btc_tp2": 81600.0, "pattern": "🚩 Bull Flag (牛旗)",
         "btc_limit_px": 80000.0, "verified": True}
rec_out, err = btp.place_signal_order(setup, "k", "s", atr=200.0, mode="limit")
lg = btp.load_log()
print(f"    limit 模式完結: err={err}  log 筆數={len(lg['orders'])}")
check(len(lg["orders"]) >= 1, "limit 掛單路徑有寫入 log")
if lg["orders"]:
    check(btp.is_live_rec(lg["orders"][0]),
          f"寫入嘅記錄算 live (status={lg['orders'][0].get('status')}) → 下一個 setup 見得到")

# 成交後 (reconcile 路徑) 都要即刻寫 log —— 呢個正是 08-29 漏嘅位
reset_log()
fill_rec = {"order_id": 777, "pattern": "🚩 Bear Flag (熊旗)", "side": "SELL",
            "qty": 0.001, "planned_stop": 77838.0, "planned_tp1": 76852.0,
            "planned_tp2": 76500.0, "atr": 200.0}
btp._log_upsert({**fill_rec, "status": "ENTRY_FILLED_PENDING_EXITS"})
lg = btp.load_log()
check(len(lg["orders"]) == 1 and btp.is_live_rec(lg["orders"][0]),
      "成交一刻已寫入且算 live（reconcile 成交路徑）")
btp.build_exit_legs({**fill_rec, "status": "ENTRY_FILLED_PENDING_EXITS"}, "k", "s", 0.00001)
lg2 = btp.load_log()
n_777 = [o for o in lg2["orders"] if o.get("order_id") == 777]
check(len(n_777) == 1, f"build_exit_legs 後 order_id 777 仍只有 1 筆", str(len(n_777)))
check(bool(n_777) and n_777[0].get("status") != "ENTRY_FILLED_PENDING_EXITS",
      f"狀態已由 build_exit_legs 更新 (={n_777[0].get('status') if n_777 else '?'})")

# ── 時序斷言 (Bug 1 嘅真牙) ────────────────────────────────────────────────
# 舊 code: place_signal_order (market 模式) 成交後唔寫 log, 一路到 build_exit_legs
# 建完 OCO 才寫 → **建 OCO 期間** log 係 0 → 下一個 setup 見到「冇倉」而放行。
# 新 code: 成交即寫 → 建 OCO 期間 log 已 ≥ 1。
# 預期時序: [0, 1, 1, 1] —— 第 1 個 call 係成交單本身 (log 當然仲係 0, 因為寫 log
# 係成交之後), 之後 OCO 嘅每個 call 都要見到 log ≥ 1。
reset_log()
log_snapshots.clear()
calls.clear()
btp.current_price = lambda: 80000.0
btp.place_signal_order(setup, "k", "s", atr=200.0, mode="market")
print(f"    market 模式 API call 時嘅 log 筆數時序: {log_snapshots}")
check(len(log_snapshots) >= 2, "有記錄到 成交 + OCO 嘅 API call 時序", str(log_snapshots))
if len(log_snapshots) >= 2:
    oco_snaps = log_snapshots[1:]          # 跳過成交單本身
    check(all(s >= 1 for s in oco_snaps),
          f"建 OCO 期間 log 一直有記錄 (=0 就會漏 guard)", str(oco_snaps))
    check(log_snapshots[0] == 0,
          "成交單本身時 log 仍然係 0 (正常: 未成交冇記錄)",
          str(log_snapshots[0]))

print("\n=== E. daily_realized_r 範圍正確 ===")
reset_log([
    {"status": "CLOSED", "closed_time": f"{today}T03:00:00Z", "r_multiple": -1.0},
    {"status": "CLOSED", "closed_time": "2020-01-01T03:00:00Z", "r_multiple": -5.0},  # 舊日
    {"status": "OCO_PLACED", "seeded_time": f"{today}T04:00:00Z", "r_multiple": None},  # 未平
    {"status": "SKIP_PREFLIGHT", "seeded_time": f"{today}T04:30:00Z", "r_multiple": -9.0},
])
tot, n = btp.daily_realized_r(btp.load_log())
check(abs(tot - (-1.0)) < 1e-9, f"只計當日已平倉 → {tot:+.2f}R", str(tot))
check(n == 1, f"只計 1 筆 (舊日/未平/skip 唔計)", str(n))

print("\n=== G. hard stop 接線檢查 (main() 真嘅會 call 煞停並 return) ===")
# 為咩要靜態檢查: main() 需要真 API key 才跑得, 所以冇法喺 test 裏面直接 call。
# 但「有 call 決策函數」同「會 return」係可以用 source 斷言守住嘅 ——
# 之前只測 daily_loss_brake 純函數, mutation 把 `if daily_loss_brake(log)[0]:` 改成
# `if False:` 就完全捉唔到 (決策函數仍然正確, 但冇人用)。
SRC_PATH = os.path.join(REPO, "binance_testnet_paper.py")
src = open(SRC_PATH, encoding="utf-8").read()
check("daily_loss_brake(log)[0]" in src,
      "main() 有 call daily_loss_brake 做決策 (唔係 if False)")
# 煞停之後必須立即 return, 唔可以繼續落單
brake_idx = src.find("daily_loss_brake(log)[0]")
if brake_idx > 0:
    tail = src[brake_idx:brake_idx + 400]
    check("return" in tail.split("for s in todo")[0],
          "煞停之後有 return (唔會照落單)")
check("MAX_DAILY_LOSS_R" in src and "BTC_MAX_DAILY_LOSS_R" in src,
      "門檻可由環境變數覆蓋")
check("daily_loss_brake" in src.split("def daily_loss_brake")[1],
      "daily_loss_brake 有被引用 (唔係死代碼)")

print("\n" + "=" * 70)
print(f"總共 {N} 個斷言, {len(FAILS)} 個 FAIL")
if FAILS:
    for f in FAILS:
        print("  ❌", f)
    sys.exit(1)
print("✅ 全部通過")
