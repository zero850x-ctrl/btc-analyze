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

print("\n=== C. guard 會擋（call 生產嘅 guards_allow，唔係 test 自己實作）===")
# ⚠️ 2026-09-20 GLM review: 第一版喺 test 入面重新實作過濾邏輯 → 測緊自己,
# mutation 刪走 main() 嘅 check 都捉唔到。而家直接 call 生產嘅 guards_allow()。
reset_log()
btp._log_upsert({"order_id": 9646238, "pattern": "🚩 Bear Flag (熊旗)", "side": "SELL",
                 "status": "OCO_PLACED", "entry_fill": 77666.0})
s_same = {"pattern": "🚩 Bear Flag (熊旗)", "btc_side": "SELL"}
allow, why = btp.guards_allow(btp.load_log(), s_same)
check(not allow, f"同 pattern 被擋 ({why})")
check("same_pattern" in why, "擋嘅理由指名 same_pattern", why)

s_same2 = {"pattern": "🚩 Bull Flag (牛旗)", "btc_side": "SELL"}
allow, why = btp.guards_allow(btp.load_log(), s_same2)
check(not allow, f"同方向 (唔同 pattern) 被擋 ({why})")
check("同向" in why or "cap" in why, "擋嘅理由指名同向上限", why)

s_opp = {"pattern": "🚩 Bull Flag (牛旗)", "btc_side": "BUY"}
allow, why = btp.guards_allow(btp.load_log(), s_opp)
check(not allow, f"反向被擋 ({why})")
check("反向" in why, "擋嘅理由指名反向", why)

reset_log()
allow, why = btp.guards_allow(btp.load_log(), s_same)
check(allow, "冇任何 live 倉時放行")

# Bug 2 情境: FLATTENED_OCO_FAILED 而家要當 live
reset_log()
btp._log_upsert({"order_id": 2, "pattern": "📐 Ascending Triangle (上升三角形)",
                 "side": "BUY", "status": "FLATTENED_OCO_FAILED", "entry_fill": 78086.0})
live = [o for o in btp.load_log()["orders"] if btp.is_live_rec(o)]
check(len(live) == 1, "FLATTENED_OCO_FAILED 而家算 live (舊版算冇倉)")
allow, why = btp.guards_allow(btp.load_log(), {"pattern": "x", "btc_side": "BUY"})
check(not allow, "孤兒狀態會擋新單 (fail-closed 生效)")

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

print("\n=== H. 孤兒狀態復原（GLM #1: fail-closed 必須有復原路徑）===")
reset_log()
btp._log_upsert({"order_id": 11, "pattern": "A", "side": "BUY",
                 "status": "FLATTENED_OCO_FAILED", "entry_fill": 80000.0})
btp._log_upsert({"order_id": 12, "pattern": "B", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80100.0})
btp._log_upsert({"order_id": 13, "pattern": "C", "side": "BUY",
                 "status": "CLOSED", "entry_fill": 80200.0})
# 情境 1: exchange 冇倉 → 孤兒歸 CLOSED (引擎解鎖)
lg = btp.load_log()
changed, summ = btp.resolve_orphan_states(lg, held_qty=0.0)
check(summ["closed_orphan"] == 2, f"2 個孤兒歸 CLOSED", str(summ))
check(not any(o.get("status") == "FLATTENED_OCO_FAILED" for o in lg["orders"]),
      "FLATTENED_OCO_FAILED 已復原")
check(all(btp.is_live_rec(o) is False for o in lg["orders"]),
      "全部記錄唔再當 live (引擎解鎖)")
check(any(o.get("resolved_via") == "no_position" for o in lg["orders"]),
      "有標記 resolved_via=no_position (可審計)")
check(len(changed) == 2 and summ["closed_orphan"] == 2,
      f"回傳 changed 有 2 筆 (唔可以丟棄改動)", f"changed={len(changed)}")
# CLOSED 唔應該被改
check(any(o.get("order_id") == 13 and o.get("resolved_via") is None for o in lg["orders"]),
      "本來已 CLOSED 嘅單唔會被改")

# 情境 2 (GLM A 修正後): 有倉 + 有 rebuild → 真正補建 legs, 唔係只標 needs_legs
reset_log()
btp._log_upsert({"order_id": 21, "pattern": "A", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
built = []


def _fake_rebuild(rec):
    built.append(rec.get("order_id"))
    return {**rec, "status": "OCO_PLACED", "exit_leg_ids": [1, 2, 3]}


changed, summ = btp.resolve_orphan_states(lg, held_qty=0.001, rebuild=_fake_rebuild)
check(summ["rebuilt"] == 1, "有倉 + 有 rebuild → 真正補建 legs", str(summ))
check(built == [21], "rebuild 收到正確記錄", str(built))
check(lg["orders"][0].get("status") == "OCO_PLACED",
      f"狀態更新為 OCO_PLACED (= {lg['orders'][0].get('status')})")
check(lg["orders"][0].get("needs_legs") is None, "唔會遺留 needs_legs (死巷已消除)")
check(lg["orders"][0].get("rebuilt_legs_ts") is not None, "有記錄補建時間 (可審計)")

# 情境 2b: rebuild 失敗 → 標 needs_legs + rebuild_error (唔會靜默)
reset_log()
btp._log_upsert({"order_id": 22, "pattern": "A", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()


def _failing_rebuild(rec):
    raise RuntimeError("exchange 拒單")


changed, summ = btp.resolve_orphan_states(lg, held_qty=0.001, rebuild=_failing_rebuild)
check(summ["needs_legs"] == 1, "rebuild 失敗 → needs_legs=1", str(summ))
check("RuntimeError" in str(lg["orders"][0].get("rebuild_error")),
      "有記錄失敗原因", str(lg["orders"][0].get("rebuild_error")))
check(btp.is_live_rec(lg["orders"][0]), "失敗後仍然當 live (唔會誤放行)")

# 情境 2c (GLM B): 冇 rebuild (lot_step 攞唔到) → 保守標記
reset_log()
btp._log_upsert({"order_id": 23, "pattern": "A", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
changed, summ = btp.resolve_orphan_states(lg, held_qty=0.001, rebuild=None)
check(summ["needs_legs"] == 1, "冇 rebuild 時標 needs_legs", str(summ))

# 情境 2d (GLM B): dust → 當冇倉 (唔會因為 testnet dust 而鎖死)
reset_log()
btp._log_upsert({"order_id": 24, "pattern": "A", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
changed, summ = btp.resolve_orphan_states(lg, held_qty=0.00005, dust_eps=0.0001)
check(summ["closed_orphan"] == 1,
      "持倉低於 dust_eps → 當冇倉歸 CLOSED (GLM B)", str(summ))
check(not btp.is_live_rec(lg["orders"][0]), "dust 情境下引擎解鎖")

# 情境 2e (GLM B): per-record callable —— 兩個孤兒一個有倉一個冇倉
reset_log()
btp._log_upsert({"order_id": 25, "pattern": "A", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
btp._log_upsert({"order_id": 26, "pattern": "B", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80100.0})
lg = btp.load_log()
held_map = {25: 0.0, 26: 0.001}
changed, summ = btp.resolve_orphan_states(
    lg, held_qty=lambda r: held_map.get(r.get("order_id")),
    dust_eps=0.0001, rebuild=_fake_rebuild)
check(summ["closed_orphan"] == 1 and summ["rebuilt"] == 1,
      "per-record: 一個歸 CLOSED 一個補建 (唔會同樣對待)", str(summ))

# 情境 3: held_qty 未知 (None) → 一律 skip, 唔改
reset_log()
btp._log_upsert({"order_id": 31, "pattern": "A", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
changed, summ = btp.resolve_orphan_states(lg, held_qty=None)
check(summ["skipped"] == 1 and summ["closed_orphan"] == 0,
      "倉位未知時唔會亂改 (skip)", str(summ))
check(btp.is_live_rec(lg["orders"][0]), "未知時維持 live (fail-closed 保守)")

print("\n=== H2. atomic save_log（GLM #9: crash mid-write 會令 log 爛 → 全部放行）===")
reset_log()
big = {"orders": [{"order_id": i, "pattern": f"p{i}", "status": "CLOSED",
                   "r_multiple": 0.1} for i in range(200)]}
btp.save_log(big)
lg = btp.load_log()
check(len(lg["orders"]) == 200, "正常寫入讀得返", str(len(lg["orders"])))
# 冇殘留 .tmp 檔
import glob as _glob
leftovers = _glob.glob(os.path.join(os.path.dirname(btp.LOG_PATH), ".orders.*.tmp"))
check(not leftovers, "冇殘留 temp 檔", str(leftovers))
# atomic: 寫入期間另一讀者永遠見到舊或新 (唔會見到空/半截)
check(os.path.exists(btp.LOG_PATH), "目標檔存在 (os.replace 生效)")
src_full = open(os.path.join(REPO, "binance_testnet_paper.py"), encoding="utf-8").read()
check("os.replace(tmp, LOG_PATH)" in src_full, "save_log 用 os.replace (atomic)")

print("\n=== H3. GLM 第三輪: F1/F2/F3/F5 ===")
# F2: rebuild 冇轉 status → 必須當失敗 (唔可以每個 cycle 重複補建 → 重複 SELL legs)
reset_log()
btp._log_upsert({"order_id": 41, "pattern": "A", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()


def _noop_rebuild(rec):
    return None          # 冇改 status = 冇真正補建


changed, summ = btp.resolve_orphan_states(lg, held_qty=0.001, rebuild=_noop_rebuild)
check(summ["rebuilt"] == 0, "rebuild 冇轉 status → 唔計 rebuilt", str(summ))
check(summ["needs_legs"] == 1, "改為 needs_legs (唔會當成功)", str(summ))
check("冇令記錄離開孤兒狀態" in str(lg["orders"][0].get("rebuild_error")),
      "有明確錯誤訊息", str(lg["orders"][0].get("rebuild_error")))
check(lg["orders"][0].get("rebuilt_legs_ts") is None, "唔會寫 rebuilt_legs_ts (假成功)")

# F2b: rebuild 返 dict 但 status 仍係孤兒 → 一樣要當失敗
reset_log()
btp._log_upsert({"order_id": 42, "pattern": "A", "side": "BUY",
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()


def _return_same_status(rec):
    return {**rec, "status": "OCO_FAILED", "note": "tried"}


changed, summ = btp.resolve_orphan_states(lg, held_qty=0.001, rebuild=_return_same_status)
check(summ["rebuilt"] == 0, "返 dict 但 status 未轉 → 仍然當失敗", str(summ))

# F2c: 成功 rebuild 要清走舊 rebuild_error
reset_log()
btp._log_upsert({"order_id": 43, "pattern": "A", "side": "BUY", "status": "OCO_FAILED",
                 "entry_fill": 80000.0, "rebuild_error": "舊錯誤"})
lg = btp.load_log()


def _good_rebuild(rec):
    rec["status"] = "OCO_PLACED"
    rec["exit_leg_ids"] = [1, 2]
    return rec


changed, summ = btp.resolve_orphan_states(lg, held_qty=0.001, rebuild=_good_rebuild)
check(summ["rebuilt"] == 1, "正常 rebuild 成功", str(summ))
check(lg["orders"][0].get("rebuild_error") is None,
      "成功後清走舊 rebuild_error (審計唔會誤導)")

# F3: FLATTENED_OCO_FAILED + flatten_ok=None → freeze, 唔可以自動補 legs
reset_log()
btp._log_upsert({"order_id": 51, "pattern": "A", "side": "BUY",
                 "status": "FLATTENED_OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
called = []
changed, summ = btp.resolve_orphan_states(
    lg, held_qty=0.001, rebuild=lambda r: called.append(r) or _good_rebuild(r))
check(not called, "結果不明嘅記錄唔會 call rebuild (唔會亂賣)", str(len(called)))
check(summ.get("frozen_unknown") == 1, "標記 frozen_unknown", str(summ))
check(lg["orders"][0].get("needs_manual_reconcile") is True,
      "標 needs_manual_reconcile (交人手對賬)")
check(btp.is_live_rec(lg["orders"][0]), "仍然當 live (保守)")

# F3b: flatten_ok=True (明確已平) → 可以正常處理
reset_log()
btp._log_upsert({"order_id": 52, "pattern": "A", "side": "BUY",
                 "status": "FLATTENED_OCO_FAILED", "entry_fill": 80000.0,
                 "flatten_ok": True})
lg = btp.load_log()
changed, summ = btp.resolve_orphan_states(lg, held_qty=0.001, rebuild=_good_rebuild)
check(summ["rebuilt"] == 1, "flatten_ok=True 時可以正常補建", str(summ))

# F5: LIVE_STATUS 死代碼已刪 (只可以剩註釋提及, 唔可以有賦值)
import re as _re2
bfull = open(os.path.join(REPO, "binance_testnet_paper.py"), encoding="utf-8").read()
check(not _re2.search(r"^LIVE_STATUS\s*=", bfull, _re2.M),
      "LIVE_STATUS 死代碼已刪 — 冇 tuple 賦值 (F5)")

# F1: estimate_held_for 必須真 per-record —— 用行為測試 (唔係 source 斷言, 因為
# source 斷言捉唔到語義被改壞, 例如退回讀 aggregate。GLM 第三輪指明呢個係盲位)。
r_self = {"order_id": 1, "qty": 0.002}
r_other = {"order_id": 2, "qty": 0.005}
# (a) 只有自己一個 live → 自己嘅持倉 = 帳戶總額
h = btp.estimate_held_for(r_self, 0.002, [r_self])
check(abs(h - 0.002) < 1e-12, "只有自己一個 live → 持倉 = 帳戶總額", str(h))
# (a2) 兩個都 live 而帳戶只夠自己 → 扣減其他記錄後 = 0 (正確行為)
h = btp.estimate_held_for(r_self, 0.002, [r_self, r_other])
check(abs(h) < 1e-12, "帳戶只夠自己但其他記錄仍 live → 扣減後 0", str(h))
# (b) 帳戶只有 0.005 = 只有其他嗰筆 → 自己嘅持倉應該係 0 (唔係 0.005)
h = btp.estimate_held_for(r_self, 0.005, [r_self, r_other])
check(abs(h) < 1e-12, f"帳戶只夠其他記錄 → 自己持倉 0 (真 per-record)", str(h))
# (c) 若係假 per-record (讀 aggregate) → 上面 (b) 會回 0.005 → 呢兩條會 FAIL
h = btp.estimate_held_for(r_other, 0.007, [r_self, r_other])
check(abs(h - 0.005) < 1e-12, f"扣減其他記錄 (0.007-0.002=0.005)", str(h))
# (d) 帳戶少過其他記錄 → clamp 到 0, 唔會負數
h = btp.estimate_held_for(r_other, 0.001, [r_self, r_other])
check(h == 0.0, "帳戶不足時 clamp 到 0 (唔會負)", str(h))
# (e) acct 未知 → None (caller skip)
check(btp.estimate_held_for(r_self, None, [r_self]) is None, "帳戶未知 → None")
# (f) 兩個孤兒一有倉一冇倉 → 唔會同樣對待 (整合測試)
reset_log()
btp._log_upsert({"order_id": 61, "pattern": "A", "side": "BUY", "qty": 0.002,
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
btp._log_upsert({"order_id": 62, "pattern": "B", "side": "BUY", "qty": 0.005,
                 "status": "OCO_FAILED", "entry_fill": 80100.0})
lg = btp.load_log()
live = [r for r in lg["orders"] if btp.is_live_rec(r)]
# 帳戶只有 0.005 (只夠第二筆) → 61 應該冇倉, 62 應該有倉
changed, summ = btp.resolve_orphan_states(
    lg, held_qty=lambda r: btp.estimate_held_for(r, 0.005, live),
    dust_eps=0.0001, rebuild=_good_rebuild)
check(summ["closed_orphan"] == 1 and summ["rebuilt"] == 1,
      f"兩孤兒唔會同樣對待 (61 冇倉→CLOSED, 62 有倉→補建)", str(summ))

full = open(os.path.join(REPO, "binance_testnet_paper.py"), encoding="utf-8").read()
print("\n=== H5. GLM 第五輪: HIGH-1 / HIGH-2 / MEDIUM-3 / LOW-4 / LOW-5 / LOW-6 ===")
# HIGH-1: freeze-on-ambiguity 必須喺**生產形狀** (held_qty 係 callable) 之下都觸發。
# 之前歧義檢查只認 scalar → 生產傳 callable → 永遠唔行 = 死代碼。
reset_log()
btp._log_upsert({"order_id": 91, "pattern": "A", "side": "BUY", "qty": 0.003,
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
btp._log_upsert({"order_id": 92, "pattern": "B", "side": "BUY", "qty": 0.003,
                 "status": "OCO_FAILED", "entry_fill": 80100.0})
lg = btp.load_log()
called3 = []
changed, summ = btp.resolve_orphan_states(
    lg, held_qty=lambda r: 0.001, dust_eps=0.0001, acct_btc=0.0035,
    rebuild=lambda r: called3.append(r) or _good_rebuild(r))
check(summ.get("frozen_ambiguous") == 2,
      "HIGH-1: callable 形狀 (生產) 之下 freeze 都會觸發", str(summ))
check(not called3, "HIGH-1: 生產形狀下唔會 call rebuild", str(len(called3)))

# HIGH-1b: 生產路徑真嘅傳 acct_btc
cfull2 = open(os.path.join(REPO, "btc_auto_trade_cycle.py"), encoding="utf-8").read()
check("acct_btc=_acct_btc" in cfull2, "HIGH-1: reconcile_cycle 有傳 acct_btc")
check("HOLDING_STATUS" in cfull2, "LOW-4: 只扣持貨類 status")

# LOW-4: LIMIT_PENDING (未成交買單) 唔應該被扣
HOLD = ("ENTRY_FILLED_PENDING_EXITS", "OCO_PLACED", "FILLED_ENTRY",
        "LIMIT_FILLED", "OCO_FAILED", "FLATTENED_OCO_FAILED", "WIPED")
mine_r = {"order_id": 93, "pattern": "A", "side": "BUY", "qty": 0.002,
          "status": "OCO_FAILED", "entry_fill": 80000.0}
pending_r = {"order_id": 94, "pattern": "B", "side": "BUY", "qty": 0.005,
             "status": "LIMIT_PENDING"}
# 生產同款過濾 (_HOLDING_STATUS): LIMIT_PENDING 唔入 live_recs → 唔會被扣
live_hold = [r for r in [mine_r, pending_r]
             if btp.is_live_rec(r) and r.get("status") in HOLD]
h = btp.estimate_held_for(mine_r, 0.002, live_hold)
check(abs(h - 0.002) < 1e-12,
      "LOW-4: LIMIT_PENDING 唔會被扣 (孤兒持倉唔會低估)", f"{h} (live_hold={len(live_hold)})")
# 對照: 若錯把 LIMIT_PENDING 也計入 (舊行為) → 會低估
live_bad = [r for r in [mine_r, pending_r] if btp.is_live_rec(r)]
h_bad = btp.estimate_held_for(mine_r, 0.002, live_bad)
check(abs(h_bad) < 1e-12, "LOW-4 對照: 錯誤做法會低估到 0", str(h_bad))

# MEDIUM-3: h is None 要出聲, 唔可以靜默
reset_log()
btp._log_upsert({"order_id": 95, "pattern": "A", "side": "BUY", "qty": 0.002,
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
changed, summ = btp.resolve_orphan_states(lg, held_qty=None)
check(summ["skipped"] == 1, "MEDIUM-3: 倉位未知 → skipped", str(summ))
check(lg["orders"][0].get("needs_manual_reconcile") is True,
      "MEDIUM-3: 標 needs_manual_reconcile (唔再靜默)")
check(lg["orders"][0].get("unknown_holding_reason") is not None, "有記錄原因")
check(bool(changed), "MEDIUM-3: 有入 changed (caller 會 save + log)", str(len(changed)))

# LOW-5: _exit_cid 冇 id 要拋異常 (唔可以撞 cid)
try:
    btp._exit_cid({"pattern": "x"}, "A")
    check(False, "LOW-5: 冇 order_id 應該拋異常")
except ValueError:
    check(True, "LOW-5: 冇 order_id 拋異常 (唔會撞 cid)")
# LOW-5: tag 放頭
c = btp._exit_cid({"order_id": 999999999}, "B")
check(c.startswith("B-"), f"LOW-5: tag 放頭 ({c})")
check(len(c) <= 36, f"LOW-5: 長度 <= 36 ({len(c)})")

# LOW-6: fsync 目錄
check("os.fsync(dfd)" in full, "LOW-6: save_log 有 fsync 目錄")

# HIGH-2: duplicate-cid adopt —— 行為測試 (source 斷言捉唔到, mutation 證實)
# 模擬: openOrders 已有同 prefix 嘅 legs → build_exit_legs 應該 adopt 而唔係再落單
reset_log()
adopt_rec = {"order_id": 777, "side": "BUY", "qty": 0.001,
             "planned_stop": 79600.0, "planned_tp1": 80400.0, "planned_tp2": 80800.0,
             "atr": 200.0, "status": "OCO_FAILED"}
posted = []


def fake_signed2(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        # 只有一條已存在 leg, clientOrderId 有正確 prefix
        return [{"orderId": 9001, "clientOrderId": btp._exit_cid({"order_id": 777}, "A")}]
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999,
            "orders": [{"orderId": 1}, {"orderId": 2}]}


orig_signed = btp._signed_request
btp._signed_request = fake_signed2
try:
    out, err = btp.build_exit_legs(dict(adopt_rec), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(out.get("adopted_existing_legs") is True,
      "HIGH-2: adopt 已存在 legs (行為測試)", str(out.get("adopted_existing_legs")))
check(out.get("exit_leg_ids") == [9001], "HIGH-2: adopt 正確 leg id", str(out.get("exit_leg_ids")))
check(out.get("status") == "OCO_PLACED", "HIGH-2: adopt 後狀態 OCO_PLACED",
      str(out.get("status")))
check(not [p for p in posted if p[1].endswith("/order/oco")],
      "HIGH-2: adopt 之後冇再落 OCO (唔會重複)", str(posted))

# 對照: 冇已存在 legs → 正常落單
reset_log()
posted.clear()
adopt_rec2 = dict(adopt_rec, order_id=888)


def fake_signed3(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        return []
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999,
            "orders": [{"orderId": 1}, {"orderId": 2}]}


btp._signed_request = fake_signed3
try:
    out2, _ = btp.build_exit_legs(dict(adopt_rec2), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(not out2.get("adopted_existing_legs"), "對照: 冇存在 legs 時唔會 adopt")
check(any(p[1].endswith("/order/oco") for p in posted),
      "對照: 冇存在 legs 時正常落 OCO", str(posted))

# LOW-4: 生產 _HOLDING_STATUS 過濾 —— 行為驗證 (用生產嘅實際 tuple)
cf_hold = open(os.path.join(REPO, "btc_auto_trade_cycle.py"), encoding="utf-8").read()
names = list(btp.HOLDING_STATUS)
check(len(names) > 0, "LOW-4: 讀到 module-level HOLDING_STATUS")
if names:
    check("LIMIT_PENDING" not in names,
          f"LOW-4: _HOLDING_STATUS 唔包含 LIMIT_PENDING", str(names))
    check("OCO_FAILED" in names and "FLATTENED_OCO_FAILED" in names,
          "LOW-4: 包含 OCO_FAILED / FLATTENED_OCO_FAILED", str(names))
    # 用生產 tuple 真嘅過濾一次 (直接 import module-level HOLDING_STATUS)
    mine_r2 = {"order_id": 93, "qty": 0.002, "status": "OCO_FAILED"}
    pend_r2 = {"order_id": 94, "qty": 0.005, "status": "LIMIT_PENDING"}
    filt = [r for r in [mine_r2, pend_r2]
            if btp.is_live_rec(r) and r.get("status") in btp.HOLDING_STATUS]
    h2 = btp.estimate_held_for(mine_r2, 0.002, filt)
    check(abs(h2 - 0.002) < 1e-12,
          "LOW-4: 用生產 tuple 過濾 → LIMIT_PENDING 唔扣", str(h2))
    # 斷言使用處真嘅用咗 _HOLDING_STATUS (唔止定義存在 —— mutation 改使用處
    # 而 tuple 定義不變嘅話, 只檢查定義會捉唔到)
    check(_re2.search(r"in HOLDING_STATUS\]", cf_hold) is not None,
          "LOW-4/LOW-I: _live_recs 用 module-level HOLDING_STATUS 過濾")
    check('_HOLDING_STATUS = (' not in cf_hold,
          "LOW-I: cycle 冇再重複定義一份 tuple (避免 drift)")

print("\n=== H6. GLM 第六輪: HIGH-A / HIGH-B / MEDIUM-C/D/E / LOW-G/H/I ===")
# HIGH-A: oid 前綴碰撞唔可以 adopt 錯倉 (oid=77 vs 777)
reset_log()
posted.clear()


def fake_collide(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        # 只有 777 嘅 leg; 記錄 77 唔應該 adopt 佢
        return [{"orderId": 7001, "clientOrderId": btp._exit_cid({"order_id": 777}, "A")}]
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999, "orders": [{"orderId": 1}, {"orderId": 2}]}


r77 = {"order_id": 77, "side": "BUY", "qty": 0.001, "planned_stop": 79600.0,
       "planned_tp1": 80400.0, "planned_tp2": 80800.0, "atr": 200.0, "status": "OCO_FAILED"}
btp._signed_request = fake_collide
try:
    out77, _ = btp.build_exit_legs(dict(r77), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(not out77.get("adopted_existing_legs"),
      "HIGH-A: oid=77 唔會 adopt oid=777 嘅 leg (冇 substring 碰撞)",
      str(out77.get("adopted_existing_legs")))
check(7001 not in (out77.get("exit_leg_ids") or []),
      "HIGH-A: 77 嘅 exit_leg_ids 唔包含 7001", str(out77.get("exit_leg_ids")))

# HIGH-B: openOrders 查詢失敗要 fail-closed (唔可以照落單)
reset_log()
posted.clear()


def fake_fail(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        raise RuntimeError("network timeout")
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999, "orders": [{"orderId": 1}]}


r_hb = dict(r77, order_id=601)
btp._signed_request = fake_fail
try:
    out_hb, err_hb = btp.build_exit_legs(dict(r_hb), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(err_hb is not None and "fail-closed" in str(err_hb),
      "HIGH-B: 查詢失敗 → 報錯 (fail-closed)", str(err_hb))
check(not [p for p in posted if p[1].endswith("/order/oco")],
      "HIGH-B: 查詢失敗時冇落新 OCO", str(posted))
check(out_hb.get("needs_manual_reconcile") is True, "HIGH-B: 標 needs_manual_reconcile")

# MEDIUM-D: partial adopt (只有 B tag, 冇 stop leg) → 唔可以宣告成功
reset_log()
posted.clear()


def fake_partial(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        return [{"orderId": 8001, "clientOrderId": btp._exit_cid({"order_id": 602}, "B")}]
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999, "orders": [{"orderId": 1}]}


r_mp = dict(r77, order_id=602)
btp._signed_request = fake_partial
try:
    out_mp, _ = btp.build_exit_legs(dict(r_mp), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(not out_mp.get("adopted_existing_legs"),
      "MEDIUM-D: 只有 B tag (冇 stop leg) → 唔宣告成功")
check(out_mp.get("needs_manual_reconcile") is True, "MEDIUM-D: 標 needs_manual_reconcile")

# MEDIUM-C: h is None 唔可以每 cycle 重複 flag + 重複入 changed
reset_log()
btp._log_upsert({"order_id": 96, "pattern": "A", "side": "BUY", "qty": 0.002,
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
c1, s1_ = btp.resolve_orphan_states(lg, held_qty=None)
c2, s2_ = btp.resolve_orphan_states(lg, held_qty=None)
check(len(c1) == 1, "MEDIUM-C: 第一次會標記 + 入 changed", str(len(c1)))
check(len(c2) == 0, "MEDIUM-C: 第二次唔會再入 changed (唔 spam)", str(len(c2)))
check(lg["orders"][0].get("needs_legs") is None,
      "MEDIUM-C: 唔用誤導性 needs_legs (我唔知有冇倉)")

# MEDIUM-E: dirname 空字串要用 "."
check('os.path.dirname(LOG_PATH) or "."' in full, "MEDIUM-E: dirname 空 → 用 '.'")
# LOW-G: 冇再用手法兼容分支
check("isinstance(held_qty, (int, float))" not in full,
      "LOW-G: 刪走 scalar→acct_btc 隱式兼容分支")
# LOW-I: tuple 抽去 module level, cycle 唔重複定義
check("HOLDING_STATUS" in full and "HOLDING_STATUS = (" in full,
      "LOW-I: HOLDING_STATUS 定義喺 module level")
check("_HOLDING_STATUS = (" not in cfull2, "LOW-I: cycle 冇重複定義")
# LOW-H: adopt-miss 要 observable
check("adopt_error" in full, "LOW-H: adopt 查詢失敗有記錄 (observable)")

print("\n=== H0. 實測揪出: FLATTENED_LOW_FILL_RR 唔應該佔 cap ===")
# 08-20 靜江實跑時, F4 warning 報「未見過嘅 status: FLATTENED_LOW_FILL_RR x3」。
# 查實: 該狀態 = 成交後 RR < MIN_RR_EXEC → 即刻市價平倉, log 有 flatten_ok=True
# → 真嘅平咗 → 必須當 done。之前 fail-closed 將佢當 live → 3 筆殭屍永久佔 cap。
check(btp.is_live_rec({"status": "FLATTENED_LOW_FILL_RR"}) is False,
      "FLATTENED_LOW_FILL_RR 唔算 live (唔佔 cap)")
check(btp.is_live_rec({"status": "FLATTENED_OCO_FAILED"}) is True,
      "FLATTENED_OCO_FAILED 仍然算 live (flatten 未確認成功)")
check("FLATTENED_LOW_FILL_RR" in btp.DONE_STATUS,
      "FLATTENED_LOW_FILL_RR 喺 DONE_STATUS")
# 該狀態唔應該再觸發「未見過」warning
_known = set(btp.DONE_STATUS) | set(btp.ORPHAN_STATUS) | {"LIMIT_PENDING", "OCO_PLACED"}
check("FLATTENED_LOW_FILL_RR" in _known,
      "F4 warning 唔會再報 FLATTENED_LOW_FILL_RR (已知)")

print("\n=== H7. GLM 第七輪: FINDING 1/2/3/4/5/6 ===")
# FINDING 1: partial adopt 必須 fail-closed —— 唔可以 fall through 落新 OCO
reset_log()
posted.clear()


def fake_partial2(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        return [{"orderId": 8001, "clientOrderId": btp._exit_cid({"order_id": 603}, "B")}]
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999, "orders": [{"orderId": 1}]}


r_f1 = dict(r77, order_id=603)
btp._signed_request = fake_partial2
try:
    out_f1, err_f1 = btp.build_exit_legs(dict(r_f1), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(not [p for p in posted if p[1].endswith("/order/oco")],
      "FINDING 1: partial adopt 時**冇落新 OCO** (fail-closed)", str(posted))
check(err_f1 is not None, "FINDING 1: partial 會回報錯誤 (唔係靜默 fall through)", str(err_f1))
check(out_f1.get("status") != "OCO_PLACED",
      "FINDING 1: partial 唔會標 OCO_PLACED", str(out_f1.get("status")))

# FINDING 3: empty-oid guard 恢復 (兩筆冇 id 唔可以 cross-adopt)
reset_log()
posted.clear()


def fake_any_leg(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        return [{"orderId": 9001, "clientOrderId": "A-EXIT-None"}]
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999, "orders": [{"orderId": 1}]}


r_f3 = {"side": "BUY", "qty": 0.001, "planned_stop": 79600.0, "planned_tp1": 80400.0,
        "atr": 200.0, "status": "OCO_FAILED"}   # 冇 order_id / oco_id
btp._signed_request = fake_any_leg
try:
    out_f3, err_f3 = btp.build_exit_legs(dict(r_f3), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(not out_f3.get("adopted_existing_legs"),
      "FINDING 3: 冇 order_id 唔會 adopt (唔會 cross-adopt)", str(out_f3.get("adopted_existing_legs")))
check("order_id" in str(out_f3.get("adopt_error") or ""),
      "FINDING 3: 錯誤訊息指名 order_id 缺失", str(out_f3.get("adopt_error")))
# FINDING 3 係**雙重保護**: _adopt_existing 有 early guard, _exit_cid 內部亦 raise。
# 所以移除任一層行為都不變 (mutation 捉唔到) —— 呢個係防禦冗餘, 唔係缺口。
# 明確斷言兩層都存在, 免得日後有人以為只靠一層而刪走另一層。
check("唔可以靠 cid 匹配 adopt" in full, "FINDING 3: _adopt_existing 有 early guard")
check("唔應該自動落 exit legs" in full, "FINDING 3: _exit_cid 亦有 raise (第二層)")
check(out_f3.get("adopt_fail_count") is None,
      "NEW-1: 資料缺失唔當查詢失敗 (counter 唔加)",
      str(out_f3.get("adopt_fail_count")))
check("資料缺失" in str(out_f3.get("adopt_error")),
      "NEW-1: 錯誤分類為資料缺失", str(out_f3.get("adopt_error")))

# FINDING 4 + LOW-H: adopt_error 要真嘅寫入 log + 含 traceback 尾幾行
reset_log()


def fake_net_fail(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        raise RuntimeError("network timeout")
    return {}


r_f4 = dict(r77, order_id=604)
btp._signed_request = fake_net_fail
try:
    out_f4, err_f4 = btp.build_exit_legs(dict(r_f4), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
saved = [o for o in btp.load_log()["orders"] if o.get("order_id") == 604]
check(len(saved) == 1, "LOW-H: 錯誤有寫入 log (行為斷言, 唔係 grep)")
check(saved and saved[0].get("adopt_error") is not None,
      "FINDING 4: log 內有 adopt_error", str(saved[0].get("adopt_error") if saved else None))
check(saved and "RuntimeError" in str(saved[0].get("adopt_error")),
      "FINDING 4: adopt_error 含異常類型 (分得出網絡/代碼)")
check(saved and "traceback" not in str(saved[0].get("adopt_error")).lower(),
      "FINDING 4: 放 traceback 尾幾行而非全文")
check(saved and saved[0].get("adopt_fail_count") == 1, "FINDING 2b: 有 fail count")

# FINDING 5a: adopt 成功要清走 transient flags
reset_log()
posted.clear()


def fake_ok(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        return [{"orderId": 9101, "clientOrderId": btp._exit_cid({"order_id": 605}, "A")}]
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999, "orders": [{"orderId": 1}]}


r_f5 = dict(r77, order_id=605, needs_manual_reconcile=True,
            adopt_error="舊錯誤", adopt_fail_count=3)
btp._signed_request = fake_ok
try:
    out_f5, _ = btp.build_exit_legs(dict(r_f5), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(out_f5.get("adopted_existing_legs") is True, "FINDING 5a: 成功 adopt")
check(out_f5.get("needs_manual_reconcile") is None,
      "FINDING 5a: 成功後清走 needs_manual_reconcile (transient)")
check(out_f5.get("adopt_error") is None, "FINDING 5a: 成功後清走 adopt_error")
check(out_f5.get("adopt_fail_count") is None, "FINDING 5a: 成功後清走 adopt_fail_count")

# FINDING 5b: orphan 恢復正常時清走 unknown_holding_reason
reset_log()
btp._log_upsert({"order_id": 97, "pattern": "A", "side": "BUY", "qty": 0.002,
                 "status": "OCO_FAILED", "entry_fill": 80000.0,
                 "needs_manual_reconcile": True, "unknown_holding_reason": "舊原因"})
lg = btp.load_log()
btp.resolve_orphan_states(lg, held_qty=0.0, dust_eps=0.0001)
check(lg["orders"][0].get("unknown_holding_reason") is None,
      "FINDING 5b: 恢復正常後清走 unknown_holding_reason")
check(lg["orders"][0].get("needs_manual_reconcile") is None,
      "FINDING 5b: 清走 needs_manual_reconcile")

# NEW-2 (第八輪): pop 必須真嘅 persist (唔止 in-memory) —— 用 load_log 斷言,
# 覆蓋「持倉正常 (非 CLOSED) 且 rebuild 成功」嗰條路徑。
reset_log()
btp.save_log({"orders": [{"order_id": 201, "pattern": "A", "side": "BUY", "qty": 0.002,
                          "status": "OCO_FAILED", "entry_fill": 80000.0,
                          "needs_manual_reconcile": True,
                          "unknown_holding_reason": "舊原因"}]})
lg = btp.load_log()
_ch, _sm = btp.resolve_orphan_states(lg, held_qty=0.002, dust_eps=0.0001,
                                     acct_btc=0.002, rebuild=_good_rebuild)
btp.save_log(lg)
_disk = [o for o in btp.load_log()["orders"] if o.get("order_id") == 201][0]
check(_disk.get("needs_manual_reconcile") is None,
      "NEW-2: pop 真嘅 persist 落 disk (持倉正常路徑)", str(_disk.get("needs_manual_reconcile")))
check(_disk.get("unknown_holding_reason") is None,
      "NEW-2: unknown_holding_reason 亦 persist 清走",
      str(_disk.get("unknown_holding_reason")))
check(len(_ch) >= 1, "NEW-2: 該記錄有入 changed (所以 caller 會 save)", str(len(_ch)))

# NEW-4: adopt miss → 落新 OCO 成功 → transient flags 清走
reset_log()
posted.clear()


def fake_miss(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        return []                       # 冇已存在 legs
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999, "orders": [{"orderId": 1}, {"orderId": 2}]}


r_n4 = dict(r77, order_id=606, needs_manual_reconcile=True, adopt_error="舊錯誤",
            adopt_fail_count=2)
btp._signed_request = fake_miss
try:
    out_n4, _e = btp.build_exit_legs(dict(r_n4), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(out_n4.get("status") == "OCO_PLACED", "NEW-4: 落新 OCO 成功",
      str(out_n4.get("status")))
check(out_n4.get("needs_manual_reconcile") is None,
      "NEW-4: 新 OCO 成功清走 needs_manual_reconcile", str(out_n4.get("needs_manual_reconcile")))
check(out_n4.get("adopt_error") is None, "NEW-4: 清走 adopt_error")
check(out_n4.get("adopt_fail_count") is None, "NEW-4: 清走 adopt_fail_count")

# NEW-1: ValueError 走「資料缺失」路徑, 唔混入「查詢失敗」counter
reset_log()
r_n1 = {"side": "BUY", "qty": 0.001, "planned_stop": 79600.0, "planned_tp1": 80400.0,
        "atr": 200.0, "status": "OCO_FAILED"}
btp._signed_request = fake_any_leg
try:
    out_n1, err_n1 = btp.build_exit_legs(dict(r_n1), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check("資料缺失" in str(out_n1.get("adopt_error")),
      "NEW-1: ValueError 標「資料缺失」(唔混入查詢失敗)", str(out_n1.get("adopt_error")))
check(out_n1.get("adopt_fail_count") is None,
      "NEW-1: 資料缺失唔 increment 查詢失敗 counter", str(out_n1.get("adopt_fail_count")))

# NEW-3: partial 要入 alert 範圍
check("partial_count" in full, "NEW-3: partial 有獨立 counter (alert 唔會盲)")
reset_log()
def fake_partial_n3(method, path, params, key, secret):
    if method == "GET" and path.endswith("/openOrders"):
        return [{"orderId": 8301, "clientOrderId": btp._exit_cid({"order_id": 607}, "B")}]
    posted.append((method, path))
    return {"orderId": 999, "orderListId": 999, "orders": [{"orderId": 1}]}


r_n3 = dict(r77, order_id=607)
btp._signed_request = fake_partial_n3
try:
    out_n3, _e3 = btp.build_exit_legs(dict(r_n3), "k", "s", 0.00001)
finally:
    btp._signed_request = orig_signed
check(out_n3.get("partial_count") == 1, "NEW-3: partial 會計數", str(out_n3.get("partial_count")))
check(out_n3.get("adopt_error") is not None, "NEW-3: partial 有 adopt_error 摘要")

# FINDING 2a: 下游冇 skip flagged 記錄 (否則 fail-closed 會變永久)
cf2a = open(os.path.join(REPO, "btc_auto_trade_cycle.py"), encoding="utf-8").read()
check("needs_manual_reconcile" not in cf2a,
      "FINDING 2a: 下游冇用 needs_manual_reconcile 做 skip 過濾 (唔會永久封鎖)")

# FINDING 6: listClientOrderId 命中時明確 lookup, 唔靠迭代
check("elif lid in wanted:" in full and "hit_tag = wanted[lid]" in full,
      "FINDING 6: lid 命中用明確 lookup")

# F4: 未知 status warning
cfull = open(os.path.join(REPO, "btc_auto_trade_cycle.py"), encoding="utf-8").read()
check("未見過嘅 status" in cfull, "F4: 未知 status 有 warning log")

print("\n=== H4. GLM 第四輪: BLOCKER 1 / HIGH / MEDIUM / LOW ===")
# HIGH: qty 缺失唔可以估成「持有全帳戶」
h = btp.estimate_held_for({"order_id": 99}, 0.002, [{"order_id": 99}])
check(h is None, f"qty 缺失 → None (唔會估成全帳戶) [GLM 第四輪 HIGH]", str(h))
h = btp.estimate_held_for({"order_id": 99, "qty": 0}, 0.002, [])
check(h is None, "qty=0 → None (唔會估成全帳戶)", str(h))
h = btp.estimate_held_for({"order_id": 99, "qty": None}, 0.002, [])
check(h is None, "qty=None → None", str(h))

# MEDIUM: 模糊歸因 (孤兒 qty 總和 > 帳戶) → 整批 freeze, 唔逐筆估
reset_log()
btp._log_upsert({"order_id": 71, "pattern": "A", "side": "BUY", "qty": 0.003,
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
btp._log_upsert({"order_id": 72, "pattern": "B", "side": "BUY", "qty": 0.003,
                 "status": "OCO_FAILED", "entry_fill": 80100.0})
lg = btp.load_log()
called2 = []
changed, summ = btp.resolve_orphan_states(
    lg, held_qty=0.0035, dust_eps=0.0001, acct_btc=0.0035,
    rebuild=lambda r: called2.append(r) or _good_rebuild(r))
check(summ.get("frozen_ambiguous") == 2, "模糊歸因 → 整批 freeze (2 筆)", str(summ))
check(not called2, "模糊歸因時唔會 call rebuild (唔賭)", str(len(called2)))
check(all(o.get("needs_manual_reconcile") for o in lg["orders"]),
      "全部標 needs_manual_reconcile")
check(all(o.get("ambiguous_reason") for o in lg["orders"]), "有記錄模糊原因")

# MEDIUM 對照: 歸因清晰 (總和 <= 帳戶) → 正常處理
reset_log()
btp._log_upsert({"order_id": 73, "pattern": "A", "side": "BUY", "qty": 0.002,
                 "status": "OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
changed, summ = btp.resolve_orphan_states(lg, held_qty=0.002, dust_eps=0.0001,
                                          acct_btc=0.002, rebuild=_good_rebuild)
check(summ.get("frozen_ambiguous", 0) == 0, "歸因清晰時唔會 freeze", str(summ))
check(summ["rebuilt"] == 1, "歸因清晰時正常補建", str(summ))

# LOW-1: rebuild=None 時結果不明嘅記錄仍要標 needs_manual_reconcile
reset_log()
btp._log_upsert({"order_id": 81, "pattern": "A", "side": "BUY", "qty": 0.001,
                 "status": "FLATTENED_OCO_FAILED", "entry_fill": 80000.0})
lg = btp.load_log()
changed, summ = btp.resolve_orphan_states(lg, held_qty=0.001, rebuild=None)
check(lg["orders"][0].get("needs_manual_reconcile") is True,
      "rebuild=None 時仍標 needs_manual_reconcile [LOW-1]")

# BLOCKER 1: exit legs 落單要有 deterministic clientOrderId (冪等)
full = open(os.path.join(REPO, "binance_testnet_paper.py"), encoding="utf-8").read()
check("def _exit_cid" in full, "有 _exit_cid 函數 (BLOCKER 1)")
check(full.count("newClientOrderId") >= 2, "STOP_LOSS_LIMIT 有 newClientOrderId",
      str(full.count("newClientOrderId")))
check(full.count("listClientOrderId") >= 2, "OCO 有 listClientOrderId",
      str(full.count("listClientOrderId")))
# 決定性: 同一 rec 叫兩次要一樣
c1 = btp._exit_cid({"order_id": 555}, "A")
c2 = btp._exit_cid({"order_id": 555}, "A")
c3 = btp._exit_cid({"order_id": 555}, "B")
check(c1 == c2, f"同 input → 同 cid ({c1})")
check(c1 != c3, f"唔同 tag → 唔同 cid ({c1} vs {c3})")
check(len(c1) <= 36, f"cid 長度 <= 36 ({len(c1)})")
# build_exit_legs 自己改 status + persist (BLOCKER 1 第 2 點)
check('rec["status"] = "OCO_PLACED"' in full,
      "build_exit_legs 自己改 status = OCO_PLACED")
_bel = full.split("def build_exit_legs")[1].split("\ndef ")[0]   # 整個函數體
check("_log_upsert(rec)" in _bel,
      "build_exit_legs 自己 persist (唔靠 caller)", str(len(_bel)))
check(cfull.count('_signed_request("GET", "/api/v3/account"') == 1,
      "F1: 帳戶餘額只查一次 (rate limit 安全)",
      str(cfull.count('_signed_request("GET", "/api/v3/account"')))
# 呢個係真正出 08-29 事嘅路徑: btc_auto_trade_cycle.reconcile_cycle 處理 LIMIT 成交。
# 舊 code: for loop 行完才 save_log → 每個成交 build_exit_legs (1-2 秒) 期間 log 冇記錄。
import re as _re
CYCLE = os.path.join(REPO, "btc_auto_trade_cycle.py")
csrc = open(CYCLE, encoding="utf-8").read()
# 斷言 fix 存在: 成交偵測段 (status=LIMIT_FILLED) 之後、build_exit_legs 之前有 save_log
m = _re.search(r'rec\["status"\]\s*=\s*"ENTRY_FILLED_PENDING_EXITS"\s*\n\s*save_log\(log_d\)',
               csrc)
check(m is not None,
      "reconcile 成交段有『成交即寫 log』(ENTRY_FILLED_PENDING_EXITS + save_log)")
if m:
    after = csrc[m.end():m.end() + 300]
    build_idx = after.find("build_exit_legs")
    save_idx = after.find("save_log")
    check(build_idx >= 0, "save_log 之後仍有 build_exit_legs (流程完整)")
    check(save_idx == -1 or save_idx > build_idx,
          f"下一次 save_log 喺 build_exit_legs 之後 (即係唔會早過建 OCO)",
          f"save@{save_idx} build@{build_idx}")
check("save_log(log_d)" in csrc,
      "檔案有 save_log(log_d) (確認變數名正確)")
# 反向: 確認原本嘅「loop 完才寫」已被改變 —— 統計 save_log 出現次數要 >= 2
n_save = len(_re.findall(r"save_log\(log_d\)", csrc))
check(n_save >= 2,
      f"save_log(log_d) 出現 >= 2 次 (loop 中途 + loop 之後)", str(n_save))

print("\n" + "=" * 70)
print(f"總共 {N} 個斷言, {len(FAILS)} 個 FAIL")
if FAILS:
    for f in FAILS:
        print("  ❌", f)
    sys.exit(1)
print("✅ 全部通過")
