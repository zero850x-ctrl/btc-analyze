# btc-analyze ₿

BTC 形態交易系統 — 由 [xauusd-analyze-v3](https://github.com/zero850x-ctrl/xauusd-analyze-v3)（黃金版）BTC 適配而成。

## 架構

| 檔案 | 角色 |
|------|------|
| `analyze_v3.py` | 形態引擎核心（vendor 自黃金版，零改動）：Double Top/Bottom、Bull/Bear Flag、Triangles、Rising/Falling Wedge、Channel、swing 檢測、ATR/RSI |
| `paper_trade.py` | staged-exit 模擬核心（vendor，零改動）：TP1/TP2 分批、1.5×ATR trailing、bar-by-bar SL/TP 判定 |
| `btc_engine.py` | **BTC 適配層**：數據源（TradingView BITSTAMP:BTCUSD → yfinance BTC-USD fallback）、Coinbase 價差驗證（>0.8% → UNVERIFIED）、24/7 無 session gates、USD % 風險 sizing、SL floor 0.8×ATR |
| `paper_trade_btc.py` | BTC paper trading：`~/.hermes/reports/paper_trade_log_btc.json`（與黃金 log 分離）、anti-stacking、dedup、daily loss limit |
| `backtest_btc.py` | 凍結窗口 backtest：M30 bars 逐 24h 取樣 → 引擎訊號 → staged-exit 模擬（含 0.1% 成本） |

## 與黃金版嘅主要差異

- **24/7**：冇 golden/danger hours — BTC 週末照開
- **數據源**：BITSTAMP 主源＋Coinbase 即時價 guard（代替黃金 GC=F basis check）
- **風險 sizing**：USD 百分比制（預設 0.5%/筆）— 冇 lot 概念
- **波動**：BTC 日內波幅 ~3.7%（黃金 1.1%）— 所有 ATR 參數自動縮放，但勝率結構唔同

## Backtest（2026-08-29，60 日 M30，68 樣本，含成本）

| 組別 | 勝率 | 平均 R |
|------|------|--------|
| 總體 | 47.1% | +0.266R |
| Bull Flag (BUY) | 59.1% | +0.587R |
| Bear Flag (SELL) | 44.4% | +0.662R |
| Triangles / Double Top | — | 負貢獻（校準中） |

⚠️ 樣本全部喺熊市 regime；黃金版 138-sample 勝率表唔適用 BTC。

## 用法

```bash
# 即時分析（TV BITSTAMP M30 500 bars → setups → btc_last_analysis.json）
python3 btc_engine.py

# paper trade：check LIVE + seed 新單
python3 paper_trade_btc.py
python3 paper_trade_btc.py --check-only   # 只 check
python3 paper_trade_btc.py --seed-only    # 只 seed
python3 paper_trade_btc.py --reset        # 清空 log

# backtest（60 日）
python3 backtest_btc.py
```

依賴：`yfinance pandas numpy requests tvDatafeed`（tvDatafeed 冇都行，自動 fallback yfinance）

## Status

- 🔬 **Paper trading 驗證中** — 未實盤，未接 cron
- 引擎與黃金版同步策略：黃金版引擎大改時手動 vendor 過嚟再 review
