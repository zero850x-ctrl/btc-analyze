#!/usr/bin/env python3
"""test_btc_pause_gate.py — 「主系統停用」硬閘測試

驗嘅嘢: `btc_pause.py` 嘅判斷邏輯 + `btc_auto_trade_cycle.main()` **真係**
跟住個判斷跳過落單 (行為測試, 唔係 source-grep)。

Negative control (必做, 見 skill lesson #38):
    REPO_DIR=/tmp/pause_prefix python3 test_btc_pause_gate.py
`/tmp/pause_prefix` 放「未修版」嘅 btc_auto_trade_cycle.py + btc_pause.py →
閘嗰幾條斷言**必須 FAIL**, 否則證明唔到測試有效。

⚠️ 測試完全唔會掂真 repo / 真 API / 真 log:
   - `reconcile_cycle` / `sh` / `_load_keys` 全部 monkeypatch
   - `HEARTBEAT` / `HISTORY` / `LOG_PATH` 全部 redirect 去 tempdir
   - `binance_testnet_paper` 係注入嘅假 module
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

REPO_DIR = os.environ.get("REPO_DIR") or os.path.dirname(os.path.abspath(__file__))

PASS = FAIL = 0
FAILURES = []


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  ❌ {name}")


class TradeAttempted(Exception):
    """唔應該出現嘅落單路徑被觸發 (needed by negative control narration)。"""


def load(name, filename):
    path = os.path.join(REPO_DIR, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fresh_cycle(tmp):
    """重新 load btc_auto_trade_cycle (乾淨 module state) + 全面隔離副作用。

    回 (mod, events) — events 記低所有會產生副作用嘅呼叫。
    """
    # 假 binance_testnet_paper: 唔可以真讀 credentials / 真打 API
    fake = type(sys)("binance_testnet_paper")
    fake._load_keys = lambda: ("k", "s")
    sys.modules["binance_testnet_paper"] = fake

    for k in ("btc_auto_trade_cycle", "btc_pause"):
        sys.modules.pop(k, None)
    pause = load("btc_pause", "btc_pause.py")
    mod = load("btc_auto_trade_cycle", "btc_auto_trade_cycle.py")

    events = {"reconcile": 0, "sh": [], "trade": 0}
    mod.REPO = tmp
    mod.HEARTBEAT = os.path.join(tmp, "heartbeat.txt")
    mod.HISTORY = os.path.join(tmp, "closed.json")
    mod.LOG_PATH = os.path.join(tmp, "orders.json")

    def fake_reconcile(key, secret):
        events["reconcile"] += 1
        return [], []

    def fake_sh(cmd):
        events["sh"].append(cmd)
        if "binance_testnet_paper.py" in cmd:
            events["trade"] += 1
            return "📌 落單檢查完成"
        if "btc_engine.py" in cmd:
            return "Trade Setups: 0"
        return ""

    mod.reconcile_cycle = fake_reconcile
    mod.sh = fake_sh
    return mod, pause, events


def run_cycle(mod):
    """跑 mod.main(), 回 (exception, stdout)。"""
    buf = io.StringIO()
    exc = None
    try:
        with redirect_stdout(buf):
            mod.main()
    except Exception as e:                      # noqa: BLE001 - 測試要睇到任何爆
        exc = e
    return exc, buf.getvalue()


# ══════════════════════════════════════════════════════════════════
print("=" * 74)
print(f"測試 REPO_DIR = {REPO_DIR}")
print("=" * 74)

TMP = tempfile.mkdtemp(prefix="pausetest_")
MARKER = os.path.join(TMP, "paused.txt")
ENOTDIR = os.path.join(TMP, "afile", "sub", "marker")   # 父係檔案 → OSError

print("\n[1] btc_pause.check() 三個狀態")
_, pause, _ = fresh_cycle(TMP)
pause.MARKER_PATH = MARKER

r = pause.check()
check("標記唔存在 → 唔停 (False,'',True)", r == (False, "", True))

with open(MARKER, "w") as f:
    f.write("BTC 主系統 — 正式標記停用\n第二行唔應該出現喺 reason\n")
r = pause.check()
check("標記載明停用 → paused=True", r[0] is True)
check("標記載明停用 → certain=True", r[2] is True)
check("reason 帶停用 emoji", pause.PAUSED_EMOJI in r[1])
check("reason 帶標記 header (有上下文)", "正式標記停用" in r[1])
check("reason 冇夾埋第二行", "第二行" not in r[1])

open(os.path.join(TMP, "afile"), "w").write("x")
r = pause.check(ENOTDIR)
check("讀唔到標記 (OSError) → fail-safe 當停用", r[0] is True)
check("讀唔到標記 → certain=False (要出聲)", r[2] is False)
check("讀唔到標記 → reason 用 ⚠️ 而唔係 ⏸️", pause.UNCERTAIN_EMOJI in r[1])

check("is_paused() 對應 check()[0]", pause.is_paused(MARKER) is True)
check("CLI: 已停用 → exit 10", pause.main() == 10)
pause.MARKER_PATH = os.path.join(TMP, "nope.txt")
check("CLI: 未停用 → exit 0", pause.main() == 0)

print("\n[2] 靜默/出聲 設計不變式 (⏸️ 唔可以落 NOTABLE_KEYS)")
cron = load("btc_weekend_cron_test", os.path.join(REPO_DIR, "cron", "btc_weekend_cron.py"))
check("⏸️ 唔喺 NOTABLE_KEYS (常規停用 = 靜默, 唔會 spam TG)",
      pause.PAUSED_EMOJI not in cron.NOTABLE_KEYS)
check("⚠️ 喺 NOTABLE_KEYS (唔確定 = 一定要出聲)",
      pause.UNCERTAIN_EMOJI in cron.NOTABLE_KEYS)

print("\n[3] 標記路徑 single source (防漂移)")
report = load("btc_dual_report_test", os.path.join(REPO_DIR, "cron", "btc_dual_report.py"))
check("btc_dual_report.PAUSED_MARKER == btc_pause 預設路徑",
      os.path.expanduser(report.PAUSED_MARKER) == os.path.expanduser(pause._DEFAULT_MARKER))

print("\n[4] 行為: 停用期間 — reconcile 照跑, 落單跳過")
mod, pause, ev = fresh_cycle(TMP)
sys.modules["btc_pause"].MARKER_PATH = MARKER       # 標記存在
exc, out = run_cycle(mod)
check("冇爆 exception", exc is None)
check("reconcile 有跑 (現有倉仍然有人管)", ev["reconcile"] == 1)
check("冇觸發落單 (binance_testnet_paper.py)", ev["trade"] == 0)
check("冇跑引擎掃描 (btc_engine.py)", not any("btc_engine" in c for c in ev["sh"]))
check("有出停用訊息", pause.PAUSED_EMOJI in out)
check("停用訊息講明 reconcile 照跑", "reconcile 照跑" in out)

print("\n[5] 行為: 冇標記 — 一切照舊 (唔可以擋多咗)")
sys.modules["btc_pause"].MARKER_PATH = os.path.join(TMP, "nope.txt")
r0, t0, sh0 = ev["reconcile"], ev["trade"], len(ev["sh"])
exc, out = run_cycle(mod)
check("冇爆 exception", exc is None)
check("reconcile 有跑", ev["reconcile"] == r0 + 1)
check("有跑引擎掃描", any("btc_engine" in c for c in ev["sh"][sh0:]))
check("有觸發落單", ev["trade"] == t0 + 1)

print("\n[6] 行為: 標記讀唔到 (唔確定) — fail-safe 連 reconcile 都唔跑")
sys.modules["btc_pause"].MARKER_PATH = ENOTDIR
r1, t1, sh1 = ev["reconcile"], ev["trade"], len(ev["sh"])
exc, out = run_cycle(mod)
check("冇爆 exception", exc is None)
check("reconcile **冇**跑 (狀態不明 → 乜都唔做)", ev["reconcile"] == r1)
check("落單次數冇增加", ev["trade"] == t1)
check("完全冇 call 過 sh (唔好連引擎都跑)", len(ev["sh"]) == sh1)
check("有出聲 (⚠️ 唔確定)", pause.UNCERTAIN_EMOJI in out)

print("\n[7] 報告要真驗閘裝好, 唔可以見 marker 就講「已封住」")
# 先將 MARKER_PATH 還原做「存在嘅標記」—— section [6] 留咗個壞路徑落 module
sys.modules["btc_pause"].MARKER_PATH = MARKER
report = load("btc_dual_report_test2", os.path.join(REPO_DIR, "cron", "btc_dual_report.py"))
report.BTC_REPO = REPO_DIR
if not hasattr(report, "_pause_gate_status"):
    # 未修版: 報告只有一句寫死嘅「落單路徑仍然存在」, 冇真驗證 → 全部 FAIL
    check("報告有 _pause_gate_status() (真驗閘裝好)", False)
    check("報告唔再假稱「落單路徑仍然存在」", False)
    check("btc_pause 載唔到 → 報告要講未生效", False)
else:
    enforced, msg = report._pause_gate_status()
    check("報告有 _pause_gate_status() (真驗閘裝好)", True)
    check("閘裝好 → enforced=True", enforced is True)
    check("訊息講明落單路徑已封", "已硬閘封住" in msg)
    check("訊息唔再講「落單路徑仍然存在」", "仍然存在" not in msg)

    # 反向: 令 btc_pause 載唔到 → 必須報「未生效」而唔係假稱封咗
    _orig_repo = report.BTC_REPO
    _saved_pause = sys.modules.pop("btc_pause", None)
    _saved_path = list(sys.path)
    report.BTC_REPO = os.path.join(TMP, "冇呢個目錄")
    sys.path = [p for p in sys.path
                if os.path.realpath(p or ".") != os.path.realpath(REPO_DIR)]
    try:
        enforced2, msg2 = report._pause_gate_status()
    finally:
        sys.path = _saved_path
        report.BTC_REPO = _orig_repo
        if _saved_pause is not None:
            sys.modules["btc_pause"] = _saved_pause
    check("btc_pause 載唔到 → enforced=False (唔會假稱封咗)", enforced2 is False)
    check("載唔到 → 訊息講「未生效」", "未生效" in msg2)

print("\n[8] 語法 + entry point")
for f in ("btc_pause.py", "btc_auto_trade_cycle.py", "cron/btc_dual_report.py"):
    p = os.path.join(REPO_DIR, f)
    try:
        compile(open(p, encoding="utf-8").read(), p, "exec")
        ok = True
    except SyntaxError:
        ok = False
    check(f"{f} 語法 OK", ok)

shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 74)
print(f"結果: {PASS} PASS / {FAIL} FAIL")
if FAILURES:
    print("失敗項:")
    for f in FAILURES:
        print(f"  - {f}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
