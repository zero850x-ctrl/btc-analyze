#!/usr/bin/env python3
"""test_cron_branch_guard.py — cron wrapper 嘅 repo 健康檢查 / branch 驗證測試.

背景 (2026-10-01 修復):
  ~/repos/btc-analyze 由多個 cron **共用**:
    btc_weekend_cron.py    (落單,   每 15 分鐘)  BRANCH_PIN = "main"
    btc_rebalance_cron.py  (再平衡, 每日 5 次)   BRANCH_PIN = "fix/btc-exit-symmetry" ← BUG
  其中 rebalance 嘅 pin 落後 main 7 個 commit (缺 PR#7 phantom-sell 修復)。

  風險鏈: repo 損壞 → 邊個 cron 先撞到就由佢復原 → 若 rebalance 先, 會
  clone 舊 branch 落共用 path → 舊檢查只睇 file + HEAD → 當健康 →
  weekend cron **靜默跑舊 code 落單**。

  修復: (a) BRANCH_PIN 對齊 main  (b) 健康檢查加 branch 驗證
        (c) branch 唔對時用輕量 checkout, 唔跌落破壞性 re-clone

跑法: python3 test_cron_branch_guard.py
"""
import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))
# CRON_DIR 可覆寫 — 用嚟對「未修版本」跑同一套測試, 證明測試真係捉到 bug
CRON = os.environ.get("CRON_DIR") or os.path.join(REPO, "cron")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


def load(name):
    """由 cron/ 載入 cron wrapper 做 module (唔會執行 main)."""
    path = os.path.join(CRON, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=True)


def make_repo(root, branch="main", files=("a.py", "b.py")):
    """建一個真 git repo: tmp/repo。回傳路徑。"""
    p = os.path.join(root, "repo")
    os.makedirs(p, exist_ok=True)
    sh("git init -q -b main", p)
    sh("git config user.email t@t; git config user.name t", p)
    for f in files:
        open(os.path.join(p, f), "w").write("x\n")
    sh("git add -A && git commit -q -m init", p)
    if branch != "main":
        sh(f"git checkout -q -b {branch}", p)
    return p


# ── 1. BRANCH_PIN 一致性 (parity) ───────────────────────────────────
def test_parity():
    print("\n【1】共用同一 REPO 嘅 cron — BRANCH_PIN 必須一致")
    pins = {}
    for fn in sorted(os.listdir(CRON)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(CRON, fn)).read()
        m_repo = re.search(r'BTC_REPO"\)\s*or\s*os\.path\.expanduser\("([^"]+)"\)', src)
        m_pin = re.search(r'BRANCH_PIN\s*=\s*os\.environ\.get\("BTC_REPO_BRANCH",\s*"([^"]*)"\)', src)
        m_br = re.search(r'^BRANCH\s*=\s*"([^"]*)"', src, re.M)
        # 有冇「復原」邏輯 — 只有會 clone/checkout 嘅 script 才需要 pin
        has_recovery = ("git\", \"clone" in src or '"clone"' in src
                        or "checkout" in src)
        if m_repo:
            pins[fn] = {"repo": m_repo.group(1), "recovery": has_recovery,
                        "pin": m_pin.group(1) if m_pin else (m_br.group(1) if m_br else None)}
        elif m_br:
            m_own = re.search(r'BTC_MARTINGALE_REPO"\)\s*or\s*os\.path\.expanduser\("([^"]+)"\)', src)
            pins[fn] = {"repo": m_own.group(1) if m_own else "?",
                        "recovery": has_recovery, "pin": m_br.group(1)}
        else:
            continue

    for fn, v in sorted(pins.items()):
        rc = "有復原" if v["recovery"] else "無復原(只讀)"
        print(f"    {fn:<26} repo={v['repo']:<28} pin={str(v['pin']):<15} {rc}")

    # 分組: 只有「同一 repo 而且都會做復原」先需要 pin 一致
    by_repo = {}
    for fn, v in pins.items():
        if v["recovery"]:
            by_repo.setdefault(v["repo"], []).append((fn, v["pin"]))
    for repo, lst in sorted(by_repo.items()):
        vals = sorted({str(p) for _, p in lst})
        names = ", ".join(f for f, _ in lst)
        check(f"repo {repo} 嘅恢復 pin 一致 ({names})", len(vals) == 1,
              f"唔一致: {vals}")
        check(f"  {repo} 嘅 pin 冇 None", all(p for _, p in lst), f"{lst}")

    # 無復原嘅 script 唔應該有 pin (read-only, 唔會污染)
    for fn, v in sorted(pins.items()):
        if not v["recovery"]:
            check(f"{fn} (無復原) 冇 pin — 唔會 clone 落共用 repo",
                  v["pin"] is None, f"got={v['pin']}")

    # 明確驗證原本嘅 bug 已修
    rebal = pins.get("btc_rebalance_cron.py", {})
    check("btc_rebalance_cron.py pin 已改為 main (原本 fix/btc-exit-symmetry)",
          rebal.get("pin") == "main", f"got={rebal.get('pin')}")
    wknd = pins.get("btc_weekend_cron.py", {})
    check("btc_weekend_cron.py pin 仍係 main", wknd.get("pin") == "main",
          f"got={wknd.get('pin')}")


# ── 2. 健康檢查: branch 驗證 ────────────────────────────────────────
def test_weekend_health():
    print("\n【2】btc_weekend_cron._repo_healthy() — branch 驗證")
    mod = load("btc_weekend_cron")
    FILES = mod.REQUIRED_FILES
    has_branch_ok = callable(getattr(mod, "_branch_ok", None))
    check("有 _branch_ok() (新增 branch 驗證)", has_branch_ok)
    if not has_branch_ok:
        return
    with tempfile.TemporaryDirectory() as tmp:
        # 2a 正確 branch
        rp = make_repo(os.path.join(tmp, "a"), "main", FILES)
        mod.REPO = rp
        check("正確 branch → healthy", mod._repo_healthy() is True)
        check("  _branch_ok() True", mod._branch_ok() is True)

        # 2b 錯 branch
        sh("git checkout -q -b old-branch", rp)
        check("錯 branch → unhealthy (核心修復)", mod._repo_healthy() is False)
        check("  _files_ok() 仍 True (只係 branch 唔對)",
              getattr(mod, "_files_ok", lambda: None)() is True)
        check("  _head_ok() 仍 True",
              getattr(mod, "_head_ok", lambda: None)() is True)

        # 2c back to main
        sh("git checkout -q main", rp)
        check("checkout 返 main → healthy", mod._repo_healthy() is True)

        # 2d detached HEAD
        sh("git checkout -q --detach HEAD", rp)
        check("detached HEAD → unhealthy", mod._repo_healthy() is False)
        sh("git checkout -q main", rp)

        # 2e 缺 file
        rp2 = make_repo(os.path.join(tmp, "b"), "main", FILES)
        os.remove(os.path.join(rp2, FILES[0]))
        mod.REPO = rp2
        check("缺 file → unhealthy", mod._repo_healthy() is False)

        # 2f 唔存在
        mod.REPO = os.path.join(tmp, "nope")
        check("repo 唔存在 → unhealthy", mod._repo_healthy() is False)


def test_weekend_recovery():
    print("\n【3】btc_weekend_cron.ensure_repo() — 錯 branch 要輕量修正")
    mod = load("btc_weekend_cron")
    FILES = mod.REQUIRED_FILES
    with tempfile.TemporaryDirectory() as tmp:
        parent = os.path.join(tmp, "a")
        rp = make_repo(parent, "main", FILES)
        mod.REPO = rp
        check("健康 → ensure_repo() 回 None (唔做嘢)", mod.ensure_repo()[1] is None)

        sh("git checkout -q -b stale", rp)
        note = mod.ensure_repo()
        note = note[1] if isinstance(note, tuple) else note
        check("錯 branch → 有 note", bool(note), f"note={note}")
        check("note 講出 checkout main", note and "checkout main" in note, f"note={note}")
        cur = sh("git rev-parse --abbrev-ref HEAD", rp).stdout.strip()
        check("repo 已還原到 main", cur == "main", f"got={cur}")
        # ⚠️ 關鍵: 唔應該做破壞性 re-clone (唔會留 .bak 目錄)
        baks = [d for d in os.listdir(parent) if ".bak." in d]
        check("冇做破壞性 re-clone (冇 .bak 目錄)", not baks, f"baks={baks}")


# ── 4. rebalance ───────────────────────────────────────────────────
def test_rebalance():
    print("\n【4】btc_rebalance_cron — _healthy() branch 驗證 + 非破壞復原")
    mod = load("btc_rebalance_cron")
    FILES = mod.REQUIRED
    check("BRANCH_PIN default == main", mod.BRANCH_PIN == "main", f"got={mod.BRANCH_PIN}")
    with tempfile.TemporaryDirectory() as tmp:
        parent = os.path.join(tmp, "a")
        rp = make_repo(parent, "main", FILES)
        mod.REPO = rp
        ok, why = mod._healthy()
        check("正確 branch → (True, '')", ok is True and why == "", f"got=({ok},{why})")

        sh("git checkout -q -b fix/btc-exit-symmetry", rp)
        ok, why = mod._healthy()
        check("錯 branch → (False, 有解釋)", ok is False and "branch" in why, f"got=({ok},{why})")

        note = mod.ensure_repo()
        check("note 講出已 checkout main", "checkout main" in note, f"note={note}")
        cur = sh("git rev-parse --abbrev-ref HEAD", rp).stdout.strip()
        check("repo 已還原到 main", cur == "main", f"got={cur}")
        baks = [d for d in os.listdir(parent) if ".broken." in d]
        check("冇做破壞性 re-clone (冇 .broken 目錄)", not baks, f"baks={baks}")

        # 缺 file → 仍然要跌落 re-clone path (唔可以當 healthy)
        os.remove(os.path.join(rp, FILES[0]))
        ok, _ = mod._healthy()
        check("缺 file → unhealthy", ok is False)


# ── 5. 馬丁 (自己 repo) ─────────────────────────────────────────────
def test_martingale():
    print("\n【5】btc_martingale_cron — branch 驗證 (獨立 repo)")
    mod = load("btc_martingale_cron")
    FILES = mod.REQUIRED_FILES
    check("BRANCH == exp/martingale", mod.BRANCH == "exp/martingale", f"got={mod.BRANCH}")
    check("有 _branch_ok() (新增 branch 驗證)",
          callable(getattr(mod, "_branch_ok", None)))
    with tempfile.TemporaryDirectory() as tmp:
        rp = make_repo(os.path.join(tmp, "m"), "exp/martingale", FILES)
        mod.REPO = rp
        check("正確 branch → healthy", mod._repo_healthy() is True)
        sh("git checkout -q -b wrong", rp)
        check("錯 branch → unhealthy", mod._repo_healthy() is False)
        note = mod.ensure_repo()
        check("note 講出 checkout", note and "checkout exp/martingale" in note, f"note={note}")
        cur = sh("git rev-parse --abbrev-ref HEAD", rp).stdout.strip()
        check("已還原到 exp/martingale", cur == "exp/martingale", f"got={cur}")


# ── 6. 語法 / 對外介面 ─────────────────────────────────────────────
def test_syntax_and_interface():
    print("\n【6】語法 + 對外介面 (cron 靠呢啲 entry point)")
    for fn in sorted(os.listdir(CRON)):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(CRON, fn)
        r = subprocess.run([sys.executable, "-m", "py_compile", p], capture_output=True, text=True)
        check(f"{fn} 語法 OK", r.returncode == 0, r.stderr[-160:])
    # main() 仲喺 (cron 用 `python3 x.py` 跑)
    for name in ("btc_weekend_cron", "btc_rebalance_cron", "btc_martingale_cron", "btc_dual_report"):
        mod = load(name)
        check(f"{name}.main() 存在", callable(getattr(mod, "main", None)))
    # __main__ guard 仲喺
    for fn in sorted(os.listdir(CRON)):
        if fn.endswith(".py"):
            src = open(os.path.join(CRON, fn)).read()
            check(f"{fn} 有 __main__ guard (import 唔會執行)",
                  '__name__ == "__main__"' in src)


def main():
    print("=" * 76)
    print(f"cron branch guard 測試   CRON_DIR={CRON}")
    print("=" * 76)
    # 每個測試獨立 try/except — 一個 crash 唔應該令其餘測試跑唔到
    # (對未修版本跑時, 新函數唔存在會 AttributeError)
    for fn in (test_parity, test_weekend_health, test_weekend_recovery,
               test_rebalance, test_martingale, test_syntax_and_interface):
        try:
            fn()
        except Exception as e:
            check(f"{fn.__name__} 冇 crash", False, f"{type(e).__name__}: {e}")

    npass = sum(1 for _, ok, _ in RESULTS if ok)
    nfail = len(RESULTS) - npass
    print("\n" + "=" * 76)
    print(f"結果: {npass} PASS / {nfail} FAIL")
    if nfail:
        print("\n失敗項:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  ❌ {name}  [{detail}]")
    else:
        print("✅ 全部通過")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
