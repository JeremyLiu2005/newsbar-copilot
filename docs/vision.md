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

目前有兩種接法：

| `--mode` | 介面 | 金鑰環境變數 |
|---|---|---|
| `anthropic` | Anthropic Messages API（強制工具輸出） | `NEWSBAR_VISION_API_KEY` 或 `ANTHROPIC_API_KEY` |
| `nvidia` | NVIDIA 託管模型（OpenAI 相容的 chat completions，回 JSON） | `NEWSBAR_VISION_API_KEY` 或 `NVIDIA_API_KEY` |

NVIDIA 的模型代號用 `GET https://integrate.api.nvidia.com/v1/models` 列出；不是每個模型都能看圖，也不是每個列出的模型都能呼叫。要接別家，在 `src/newsbar/vision.py` 照 `read_title()` 的形狀加一個類別即可；`NEWSBAR_VISION_BASE_URL` 可指到相容的位址。

## 送哪一張截圖

每段標題的文字是前後 5 秒多數決的結果，所以某一秒可能「有標題」、但那一秒的畫面其實沒有標題條（主播走位、地圖、轉場）。有 `ocr_raw.jsonl`（每秒未投票的 OCR 讀數）時，程式會挑 OCR 當下真的讀到字的那一秒的截圖：同字優先，其次最接近、信心最高、最靠中間。整段都沒讀到字的標題不送，另外計數。

失敗處理：模型回空白或伺服器 5xx 時重試一次；4xx（金鑰錯、模型不存在、超過額度）不重試。

## 一次小實驗（2026-11-23，僅供參考）

資料：11/06 四台一小時的最新重跑；模型 `nvidia/nemotron-3.5-super-vl-120b-a12b`；每次抽 40 段（四台各 10 段）。

| | 挑中間那張 | 挑 OCR 當下讀到字的那張 |
|---|---|---|
| 和 OCR 幾乎一樣（same） | 4 | 8 |
| 接近（close，多半只差標點與空格） | 23 | 24 |
| 差很多（differs） | 1 | 1 |
| 模型說讀不到（unreadable） | 10 | 2 |
| 錯誤（空白答案、HTTP 502） | 2 | 5 |

- 兩次的 40 段有 33 段相同，不是完全相同的樣本。第二次多了 5 個錯誤，當時還沒有重試機制。
- 剩下的 2 段「讀不到」和 1 段「差很多」，OCR 讀到的是台標、注意事項或廣告字，不是新聞標題，模型的判斷是對的。
- 模型能修正明顯的錯字（如「數子」→「數字」、「專冢澬金」→「專家:資金」），但也會讀錯（「張俊傑」讀成「張傑傑」），所以只能當重讀參考，不能直接取代。
- 沒有人工標準答案，以上只是兩種讀法的比較，不是準確率。

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
