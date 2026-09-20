#!/usr/bin/env python3
"""binance_testnet_paper.py — 引擎訊號 → Binance Spot Testnet 落真單

前置:
  1. https://testnet.binance.vision 用 GitHub 授權登入
  2. Generate HMAC-SHA256 key pair → 攞 API key/secret
  3. export BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET
     (或寫入 ~/.hermes/secrets/binance_testnet.env)

用法:
  python3 binance_testnet_paper.py                # 主流程: seed 引擎訊號 → 落單 → 檢查
  python3 binance_testnet_paper.py --status       # 睇 testnet 帳戶＋開放單
  python3 binance_testnet_paper.py --cancel-all   # 取消全部開放單
  python3 binance_testnet_paper.py --reconcile    # 對帳: testnet 成交 → 更新本地 log
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

BASE = "https://testnet.binance.vision"  # endpoints 自帶 /api/v3/...
SYMBOL = "BTCUSDT"
LOG_PATH = os.path.expanduser("~/.hermes/reports/btc_testnet_orders.json")
ENV_PATH = os.path.expanduser("~/.hermes/secrets/binance_testnet.env")

TAKER_FEE = 0.001          # 0.1% (testnet 同主網同 fee schedule)
MIN_NOTIONAL = 10.0        # BTCUSDT minimum
LOT_STEP = 0.00001         # BTC lot step

# fix/btc-exit-symmetry: 落單前 hard RR gate — 歷史實證 RR<1.2 嘅單全部贏細輸大
# (engine json 嘅 rr_tp1 有缺口, 呢度做最後防線, 唔信 json)
MIN_RR_EXEC = 1.2

# 入場模式 (feat/limit-entry, 09-16):
#   "limit"  = 掛 LIMIT @ engine 指定價, 等價格返到 zone 才成交 (RR 準確, 冇滑價; 可能唔成交)
#   "market" = legacy 市價追入 (09-13 前嘅行為)
ENTRY_MODE = os.environ.get("BTC_ENTRY_MODE", "limit")
# 限價單存活上限 — 超過就 cancel 放返 cap (engine 每 15 分鐘重算, 形態會過期)
LIMIT_TTL_HOURS = float(os.environ.get("BTC_LIMIT_TTL_HOURS", "8"))


def _compute_rr(setup, entry_override=None):
    """setup → TP1/risk RR (落單前驗證用).

    entry_override: 用現價/實際成交價代替 planned entry 計 RR。
    09-13 實證: gate 用 planned entry 計出 1.35, 但 MARKET 成交差 57-77 點
    (0.07-0.1%) → 實際 RR 跌到 0.51。25 單統計: 實際 RR<1.2 佔 21 單
    (贏 +0.37R / 輸 -0.90R = 贏細輸大)。所以 gate 要同時用現價計一次。
    """
    try:
        if entry_override is not None:
            entry = float(entry_override)
        elif setup.get("btc_limit_px"):
            # feat/limit-entry: 實際成交價 = 限價, 用佢計 RR 才準
            entry = float(setup["btc_limit_px"])
        else:
            entry = float(setup["btc_entry"])
        stop = float(setup["btc_stop"])
        tp1 = float(setup["btc_tp1"]) if setup.get("btc_tp1") else None
    except (KeyError, TypeError, ValueError):
        return None
    if tp1 is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return abs(tp1 - entry) / risk


def _load_keys():
    key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
    secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")
    if not key and os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_TESTNET_API_KEY="):
                    key = line.split("=", 1)[1]
                elif line.startswith("BINANCE_TESTNET_API_SECRET="):
                    secret = line.split("=", 1)[1]
    return key, secret


RETRYABLE_HTTP = {429, 500, 502, 503, 504}   # testnet 偶發 502/5xx/429 → retry
MAX_RETRIES = 3
RETRY_DELAYS = (2.0, 5.0, 10.0)


def _http_open(req, retries=MAX_RETRIES):
    """urlopen with retry — 食甩 testnet 短暫 502/5xx/429/network 問題.

    全部 retry 完都失敗 → 照 raise (caller 可 catch). 2026-09-09 實證:
    testnet 全面 502 時 cron 每 15 分鐘 crash spam; retry 只係食甩短暫
    中斷, 長時間 down 要靠 wrapper 靜默 (見 cron wrapper 暫時性錯誤處理).
    """
    last_exc = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code not in RETRYABLE_HTTP:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as e:
            last_exc = e
        if attempt < retries - 1:
            time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
    raise last_exc


def _signed_request(method, path, params, key, secret):
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10000
    query = urllib.parse.urlencode(params)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{query}&signature={sig}"
    req = urllib.request.Request(url, method=method, headers={"X-MBX-APIKEY": key})
    return _http_open(req)


def _public_request(path, params=None):
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return _http_open(req)


def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            return json.load(f)
    return {"orders": [], "history": []}


# status 分類 (2026-09-20): guard 一律 fail-closed —— 唔喺明確「已完結」清單就當 still-live。
# F5 (GLM 第三輪): 原本仲有一個 LIVE_STATUS tuple 但冇任何消費者 —— 已刪 (死代碼,
# 而且「WIPED 當 live」呢個政策決定藏喺死 tuple 入面, 會誤導讀者)。
# 明確「已完結」= 唔再佔用倉位 (其餘一律當 live)
DONE_STATUS = ("CLOSED", "LIMIT_EXPIRED", "LIMIT_CANCELLED", "SKIP_PREFLIGHT")

# 每日虧損硬上限 (R)。XAUUSD 用 -3R hard stop; BTC 實測最差單日 -3.59R (09-04, 4 單),
# 11 日之中只有 1 日 ≤ -3R → -3R 唔會過度封鎖, 但會截斷最壞嘅日。
MAX_DAILY_LOSS_R = float(os.environ.get("BTC_MAX_DAILY_LOSS_R", "3.0"))

# 同方向並行上限。XAUUSD 用 3; BTC 維持 1 —— BTC 24/7 + 波動 3.4× 黃金,
# 3 個同向倉嘅實際風險暴露大好多。用戶 09-04 放寬 XAUUSD 係針對黃金, 唔應自動套去 BTC。
SAME_DIR_MAX = int(os.environ.get("BTC_SAME_DIR_MAX", "1"))


def is_live_rec(o):
    """呢筆記錄仲佔住倉位嗎? fail-closed: 唔係明確完結就當 live。

    ⚠️ fail-closed 嘅代價: 一個「唔喺 DONE_STATUS 但實際已經冇倉」嘅狀態會永久
    鎖死引擎 (所有新單被 same_side guard 擋)。所以必須有復原路徑 ——
    見 resolve_orphan_states(): 用 exchange 實際倉位/成交對帳, 有倉就重建 legs,
    冇倉就歸 CLOSED。2026-09-20 GLM review 指出, 呢個係 fail-closed 嘅必要配套。
    """
    return str(o.get("status") or "") not in DONE_STATUS


# 需要對帳復原嘅「孤兒」狀態: 卡住但可能已經冇倉 (或者冇止損裸掛)
ORPHAN_STATUS = ("FLATTENED_OCO_FAILED", "OCO_FAILED", "ENTRY_FILLED_PENDING_EXITS",
                 "FILLED_ENTRY", "LIMIT_FILLED", "WIPED")


def estimate_held_for(rec, acct_btc, live_recs):
    """估算某一筆記錄自己嘅持倉 = 帳戶總額 − 其他 live 記錄嘅 qty (>=0)。

    ⚠️ 2026-09-20 GLM 第三輪 F1: 抽出嚟係為了可測。
    ⚠️ 2026-09-20 GLM 第四輪 HIGH: 之前 `mine <= 0` 時回**全個帳戶餘額** ——
       一筆冇 qty 欄位嘅舊孤兒會被估成「持有全帳戶」→ 永遠判定有倉 → rebuild。
       兩筆咁嘅記錄 = 兩筆都各自「持有全帳戶」。呢個正正係 F1 想消滅嘅
       aggregate-as-per-record 錯誤, 只係換咗觸發條件。
       修法: qty 缺失 = 冇法歸因 → 回 None (caller 當「未知」freeze, 唔估)。
    acct_btc=None → None。
    """
    if acct_btc is None:
        return None
    try:
        mine = float(rec.get("qty") or 0.0)
    except (TypeError, ValueError):
        mine = 0.0
    if mine <= 0:
        return None                 # 冇 qty = 冇法歸因, 唔估 (GLM 第四輪 HIGH)
    others = 0.0
    for o in live_recs or []:
        if o is rec:
            continue
        try:
            others += float(o.get("qty") or 0.0)
        except (TypeError, ValueError):
            continue
    return max(0.0, acct_btc - others)


def resolve_orphan_states(log, held_qty, dust_eps=0.0, rebuild=None, acct_btc=None):
    """對帳孤兒狀態 —— fail-closed 嘅復原路徑。

    held_qty: 每筆持倉。callable(rec) -> float|None, 或 scalar。
    acct_btc: **帳戶總額** —— 獨立參數 (2026-09-20 GLM 第五輪 HIGH-1)。
              之前歧義檢查只認 scalar held_qty, 而生產傳 callable → 檢查永遠唔行
              = 死代碼。freeze-on-ambiguity 必須用呢個參數做。
    dust_eps: 低於此當「冇倉」。
    rebuild:  有倉時補建函數。
    """
    changed = []
    summary = {"closed_orphan": 0, "rebuilt": 0, "needs_legs": 0, "skipped": 0,
               "frozen_unknown": 0, "frozen_ambiguous": 0}

    # ── 模糊歸因前置檢查 (2026-09-20 GLM 第四輪 MEDIUM) ────────────────────
    # 如果孤兒 qty 總和 > 帳戶餘額, 逐筆估出嚟嘅持倉**唔可能同時啱** (每人各分到
    # 一部分, 但實際只夠一筆)。呢個情況下自動行動就係賭博 → 整批 freeze。
    orphans = [r for r in log.get("orders", [])
               if str(r.get("status") or "") in ORPHAN_STATUS]
    # acct_btc 優先; 向後兼容: 若冇傳而 held_qty 係 scalar 就當佢係帳戶總額
    if acct_btc is None and isinstance(held_qty, (int, float)):
        acct_btc = float(held_qty)
    _acct = float(acct_btc) if acct_btc is not None else None
    if _acct is not None:
        try:
            _sum_orph = sum(float(r.get("qty") or 0.0) for r in orphans)
        except (TypeError, ValueError):
            _sum_orph = None
        if _sum_orph is not None and _sum_orph > _acct + dust_eps and len(orphans) > 1:
            for rec in orphans:
                rec["needs_manual_reconcile"] = True
                rec["needs_legs"] = True
                rec["ambiguous_reason"] = (
                    f"孤兒 qty 總和 {_sum_orph:.5f} > 帳戶 {_acct:.5f} — 歸因模糊, freeze")
                changed.append(rec)
            summary["frozen_ambiguous"] = len(orphans)
            summary["needs_legs"] = len(orphans)
            return changed, summary

    for rec in log.get("orders", []):
        st = str(rec.get("status") or "")
        if st not in ORPHAN_STATUS:
            continue
        if callable(held_qty):
            h = held_qty(rec)
        else:
            h = held_qty
        if h is None:
            # MEDIUM-3 (GLM 第五輪): 之前靜默 continue → 連 log 都冇。
            # 一個可能真有倉嘅記錄永久裸掛但零 observability。改為標記 + 出聲。
            rec["needs_manual_reconcile"] = True
            rec["needs_legs"] = True
            rec["unknown_holding_reason"] = "冇法判斷持倉 (qty 缺失或帳戶查唔到)"
            summary["skipped"] += 1
            changed.append(rec)
            continue
        if float(h) <= dust_eps:
            rec["status"] = "CLOSED"
            rec["resolved_via"] = "no_position"
            rec["resolved_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec.setdefault("closed_note", "孤兒狀態復原: exchange 冇倉 → 歸 CLOSED")
            changed.append(rec)
            summary["closed_orphan"] += 1
        elif rebuild is None:
            # LOW-1 (GLM 第四輪): F3 嘅 freeze 檢查要喺 rebuild 檢查**之前**。
            # 唔係嘅話 rebuild=None (lot_step 攞唔到) 時, 結果不明嘅
            # FLATTENED_OCO_FAILED 只會標 needs_legs, 冇 needs_manual_reconcile
            # → 「結果不明」嘅訊號唔見咗。
            if st == "FLATTENED_OCO_FAILED" and rec.get("flatten_ok") is None:
                rec["needs_manual_reconcile"] = True
            rec["needs_legs"] = True
            summary["needs_legs"] += 1
            changed.append(rec)
        elif rebuild is not None:
            # F3 (GLM 第三輪): 結果不明嘅記錄唔可以自動行動。
            # 9 筆 FLATTENED_OCO_FAILED 嘅 flatten_ok 係 None = 唔知 flatten 有冇成交
            # = 唔知有冇倉。喺「唔知」嘅前提下自動補 SELL legs 可能賣走唔屬於呢筆嘅幣。
            # 將未知轉成明確 hold + warning, 唔俾自動化路徑估。
            if st == "FLATTENED_OCO_FAILED" and rec.get("flatten_ok") is None:
                rec["needs_manual_reconcile"] = True
                rec["needs_legs"] = True
                summary["needs_legs"] += 1
                summary["frozen_unknown"] = summary.get("frozen_unknown", 0) + 1
                changed.append(rec)
                continue
            # 2026-09-20 GLM review A: 有倉孤兒必須真正補建 exit legs,
            # 唔可以只標 needs_legs (冇消費者 = 死巷 → 倉永久裸掛冇止損)。
            # F2: contract —— rebuild 必須令 rec 離開 ORPHAN_STATUS, 否則當失敗
            # (免得每個 cycle 重複補建 → 重複 SELL legs)。
            try:
                before = str(rec.get("status") or "")
                new_rec = rebuild(rec)
                if isinstance(new_rec, tuple):
                    new_rec = new_rec[0]
                if isinstance(new_rec, dict) and new_rec is not rec:
                    rec.update(new_rec)
                now_st = str(rec.get("status") or "")
                if now_st in ORPHAN_STATUS or now_st == before:
                    raise RuntimeError(
                        f"rebuild 冇令記錄離開孤兒狀態 (仍為 {now_st!r}) — 拒絕當成功")
                rec.pop("needs_legs", None)
                rec.pop("rebuild_error", None)
                rec["rebuilt_legs_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                summary["rebuilt"] += 1
            except Exception as e:                       # noqa: BLE001
                rec["rebuild_error"] = f"{type(e).__name__}: {e}"
                rec["needs_legs"] = True
                summary["needs_legs"] += 1
            changed.append(rec)
        else:
            rec["needs_legs"] = True
            summary["needs_legs"] += 1
            changed.append(rec)
    return changed, summary


def guards_allow(log, setup, same_dir_max=1):
    """落單前 guard 決策 —— 抽成純函數以便測試。

    ⚠️ 2026-09-20 GLM review: 之前 guard 邏輯 inline 喺 main() 裏面, 令 test 只能
    「喺 test 裏面重新實作一次」→ 測緊自己, mutation 刪走 main() 嘅 check 都捉唔到。
    抽成函數之後 test 直接 call 呢個, 先算真牙。

    回傳 (allow: bool, reason: str)。
    """
    live = [o for o in log.get("orders", []) if is_live_rec(o)]
    if any(o.get("pattern") == setup.get("pattern") for o in live):
        return False, f"same_pattern 已 live, 限 {1} 單"
    if any(o.get("side") != setup.get("btc_side") for o in live):
        opp = [o for o in live if o.get("side") != setup.get("btc_side")]
        return False, f"有一邊向 {opp[0].get('side')} live 倉, 唔開反向"
    same = [o for o in live if o.get("side") == setup.get("btc_side")]
    if len(same) >= same_dir_max:
        return False, f"同向 live 已 {len(same)} 單 (cap {same_dir_max})"
    return True, ""


def daily_realized_r(log, day=None):
    """某日 (UTC, 預設今日) 已實現 R 總和 —— 只計真正平倉嘅單。"""
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tot = 0.0
    n = 0
    for o in log.get("orders", []):
        if str(o.get("status") or "") != "CLOSED":
            continue
        ts = str(o.get("closed_time") or o.get("seeded_time") or "")
        if not ts.startswith(day):
            continue
        try:
            tot += float(o.get("r_multiple") or 0.0)
            n += 1
        except (TypeError, ValueError):
            continue
    return tot, n


def save_log(log):
    """原子寫入 —— temp file + os.replace。

    2026-09-20 GLM review: 原本直接 open(w) 寫, crash mid-write → log 爛/空 →
    下個 tick load_log() 見 0 orders → guards_allow 全部放行 → 重複開倉。
    加咗多個寫入點之後呢個窗口按比例變大, 所以要 atomic。
    """
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".orders.", suffix=".tmp",
                               dir=os.path.dirname(LOG_PATH))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, LOG_PATH)
        # LOW-6 (GLM 第五輪): fsync 目錄 —— 否則 crash 時 rename 唔保證持久
        # (可能彈返舊檔)。open dir + fsync 係 POSIX rename durability 嘅標準做法。
        try:
            dfd = os.open(os.path.dirname(LOG_PATH), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass                # 某啲 FS 唔支援 dir fsync — 唔應該因此失敗
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def daily_loss_brake(log, limit=None):
    """每日虧損硬煞停決策: (是否煞停, 當日已實現 R, 單數)。

    抽成純函數係為了可測 —— 之前只測 daily_realized_r 嘅算術, 冇測「煞停會唔會真係停」。
    """
    if limit is None:
        limit = MAX_DAILY_LOSS_R
    tot, n = daily_realized_r(log)
    return (tot <= -limit), tot, n


def _log_upsert(rec):
    """插入或更新一筆記錄 (以 order_id 對照)。

    用嚟令「成交」同「exit legs 建好」兩個階段共用同一筆記錄, 而成交一刻就
    已經喺 log 見到 (封死 guard 睇唔到倉嘅 1-2 秒窗口)。
    """
    log = load_log()
    oid = rec.get("order_id")
    for i, o in enumerate(log["orders"]):
        if oid is not None and o.get("order_id") == oid:
            log["orders"][i] = rec
            save_log(log)
            return
    log["orders"].append(rec)
    save_log(log)


def account_status(key, secret):
    return _signed_request("GET", "/api/v3/account", {}, key, secret)


def current_price():
    return float(_public_request("/api/v3/ticker/price", {"symbol": SYMBOL})["price"])


def exchange_filters(key, secret):
    """攞 LOT_SIZE / MIN_NOTIONAL — testnet 可能同主網唔同.

    Testnet 實測 (2026-08-29): BTCUSDT stepSize/minQty 返 0 — 嗰陣用預設 LOT_STEP.
    """
    info = _public_request("/api/v3/exchangeInfo", {"symbol": SYMBOL})
    sym = info["symbols"][0]
    lot_step, lot_min = LOT_STEP, 0.0
    min_notional = MIN_NOTIONAL
    for f in sym["filters"]:
        if f["filterType"] == "LOT_SIZE":
            lot_step = float(f["stepSize"]) or LOT_STEP   # testnet 返 0 → fallback
            lot_min = float(f["minQty"])
        elif f["filterType"] in ("NOTIONAL", "MIN_NOTIONAL"):
            min_notional = float(f.get("minNotional") or f.get("notional") or MIN_NOTIONAL)
    return lot_step, lot_min, min_notional


def round_step(qty, step):
    return max(0.0, int(qty / step) * step)


def place_signal_order(setup, key, secret, atr=None, mode=None):
    """引擎 setup → testnet 真單 (market 進場 + 3 段出場).

    fix/btc-exit-symmetry exit 結構:
      - 1/3 qty → OCO A: stop-limit SL + limit TP1 (掛 TP 本價, 唔食 0.1% maker 差價)
      - 1/3 qty → OCO B: stop-limit SL + limit TP2 (冇 TP2 就併入尾倉)
      - 尾倉 1/3 → 獨立 stop-limit SL, 之後由 reconcile 按 ATR trailing
    任一段 exit 建立失敗 → 取消已建 exit + market flatten (冇裸倉).

    setup: btc_engine.py 輸出格式 (btc_side/btc_entry/btc_stop/btc_tp1/btc_tp2/pattern)
    """
    side = setup["btc_side"]
    entry = float(setup["btc_entry"])
    stop = float(setup["btc_stop"])
    tp1 = float(setup["btc_tp1"]) if setup.get("btc_tp1") else None
    tp2 = float(setup["btc_tp2"]) if setup.get("btc_tp2") else None
    pattern = setup.get("pattern", "?")
    if mode is None:
        mode = ENTRY_MODE

    # RR hard gate — 落單前最後防線, 唔信 json; 冇 TP1 = 冇法計 RR = 一律拒
    rr = _compute_rr(setup)
    if rr is None or rr < MIN_RR_EXEC:
        return None, f"RR {'n/a(冇TP1)' if rr is None else f'{rr:.2f}'} < {MIN_RR_EXEC} hard gate — skip"

    px = current_price()
    lot_step, lot_min, min_notional = exchange_filters(key, secret)

    # fix 1 (09-13): 落單前 RR 用「現價」重算 — 只適用 market 模式。
    # (limit 模式成交價 = limit_px, 唔受市價影響; 佢自己喺下面 limit 分支覆核 RR)
    if mode == "market":
        if tp1:
            if px == stop:
                return None, f"risk 0 (px={px:.0f} = stop) — skip"
            rr_px = abs(tp1 - px) / abs(px - stop)
            if rr_px < MIN_RR_EXEC:
                return None, (f"RR(現價 ${px:,.0f}) {rr_px:.2f} < {MIN_RR_EXEC} — skip "
                              f"(planned RR {rr:.2f} 但市價已追高 {abs(px - entry) / entry * 100:.2f}%)")
        else:
            return None, "冇 TP1 — 冇法計 RR, skip"

    # Pre-flight level 驗證 — OCO 拒單係因為 TP 喺市價錯邊 (下單必敗, 先擋慳手續費)
    # limit 模式: OCO 喺成交後才建, 屆時價 ≈ limit_px, 所以用 limit_px 做參考價
    ref_px = float(setup.get("btc_limit_px") or entry) if mode == "limit" else px
    if tp1:
        if side == "BUY" and tp1 <= ref_px * 1.0005:
            return None, f"TP1 {tp1} 喺參考價 {ref_px:.0f} 下面 — BUY OCO 必拒, skip"
        if side == "SELL" and tp1 >= ref_px * 0.9995:
            return None, f"TP1 {tp1} 喺參考價 {ref_px:.0f} 上面 — SELL OCO 必拒, skip"
        if side == "BUY" and stop >= ref_px * 0.9995:
            return None, f"SL {stop} 喺參考價 {ref_px:.0f} 上面 — BUY OCO 必拒, skip"
        if side == "SELL" and stop <= ref_px * 1.0005:
            return None, f"SL {stop} 喺參考價 {ref_px:.0f} 下面 — SELL OCO 必拒, skip"

    # qty: USD 200 notional / entry (paper 額度), round 落 step
    notional = 200.0
    qty = round_step(notional / entry, lot_step)
    if qty < lot_min or qty * entry < min_notional:
        return None, f"qty {qty} below filter (step={lot_step}, min_notional={min_notional})"

    # ── feat/limit-entry: 限價掛單 (唔追市價) ──────────────────────
    # 09-14 實證: 市價追入時價已穿過 entry zone 0.10-0.17%, TP1 又近 → 實際 RR 0.44-0.90
    # (planned 1.23-1.74), 32 次 setup 冇一個追得過 gate。改為掛 LIMIT 等價格返到
    # 引擎指定價位, 成交價 = limit_px → RR 準確、冇滑價。
    # 代價: 唔成交就冇 trade (由 reconcile 用 TTL / setup 失效 cancel)。
    if mode == "limit":
        limit_px = float(setup.get("btc_limit_px") or entry)
        # 掛單價一定要喺市價「正確一邊」, 否則變 taker 立即成交 = 追高
        if side == "BUY" and limit_px >= px * 0.9995:
            return None, (f"限價 {limit_px:,.2f} 唔低過市價 {px:,.0f} — skip "
                          f"(等返 zone 先入)")
        if side == "SELL" and limit_px <= px * 1.0005:
            return None, (f"限價 {limit_px:,.2f} 唔高過市價 {px:,.0f} — skip "
                          f"(等返 zone 先入)")
        # RR 覆核: 成交價 = limit_px, 用呢個價計先係真實 RR
        rr_lim = abs(tp1 - limit_px) / abs(limit_px - stop) if (tp1 and limit_px != stop) else None
        if rr_lim is None or rr_lim < MIN_RR_EXEC:
            return None, (f"RR(限價) {rr_lim if rr_lim is None else round(rr_lim, 2)} "
                          f"< {MIN_RR_EXEC} — skip")
        qty = round_step(notional / limit_px, lot_step)
        if qty < lot_min or qty * limit_px < min_notional:
            return None, f"qty {qty} below filter (limit_px={limit_px})"
        order = _signed_request("POST", "/api/v3/order", {
            "symbol": SYMBOL, "side": side, "type": "LIMIT",
            "quantity": f"{qty:.5f}", "price": f"{limit_px:.2f}",
            "timeInForce": "GTC",
        }, key, secret)
        rec = {
            "pattern": pattern, "side": side, "qty": qty,
            "order_id": order["orderId"], "status": "LIMIT_PENDING",
            "limit_px": round(limit_px, 2), "limit_ts": time.time(),
            "planned_stop": stop, "planned_tp1": tp1, "planned_tp2": tp2,
            "planned_entry": round(entry, 2),
            "atr": round(float(atr), 2) if atr else None,
            "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rr_planned": round(rr, 2) if rr else None,
            "rr_limit": round(rr_lim, 2),
            "px_at_place": round(px, 2),
        }
        log = load_log()
        log["orders"].append(rec)
        save_log(log)
        return rec, None

    # 進場: market (legacy 模式; BTC_ENTRY_MODE=market)
    order = _signed_request("POST", "/api/v3/order", {
        "symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": f"{qty:.5f}",
    }, key, secret)

    fills = order.get("fills", [])
    fill_px = sum(float(f["price"]) * float(f["qty"]) for f in fills) / sum(float(f["qty"]) for f in fills) if fills else px
    fill_qty = sum(float(f["qty"]) for f in fills) or qty
    fee_paid = sum(float(f["commission"]) for f in fills)

    rec = {
        "pattern": pattern, "side": side, "qty": fill_qty,
        "order_id": order["orderId"], "status": "FILLED_ENTRY",
        "entry_fill": round(fill_px, 2), "fee": fee_paid,
        "planned_stop": stop, "planned_tp1": tp1, "planned_tp2": tp2,
        "atr": round(float(atr), 2) if atr else None,
        "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rr_planned": round(rr, 2) if rr else None,
        "rr_px": round(abs(tp1 - px) / abs(px - stop), 2) if (tp1 and px != stop) else None,
    }

    # ── 2026-09-20 fix: 成交即刻寫 log, 唔等 build_exit_legs ──────────────────
    # 舊寫法: 成交後一路唔寫, 直到 build_exit_legs 建完所有 OCO legs 才 append。
    # 建 OCO 要 call 2-3 次 API, 需時 1-2 秒 → 呢段窗口 log 完全冇記錄, 令下一個
    # setup 嘅 same_pattern / same_side / opp_side guard 見到「冇 live 倉」而全部放行。
    # 實證 (2026-08-29~08-31): 4 對同 pattern 重疊倉, seeded_time 全部相隔 1-2 秒,
    # 而 log 由頭到尾冇一筆 same_pattern skip 記錄。
    rec["status"] = "ENTRY_FILLED_PENDING_EXITS"
    _log_upsert(rec)

    # ── fix 2 (09-13): 成交後 RR 覆核 ─────────────────────────────
    # MARKET 一定有滑價, 落單前估嘅 RR 同實際成交可以差好遠 (09-12 單: 計劃 1.35 → 實際 0.51)。
    # 未建 exit legs 就發現 → 即刻市價平倉 (唔使 cancel 任何 order, 成本 = spread)。
    exit_side = "SELL" if side == "BUY" else "BUY"
    risk_fill = abs(fill_px - stop)
    rr_fill = (abs(tp1 - fill_px) / risk_fill) if (tp1 and risk_fill > 0) else None
    rec["rr_fill"] = round(rr_fill, 2) if rr_fill is not None else None
    if rr_fill is None or rr_fill < MIN_RR_EXEC:
        rec["status"] = "FLATTENED_LOW_FILL_RR"
        rec["flatten_note"] = (f"成交後 RR {rr_fill:.2f} < {MIN_RR_EXEC} "
                               f"(entry_fill={fill_px:.2f}, 計劃 RR {rr:.2f}) — 即刻市價平倉")
        try:
            _signed_request("POST", "/api/v3/order", {
                "symbol": SYMBOL, "side": exit_side, "type": "MARKET",
                "quantity": f"{fill_qty:.5f}",
            }, key, secret)
            rec["flatten_ok"] = True
        except urllib.error.HTTPError as e2:
            rec["flatten_ok"] = False
            rec["flatten_error"] = e2.read().decode()[:200]
        rec["flatten_ts"] = time.time()
        rec["status"] = "FLATTENED_OCO_FAILED"
        _log_upsert(rec)
        return rec, None

    return build_exit_legs(rec, key, secret, lot_step)


def _exit_cid(rec, tag):
    """deterministic clientOrderId —— 令 exit legs 落單冪等。

    2026-09-20 GLM 第四輪 BLOCKER 1 / 第五輪 LOW-5:
      - 冇冪等 id 嘅話, 「落咗單但 crash 喺 _log_upsert 之前」會令重試再落一套
        legs (重複 SELL)。
      - LOW-5: fallback "x" 會令兩筆都冇 order_id 嘅記錄撞同一 cid → 互相 reject。
        改為拋異常 (冇 id 就唔應該自動落 legs)。
      - LOW-5: tag 放**頭**, 避免 oid 異常長時截尾把 tag 切走 (A/B 撞 cid)。
    """
    oid = rec.get("order_id") or rec.get("oco_id")
    if not oid:
        raise ValueError("_exit_cid: 記錄冇 order_id/oco_id — 唔應該自動落 exit legs")
    cid = f"{tag}-EXIT-{oid}"
    if len(cid) > 36:
        raise ValueError(f"_exit_cid: clientOrderId 太長 ({len(cid)} > 36): {cid}")
    return cid


def build_exit_legs(rec, key, secret, lot_step):
    """已成交倉 (rec) → 建 3 段 exit: OCO_A(SL+TP1) / OCO_B(SL+TP2) / L3(尾倉 SL).

    由 place_signal_order 抽出, 因為 feat/limit-entry 之後限價單係「掛單 → 遲啲成交」,
    成交時已經係另一個 reconcile tick, 兩邊都要用同一套建 leg 邏輯。

    任一段建立失敗 → 取消已建 exit + market flatten (冇裸倉)。
    rec 需要: side / qty / planned_stop / planned_tp1 / planned_tp2 / pattern / atr
    """
    side = rec["side"]
    stop = rec["planned_stop"]
    tp1 = rec.get("planned_tp1")
    tp2 = rec.get("planned_tp2")
    fill_qty = rec["qty"]
    exit_side = "SELL" if side == "BUY" else "BUY"

    # ── 3 段出場 (1/3 each; TP2 冇就尾倉 2/3) ─────────────────────
    q1 = round_step(fill_qty / 3, lot_step)
    q2 = round_step(fill_qty / 3, lot_step) if tp2 else 0.0
    q3 = round_step(fill_qty - q1 - q2 + 1e-9, lot_step)   # +epsilon: float 精度唔好蝕尾數
    if q1 + q2 + q3 > fill_qty + 1e-12:      # 尾數保護 (float 誤差唔計, round 後唔可以 over-sell)
        q3 = round_step(fill_qty - q1 - q2, lot_step) - lot_step
        if q3 < 0:
            q3 = 0.0

    exit_orders = []   # (tag, ids) for cleanup
    leg_ids = []

    def _oco_qty(sl_qty, tp_price, tag="A"):
        if sl_qty <= 0:
            return None
        if exit_side == "SELL":
            return _signed_request("POST", "/api/v3/order/oco", {
                "symbol": SYMBOL, "side": "SELL",
                "quantity": f"{sl_qty:.5f}",
                "price": f"{tp_price:.2f}",              # TP 本價 (fix: 唔再 ×0.999 蝕 0.1%)
                "stopPrice": f"{stop:.2f}",
                "stopLimitPrice": f"{stop * 0.9985:.2f}",
                "stopLimitTimeInForce": "GTC",
                "listClientOrderId": _exit_cid(rec, tag),
            }, key, secret)
        return _signed_request("POST", "/api/v3/order/oco", {
            "symbol": SYMBOL, "side": "BUY",
            "quantity": f"{sl_qty:.5f}",
            "price": f"{tp_price:.2f}",
            "stopPrice": f"{stop:.2f}",
            "stopLimitPrice": f"{stop * 1.0015:.2f}",
            "stopLimitTimeInForce": "GTC",
            "listClientOrderId": _exit_cid(rec, tag),
        }, key, secret)

    def _sl_only_qty(sl_qty):
        if sl_qty <= 0:
            return None
        # clientOrderId 冪等 (2026-09-20 GLM 第四輪 BLOCKER 1):
        # 落咗單但 crash 喺 _log_upsert 之前 → 本地唔知。重試時同一 clientOrderId
        # 會被 exchange reject → 天然冪等, 唔會重複落 legs。
        cid = _exit_cid(rec, "L3")
        if exit_side == "SELL":
            return _signed_request("POST", "/api/v3/order", {
                "symbol": SYMBOL, "side": "SELL", "type": "STOP_LOSS_LIMIT",
                "quantity": f"{sl_qty:.5f}",
                "stopPrice": f"{stop:.2f}",
                "price": f"{stop * 0.9985:.2f}",
                "timeInForce": "GTC",
                "newClientOrderId": cid,
            }, key, secret)
        return _signed_request("POST", "/api/v3/order", {
            "symbol": SYMBOL, "side": "BUY", "type": "STOP_LOSS_LIMIT",
            "quantity": f"{sl_qty:.5f}",
            "stopPrice": f"{stop:.2f}",
            "price": f"{stop * 1.0015:.2f}",
            "timeInForce": "GTC",
            "newClientOrderId": cid,
        }, key, secret)

    # ── HIGH-2 (GLM 第五輪): duplicate-cid 要 adopt 而唔係當失敗 ─────────────
    # 若之前一次 attempt 已經落過 legs (EXIT-<oid>-* prefix), 重試時 duplicate
    # reject 會令整個 rebuild 當失敗 → 永久 retry loop, 而且舊 attempt 嘅 legs
    # 喺 exchange 上 untracked (OCO_B/L3 永遠補唔上 = 1/3 倉裸掛)。
    # 修: rebuild 前先查 openOrders 有冇同 prefix 嘅 legs, 有就直接 adopt。
    def _adopt_existing():
        try:
            opens_now = _signed_request("GET", "/api/v3/openOrders",
                                        {"symbol": SYMBOL}, key, secret)
        except Exception:                               # noqa: BLE001
            return False
        # prefix 要同 _exit_cid 一致: <tag>-EXIT-<oid>
        # OCO 落單用 listClientOrderId, 但 openOrders 回報嘅 leg 層欄位係
        # clientOrderId; 兩者都要查 (OCO list id 會出現喺 leg 嘅 clientOrderId 後綴
        # 或者獨立 list 查詢, 所以用 "EXIT-<oid>" 子串匹配最穩)。
        oid_s = str(rec.get("order_id") or rec.get("oco_id") or "")
        if not oid_s:
            return False
        needle = f"-EXIT-{oid_s}"
        got = []
        for o in opens_now:
            cid = str(o.get("clientOrderId") or "")
            lid = str(o.get("listClientOrderId") or "")
            if needle in cid or needle in lid:
                got.append(o.get("orderId"))
        if not got:
            return False
        rec["exit_leg_ids"] = got
        rec["adopted_existing_legs"] = True
        rec["status"] = "OCO_PLACED"
        rec["adopt_note"] = f"重試時 adopt 咗 {len(got)} 條已存在 exit legs (避免重複)"
        _log_upsert(rec)
        return True

    if _adopt_existing():
        return rec, None

    try:
        oco_a = _oco_qty(q1, tp1, "A") if tp1 else None
        if oco_a is not None:
            rec["oco_a_id"] = oco_a["orderListId"]
            ids_a = [o["orderId"] for o in oco_a.get("orders", [])]
            rec["oco_a_leg_ids"] = ids_a          # TP1 fill 偵測用 (breakeven trigger)
            leg_ids.extend(ids_a)
            exit_orders.append(("OCO_A", ids_a))
        oco_b = _oco_qty(q2, tp2, "B") if tp2 else None
        if oco_b is not None:
            rec["oco_b_id"] = oco_b["orderListId"]
            ids_b = [o["orderId"] for o in oco_b.get("orders", [])]
            leg_ids.extend(ids_b)
            exit_orders.append(("OCO_B", ids_b))
        l3 = _sl_only_qty(q3)
        if l3 is not None:
            rec["l3_id"] = l3["orderId"]
            leg_ids.append(l3["orderId"])
            exit_orders.append(("L3", [l3["orderId"]]))
        if not leg_ids:
            # exit 完全建唔成 (q1/q3 全 0 等) → 冇裸倉, 即 flatten (GLM review #B)
            raise RuntimeError("no exit legs built (q1/q3 both 0)")
    except (urllib.error.HTTPError, RuntimeError) as e:
        # exit 建立失敗 → 取消已建 exit order + market flatten (冇裸倉)
        for tag, ids in exit_orders:
            for oid in ids:
                try:
                    _signed_request("DELETE", "/api/v3/order",
                                    {"symbol": SYMBOL, "orderId": oid}, key, secret)
                except Exception:
                    pass
        try:
            _signed_request("POST", "/api/v3/order", {
                "symbol": SYMBOL, "side": exit_side, "type": "MARKET",
                "quantity": f"{fill_qty:.5f}",
            }, key, secret)
            rec["status"] = "FLATTENED_OCO_FAILED"
            rec["flatten_note"] = f"exit order 建立失敗 ({type(e).__name__}) → emergency market close"
        except urllib.error.HTTPError as e2:
            rec["flatten_error"] = e2.read().decode()[:200]
        rec["oco_error"] = (e.read().decode()[:200] if isinstance(e, urllib.error.HTTPError)
                            else str(e)[:200])
        rec["flatten_ts"] = time.time()

    if leg_ids and rec.get("status") != "FLATTENED_OCO_FAILED":
        rec["exit_leg_ids"] = leg_ids
        rec["status"] = "OCO_PLACED"
    elif not leg_ids and rec.get("status") in ("FILLED_ENTRY", "LIMIT_FILLED"):
        rec["status"] = "OCO_FAILED"
        rec["oco_error"] = "no exit legs built"

    _log_upsert(rec)
    return rec, None


def reconcile(key, secret):
    """對帳: 開放 OCO 有冇成交 → 更新本地 log + 統計."""
    log = load_log()
    open_orders = _signed_request("GET", "/api/v3/openOrders", {"symbol": SYMBOL}, key, secret)
    open_ids = {o["orderId"] for o in open_orders}
    changed = 0
    for rec in log["orders"]:
        if rec.get("status") != "OCO_PLACED":
            continue
        if rec.get("oco_id") is None:
            continue
        # OCO 兩腿 — 攞 openOrders 睇剩邊條
        pass
    # 簡化對帳: 用 myTrades 對成交
    trades = _signed_request("GET", "/api/v3/myTrades", {"symbol": SYMBOL}, key, secret)
    log["trades_raw"] = trades[-50:]
    save_log(log)
    return open_orders, trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--cancel-all", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只顯示會落咩單, 唔真落")
    args = ap.parse_args()

    key, secret = _load_keys()
    if not key or not secret:
        print("❌ 未設定 BINANCE_TESTNET_API_KEY/SECRET")
        print(f"   1. 去 https://testnet.binance.vision (GitHub 授權登入)")
        print(f"   2. Generate key pair")
        print(f"   3. mkdir -p ~/.hermes/secrets && cat > ~/.hermes/secrets/binance_testnet.env")
        print(f"      BINANCE_TESTNET_API_KEY=...")
        print(f"      BINANCE_TESTNET_API_SECRET=...")
        sys.exit(1)

    if args.status:
        acct = account_status(key, secret)
        balances = {b["asset"]: b["free"] for b in acct["balances"] if float(b["free"]) > 0}
        print("💰 Testnet balances:", balances)
        opens = _signed_request("GET", "/api/v3/openOrders", {"symbol": SYMBOL}, key, secret)
        print(f"📋 Open orders ({len(opens)}):")
        for o in opens:
            print(f"   {o['symbol']} {o['side']} {o['type']} qty={o['origQty']} px={o.get('price','-')} stop={o.get('stopPrice','-')}")
        return

    if args.cancel_all:
        _signed_request("DELETE", "/api/v3/openOrders", {"symbol": SYMBOL}, key, secret)
        print("🧹 All open orders cancelled")
        return

    if args.reconcile:
        opens, trades = reconcile(key, secret)
        print(f"📋 open: {len(opens)} | trades: {len(trades)}")
        for t in trades[-5:]:
            print(f"   {t['time']} {t['qty']}@{t['price']} commission={t['commission']} {t.get('isBuyer')}")
        return

    # 主流程: 引擎訊號 → 落單
    json_path = os.path.join(REPO, "btc_last_analysis.json")
    if not os.path.exists(json_path):
        print("⚠️ 冇 btc_last_analysis.json — 先跑 python3 btc_engine.py")
        sys.exit(1)
    with open(json_path) as f:
        data = json.load(f)
    setups = [s for s in data.get("setups", []) if s.get("verified")]
    if not setups:
        print("⏳ 冇 verified setups")
        return
    log = load_log()
    # 同 pattern dedup (即時更新: 落一單入一單, 5min cron 唔會重複)
    def _live_patterns():
        return {o["pattern"] for o in load_log()["orders"]
                if o.get("status") in ("FILLED_ENTRY", "OCO_PLACED", "OCO_FAILED", "LIMIT_PENDING")}

    todo = [s for s in setups if s.get("pattern") not in _live_patterns()]
    # C: 平倉/flatten 後同 pattern 60 分鐘冷靜期 — 防 churn (平完即刻重入, 每次俾費用)
    now = datetime.now(timezone.utc)
    cooled_out = []
    for s in todo:
        blocked = False
        for o in log["orders"]:
            if o.get("pattern") != s.get("pattern"):
                continue
            ts_raw = o.get("closed_time") or o.get("seeded_time") or ""
            if o.get("status") in ("SKIP_PREFLIGHT",) and o.get("closed_time") is None:
                continue  # skip 記憶由 preflight block 處理, 呢度只管平倉/失敗
            if o.get("status") not in ("CLOSED", "FLATTENED_OCO_FAILED", "OCO_FAILED", "SKIP_PREFLIGHT"):
                continue
            try:
                ts = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            age_min = (now - ts).total_seconds() / 60
            if age_min < 60:
                blocked = True
                break
        if blocked:
            print(f"🧊 {s.get('pattern','?')} skip — 平倉/失敗後冷靜期 (60 分鐘)")
        else:
            cooled_out.append(s)
    todo = cooled_out
    if not todo:
        print("⏳ 全部 setups 已落單")
        return

    # Flatten 冷靜期: 同 pattern 15 分鐘內 OCO 失敗過 → 冇意義即刻再入 (條件冇變)
    now = time.time()
    cooled = []
    for s in todo:
        recent_fail = False
        for o in log["orders"]:
            if o.get("status") not in ("FLATTENED_OCO_FAILED", "OCO_FAILED"):
                continue
            if o.get("pattern") != s.get("pattern"):
                continue
            ts = o.get("flatten_ts")
            if isinstance(ts, (int, float)) and now - ts < 900:
                recent_fail = True
                break
        if recent_fail:
            print(f"🧊 {s.get('pattern','?')} skip — OCO 失敗冷靜期 (15 分鐘)")
        else:
            cooled.append(s)
    todo = cooled
    if not todo:
        return
    # 風控 (fix/btc-exit-symmetry, 2026-09-20 改 fail-closed):
    #   同 pattern 限 1 單 + 同方向限 1 單 + 相反方向鎖。
    #   live 判定改用 is_live_rec() (唔係白名單) —— FLATTENED_OCO_FAILED / OCO_FAILED
    #   呢啲「可能仲喺市場」嘅狀態以前被當成冇倉, 令 guard 失效。
    #   每日 -MAX_DAILY_LOSS_R 硬煞停: 用當日已實現 R 計 (封頂後唔再開新倉)。
    day_tot, day_n = daily_realized_r(log)
    if daily_loss_brake(log)[0]:
        print(f"🛑 當日已實現 {day_tot:+.2f}R ({day_n} 單) ≤ -{MAX_DAILY_LOSS_R}R "
              f"— 每日虧損硬煞停, 今日唔再開新倉")
        return
    for s in todo:
        # guard 邏輯抽咗去 guards_allow() —— 令 test 可以 call 生產嘅同一份邏輯。
        # 可用 BTC_SAME_DIR_MAX 覆蓋同向上限 (預設 1)。
        allow, why = guards_allow(load_log(), s, same_dir_max=SAME_DIR_MAX)
        if not allow:
            print(f"🚫 {s.get('pattern','?')} skip — {why}")
            continue
        # RR hard gate — 落單前最後防線 (歷史單 RR<1.2 全部贏細輸大); 冇 TP1 一律拒
        rr = _compute_rr(s)
        if rr is None or rr < MIN_RR_EXEC:
            rr_txt = "n/a(冇TP1)" if rr is None else f"{rr:.2f}"
            log2 = load_log()
            log2["orders"].append({
                "pattern": s.get("pattern", "?"), "side": s.get("btc_side"),
                "status": "SKIP_PREFLIGHT", "skip_reason": f"RR {rr_txt} < {MIN_RR_EXEC} hard gate",
                "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            save_log(log2)
            print(f"❌ {s.get('pattern','?')}: RR {rr_txt} < {MIN_RR_EXEC} hard gate — skip")
            continue
        if args.dry_run:
            _lp = s.get("btc_limit_px") or s["btc_entry"]
            print(f"[DRY] {s['btc_side']} {s.get('pattern','?')} "
                  f"{'掛LIMIT @ ' + format(_lp, ',.2f') if ENTRY_MODE == 'limit' else '市價'} "
                  f"(zone 下緣 {s['btc_entry']}) SL={s['btc_stop']} TP1={s.get('btc_tp1')} "
                  f"TP2={s.get('btc_tp2')} RR={rr}")
            continue
        rec, err = place_signal_order(s, key, secret, atr=data.get("atr"))
        if err:
            # 記入 log (SKIP_PREFLIGHT) — 下次 tick 見到同 pattern 已 skip 就靜默, 唔會重複 ❌
            log2 = load_log()
            log2["orders"].append({
                "pattern": s.get("pattern", "?"), "side": s.get("btc_side"),
                "status": "SKIP_PREFLIGHT", "skip_reason": err,
                "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            save_log(log2)
            print(f"❌ {s.get('pattern','?')}: {err}")
        else:
            if rec.get("status") == "FLATTENED_LOW_FILL_RR":
                print(f"⚠️ {rec['side']} {rec['pattern']} 成交後 RR {rec.get('rr_fill')} < "
                      f"{MIN_RR_EXEC} → 即刻平倉 (fill={rec['entry_fill']}, 計劃 RR {rec.get('rr_planned')})")
            elif rec.get("status") == "LIMIT_PENDING":
                print(f"📌 {rec['side']} {rec['pattern']} 掛 LIMIT @ {rec['limit_px']:,.2f} "
                      f"(市價 {rec['px_at_place']:,.0f}, RR {rec['rr_limit']}) — 等成交")
            else:
                print(f"✅ {rec['side']} {rec['pattern']} fill={rec['entry_fill']} qty={rec['qty']} status={rec['status']} ocoA={rec.get('oco_a_id','-')} ocoB={rec.get('oco_b_id','-')} l3={rec.get('l3_id','-')}")


if __name__ == "__main__":
    main()
