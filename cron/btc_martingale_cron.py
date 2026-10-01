#!/usr/bin/env python3
"""btc_martingale_cron.py — 馬丁格爾實驗 cron 入口 (no_agent watchdog)

Repo 獨立 clone 喺 ~/repos/btc-martingale (exp/martingale branch):
唔影響主系統 ~/repos/btc-analyze (fix/btc-exit-symmetry).
2026-09-13: 兩個 clone 都由 /tmp 搬入 ~/repos (macOS clean-tmps 會靜默清 tracked file).
缺失時自動 re-clone (GitHub 係 source of truth).
訊息: 有 🔵/➕/✅/❌/🛑 先出聲; ⏳ 靜默 = 正常.
"""
import os
import subprocess
import sys
from datetime import datetime

# 2026-09-13: clone 由 /tmp 搬入 ~/repos (macOS clean-tmps 會靜默清 tracked file)
REPO = os.environ.get("BTC_MARTINGALE_REPO") or os.path.expanduser("~/repos/btc-martingale")
GIT_URL = "https://github.com/zero850x-ctrl/btc-analyze.git"
BRANCH = "exp/martingale"
NOTABLE = ("🔵", "➕", "✅", "❌", "🛑", "⚠️")
# 暫時性錯誤 (testnet down/network): crash 都唔出聲 — 下個 15min tick 自然恢復
SILENT_ERROR_MARKERS = ("HTTP Error 502", "HTTP Error 503", "HTTP Error 504",
                        "HTTP Error 429", "HTTP Error 500", "HTTP Error 4001",
                        "urlopen error", "URLError", "timed out", "ConnectionResetError",
                        "getaddrinfo failed")


REQUIRED_FILES = ("btc_martingale.py", "binance_testnet_paper.py")


def _repo_env():
    env = dict(os.environ)
    env.setdefault("HOME", "/Users/gordonlui")
    return env


def _files_ok():
    if not os.path.isdir(REPO):
        return False
    return all(os.path.isfile(os.path.join(REPO, f)) for f in REQUIRED_FILES)


def _head_ok():
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "HEAD"],
                       capture_output=True, timeout=30, env=_repo_env())
    return r.returncode == 0


def _branch_ok():
    """HEAD 喺 BRANCH (detached HEAD 當唔 OK).

    2026-10-01 加: 舊檢查只睇 file + HEAD — 若 repo 被留喺另一個 branch
    (例如人手 checkout 完唔記得還原), 會照當健康 → 靜默跑錯版本落單。
    """
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"],
                       capture_output=True, text=True, timeout=30, env=_repo_env())
    return r.returncode == 0 and r.stdout.strip() == BRANCH


def _repo_healthy():
    """必需 file 就位 + .git 完整 + 喺預期 branch. 2026-09-12: macOS clean-tmps 逐個 file 清
    /tmp, 舊檢查只睇一個 file → 當健康 → bot 跑即死。"""
    return _files_ok() and _head_ok() and _branch_ok()


def ensure_repo():
    """確保馬丁 repo 完整, 回傳 note (非 None = 做過恢復, 要通知用戶)."""
    env = _repo_env()
    entry = os.path.join(REPO, "btc_martingale.py")
    if _repo_healthy():
        return None
    missing = [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(REPO, f))]
    if missing:
        why = f"缺 {', '.join(missing)}"
    elif os.path.isdir(os.path.join(REPO, ".git")) and not _head_ok():
        why = "git metadata 壞"
    elif os.path.isdir(os.path.join(REPO, ".git")) and not _branch_ok():
        why = f"branch 唔對 (應該係 {BRANCH})"
    else:
        why = "repo 唔完整"
    note = f"🔧 馬丁 repo 唔完整 ({why}) — 自動恢復中"
    if os.path.isdir(os.path.join(REPO, ".git")):
        subprocess.run(["git", "-C", REPO, "checkout", "--", "."],
                       capture_output=True, timeout=90, env=env)
        if _repo_healthy():
            return note + " (git checkout 修好)"
        # 2026-10-01: file/git 完好但 branch 唔對 → 輕量 checkout, 唔使 re-clone
        if _files_ok() and _head_ok():
            r = subprocess.run(["git", "-C", REPO, "checkout", "-q", BRANCH],
                               capture_output=True, timeout=90, env=env)
            if _repo_healthy():
                return note + f" (branch 唔對 → checkout {BRANCH})"
            return note + f" (⚠️ branch checkout {BRANCH} 失敗: {(r.stdout + r.stderr)[-200:]!r})"
    if os.path.isdir(REPO):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.rename(REPO, f"{REPO}.bak.{stamp}")
    r = subprocess.run(["git", "clone", "-b", BRANCH, GIT_URL, REPO],
                       capture_output=True, timeout=180, env=env)
    if not _repo_healthy():
        raise RuntimeError(f"re-clone 失敗: {(r.stdout + r.stderr)[-300:]}")
    return note + f" (re-clone {BRANCH} 完成)"


def main():
    try:
        recover_note = ensure_repo()
    except Exception as e:
        print(f"❌ 馬丁 repo restore 失敗: {e}")
        return
    prepend = [recover_note] if recover_note else []
    env = _repo_env()
    py = "/opt/homebrew/bin/python3.11"
    r = subprocess.run([py, os.path.join(REPO, "btc_martingale.py")],
                       cwd=REPO, capture_output=True, text=True, timeout=120, env=env)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        # 暫時性錯誤 (testnet down/network) → 靜默, 等 15min 後自然恢復
        if any(m in out for m in SILENT_ERROR_MARKERS):
            if prepend:
                print("\n".join(prepend))
            return
        print("\n".join(prepend + [f"❌ 馬丁 crash (exit {r.returncode}):\n{out[-500:]}"]))
        return
    lines = [l for l in out.splitlines() if any(k in l for k in NOTABLE)]
    if prepend or lines:
        print("\n".join(prepend + lines))


if __name__ == "__main__":
    main()