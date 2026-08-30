#!/usr/bin/env python3
"""pen_test_btc.py — paper trade 機制 pen test

注入測試場景驗證防護機制 (用完即刪, 唔留痕蹟喺正式 log):
  T1. 假 LIVE 單 SL 被穿 → check_outcomes 應該正確 close 計 R
  T2. Coinbase fail-closed: Coinbase API 冇回應 → UNVERIFIED 保持 LIVE 唔計 R
  T3. 同 pattern+direction dedup: 重複 seed 被擋
  T4. opposite-direction lock: 有 BUY LIVE 時 SELL 被擋
  T5. daily loss limit: 當日 -2R 封盤
  T6. log 隔離: BTC log 唔會污染黃金 log
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from unittest import mock

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

LOG_BAK = None
RESULTS = []

def result(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}  {detail}")

def fake_bars(n=120, start_px=77000.0, drift=0.0, datetime_str="2026-08-29T09:00:00Z"):
    import pandas as pd
    rows = []
    px = start_px
    for i in range(n):
        px = px + drift
        rows.append({"datetime": datetime_str, "open": px - 10, "high": px + 30,
                     "low": px - 30, "close": px, "volume": 1000})
    return pd.DataFrame(rows)

print("=== T1: 假單 SL 穿越觸發 close ===")
import paper_trade_btc as ptb

# 備份正式 log — pen test 用 temp log, 完成後還原
orig_log_path = ptb.LOG_PATH
ptb.LOG_PATH = os.path.join(tempfile.mkdtemp(), "pen_test_log.json")
try:
    # T1: 注入 LIVE SELL 單, SL 78000, 假 bars 暴升穿 SL
    ptb.save_log({"trades": [], "history": []})
    fake_trade = {
        "id": "btc-pen-001", "seeded_date": "2026-08-29",
        "seeded_time": "2026-08-29T08:00:00Z", "status": "LIVE",
        "direction": "SELL", "pattern": "PEN_TEST", "entry": 77000.0,
        "stop_loss": 77200.0, "tp1": 76500.0, "tp2": 76000.0,
        "risk_amount": 200.0, "atr": 192.0,
    }
    log = ptb.load_log(); log["trades"].append(fake_trade); ptb.save_log(log)

    bars = fake_bars(60, start_px=77100.0, drift=5.0)   # 暴升穿 SL 77200
    data = {"price": 77400.0, "atr": 192.0}
    with mock.patch.object(ptb, "_fetch_m30_btc", return_value=(bars, "tv")), \
         mock.patch.object(ptb, "_coinbase_spot", return_value=77400.0):
        ptb.check_outcomes(data)
    log = ptb.load_log()
    closed = [t for t in log["history"] if t["id"] == "btc-pen-001"]
    ok = len(closed) == 1 and closed[0]["result"] == "SL" and closed[0]["pnl_r"] < 0
    result("T1 SL 穿越 close", ok,
           f"result={closed[0]['result'] if closed else '?'} pnl_r={closed[0].get('pnl_r') if closed else '?'}")

    # T2: Coinbase fail-closed — mock Coinbase 冇回應
    ptb.save_log({"trades": [], "history": []})
    log = ptb.load_log(); log["trades"].append(dict(fake_trade)); ptb.save_log(log)
    with mock.patch.object(ptb, "_fetch_m30_btc", return_value=(bars, "tv")), \
         mock.patch.object(ptb, "_coinbase_spot", return_value=None):
        ptb.check_outcomes(data)
    log = ptb.load_log()
    still = [t for t in log["trades"] if t["id"] == "btc-pen-001"]
    ok = len(still) == 1 and still[0].get("last_unverified") is not None
    result("T2 Coinbase fail-closed → UNVERIFIED 保持 LIVE", ok)

    # T3: dedup — 同 pattern 重複 seed 被擋
    ptb.save_log({"trades": [], "history": []})
    log = ptb.load_log(); log["trades"].append(dict(fake_trade)); ptb.save_log(log)
    setup = {"btc_side": "SELL", "pattern": "PEN_TEST", "verified": True,
             "btc_entry": 77000.0, "btc_stop": 77200.0, "btc_tp1": 76500.0, "btc_tp2": 76000.0}
    with mock.patch.object(ptb, "_coinbase_spot", return_value=77400.0):
        ptb.seed_trades({"price": 77100.0, "atr": 192.0}, [setup])
    log = ptb.load_log()
    n = sum(1 for t in log["trades"] if t["pattern"] == "PEN_TEST")
    result("T3 同 pattern dedup", n == 1, f"LIVE count={n}")

    # T4: opposite-direction lock
    ptb.save_log({"trades": [], "history": []})
    log = ptb.load_log()
    log["trades"].append(dict(fake_trade))  # SELL LIVE
    ptb.save_log(log)
    buy_setup = {"btc_side": "BUY", "pattern": "PEN_TEST_BUY", "verified": True,
                 "btc_entry": 77200.0, "btc_stop": 77000.0, "btc_tp1": 77700.0, "btc_tp2": 78200.0}
    with mock.patch.object(ptb, "_coinbase_spot", return_value=77400.0):
        ptb.seed_trades({"price": 77100.0, "atr": 192.0}, [buy_setup])
    log = ptb.load_log()
    n_buy = sum(1 for t in log["trades"] if t["direction"] == "BUY")
    result("T4 opposite-direction lock", n_buy == 0, f"BUY count={n_buy}")

    # T5: daily loss limit
    ptb.save_log({"trades": [], "history": []})
    log = ptb.load_log()
    closed_trade = dict(fake_trade)
    closed_trade.update({"status": "CLOSED", "result": "SL", "pnl_r": -2.5,
                         "closed_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    log["history"].append(closed_trade)
    ptb.save_log(log)
    with mock.patch.object(ptb, "_coinbase_spot", return_value=77400.0):
        seeded = ptb.seed_trades({"price": 77100.0, "atr": 192.0}, [setup])
    result("T5 daily loss limit -2R 封盤", seeded is False)

    # T6: 黃金 log 隔離檢查
    gold_log = os.path.expanduser("~/.hermes/reports/paper_trade_log.json")
    btc_log = os.path.expanduser("~/.hermes/reports/paper_trade_log_btc.json")
    with open(gold_log) as f:
        gold = json.load(f)
    ids = [t.get("id", "") for t in gold.get("trades", []) + gold.get("history", [])]
    ok = not any(str(i).startswith("btc-") for i in ids)
    result("T6 BTC/黃金 log 隔離 (黃金 log 冇 btc- 單)", ok)
finally:
    # 清理 pen test log, 還原路徑
    shutil.rmtree(os.path.dirname(ptb.LOG_PATH), ignore_errors=True)
    ptb.LOG_PATH = orig_log_path

print()
failed = [r for r in RESULTS if not r[1]]
print(f"{'='*46}")
print(f"PEN TEST: {len(RESULTS)-len(failed)}/{len(RESULTS)} PASSED" + (f"  ❌ {len(failed)} failed" if failed else "  🎉"))
sys.exit(1 if failed else 0)
