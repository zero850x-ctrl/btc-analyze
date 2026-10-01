#!/usr/bin/env python3
"""冒煙: 用**真嘅 production 停用標記** + worktree 版 code 行一次 cycle.main()。

唔會打 API、唔會落單、唔會寫 production log —— reconcile_cycle / sh 全部 monkeypatch。
目的係證明「真 marker 檔 → 真 gate 生效」唔係 فقط 測試環境先得。
"""
import importlib.util
import io
import os
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fake = type(sys)("binance_testnet_paper")
fake._load_keys = lambda: ("k", "s")
sys.modules["binance_testnet_paper"] = fake


def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


pause = load("btc_pause", "btc_pause.py")
mod = load("btc_auto_trade_cycle", "btc_auto_trade_cycle.py")

print(f"  marker (真預設路徑) : {pause.MARKER_PATH}")
print(f"  標記檔存在          : {os.path.exists(pause.MARKER_PATH)}")
print(f"  btcpause.check()    : {pause.check()}")
print()

calls = {"reconcile": 0, "sh": []}
mod.reconcile_cycle = lambda k, s: (calls.__setitem__("reconcile", calls["reconcile"] + 1), ([], []))[1]
mod.sh = lambda cmd: (calls["sh"].append(cmd), "Trade Setups: 0")[1]
mod.HEARTBEAT = "/tmp/smoke_pause_heartbeat.txt"
mod.HISTORY = "/tmp/smoke_pause_closed.json"
mod.LOG_PATH = "/tmp/smoke_pause_orders.json"

buf = io.StringIO()
with redirect_stdout(buf):
    mod.main()
out = buf.getvalue()

print("  ── cycle.main() 實際輸出 ──")
for line in out.strip().splitlines():
    print(f"    {line}")
print()
print(f"  reconcile 呼叫次數 : {calls['reconcile']}  (停用期間應該 = 1)")
print(f"  sh() 呼叫          : {calls['sh']}  (停用期間應該 = [] 空)")
print()
ok_gate = calls["reconcile"] == 1 and not calls["sh"] and pause.PAUSED_EMOJI in out
print(f"  {'✅ 硬閘生效' if ok_gate else '❌ 硬閘冇生效'} —— 真 marker 下 reconcile 有跑、落單完全冇跑")
sys.exit(0 if ok_gate else 1)
