# NewsBar Copilot

新聞監看全自動填表：輸入四個競台的側錄影片與「12監看」範本，自動判斷換題與廣告、產生五字標，寫回 Excel。

- 需求分析（Contextual Design）：[docs/requirements.md](docs/requirements.md)
- 功能規格（痛點 → 規則 → 測試案例）：[docs/spec.md](docs/spec.md)
- 系統架構：[docs/architecture.md](docs/architecture.md)
- 狀態：設計稿（需求 v0.1、規格 v0.1、架構 v0.2），尚未實作

## 核心流程

```
側錄影片 → 抽框／抽音軌 → 主 bar OCR ＋ 語音轉錄 ＋ 畫面訊號
        → 廣告狀態機 → 換題引擎 → 五字標（受限 LLM＋校驗）
        → 秒轉分鐘格 → 寫回 12監看.xlsx
```

## 設計原則

1. 先判廣告、再判換題，包框殘留的 bar 文字不採用
2. 判斷用可解釋的規則，LLM 只負責命名且必須通過來源校驗
3. 每一格都由系統決定，不停下來等人工
4. 只動四個競台的五字標欄，其他欄位與格式不變

## 開發

```bash
uv sync --python 3.11 --extra mac   # mac = Apple Vision OCR
uv run pytest                        # 104 個測試：規格 T01–T24、合成影片 OCR／包框／平行端到端

# 1. 檢查 ROI（可順便存包框樣板）
uv run newsbar calibrate --video data/tvbs.mp4 --channel tvbs --channels config/channels.yaml --at 30
# 2. 一鍵：四台影片 → 監看表、依據、處理報告、抽查頁
uv run newsbar run --template 範本.xlsx --out out/12監看_20261106.xlsx \
  --video tvbs=data/tvbs.mp4@11:59:50 --video ettv=data/ettv.mp4@11:59:50 \
  --video ftv=data/ftv.mp4@11:59:50 --video cts=data/cts.mp4@11:59:50 \
  --channels config/channels.yaml --rules config/rules.yaml --jobs 4
# 3. 與標準答案比對（前 20 分鐘留給調參）
uv run newsbar eval --system out/12監看_20261106.xlsx --gold gold.xlsx --test-from 1220
# 4. 抽查修正 → 另存修正版（原檔不動）
uv run newsbar apply-corrections --sheet out/12監看_20261106.xlsx --corrections corrections.json \
  --template 範本.xlsx --out out/12監看_20261106.修正版.xlsx
```

全天自動：`newsbar watch --inbox 側錄資料夾 --out-dir out --template 範本.xlsx --keep-frames-days 7`（檔名 `{台別}_{YYYYMMDD}_{HHMM}.mp4`）。

產出：`*.xlsx`（監看表）、`*.evidence.jsonl`（每格依據）、`*.report.csv`（抽查清單）、`*.report.json`（處理報告）、`*.review.html`（抽查頁）。

選配：`uv add mlx-whisper`（語音轉錄，`--asr mlx`）、`ollama pull qwen2.5:7b-instruct`（地端 LLM，rules `labeler.generator: ollama`）。

實測（M4、合成 720p 影片）：Apple Vision 每張 ROI 約 45 ms；一台一小時只讀主 bar 約 3 分鐘；ROI 截圖每台每小時約 60 MB。

實作狀態見 [architecture.md §12](docs/architecture.md#12-實作狀態)，需求逐條狀態見 [requirements.md §6](docs/requirements.md#6-需求清單)。

影片與中間產物放在 `data/`，不進版本控制。
