#!/usr/bin/env python3
"""btc_pause.py — BTC **主系統**停用標記 (single source of truth)

─────────────── 呢個 file 係做乜 ───────────────
「主系統 (Flag pattern) 已停用」呢個決定, 以前只係一紙文檔
(`~/.hermes/reports/btc_main_system_paused.txt`) —— 冇任何 code 讀佢。
結果係: 如果將來有一個 setup 過到 gate, 系統**照樣會落單**。
「0 單」純粹係 gate 嘅副作用, 唔係設計出嚟嘅停用。

本 module 將嗰張紙變成**硬閘**: 落單路徑開跑之前先問呢度。

─────────────── 語義 (重要, 唔好簡化成「全部唔跑」) ───────────────
停用 = **唔開新倉**, 唔係「乜都唔做」。

| 動作 | 停用期間 |
|---|---|
| `reconcile_cycle()` — 管理/平掉**現有**倉 (exit legs / trailing / breakeven) | ✅ **照跑** |
| `btc_engine.py` 掃描 + `binance_testnet_paper.py` 落**新**單 | ⛔ 跳過 |

點解 reconcile 一定要照跑: 佢係唯一會幫已開倉掛 exit leg / 推 trailing stop
嘅路徑。停用期間唔跑 = 一旦有倉未平就會**冇人管**, 風險比唔停用更大。
(2026-10-01: 當時 0 live 倉, 所以今日兩者等價; 但唔可以因此寫成「全部唔跑」。)

⚠️ 「reconcile 只會減倉」係上面論證嘅地基, 所以有核實, 唔係假設
(2026-10-01 GLM review 質疑過)。逐行查 `build_exit_legs()` 嘅證據:
  - `exit_side = "SELL" if side == "BUY" else "BUY"` → 永遠係入場方向嘅**相反**
  - `q1 + q2 + q3 == fill_qty`（有 over-sell 保護: round 後唔可以多過 fill_qty）
  - 另一個路徑用 `allow_flatten=False`（更保守）
  ⇒ 落單量恰好等於記錄倉位、方向相反 → 結構上 reduce-only。
**殘餘風險（未修）**: 如果 `rec["side"]` 唔係 "BUY"/"SELL"（例如 None / "LONG"），
`exit_side` 會 fallback 成 "BUY" → 理論上可以加倉。屬既有資料品質風險
(同 PR#10 個 `side` 符號反轉同源), 未加 reduce-only assertion。

⚠️ **「讀唔到標記」(uncertain) 都一樣照跑 reconcile。**
2026-10-01 GLM review 捉到原本設計方向反咗: 原本 uncertain 連 reconcile 都跳,
但「讀唔到 marker」同「知唔知有冇倉」完全無關 —— 佢唔會令 reconcile 變得唔安全
(reconcile 唔開新倉)。停用唔確定 ≠ 管理現有倉唔安全。
所以 uncertain = **同「已停用」一樣嘅行為, 只係大聲出聲**。

─────────────── Fail-safe 方向 ───────────────
- 標記載明「已停用」→ 停落單 (paused=True, certain=True) → 常規訊息, 靜默
- 標記唔存在 → 照跑 (paused=False, certain=True)
- **讀取標記出錯 (OSError) → 停落單 + 大聲講** (paused=True, certain=False)

最後一項係刻意嘅: 呢個 marker 係「opt-out」檔。讀唔到 = 唔知有冇被停用。
喺唔知嘅情況下**開新倉**係風險行為 → 所以 block 落單, 而且 `certain=False`
會令呼叫者**出聲** (唔可以靜靜地永遠唔交易)。

⚠️ fail-closed 只 apply 喺**有風險嘅動作 (開新倉)** 度, 唔係一刀切乜都唔做。

─────────────── 靜默 vs 出聲 (唔好改 emoji) ───────────────
`⏸️` (常規停用) **唔喺** cron wrapper 嘅 `NOTABLE_KEYS` → 每 15 分鐘 tick 靜默;
`⚠️` (唔確定) **喺** → 推 Telegram。
兩條不變式由 `test_btc_pause_gate.py` 釘住 —— 調亂 = 半夜 spam 或者靜默事故。

⚠️ 因此 `check()` **刻意唔會**將 marker 檔內容注入訊息。marker 第一行係人手
自由文本 (例如有人寫「❌ 唔好再開倉」), 一旦注入就會經 substring 過濾推 TG,
令 anti-spam 設計靜靜雞失效。訊息只帶固定文字 + 路徑, 詳情叫人去睇 marker 檔。

─────────────── 用法 ───────────────
    from btc_pause import check
    paused, reason, certain = check()
    if not certain:              # 唔確定 → 一定要出聲 (⚠️ 會經 wrapper 推 TG)
        log(reason)
    ...
    block_trading = paused or not certain    # fail-closed 寫明, 唔好靠隱含 invariant
    ...
    if paused:
        log(reason)              # 常規停用 → ⏸️ 靜默, 唔使每次推 TG
        return

CLI (運維用):
    python3 btc_pause.py            # 印狀態 + exit code (0=照跑, 10=已停用)
    BTC_PAUSE_MARKER=/tmp/x python3 btc_pause.py
"""
import os
import sys

# ⚠️ 呢個路徑係 single source of truth。其他 module (cron/btc_dual_report.py 等)
#    嘅同類常數要靠 test_btc_pause_gate.py 嘅相等斷言綁住 —— 改咗呢度,
#    冇同步改其他就會測試 FAIL, 唔會靜默漂移。
_DEFAULT_MARKER = "~/.hermes/reports/btc_main_system_paused.txt"

# ⚠️ `BTC_PAUSE_MARKER` env 可以覆寫路徑 (測試 / 第二部機)。
#    注意 `cron/btc_dual_report.py` 嘅 `PAUSED_MARKER` 係**寫死**同一個預設值,
#    **唔** honor 呢個 env —— 如果真係要用 env 改路徑, 報告嗰邊要同步改,
#    否則報告嘅「標記存在」分支同 gate 結果可以講兩回事。測試只綁默認值相等。

# 喺 import 時解析 (env override 方便測試 / 第二部機)。
# 函數唔會 capture 呢個值做 default arg —— 咁樣 monkeypatch MARKER_PATH 才生效。
MARKER_PATH = os.path.expanduser(os.environ.get("BTC_PAUSE_MARKER") or _DEFAULT_MARKER)

# 停用時嘅 canonical 訊息前綴。
# ⚠️ 唔係 "⚠️"/"❌" —— 常規停用會**每 15 分鐘**出現一次,
#    落 cron wrapper 嘅 NOTABLE_KEYS 過濾會變 TG spam。
#    "⏸️" 唔喺 NOTABLE_KEYS → 靜默 (狀態由 btc_dual_report 每日 4 次報)。
#    反過來「唔確定」係異常 → 用 "⚠️" 出聲。
PAUSED_EMOJI = "⏸️"
UNCERTAIN_EMOJI = "⚠️"


def check(marker=None):
    """檢查主系統有冇被停用。

    回 (paused: bool, reason: str, certain: bool)
      - (False, "", True)              → 冇停用, 照跑
      - (True,  "<msg>", True)         → 標記載明停用
      - (True,  "<msg>", False)        → 讀唔到標記, 唔知 → fail-safe 當停用
    """
    path = marker if marker is not None else MARKER_PATH
    try:
        st = os.stat(path)          # ⚠️ 唔用 os.path.exists —— 佢會吞 OSError
    except FileNotFoundError:
        return False, "", True
    except OSError as e:
        return (True,
                f"{UNCERTAIN_EMOJI} 讀唔到停用標記 ({path}): "
                f"{type(e).__name__}: {e} — 無法確認, 為安全起見當停用",
                False)

    # 標記存在 → 停用。
    # ⚠️ 刻意**唔**注入 marker 檔內容: 「靜默/出聲」係靠 log line substring
    #    過濾 (wrapper NOTABLE_KEYS)。marker 第一行係人手自由文本, 有人寫
    #    「❌ 唔好再開倉」就會經嗰個 filter 每 15 分鐘推 TG —— anti-spam
    #    設計靜靜雞失效。而且咁樣 sanitize 都唔夠 ("FLATTENED" 係普通 \w 字)。
    #    所以訊息只帶固定文字 + 路徑。
    return (True,
            f"{PAUSED_EMOJI} 主系統已停用標記中（{path}）— 跳過開新倉",
            True)


def is_paused(marker=None):
    """只想要 bool 嘅方便版 (唔理 certain)。"""
    return check(marker)[0]


def main():
    paused, reason, certain = check()
    if not paused:
        print(f"✅ 主系統冇停用標記 ({MARKER_PATH}) — 落單路徑開通")
        return 0
    print(reason)
    if not certain:
        print(f"   {UNCERTAIN_EMOJI} 呢個係 fail-safe 判斷 (唔係讀到標記) — 請人手查")
    print(f"   標記檔: {MARKER_PATH}")
    return 10                       # 用 10 而唔係 1: 同「crash」分開


if __name__ == "__main__":
    sys.exit(main())
