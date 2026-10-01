#!/usr/bin/env python3
"""test_cron_branch_guard.py — cron wrapper 嘅 repo 健康檢查 / branch 驗證測試.

背景 (2026-10-01 修復):
  ~/repos/btc-analyze 由多個 cron **共用**:
    btc_weekend_cron.py    (落單,   每 15 分鐘)  BRANCH_PIN = "main"
    btc_rebalance_cron.py  (再平衡, 每日 5 次)   BRANCH_PIN = "fix/btc-exit-symmetry" ← BUG
    btc_dual_report.py     (只讀報告)            冇 branch 意識
  其中 rebalance 嘅 pin 落後 main 7 個 commit (缺 PR#7 phantom-sell 修復)。

  風險鏈: repo 損壞 → 邊個 cron 先撞到就由佢復原 → 若 rebalance 先, 會
  clone 舊 branch 落共用 path → 舊檢查只睇 file + HEAD → 當健康 →
  weekend cron **靜默跑舊 code 落單**。

  修復: (a) BRANCH_PIN 對齊 main  (b) 健康檢查加 branch 驗證
        (c) branch 唔對時用輕量 checkout, 唔跌落破壞性 re-clone
        (d) 復原失敗 = **fail-safe 唔跑落單** (GLM review BLOCKER)
        (e) 共用 repo 嘅復原/落單加 flock (GLM review HIGH)

跑法:
  python3 test_cron_branch_guard.py                      # 對 repo cron/ 跑
  CRON_DIR=~/.hermes/scripts python3 test_cron_branch_guard.py   # 對已部署版本跑
  CRON_DIR=/tmp/cronprefix  python3 test_cron_branch_guard.py    # 對未修版本跑 (應該 FAIL)
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

# 只驗呢 4 個 BTC cron wrapper — CRON_DIR 可能係 ~/.hermes/scripts (56 個無關 script),
# 唔應該掃全目錄 (會誤報其他 script 冇 __main__ guard 等)
WRAPPERS = ("btc_weekend_cron", "btc_rebalance_cron",
            "btc_martingale_cron", "btc_dual_report")

# 每個 wrapper 會跑嘅「落單/主要工作」entry — fail-safe 測試要確認佢冇被叫
TRADE_ENTRY = {
    "btc_weekend_cron": "btc_auto_trade_cycle.py",
    "btc_rebalance_cron": "btc_rebalance.py",
    "btc_martingale_cron": "btc_martingale.py",
}


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


def load_safe(name):
    """load() 但唔會因為 import 失敗而炸晒成份測試 (未修版本可能 import 唔到)."""
    try:
        return load(name)
    except Exception as e:
        print(f"    (⚠️ {name} module load 失敗: {type(e).__name__}: {e})")
        return None


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=True)


def make_repo(root, branch="main", files=("a.py", "b.py")):
    """建一個真 git repo: tmp/repo。回傳路徑。"""
    p = os.path.join(root, "repo")
    os.makedirs(p, exist_ok=True)
    sh("git init -q -b main", p)
    sh("git config user.email t@t; git config user.name t", p)
    for f in files:
        fp = os.path.join(p, f)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, "w").write("x\n")
    sh("git add -A && git commit -q -m init", p)
    if branch != "main":
        sh(f"git checkout -q -b {branch}", p)
    return p


def req_files(mod):
    """唔同 wrapper 用唔同常數名."""
    return getattr(mod, "REQUIRED_FILES", None) or getattr(mod, "REQUIRED", None) or ("a.py",)


# ── 1. BRANCH_PIN 一致性 (parity) ───────────────────────────────────
def test_parity():
    print("\n【1】共用同一 REPO 嘅 cron — 期望 branch 必須一致")
    info = {}
    for name in WRAPPERS:
        fn = f"{name}.py"
        if not os.path.exists(os.path.join(CRON, fn)):
            continue
        mod = load_safe(name)
        if mod is not None:
            # 由 module 屬性讀 (穩陣) — regex 綁死寫法, 改個寫法就測唔到
            repo = getattr(mod, "BTC_REPO", None) or getattr(mod, "REPO", None)
            pin = getattr(mod, "BRANCH_PIN", None) or getattr(mod, "BRANCH", None)
            rec = callable(getattr(mod, "ensure_repo", None))
        else:
            # fallback: 由 source 抽 (未修版本 / import 失敗)
            src = open(os.path.join(CRON, fn)).read()
            m_repo = re.search(r'BTC_(?:MARTINGALE_)?REPO"\)\s*or\s*os\.path\.expanduser\("([^"]+)"\)', src)
            m_pin = re.search(r'^BRANCH_PIN\s*=\s*os\.environ\.get\("BTC_REPO_BRANCH",\s*"([^"]*)"\)', src, re.M)
            m_br = re.search(r'^BRANCH\s*=\s*"([^"]*)"', src, re.M)
            repo = m_repo.group(1) if m_repo else None
            pin = m_pin.group(1) if m_pin else (m_br.group(1) if m_br else None)
            rec = "def ensure_repo" in src
        info[fn] = {"repo": repo, "pin": pin, "recovery": bool(rec)}

    for fn, v in sorted(info.items()):
        rc = "有復原" if v["recovery"] else "無復原(只讀)"
        print(f"    {fn:<26} repo={str(v['repo']):<28} pin={str(v['pin']):<15} {rc}")

    # 分組: **所有讀同一個 repo 嘅 script** 都應該對「repo 應該喺邊個 branch」有共識。
    # (唔止做復原嗰啲 — 只讀 script 一樣會因為錯 branch 而讀錯 code 出錯報告)
    by_repo = {}
    for fn, v in info.items():
        if v["repo"]:
            by_repo.setdefault(v["repo"], []).append((fn, v["pin"]))
    for repo, lst in sorted(by_repo.items()):
        vals = sorted({str(p) for _, p in lst})
        names = ", ".join(f for f, _ in lst)
        check(f"repo {repo} 嘅期望 branch 一致 ({names})", len(vals) == 1,
              f"唔一致: {vals}")
        check(f"  {repo} 每個 script 都有 pin (冇 None)", all(p for _, p in lst), f"{lst}")

    # 唯一可以 clone/污染共用 repo 嘅, 一定要係會驗 branch 嘅 script
    for fn, v in sorted(info.items()):
        if v["recovery"] and v["repo"] and "btc-analyze" in v["repo"]:
            check(f"{fn} 有復原 → 必須有 pin (否則會 clone 錯 branch 落共用 repo)",
                  bool(v["pin"]), f"got={v['pin']}")

    # 明確驗證原本嘅 bug 已修
    check("btc_rebalance_cron.py pin 已改為 main (原本 fix/btc-exit-symmetry)",
          (info.get("btc_rebalance_cron.py") or {}).get("pin") == "main",
          f"got={(info.get('btc_rebalance_cron.py') or {}).get('pin')}")
    check("btc_weekend_cron.py pin 仍係 main",
          (info.get("btc_weekend_cron.py") or {}).get("pin") == "main",
          f"got={(info.get('btc_weekend_cron.py') or {}).get('pin')}")


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
    check("git 指令有 timeout (cron hang 住會塞車)",
          getattr(mod, "GIT_TIMEOUT", None) is not None, "冇 GIT_TIMEOUT")
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
    for name in WRAPPERS:
        p = os.path.join(CRON, f"{name}.py")
        if not os.path.exists(p):
            check(f"{name}.py 存在", False)
            continue
        r = subprocess.run([sys.executable, "-m", "py_compile", p],
                           capture_output=True, text=True)
        check(f"{name}.py 語法 OK", r.returncode == 0, r.stderr[-160:])
        # main() 仲喺 (cron 用 `python3 x.py` 跑)
        # 用 py_compile + source 檢查, 唔 load — 未修版本會有 module-level import 副作用
        src = open(p).read()
        tree = ast.parse(src)
        fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        check(f"{name}.main() 存在", "main" in fns)
        check(f"{name}.py 有 __main__ guard (import 唔會執行)",
              '__name__ == "__main__"' in src)


# ── 7. fail-safe: 復原失敗 → 唔可以照跑落單 (GLM review BLOCKER) ─────
class FakeProc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _run_main_with_failing_checkout(mod, trade):
    """令 branch checkout 一定失敗, 睇 main() 仲會唔會跑落單 entry。

    只有「branch checkout -q …」被攔截 (模擬 checkout 失敗), 其餘 git 指令
    照跑真嘅 — 咁 _repo_healthy() 讀到嘅係真 repo 狀態。
    回傳 (ran_trade_list, raised, captured_stdout)。
    """
    import contextlib
    import io

    ran, raised = [], None
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        parts = [str(c) for c in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
        joined = " ".join(parts)
        # branch checkout = `git -C REPO checkout -q <pin>` (要 -q 先係切 branch;
        # `checkout -- .` 係還原檔案, 要放行)
        if "checkout" in parts and "-q" in parts:
            return FakeProc(1, "", "fatal: simulated checkout failure")
        if trade in joined:
            ran.append(joined)
            return FakeProc(0, "TRADE RAN", "")
        return real_run(cmd, *a, **kw)

    buf = io.StringIO()
    subprocess.run = fake_run
    try:
        with contextlib.redirect_stdout(buf):
            try:
                mod.main()
            except SystemExit:
                pass
            except BaseException as e:
                raised = e
    finally:
        subprocess.run = real_run
    return ran, raised, buf.getvalue()


def test_failsafe():
    print("\n【7】fail-safe: 復原失敗 → main() 唔可以跑落單 (BLOCKER)")
    for name, trade in TRADE_ENTRY.items():
        if not os.path.exists(os.path.join(CRON, f"{name}.py")):
            continue
        mod = load_safe(name)
        if mod is None:
            check(f"{name}: 載入到", False)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            pin = getattr(mod, "BRANCH_PIN", None) or getattr(mod, "BRANCH", None) or "main"
            rp = make_repo(os.path.join(tmp, "r"), pin, req_files(mod))
            mod.REPO = rp
            # repo 留喺錯 branch
            sh("git checkout -q -b wrongbranch", rp)
            # 繞過唔相關嘅前置條件 (唔想測試受時間窗口 / interpreter 探測影響)
            if hasattr(mod, "in_window"):
                mod.in_window = lambda: True
            if hasattr(mod, "pick_python"):
                mod.pick_python = lambda: sys.executable
            if hasattr(mod, "hourly_status"):
                mod.hourly_status = lambda: None      # 免得污染 sys.path / 出雜訊

            ran, raised, out = _run_main_with_failing_checkout(mod, trade)
            check(f"{name}: 修復失敗 → 冇跑 {trade} (fail-safe)",
                  not ran, f"有跑: {ran}")
            # ⚠️ 一定要真斷言「有出聲」— 唔可以寫成永遠 PASS (會掏空呢個回歸測試)
            spoke = ("❌" in out) or (raised is not None)
            check(f"{name}: 修復失敗 → 有出聲 (唔會靜默)",
                  spoke, f"out={out[:120]!r} raised={raised!r}")
            check(f"{name}: 修復失敗 → 冇 unhandled traceback 爆出",
                  raised is None or isinstance(raised, (RuntimeError, SystemExit)),
                  f"raised={type(raised).__name__ if raised else None}")

            # 對照: 健康 → 應該照跑 (證明上面唔係「永遠唔跑」)
            sh(f"git checkout -q {pin}", rp)
            cur = sh("git rev-parse --abbrev-ref HEAD", rp).stdout.strip()
            check(f"{name}: 對照組前置 — repo 已還原到 {pin}", cur == pin, f"got={cur}")
            ran2, _, _ = _run_main_with_failing_checkout(mod, trade)
            check(f"{name}: repo 健康 → 照跑 {trade} (對照組)",
                  bool(ran2), "健康但冇跑 = 過度保守")


# ── 8. flock (GLM review HIGH) ─────────────────────────────────────
def test_lock():
    print("\n【8】跨 cron 互斥鎖 — 共用 repo 唔可以同時復原")
    mod = load_safe("btc_weekend_cron")
    if mod is None or not callable(getattr(mod, "_repo_lock", None)):
        check("有 _repo_lock() (共用 repo 復原互斥)", False, "未修版本冇鎖")
        return
    check("有 _repo_lock() (共用 repo 復原互斥)", True)

    # ⚠️ 2026-10-01 GLM review LOW: 一定要 redirect 鎖檔去 tmp。
    # 原本直接用 module-level _LOCK_PATH = production 鎖檔
    # (~/.hermes/reports/.repo_recover_btc-analyze.lock) →
    # 喺 production 機跑測試會同 live cron 爭鎖 (測試 timeout FAIL, 或者
    # cron 嘅落單/復原要等測試)。
    real_lock = getattr(mod, "_LOCK_PATH", None)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_lock = os.path.join(tmp, "repo.lock")
        mod._LOCK_PATH = tmp_lock
        try:
            # 獨佔中唔可以再攞 (獨佔或共享) — 否則兩個 cron 會同時做復原/落單
            try:
                with mod._repo_lock(exclusive=True, timeout=30):
                    check("測試期間鎖檔已 redirect 去 tmp (唔會同 live cron 爭鎖)",
                          os.path.exists(tmp_lock) and mod._LOCK_PATH != real_lock,
                          f"tmp_lock 存在={os.path.exists(tmp_lock)} "
                          f"same_as_real={mod._LOCK_PATH == real_lock}")
                    try:
                        with mod._repo_lock(exclusive=True, timeout=2):
                            excl_ok = False
                    except Exception:
                        excl_ok = True
                    check("獨佔鎖生效 — 期間唔可以再有獨佔 (唔會同時 re-clone)", excl_ok)
                    try:
                        with mod._repo_lock(exclusive=False, timeout=2):
                            sh_ok = False
                    except Exception:
                        sh_ok = True
                    check("獨佔鎖擋住共享鎖 (復原中唔會落單)", sh_ok)
            except Exception as e:
                check("可以攞到獨佔鎖", False, f"{type(e).__name__}: {e}")

            # 共享鎖之間可以並存 (兩個 cron 可以同時落單)
            try:
                with mod._repo_lock(exclusive=False, timeout=5):
                    with mod._repo_lock(exclusive=False, timeout=5):
                        pass
                check("共享鎖可並存 (兩個 cron 可以同時落單)", True)
            except Exception as e:
                check("共享鎖可並存 (兩個 cron 可以同時落單)", False,
                      f"{type(e).__name__}: {e}")

            # 釋放後可以再攞 (唔會死鎖)
            try:
                with mod._repo_lock(exclusive=True, timeout=5):
                    pass
                check("鎖釋放後可以重新攞 (冇死鎖)", True)
            except Exception as e:
                check("鎖釋放後可以重新攞 (冇死鎖)", False, f"{type(e).__name__}: {e}")
        finally:
            mod._LOCK_PATH = real_lock

    check("測試完回復原本 _LOCK_PATH (唔會污染其他測試)",
          getattr(mod, "_LOCK_PATH", None) == real_lock, f"got={mod._LOCK_PATH}")

    # 落單入口真係有持共享鎖 (結構檢查)
    src = open(os.path.join(CRON, "btc_weekend_cron.py")).read()
    check("weekend main() 落單期間持共享鎖",
          "exclusive=False" in src and "_repo_lock(exclusive=False" in src)

    # 【唔抽共用模組嘅補償】三個 wrapper 嘅 _repo_lock 實作要一致 —
    # 今次個 bug 正正就係「兩邊唔同步」, 唔想將來改一個漏咗其他。
    import inspect
    srcs = {}
    for name in ("btc_weekend_cron", "btc_rebalance_cron", "btc_martingale_cron"):
        m = load_safe(name)
        if m is not None and callable(getattr(m, "_repo_lock", None)):
            try:
                srcs[name] = inspect.getsource(m._repo_lock)
            except Exception:
                pass
    if len(srcs) >= 2:
        norm = {k: re.sub(r"\s+", " ", v).strip() for k, v in srcs.items()}
        check(f"_repo_lock 實作 {len(srcs)} 個 wrapper 一致 (防將來改漏)",
              len(set(norm.values())) == 1, f"唔一致: {sorted(norm)}")


# ── 9. dual_report 唔應該喺 import 時拉真 repo ──────────────────────
def test_dual_report_import_purity():
    print("\n【9】btc_dual_report — import 唔應該即刻拉真 repo code")
    p = os.path.join(CRON, "btc_dual_report.py")
    if not os.path.exists(p):
        return
    src = open(p).read()
    tree = ast.parse(src)
    top_imports = set()
    for node in tree.body:                      # 只睇 module level
        if isinstance(node, ast.ImportFrom) and node.module:
            top_imports.add(node.module)
        elif isinstance(node, ast.Import):
            top_imports.update(a.name for a in node.names)
    check("btc_dual_report 唔喺 module level import binance_testnet_paper",
          "binance_testnet_paper" not in top_imports, f"top={sorted(top_imports)}")
    # 而且真係載入得到 (唔會拉真 repo / 唔會爆)
    mod = load_safe("btc_dual_report")
    check("btc_dual_report 可以乾淨載入", mod is not None)
    check("btc_dual_report 有 BRANCH_PIN (只讀都要知預期 branch)",
          getattr(mod, "BRANCH_PIN", None) == "main",
          f"got={getattr(mod, 'BRANCH_PIN', None)}")


# ── 10. pick_python: cache 唔可以喺 REPO + candidate 失敗唔可以炸 ──
def test_pick_python():
    print("\n【10】pick_python — cache 唔喺 REPO、candidate 失敗唔會炸")
    mod = load_safe("btc_weekend_cron")
    if mod is None or not callable(getattr(mod, "pick_python", None)):
        return
    with tempfile.TemporaryDirectory() as tmp:
        rp = make_repo(os.path.join(tmp, "r"), "main", req_files(mod))
        mod.REPO = rp
        home = os.path.join(tmp, "home")
        os.makedirs(home, exist_ok=True)

        # redirect "~" 去 tmp, 免得污染真 ~/.hermes/reports
        real_expanduser = os.path.expanduser
        real_run = subprocess.run
        os.path.expanduser = lambda p: (p.replace("~", home, 1)
                                        if isinstance(p, str) and p.startswith("~")
                                        else real_expanduser(p))
        try:
            # (a) 全部 candidate 都 timeout → 要回 None, 唔可以拋出去
            def boom(cmd, *a, **kw):
                parts = [str(c) for c in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
                if "-c" in parts:
                    raise subprocess.TimeoutExpired(cmd, 30)
                return real_run(cmd, *a, **kw)

            subprocess.run = boom
            try:
                res, crashed = mod.pick_python(), None
            except BaseException as e:
                res, crashed = None, e
            check("全部 candidate timeout → 回 None (唔炸出 main)",
                  crashed is None and res is None, f"res={res} raised={crashed!r}")

            # (b) 全部 candidate 唔存在 / OSError → 一樣唔可以炸
            def boom2(cmd, *a, **kw):
                parts = [str(c) for c in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
                if "-c" in parts:
                    raise FileNotFoundError("simulated")
                return real_run(cmd, *a, **kw)

            subprocess.run = boom2
            try:
                res2, crashed2 = mod.pick_python(), None
            except BaseException as e:
                res2, crashed2 = None, e
            check("candidate FileNotFoundError → 回 None (唔炸出 main)",
                  crashed2 is None and res2 is None, f"res={res2} raised={crashed2!r}")

            # (c) 正常路徑 → cache 一定要寫喺 REPO **以外**
            subprocess.run = real_run
            got = mod.pick_python()
            new_cache = os.path.join(home, ".hermes", "reports", ".cron_python")
            check("正常揀到 interpreter", bool(got), f"got={got}")
            check("cache 唔會寫入 REPO (唔再污染 worktree)",
                  not os.path.exists(os.path.join(rp, ".cron_python")))
            check("cache 寫喺 ~/.hermes/reports (REPO 以外)", os.path.exists(new_cache),
                  f"expect={new_cache}")

            # (d) 舊位置 REPO/.cron_python 仍然讀得到 (migration)
            # ⚠️ 唔好假設 new_cache 一定存在 — 未修版本唔會寫新位置,
            #    直接 os.remove 會炸咗成份測試, 令後面嘅斷言跑唔到。
            if os.path.exists(new_cache):
                os.remove(new_cache)
            legacy = os.path.join(rp, ".cron_python")
            open(legacy, "w").write(got)
            migrated = mod.pick_python()
            check("讀得返舊位置 REPO/.cron_python (migration 唔會斷)",
                  migrated == got, f"got={migrated}")
            check("migration: 順手寫埋新位置",
                  os.path.exists(new_cache), f"expect={new_cache}")
        finally:
            subprocess.run = real_run
            os.path.expanduser = real_expanduser


# ── 11. dual_report: 馬丁段缺欄 / 壞 log 都唔可以炸報告 ─────────────
def test_dual_report_martingale_robust():
    print("\n【11】btc_dual_report — 馬丁段缺欄/壞 log 唔可以令報告 crash")
    import contextlib
    import io
    import json as _json

    mod = load_safe("btc_dual_report")
    if mod is None:
        return

    def run_with_mart(payload_text):
        """用指定嘅馬丁 log 內容跑 main(), 回傳 (output, raised)。"""
        with tempfile.TemporaryDirectory() as tmp:
            mp = os.path.join(tmp, "mart.json")
            with open(mp, "w") as f:
                f.write(payload_text)
            mod.MART = mp
            mod.ORDERS = os.path.join(tmp, "orders.json")     # 唔存在 → 走 except
            mod.HIST = os.path.join(tmp, "hist.json")
            mod.PAUSED_MARKER = os.path.join(tmp, "no.txt")
            mod.HIST_MARKER = os.path.join(tmp, "no2.txt")
            mod.current_price = lambda: 100000.0            # 唔扯 network
            mod.branch_warning = lambda: None
            buf, raised = io.StringIO(), None
            try:
                with contextlib.redirect_stdout(buf):
                    mod.main()
            except BaseException as e:
                raised = e
            return buf.getvalue(), raised

    # (a) 對照: 舊寫法喺呢啲資料上真係會爆 (證明 bug 真實, 唔係假想)
    entries = [{"qty": 0.001}]                       # 缺 px
    try:
        sum(e["px"] for e in entries)
        old_crashed = False
    except KeyError:
        old_crashed = True
    check("對照: 舊寫法 `e['px']` 直接 index 會 KeyError (bug 真實)",
          old_crashed)

    # (b) entry 缺 px + active 缺 id/side/level/opened → 報告要照出
    bad = _json.dumps({"active": {"entries": [{"qty": 0.001}, {"px": 90000.0}]},
                       "daily": {"2026-10-01": {"wins": 1, "losses": 0,
                                                "loss_usd": 2.5}}})
    out, raised = run_with_mart(bad)
    check("active 缺 id/side/level/opened + entry 缺 px → 唔會 crash",
          raised is None, f"raised={raised!r}")
    check("  → 報告仍然出到馬丁行", "🔵 馬丁" in out, f"out={out[:200]!r}")
    check("  → 報告仍然出到 daily 行", "今日 1W/0L" in out, f"out={out[:200]!r}")

    # (c) 壞 JSON → 出警告, 唔會 crash
    out2, raised2 = run_with_mart("{ not json")
    check("馬丁 log 係壞 JSON → 唔會 crash", raised2 is None, f"raised={raised2!r}")
    check("  → 有出警告", "⚠️" in out2, f"out={out2[:200]!r}")

    # (d) 空 log / 冇 active → 正常出「無 active chain」
    out3, raised3 = run_with_mart("{}")
    check("冇 active chain → 唔會 crash", raised3 is None, f"raised={raised3!r}")
    check("  → 出「無 active chain」", "無 active chain" in out3, f"out={out3[:200]!r}")

    # (e) entries 唔係 dict (人手改壞) → 唔會 crash
    out4, raised4 = run_with_mart(_json.dumps(
        {"active": {"entries": ["oops", None], "id": 1, "side": "BUY"}}))
    check("entries 含非 dict → 唔會 crash", raised4 is None, f"raised={raised4!r}")
    check("  → 報告仍然出得到", "🔵 馬丁" in out4, f"out={out4[:200]!r}")

    # (f) 最重要: 報告一定要 print 到嘢 (main() 存在意義)
    for label, o in (("缺欄", out), ("壞 JSON", out2), ("空 log", out3), ("壞 entries", out4)):
        check(f"  ({label}) 報告有輸出 (唔會空白)", bool(o.strip()), f"out={o!r}")


def main():
    print("=" * 76)
    print(f"cron branch guard 測試   CRON_DIR={CRON}")
    print("=" * 76)
    # 每個測試獨立 try/except — 一個 crash 唔應該令其餘測試跑唔到
    # (對未修版本跑時, 新函數唔存在會 AttributeError)
    for fn in (test_parity, test_weekend_health, test_weekend_recovery,
               test_rebalance, test_martingale, test_syntax_and_interface,
               test_failsafe, test_lock, test_dual_report_import_purity,
               test_pick_python, test_dual_report_martingale_robust):
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
