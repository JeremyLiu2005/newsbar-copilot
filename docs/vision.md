# 低信心標題交給雲端視覺模型重讀（實驗，預設關閉）

這是評審可以自己接上雲端模型的入口：用自己的 API 金鑰，把「文字辨識（OCR）信心低的標題截圖」送去重讀，和 OCR 的結果並排比對。**`newsbar run` 不會呼叫它，也不會改動 Excel。**

## 一定遵守的規則

- **預設不送任何東西。** 沒加 `--send` 只列出「會送什麼」（幾段、共多少 KB）。
- **只送主標題那一條的裁切圖（小 JPEG）。** 不送影片、不送聲音、不送整張畫面，也不把 OCR 讀到的字一起送出去。
- **金鑰由執行的人自己放在環境變數。** 程式不附金鑰，也不從任何檔案讀金鑰。
- **模型代號由環境變數指定，** 程式不寫死，避免寫了過期的代號。

## 怎麼用

1. 先用 `newsbar run` 跑出工作資料夾（要有各台的 `obs.jsonl` 和 `frames/main_bar/` 截圖）。
2. 乾跑，看會送多少：

```bash
uv run newsbar vision-check --workdir data/work_final
```

3. 不連網，只驗證流程（`--mode mock`，結果會和 OCR 一模一樣）：

```bash
uv run newsbar vision-check --workdir data/work_final --mode mock --send --limit 5 --out out/vision_mock.jsonl
```

4. 用自己的金鑰真的送出（先小量）：

```bash
export NEWSBAR_VISION_API_KEY="你的金鑰"        # 也可用 ANTHROPIC_API_KEY
export NEWSBAR_VISION_MODEL="供應商文件上的模型代號"
uv run newsbar vision-check --workdir data/work_final --send --limit 10 --out out/vision_check.jsonl
```

目前只實作 Anthropic 的 Messages API（`--mode anthropic`）。要接別家，在 `src/newsbar/vision.py` 照 `AnthropicVision` 的形狀加一個 `read_title()` 即可；`NEWSBAR_VISION_BASE_URL` 可指到相容的代理位址。

## 輸出怎麼看

`out/vision_check.jsonl` 每行一段穩定標題（同一個字串連續 7 秒以上、平均信心低於 0.6）：

| 欄位 | 意思 |
|---|---|
| `ocr`／`model` | OCR 讀到的字／模型讀到的字 |
| `verdict` | `same`（字元重疊 ≥95%）、`close`（≥70%）、`differs`、`unreadable`、`error` |
| `overlap` | 字元重疊比例（不看順序） |

`differs` 只代表「和 OCR 不一樣」，**不代表模型比較對**；要看截圖才知道誰對。

## 限制

- 這個功能**還沒有量過準確度提升**，也沒有估算費用；先用 `--limit` 小量試。
- 低信心的判斷用的是 Apple Vision 的信心值，它只有 0.3、0.5、1.0 三級，所以「低於 0.6」不等於讀錯。
- 重讀結果目前不會寫回 Excel；要不要採用，是比對之後的決定。
- 錯誤訊息只記錄 HTTP 狀態碼或錯誤類型，不含金鑰或請求內容。
