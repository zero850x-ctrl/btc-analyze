#!/usr/bin/env python3
"""test_btc_pattern_gate.py — BTC pattern gate 明示化 (2026-09-20) 嘅守門測試。

背景: 舊 gate 用 `any(k in pattern for k in ("Flag",))` —— 靠**顯示標籤 substring**。
fib0786/fib 嘅標籤係 "0.786 深度回調 ($…)", 唔含 "Flag", 於是永遠被 drop。
而 justify 個 gate 嘅 83 樣本實驗 (bt_gate_results.json) 裏面完全冇 fib/0.786 樣本
⇒ 排除 0.786 係未經評估嘅副作用, 唔係測過嘅決定。證據: gate 上線 (08-30 20:24) 前
12 小時有 12 筆 fib 單, 上線後 0 筆。

呢個 test 守三件事:
  A. **行為等價** —— 重構之後, 每個家族嘅去留同舊 substring 寫法**完全一樣** (政策不變)
  B. **明示** —— 每個家族嘅去留寫死喺 EXPECTED_KEEP, 改政策一定要改呢張表
  C. **唔靜默** —— 冇一個被 drop 嘅 setup 係冇 _gate_skip 原因嘅

跑: python3 test_btc_pattern_gate.py
"""
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("btc_engine", os.path.join(REPO, "btc_engine.py"))
be = importlib.util.module_from_spec(spec)
spec.loader.exec_module(be)

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


# ── fixtures: (pattern 標籤, entry_mode) ────────────────────────────────────────
FIXTURES = [
    ("🚩 Bull Flag (牛旗)", "breakout"),
    ("🚩 Bear Flag (熊旗)", "breakout"),
    ("🚩 Bull Flag (牛旗)", "pullback"),
    ("🔻 Double Top (雙頂)", "breakout"),
    ("🔺 Double Bottom (雙底)", "breakout"),
    ("△ Rising Wedge (上升楔形)", "breakout"),
    ("📐 Ascending Triangle (上升三角形)", "breakout"),
    ("📐 Descending Triangle (下降三角形)", "breakout"),
    ("Channel (通道)", "breakout"),
    ("0.786 深度回調 ($80556→$81336)", "fib0786"),
    ("0.618 Fib 回調 ($77957→$78299)", "fib"),
    ("🔻 Double Top (雙頂)", "boundary"),
    ("△ Rising Wedge (上升楔形)", "boundary"),
    ("🆕 Brand New Pattern (未見過)", "breakout"),
]


def old_gate_keeps(s):
    """舊實作嘅參考: any(k in pattern for k in ("Flag",))。"""
    return any(k in str(s.get("pattern", "?")) for k in ("Flag",))


print("=== A. 行為等價: 新明示家族 gate vs 舊 substring gate ===")
mismatch = []
for pat, mode in FIXTURES:
    s = {"pattern": pat, "entry_mode": mode}
    fam = be.setup_family(s)
    new_keep = fam in be.ALLOWED_PATTERN_FAMILIES
    old_keep = old_gate_keeps(s)
    if new_keep != old_keep:
        mismatch.append((pat, mode, fam, old_keep, new_keep))
    print(f"  {'OK ' if new_keep == old_keep else 'FAIL'} {pat[:34]:<36} mode={mode:<9} "
          f"fam={fam:<14} 舊={'keep' if old_keep else 'drop'} 新={'keep' if new_keep else 'drop'}")
check(not mismatch, "所有 fixture 嘅去留同舊寫法一致 (政策不變)",
      f"{len(mismatch)} 個唔一致: {mismatch}")

print("\n=== B. 明示對照表: 每個家族嘅去留都要寫死 ===")
EXPECTED_KEEP = {
    "Flag": True,
    "DoubleTop": False,
    "DoubleBottom": False,
    "Wedge": False,
    "Triangle": False,
    "Channel": False,
    "fib0786": False,      # ← 政策: 唔開 (但係明示, 唔係靠標籤撞)
    "fib": False,          # ← 同上
    "boundary": False,
}
seen = set()
for pat, mode in FIXTURES:
    fam = be.setup_family({"pattern": pat, "entry_mode": mode})
    if fam.startswith("UNKNOWN:"):
        continue
    seen.add(fam)
    exp = EXPECTED_KEEP.get(fam)
    check(exp is not None, f"家族 {fam} 有寫入 EXPECTED_KEEP (唔可以漏)")
    if exp is not None:
        got = fam in be.ALLOWED_PATTERN_FAMILIES
        check(got == exp, f"家族 {fam} 去留 = {'keep' if exp else 'drop'}")
for fam in EXPECTED_KEEP:
    check(fam in seen, f"EXPECTED_KEEP 嘅 {fam} 有 fixture 覆蓋 (唔可以有死項)")

print("\n=== C. 唔靜默: 被 drop 嘅一律有 _gate_skip 原因 ===")
raw = [{"pattern": p, "entry_mode": m, "direction": "🟢 BUY",
        "entry_zone": "$80,000", "stop_loss": "$78,000", "tp1": "$84,000",
        "risk_amount": "100"} for p, m in FIXTURES]
kept = be.btc_filter_setups(raw, atr=200.0, px=80450.0,
                            diff_check={"status": "OK", "note": "t"}, ma50=81205.0)
silent = [s for s in raw if s not in kept and not s.get("_gate_skip")]
check(not silent, "冇 setup 係「唔喺 kept 但又冇 _gate_skip」", f"{len(silent)} 個靜默")
check(all(s.get("_family") for s in raw), "每個 setup 都有 _family 標記")

print("\n=== D. 關鍵回歸: fib0786 被擋嘅原因必須指名家族 ===")
fib = {"pattern": "0.786 深度回調 ($80556→$81336)", "entry_mode": "fib0786",
       "direction": "🟢 BUY", "entry_zone": "$80,000", "stop_loss": "$78,000",
       "tp1": "$84,000", "risk_amount": "100"}
be.btc_filter_setups([fib], atr=200.0, px=80450.0, diff_check={"status": "OK", "note": "t"}, ma50=81205.0)
reason = str(fib.get("_gate_skip", ""))
check(reason == "family_fib0786_not_allowed",
      "fib0786 原因 = family_fib0786_not_allowed (舊版係 pattern_not_allowed)",
      reason)

print("\n=== E. 未知家族 fail-closed 而且要標明 UNKNOWN ===")
unk = {"pattern": "🆕 Brand New Pattern (未見過)", "entry_mode": "breakout"}
fam = be.setup_family(unk)
check(fam.startswith("UNKNOWN:"), "未知標籤 → UNKNOWN: 前綴", fam)
check(fam not in be.ALLOWED_PATTERN_FAMILIES, "未知家族唔會默認當合格 (fail-closed)")

print("\n=== F. summarize_gate_skips 匯報完整 ===")
summ = be.summarize_gate_skips(raw)
check(summ["n_raw"] == len(raw), f"n_raw = {len(raw)}", str(summ["n_raw"]))
check(summ["n_kept"] == len(kept), f"n_kept = {len(kept)}", str(summ["n_kept"]))
check(sum(summ["reasons"].values()) == summ["n_raw"] - summ["n_kept"],
      "reasons 加總 = raw - kept (每個 drop 都有紀錄)")
check(any(k.startswith("family_fib0786") for k in summ["reasons"]),
      "fib0786 出現喺 reasons (唔會無聲無息)")

print("\n" + "=" * 70)
print(f"總共 {N} 個斷言, {len(FAILS)} 個 FAIL")
if FAILS:
    for f in FAILS:
        print("  ❌", f)
    sys.exit(1)
print("✅ 全部通過")
