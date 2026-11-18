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
uv run pytest                        # 規格 §11 的 T01–T24 全部有對應測試，另含合成影片 OCR 端到端測試

# 1. 檢查 ROI：取第 30 秒畫面，印出每個區域 OCR 讀到的字
uv run newsbar calibrate --video data/tvbs.mp4 --channel tvbs --channels config/channels.yaml
# 2. 影片 → 主 bar 文字流（--clock = 影片第 0 秒的實際時刻）
uv run newsbar ocr --video data/tvbs.mp4 --channel tvbs --channels config/channels.yaml --clock 11:59:50
# 3. 事件 → 12監看 xlsx
uv run newsbar write-sheet --events events.jsonl --template 範本.xlsx --out out/12監看_20261106.xlsx
```

實測（M4、合成 720p 影片）：Apple Vision 每張 ROI 約 45 ms；一小時一台只讀主 bar 約 3 分鐘，四台循序約 11 分鐘；ROI 截圖每台每小時約 60 MB。

已實作：從每秒觀測值（`SecondObs`）到 Excel 的整段決策邏輯——廣告狀態機、換題判定、五字標校驗與 fallback、秒轉分鐘、寫表、證據包。
已實作抽取端：ingest（ffprobe、校時、每秒裁 ROI）、OCR（Apple Vision／PaddleOCR 介面、5 秒投票、雜訊吸附、跑馬燈防呆）。
未實作：ASR、畫面訊號（版面、台徽、黑場）、音訊指紋，以及 LLM 產生器（目前以規則擷取代替）。

影片與中間產物放在 `data/`，不進版本控制。
