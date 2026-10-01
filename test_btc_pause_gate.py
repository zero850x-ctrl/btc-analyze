#!/usr/bin/env python3
"""test_btc_pause_gate.py — 「主系統停用」硬閘測試

驗嘅嘢: `btc_pause.py` 嘅判斷邏輯 + `btc_auto_trade_cycle.main()` 同
`binance_testnet_paper.main()` **真係**跟住個判斷跳過落單
(行為測試, 唔係 source-grep — skill lesson #44)。

Negative control (必做, 見 skill lesson #38):
    REPO_DIR=/tmp/pause_prefix python3 test_btc_pause_gate.py
`/tmp/pause_prefix` 放「未修版」檔案 → 閘嗰幾條斷言**必須 FAIL**,
否則證明唔到測試有效。

⚠️ 測試完全唔會掂真 repo / 真 API / 真 log:
   - `reconcile_cycle` / `sh` / `_load_keys` / `place_signal_order` 全部 monkeypatch
   - `HEARTBEAT` / `HISTORY` / `LOG_PATH` / `REPO` 全部 redirect 去 tempdir
   - `binance_testnet_paper` 係注入嘅假 module (cycle 用)
   - `binance_testnet_paper.py` 自己嗰個係 monkeypatch 咗 `_load_keys` 才叫
"""
import importlib.util
import io
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


def load(name, filename):
    path = os.path.join(REPO_DIR, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fresh_cycle(tmp):
    """重新 load btc_auto_trade_cycle (乾淨 state) + 全面隔離副作用。

    回 (mod, pause, events) — events 記低所有會產生副作用嘅呼叫。
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


def set_marker(path):
    """改 btc_pause 用邊個標記路徑 (check() 每次 call 都讀 module global)。"""
    sys.modules["btc_pause"].MARKER_PATH = path


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
    f.write("BTC 主系統（Flag pattern）— 正式標記停用\n第二行\n")
r = pause.check()
check("標記載明停用 → paused=True", r[0] is True)
check("標記載明停用 → certain=True", r[2] is True)
check("reason 帶停用 emoji", pause.PAUSED_EMOJI in r[1])
check("reason 帶標記路徑 (叫人去睇詳情)", MARKER in r[1])

open(os.path.join(TMP, "afile"), "w").write("x")
r = pause.check(ENOTDIR)
check("讀唔到標記 (OSError) → fail-safe 當停用", r[0] is True)
check("讀唔到標記 → certain=False (要出聲)", r[2] is False)
check("讀唔到標記 → reason 用 ⚠️ 而唔係 ⏸️", pause.UNCERTAIN_EMOJI in r[1])

check("is_paused() 對應 check()[0]", pause.is_paused(MARKER) is True)
check("CLI: 已停用 → exit 10", pause.main() == 10)
pause.MARKER_PATH = os.path.join(TMP, "nope.txt")
check("CLI: 未停用 → exit 0", pause.main() == 0)

print("\n[2] 靜默/出聲 設計不變式 (emoji 唔可以調亂)")
cron = load("btc_weekend_cron_test",
            os.path.join(REPO_DIR, "cron", "btc_weekend_cron.py"))
check("⏸️ 唔喺 NOTABLE_KEYS (常規停用 = 靜默, 唔會 spam TG)",
      pause.PAUSED_EMOJI not in cron.NOTABLE_KEYS)
check("⚠️ 喺 NOTABLE_KEYS (唔確定 = 一定要出聲)",
      pause.UNCERTAIN_EMOJI in cron.NOTABLE_KEYS)

# ⚠️ 最強嘅一條: 就算 marker 檔內容塞滿所有會觸發通知嘅 token,
#    check() 嘅 reason 都唔可以漏任何一個出去 —— 否則 anti-spam 靜靜失效。
with open(MARKER, "w") as f:
    f.write("❌ 唔好再開倉 🚨 FLATTENED ⚠️ ✅ 🔒 🧹 📌 ⌛ 🎯 🚫\n")
r_certain = pause.check()
leaked = [k for k in cron.NOTABLE_KEYS if k in r_certain[1]]
check(f"marker 內容塞滿 NOTABLE_KEYS → reason 一個都唔漏 ({leaked})", not leaked)
r_unc = pause.check(ENOTDIR)
# ⚠️ uncertain reason **故意**帶 ⚠️ (就係要出聲) —— 所以驗證時要剔走佢,
#    其餘 marker 內容漏出嚟嘅 token 一個都唔可以有。
leaked2 = [k for k in cron.NOTABLE_KEYS
           if k != pause.UNCERTAIN_EMOJI and k in r_unc[1]]
check(f"uncertain reason 內容唔可以漏 token (⚠️ 除外, {leaked2})", not leaked2)
check("uncertain reason 帶 ⚠️ (刻意出聲)", pause.UNCERTAIN_EMOJI in r_unc[1])

print("\n[3] 標記路徑 single source (防漂移)")
report = load("btc_dual_report_test",
              os.path.join(REPO_DIR, "cron", "btc_dual_report.py"))
check("btc_dual_report.PAUSED_MARKER == btc_pause 預設路徑",
      os.path.expanduser(report.PAUSED_MARKER)
      == os.path.expanduser(pause._DEFAULT_MARKER))
check("btc_pause.py 喺 cron REQUIRED_FILES (缺檔會自動復原, 唔會 crash)",
      "btc_pause.py" in cron.REQUIRED_FILES)

print("\n[4] 行為: 停用 (certain) — reconcile 照跑, 落單跳過")
with open(MARKER, "w") as f:
    f.write("BTC 主系統（Flag pattern）— 正式標記停用\n")
mod, pause, ev = fresh_cycle(TMP)
set_marker(MARKER)
exc, out = run_cycle(mod)
check("冇爆 exception", exc is None)
check("reconcile 有跑 (現有倉仍然有人管)", ev["reconcile"] == 1)
check("冇觸發落單 (binance_testnet_paper.py)", ev["trade"] == 0)
check("冇跑引擎掃描 (btc_engine.py)", not any("btc_engine" in c for c in ev["sh"]))
check("有出停用訊息", pause.PAUSED_EMOJI in out)
check("停用訊息講明 reconcile 照跑", "reconcile 照跑" in out)

print("\n[5] 行為: 冇標記 — 一切照舊 (唔可以擋多咗)")
set_marker(os.path.join(TMP, "nope.txt"))
r0, t0, sh0 = ev["reconcile"], ev["trade"], len(ev["sh"])
exc, out = run_cycle(mod)
check("冇爆 exception", exc is None)
check("reconcile 有跑", ev["reconcile"] == r0 + 1)
check("有跑引擎掃描", any("btc_engine" in c for c in ev["sh"][sh0:]))
check("有觸發落單", ev["trade"] == t0 + 1)

print("\n[6] 行為: 標記讀唔到 (唔確定) — 落單跳過, 但 reconcile **照跑**")
# ⚠️ 2026-10-01 GLM review: 原本 uncertain 連 reconcile 都跳 —— 方向反咗。
#    讀唔到 marker 同「知唔知有冇倉」無關, 唔會令 reconcile 變唔安全。
set_marker(ENOTDIR)
r1, t1, sh1 = ev["reconcile"], ev["trade"], len(ev["sh"])
exc, out = run_cycle(mod)
check("冇爆 exception", exc is None)
check("reconcile **有**跑 (現有倉唔可以冇人管)", ev["reconcile"] == r1 + 1)
check("落單跳過 (唔確定 = fail-closed)", ev["trade"] == t1)
check("冇跑引擎掃描", len(ev["sh"]) == sh1)
check("有出聲 (⚠️ 唔確定)", pause.UNCERTAIN_EMOJI in out)
check("唔確定時唔會出 ⏸️ (唔應該出兩次)", pause.PAUSED_EMOJI not in out)

print("\n[7] 行為: binance_testnet_paper.main() 自己都要擋 (defense in depth)")
btp = load("btp_cli_test", "binance_testnet_paper.py")
set_marker(MARKER)                                     # 標記存在 → 應該擋
btp.REPO = TMP
btp._load_keys = lambda: ("k", "s")                    # 唔好讀真 credentials
btp.place_signal_order = lambda *a, **k: None          # 唔好真落單
placed = []
btp.place_signal_order = lambda *a, **k: placed.append(a)
argv_bak = sys.argv
sys.argv = ["binance_testnet_paper.py"]
buf = io.StringIO()
try:
    with redirect_stdout(buf):
        btp.main()
except SystemExit:
    pass
except Exception as e:                                 # noqa: BLE001
    print(f"       (CLI 拋出 {type(e).__name__}: {e})")
finally:
    sys.argv = argv_bak
out_cli = buf.getvalue()
check("CLI 落單路徑被擋 (印 ⛔)", "⛔" in out_cli)
check("CLI 冇真落單 (place_signal_order 冇 call)", not placed)

print("\n[8] 報告要真驗閘裝好, 唔可以見 marker 就講「已封住」")
set_marker(MARKER)
report = load("btc_dual_report_test2",
              os.path.join(REPO_DIR, "cron", "btc_dual_report.py"))
report.BTC_REPO = REPO_DIR
gate = getattr(report, "_pause_gate_status", None)
check("報告有 _pause_gate_status() (真驗閘裝好, 唔係寫死一句)", callable(gate))
if not callable(gate):
    # 未修版: 報告只有一句寫死嘅「落單路徑仍然存在」, 冇真驗證 → 以下全部 FAIL
    check("閘裝好 + 已停用 → enforced=True", False)
    check("訊息講明停用有效", False)
    check("標記已刪 → enforced=False (唔會假稱封咗)", False)
    check("標記讀唔到 → enforced=False", False)
    check("btc_pause 載唔到 → enforced=False", False)
else:
    enforced, msg = gate()
    check("閘裝好 + 已停用 → enforced=True", enforced is True)
    check("訊息講明停用有效", "停用有效" in msg)
    check("訊息唔再講「落單路徑仍然存在」", "仍然存在" not in msg)

    # 反向 A: 標記被刪 → check() 回 (False,'',True) → 唔可以報「已封住」
    set_marker(os.path.join(TMP, "nope.txt"))
    enforced_del, msg_del = gate()
    check("標記已刪 → enforced=False (唔會假稱封咗)", enforced_del is False)
    check("標記已刪 → 訊息講明未停用", "未" in msg_del and "停用" in msg_del)

    # 反向 B: 讀唔到標記 → enforced=False
    set_marker(ENOTDIR)
    enforced_unc, msg_unc = gate()
    check("標記讀唔到 → enforced=False", enforced_unc is False)

    # 反向 C: btc_pause 載唔到 → enforced=False, 要講「未生效」
    set_marker(MARKER)
    _orig_repo = report.BTC_REPO
    _saved_pause = sys.modules.pop("btc_pause", None)
    _saved_path = list(sys.path)
    report.BTC_REPO = os.path.join(TMP, "冇呢個目錄")
    sys.path = [p for p in sys.path
                if os.path.realpath(p or ".") != os.path.realpath(REPO_DIR)]
    try:
        enforced2, msg2 = gate()
    finally:
        sys.path = _saved_path
        report.BTC_REPO = _orig_repo
        if _saved_pause is not None:
            sys.modules["btc_pause"] = _saved_pause
    check("btc_pause 載唔到 → enforced=False", enforced2 is False)
    check("載唔到 → 訊息講「未生效」", "未生效" in msg2)

print("\n[9] 語法")
for f in ("btc_pause.py", "btc_auto_trade_cycle.py", "binance_testnet_paper.py",
          "cron/btc_dual_report.py", "cron/btc_weekend_cron.py"):
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
