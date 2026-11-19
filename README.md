# NewsBar Copilot

新聞監看全自動填表：輸入四個競台的側錄影片與「12監看」範本，自動判斷換題與廣告、產生五字標，寫回 Excel。

- 需求分析（Contextual Design）：[docs/requirements.md](docs/requirements.md)
- 功能規格（痛點 → 規則 → 測試案例）：[docs/spec.md](docs/spec.md)
- 系統架構：[docs/architecture.md](docs/architecture.md)
- 要問業師的問題：[docs/mentor-questions.md](docs/mentor-questions.md)

## 系統狀態

後端核心流程已用 11/06 四台一小時真實側錄驗證：主 bar OCR、換題判定、廣告狀態機、五字標生成與校驗、寫回「12監看」Excel。執行方式是命令列 `newsbar run`（地端，Mac M4）。

前端兩個網頁，用途不同：

- **監看台網頁**：後端實跑結果的瀏覽器，資料全是真實輸出（[demo/index.html](demo/index.html)）。
- **監看員網頁 `newsbar serve`（已串接後端）**：在本機開 http://127.0.0.1:8765 ，選四台側錄 → 開始分析（真的執行 `newsbar run`）→ 即時看各台進度 → 看結果 → 下載 Excel、開抽查頁。只綁本機，影片與表格不離開這台電腦。
- **操作原型**：同一流程的靜態展示版，給沒有後端環境的人點著看，**未串接後端**（[demo/prototype.html](demo/prototype.html)，線上版 https://claude.ai/artifact/RL2VhX96ci22nN5znaiBSK ）。

```bash
uv run newsbar serve --channels config/channels.1106.yaml --rules config/rules.1106.yaml \
  --rules-fast config/rules.1106.fast.yaml --workdir data/work_full
```

設計上系統跑在三立內部，側錄影片與監看資料不出內網。

## 成果（2026/11/06 12:00–13:00，四台實跑）

| 指標 | 目標 | 實測 |
|---|---|---|
| 換題起始分鐘 F1（容許 ±1 分鐘，TVBS 對照研究者初標） | ≥0.85 | 0.885（未調參測試段 0.867） |
| 廣告分鐘 F1 | ≥0.90 | 0.95 |
| 自動填寫率 | 100% | 100% |
| 四台一小時處理時間（Mac M4） | ≤15 分鐘 | 46 分鐘（瓶頸：語音轉錄一次只能一台） |

- 監看台網頁（每格判斷依據、主 bar 截圖、廣告時間軸、TVBS 對照）：https://claude.ai/artifact/8P4J7qDJsnfJyGeUFKCcJ2
- Excel 成品：Google 試算表「12監看_20261106」（需權限）
- 標準答案目前是研究者初標，正式雙人標註尚未完成

## 影片從哪裡來

四台側錄影片（民視、TVBS、東森、華視）由課程提供，版權屬各電視台，**不放進這個 repo**。
要重跑請向課程取得影片，放到 `data/raw/{tvbs,ettv,ftv,cts}.ts`，再照下面「開發」的指令執行。
沒有影片也能跑 `uv run pytest`：測試用的是程式合成的新聞畫面，不需要真實側錄。

執行環境：macOS（Apple Vision OCR）、Python 3.11、FFmpeg、Ollama（`qwen2.5:7b-instruct`）。

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
uv run pytest                        # 132 個測試：規格 T01–T24、合成影片 OCR／包框／平行端到端

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
