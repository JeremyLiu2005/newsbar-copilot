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
uv sync --python 3.11
uv run pytest            # 規格 §11 的 T01–T24 全部有對應測試
uv run newsbar write-sheet --events events.jsonl --template 範本.xlsx --out out/12監看_20261106.xlsx
```

已實作：從每秒觀測值（`SecondObs`）到 Excel 的整段決策邏輯——廣告狀態機、換題判定、五字標校驗與 fallback、秒轉分鐘、寫表、證據包。
未實作：影片抽取端（OCR、ASR、畫面訊號），以及 LLM 產生器（目前以規則擷取代替）。

影片與中間產物放在 `data/`，不進版本控制。
