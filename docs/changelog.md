# NewsBar Copilot 變更紀錄（逐 commit）

本文件逐筆記錄 `git log --all` 的 81 個 commit：每筆說明它處理哪個痛點、為什麼這樣做、用到哪些 AI／工具、實際做了什麼。內容依 `git show --stat`、關鍵 diff、測試檔與 commit 訊息整理；看不出來的地方寫「diff 未顯示」或「未確認」，不推測。

## 怎麼讀

- 每筆標題：`### <短 hash> <日期時間> <作者> — <原 commit 標題>`，後面 〔分支｜在 main｜狀態〕。「在 main」依 `git branch --contains` 與 `main` 的祖先關係判定。
- 日期為作者日期。11/21 沒有 commit。作者三個身分：`rei`（11/18）、`mm113048210-ui`（11/19 起的主要作者）、`JeremyLiu2005`（只有 GitHub 合併 PR 的 merge commit）。
- 「署名」只引用 commit 訊息裡的 Co-Authored-By；全庫只有 3 筆有（069f073、704f71b、b7ee699），其餘一律寫「署名未標示」，不推測是哪個助手寫的。
- 狀態圖例：**已合併 main**＝已在 main 的歷史中；**僅在分支**＝只存在於未合併的分支；**已實作未實測**＝程式與單元測試有，但文件自承沒有在真實環境跑過（可與前兩者並列）。
- 本文件撰寫時沒有執行流程或測試；「測試新增」指 diff 內的測試檔。

### 痛點編號

① 畫面雜訊（跑馬燈、左側標、角標、天氣）／② 廣告包框（舊標題殘留）／③ 換題斷點（鏡位切換不等於換題）／④ 秒級切換對準分鐘級表格／⑤ 五字標品質（要有地點、不杜撰、不寫成定論）／⑥ 不確定時不停擺（標記、備援、可回查）／⑦ 一鍵全自動、可複製／⑧ 驗證與可交付（原表格式、收視率欄不動）／⑨ 擴大到全天、速度與成本／⑩ 文件、簡報、示範（非程式痛點）。

### 規則代碼速查（定義見 docs/rules.md）

TXT＝只讀主標題與文字過濾；AD＝廣告判斷（AD-a 至 AD-j 是十種訊號，AD-OVL＝包框外框文字不當標題）；NEW／SAME＝換題佐證與「同一則」判斷（SAME-6 有語音但沒佐證就不換題，SAME-8 標題 180 秒內回來就合併）；MAP／OUT＝分鐘格寫法；LBL／SRC／X＝五字標校驗與跨台沿用；FB＝備援（FB-1 換題只有標題變了、FB-3 讀不到標題改用語音、FB-4 規則擷取）；R20＝容錯；QA／RUN／EVAL／EVD／BRIEF＝品質狀態、版本紀錄、評估、證據欄位、完整短播報。

## 依痛點總覽

| 痛點 | 相關 commit | 做了什麼 | 目前狀態 |
|---|---|---|---|
| ① 畫面雜訊 | 3c2dfa0、89ac640、33306a7、49ab665、25b91cf、6d7176d | 只裁主標題條、逐秒 OCR 投票與捲動防護；無中文讀數視為雜訊；LIVE／溫度疊字過濾；OCR 引號統一；包框外框文字不當標題；I／l／| 改回驚嘆號、低信心列複查 | 前四項已在 main；外框文字與後兩項僅在分支 |
| ② 廣告包框 | 01b733d、cc2ebfd、247cf7d、a9a3b29、eff206f、9e8bbde、89ac640、25b91cf、6d7176d | 多訊號廣告狀態機；聲音指紋；包框樣板；輪播預告、廣告文案、時鐘消失訊號；最短廣告與合併；殘留標題比對容差 | 主體已在 main；AD-OVL、BRIEF-1 與殘留比對容差僅在分支 |
| ③ 換題斷點 | 01b733d、069f073、a9a3b29、9e8bbde、d28b42b、5965932、25b91cf、6d7176d | 穩定 7 秒、名條、同則留守、主播鏡頭、音高換人、標題回來合併；LLM 第一意見；語音佐證與收尾併回（僅分支）；證據欄位 | 到 d28b42b 已在 main；ML 選配、預設關；語音佐證與證據欄位僅在分支 |
| ④ 秒對分 | 01b733d、069f073、33306a7、fdf26b2、142d4b2 | 分鐘寫入器；畫面時鐘校時；讀不到時鐘退回檔名時間；output.minute_rule＝next／floor | next 已在 main（11/06 設定檔採用）；簡報同步僅在分支 |
| ⑤ 五字標 | 01b733d、cc2ebfd、a9a3b29、9e8bbde、b9a148c、5674fe7、33306a7、b4c5b61、fdf26b2、cccfd04、5965932、6d7176d | 提案者／校驗者分離；只用標題字元；地點檢查；字數上限到正好 5 字；不斷詞；快取與提示詞版本；RAG 選配；Mac ICU 斷詞、繁體原字 | 到 cccfd04 已在 main；不斷詞升級僅在分支 |
| ⑥ 不確定不停擺 | 6125b08、e17f1d4、6393a3b、47f5fca、92521d1、d28b42b、33306a7、6d7176d | ASR 卡死保護；證據面板；人工修正另存新檔；失敗退回規則並標記；失敗分類、品質狀態 | 前面已在 main；失敗分類與品質狀態僅在分支 |
| ⑦ 一鍵、可複製 | cc2ebfd、583dd78、d907d19、5674fe7、33306a7、9e36877、6d7176d | `newsbar run`；每小時監看；網頁介面；temperature 0＋快取；`--input-dir`；預設開語音；雙擊啟動器 | 啟動器僅在分支，其餘已在 main |
| ⑧ 驗證與可交付 | 01b733d、a9a3b29、49ab665、eff206f、3e3d90d、33306a7、6d7176d | 原表寫入證明其餘格不變；標準答案標註工具；暫定標準答案；工作簿完整檢查；交付檢查、版本紀錄、秒級評估 | 前面已在 main；交付檢查等僅在分支 |
| ⑨ 全天、速度成本 | 247cf7d、49ab665、6125b08、eff206f、f95d0b4 | 多台平行；OCR 跳讀；ASR 保護與鎖；標籤平行；NVIDIA GPU 版 | 已在 main；GPU 版未實測；處理時間 46 分鐘未達 15 分目標（README） |
| ⑩ 文件、簡報、示範 | 其餘 commit | 需求、規格、架構、README、規則手冊、示範頁、簡報生成腳本 | 大多已在 main；兩筆簡報與大綱僅在分支 |


## 11/18（週三）

### 81f6a3f 2026/11/18 11:08 rei — docs: requirements (Contextual Design) and system architecture v0.2　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件（非程式痛點）；替後面所有痛點先定義「什麼叫做對」。
- 為什麼這樣做：先寫需求與架構、再寫程式。依 docs/requirements.md 現況註記，實地訪查尚未進行，需求模型以三種二手資料重建，每筆都標出處。
- 用到的 AI／工具：無 AI 工具（純文件與設定範例）。署名未標示。
- 做了什麼：新增 docs/requirements.md（約 300 行）、docs/architecture.md v0.2（約 390 行）、config/channels.example.yaml 與 rules.example.yaml、.gitignore、README。

### 822dbdf 2026/11/18 11:48 rei — docs: functional spec from pain-point analysis　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件；把客戶題目的痛點拆成可驗收的根因（spec 的 P1–P10）。
- 為什麼這樣做：核心判斷是「換題」與「廣告」沒有機器可讀的結構化訊號，所以 spec 把系統核心定為兩個判斷器（廣告狀態機、換題判定），OCR 與 LLM 只當輸入與輸出。每條規則編號，測試以編號對應。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：新增 docs/spec.md（375 行：痛點分析、名詞與輸出格式、規則編號），小幅更新 README 與 requirements。

### 01b733d 2026/11/18 11:54 rei — feat: decision pipeline skeleton with spec acceptance tests T01-T24　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：③ 換題斷點、② 廣告包框、④ 秒對分、⑤ 五字標、⑧ 原表格式（第一版骨架，五項都碰到）。
- 為什麼這樣做：先做「決策層」再接 OCR：用假的逐秒觀測資料驗證規格，所以以 spec 的驗收案例 T01–T24 當測試（commit 標題所述）。標籤由 Generator 提案、validator 決定，沒通過就走只複製標題字元的規則備援；Excel 寫入以表頭文字找列欄、拒絕覆蓋非空格並證明其餘格子值與樣式未變。
- 用到的 AI／工具：尚無 AI 模型接上；Excel 以 openpyxl 寫入（非 AI）。署名未標示。
- 做了什麼：新增 src/newsbar/ 的 ad_state（廣告狀態機）、segmenter（換題）、minute_mapper（秒→分鐘格）、labeler（五字標校驗＋備援）、excel_writer、evidence、pipeline、schemas、cli；tests 6 支（ad_state、excel_writer、labeler、minute_mapper、pipeline、segmenter）加 conftest；pyproject 與 uv.lock。

### 3c2dfa0 2026/11/18 12:01 rei — feat: video ingest and OCR extraction (Apple Vision, voting, scroll guard)　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：① 畫面雜訊（只讀主標題條）。
- 為什麼這樣做：一秒一張 ROI 裁圖（只存設定的區域，一小時一台約 100 MB 而非整幀，NFR-04）；OCR 單秒會讀錯，所以滑動視窗投票、與前一秒相似就沿用（避免一個字的雜訊被當成換題）、偵測橫向捲動以防 ROI 誤蓋到跑馬燈。
- 用到的 AI／工具：OCR：Apple Vision 與 PaddleOCR 兩個後端，用來讀標題條文字。ffmpeg 擷取畫面。署名未標示。
- 做了什麼：新增 ingest.py（ffmpeg 裁圖）、ocr/（base、vision_backend、paddle_backend、stream 投票）、cli 的擷取指令；tests：test_ocr_stream、test_ocr_e2e。

### cc2ebfd 2026/11/18 12:12 rei — feat: visual signals, ASR post-processing, LLM generators, cross-channel naming, eval, review page, newsbar run　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：② 廣告包框、③ 換題、⑤ 五字標、⑦ 一鍵（newsbar run）、⑧ 評估與複查頁。
- 為什麼這樣做：把骨架接成可跑完整流程：畫面訊號（版面變化＝主畫面縮成側框、標題條底色、台標消失、黑畫面、鏡頭切換）判斷廣告；語音當輔助訊號，除非 OCR 讀不到才拿來命名（FB-3＝讀不到標題時用語音）；LLM 只「提案」，由 validate 決定；跨台沿用標籤時每個字仍須有本台來源（X-2），避免把別台的事實帶進來。
- 用到的 AI／工具：faster-whisper 與 MLX Whisper（ASR，語音轉文字，供換題主題判斷）；本機 LLM（Ollama）為預設標籤產生器、Claude API 為選配（規則未允許外部呼叫就拒絕，只送標題與短語音節錄）；ffmpeg blackdetect／silencedetect。署名未標示。
- 做了什麼：新增 visual.py、asr/（含兩個後端）、llm.py（OllamaGenerator／ClaudeGenerator）、cross_channel.py、evaluate.py（F1）、review.py（複查頁）、extract.py、cli 的 `newsbar run`；tests：test_asr、test_eval_review、test_llm、test_visual_e2e。

### 247cf7d 2026/11/18 12:19 rei — feat: ad audio fingerprints, framed-ad template, parallel channel extraction　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：② 廣告包框（重播廣告、包框版型）、⑨ 速度（多台平行擷取）。
- 為什麼這樣做：同一支廣告會在多台、多天重播，所以用聲音認廣告，不靠畫面；人工確認過的廣告自動加入指紋庫。包框版型每台不同，改成「存一張包框樣板、用相似度比對」。
- 用到的 AI／工具：聲音指紋（自建，numpy；Haitsma–Kalker 式，300 Hz–2 kHz 取 33 個頻帶、每幀 32 位元、以位元錯誤率比對）；樣板比對用 NCC（正規化相關）。署名未標示。
- 做了什麼：新增 fingerprint.py（指紋庫會自己長大）、visual.py 的包框樣板與 save_framed_template、extract 寫入 fingerprint_hit、cli 以多行程平行擷取各台；tests：test_fingerprint、test_visual_e2e。

### cb94f38 2026/11/18 12:21 rei — docs: requirements v0.2 with ISO 25010 NFRs and status, architecture status, README usage　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件。
- 為什麼這樣做：需求書升到 v0.2，加入 ISO 25010 品質面向的非功能需求（NFR）與實作狀態；架構與 README 補上現況與用法。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：改寫 requirements.md（約 143 行變動）、architecture.md、README；另有 cli、evaluate、ingest、llm、review、schemas、ocr/base 與測試各 1–6 行小改（diff 未逐行確認，看起來是註解或字串）。

### 583dd78 2026/11/18 12:24 rei — feat: hourly watch folder, frame retention cleanup, reviewer identity in review page　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑦ 一鍵全自動（每小時自動處理）、⑥ 可回查（抽查者身分）。
- 為什麼這樣做：側錄系統每小時每台丟一個檔；某台沒到不能擋住其他台，所以等到齊或超過寬限時間就用現有的先跑；處理狀態寫檔，重啟不重複處理。截圖有保留期限以控制硬碟。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：新增 ops.py（資料夾監看、截圖清理）、cli 的 watch 指令、複查頁加入審核者欄；tests/test_ops.py。

### a4504b6 2026/11/18 12:24 rei — docs: test count　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件。
- 為什麼這樣做：更新測試數量字樣。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：README、architecture、requirements 各改 1 行。

### 069f073 2026/11/18 13:47 rei — feat: on-screen clock sync, credit cue and SAME-6 from real 11/06 footage, MPS ASR, history/rollback/daily report　〔分支：main｜在 main：是｜狀態：已合併 main；其中退版／日報寄送已實作未實測〕
- 痛點：④ 秒對分（畫面時鐘校時）、③ 換題（真實資料校正）、⑥ 營運可回查。
- 為什麼這樣做：用 11/06 真實側錄調參：標題一般停 7 秒以上，語音字幕與名條只停 6 秒內，所以穩定秒數由 3 改為 7；記者名條（只含 2–12 個中文字、無「的了是」等人名裡不會有的字）閃現後 25 秒內出現新標題，視為換題預告；有語音但新標題沒有任何佐證就留在同一則（SAME-6）。畫面角落的時鐘每分鐘翻動的那一秒即 HH:MM:00，可反推影片起點，誤差約 ±1 秒。
- 用到的 AI／工具：ASR：faster-whisper 在 Apple MPS 上跑；OCR 讀時鐘。Co-Authored-By: Claude Opus 5.5（commit trailer 所載）。
- 做了什麼：新增 clock.py、history.py（歷史搜尋、規則版本自動退版、日報）、channels.1106.yaml；segmenter 加名條與 SAME-6；tests：test_clock、test_history、test_asr_backend、test_segmenter。docs/architecture 之後標示「退版演練與 SMTP 寄送為模擬、未實跑」。

### a9a3b29 2026/11/18 14:18 rei — fix: real 11/06 footage findings - writer round-trip quirks, title-only cross-channel match, ticker-aware ad rule (AD-g), clause-based rule labels, speaker pitch, ROI calibration page, 11/06 channel config　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：③ 換題（主播鏡頭、換人講話）、② 廣告（跑馬燈在時加嚴）、⑤ 五字標、⑧ Excel 往返。
- 為什麼這樣做：真實 11/06 發現：openpyxl 存回時布林樣式旗標會變 None 造成「樣式不同」誤判，所以比對前正規化；跨台比對原本用語音，長語音什麼詞都共有，改為只比標題並排除泛詞；新聞圖卡會蓋住台標、畫出框線，跑馬燈還在時廣告要多一個訊號才算；標籤來源有標題時不可用語音字元當白名單（語音幾乎含所有常用字，任何標籤都會過關）。
- 用到的 AI／工具：自建音高估計（numpy 自相關）判斷換人講話（NEW-2c）；主播台樣板比對（NEW-2b）；本機 LLM 的標籤驗證規則。署名未標示。
- 做了什麼：excel_writer 樣式正規化；cross_channel 只比標題（共有詞門檻 3）；ad_state 的 ticker-aware 規則；asr 的 segment_pitch／assign_speakers；visual 的主播鏡頭；labeler 以標點切子句再取字；calibrate_ui.py（ROI 校準頁）、rules.1106.yaml；tests：asr、calibrate_ui、excel_writer。

### 49ab665 2026/11/18 15:45 rei — feat: OCR skips unchanged seconds and input-side duration fix (4x faster), local LLM labels, annotation tool, Google Drive upload　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑨ 速度（OCR 少做）、⑧ 驗證（標準答案工具）、⑦ 交付（上傳）。
- 為什麼這樣做：標題通常停 7–45 秒，多數秒數的標題條沒變，所以先存 96×12 灰階縮圖，沒變就沿用上一次 OCR 結果；標題所述速度提升約 4 倍（此為 commit 標題的說法，diff 內無量測碼）。人工逐小時標四台標準答案太慢，做出標註頁，且頁面不顯示系統決策，避免標準答案被系統輸出牽著走；兩位標註者算 Cohen's κ。
- 用到的 AI／工具：OCR（Apple Vision 後端）少跑；ffmpeg 同步輸出縮圖；本機 LLM 標籤：標題提到，cli 內對應程式 diff 未逐行確認。署名未標示。
- 做了什麼：新增 annotate.py、gdrive.py（以 OAuth 權杖把成品推到使用者自己的 Google 試算表）；ingest 與 ocr/stream 的縮圖跳讀；cli 加 annotate、gold-import、upload；tests：test_annotate、test_gdrive、test_ocr_e2e。

### eff206f 2026/11/18 16:30 rei — feat: framed-ad carousel (AD-i), ad copy (AD-h), clock-gone (AD-j), alt title ROI, merged breaks, parallel LLM labels, researcher provisional gold　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：② 廣告包框（輪播預告、廣告文案、時鐘消失）、⑨ 速度（標籤平行）、⑧ 暫定標準答案。
- 為什麼這樣做：11/06 民視 12:36–12:38 把 10 則預告輪了兩次、每則 8–9 秒（見程式註解），若不處理，標題輪換會被當成換題候選，所以把「120 秒內至少 6 個不同標題各停 7–12 秒且有重複」視為廣告訊號 AD-i；標題條出現電話、0800、「廣告後」等文案為 AD-h；頻道時鐘消失為 AD-j；兩段廣告相隔 8 秒內合併。命名改為先把所有新聞標題排進執行緒池一起問 LLM。
- 用到的 AI／工具：本機 LLM（Ollama）平行請求；OCR 讀時鐘；署名未標示。
- 做了什麼：ad_state 的 carousel_seconds、merge_gap_sec；extract.add_clock；schemas 的廣告文案判斷；pipeline 以 ThreadPoolExecutor 平行標籤；新增 eval/gold/tvbs_researcher_provisional.json（研究者初標）；tests/test_segmenter.py。

### 3e3d90d 2026/11/18 22:35 rei — chore: 11/06 provisional scoring and clock backfill scripts　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑧ 驗證（工具腳本）。
- 為什麼這樣做：把 11/06 暫定標準答案的評分與時鐘補值做成腳本。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：新增 tools/add_clock_1106.py（以 OCR 補各台時鐘）與 tools/score_provisional.py（對 TVBS 暫定標準答案評分，全小時與 12:20–12:59 測試段）。

### 9e8bbde 2026/11/18 23:18 rei — fix: name cards, pre-ad copy and intro no longer become cells; SAME-8 returning title; title-only LLM prompt and LBL-4 basis; 4-slot Ollama; fast profile　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：③ 換題（名條不成格、標題回來要合併）、② 廣告（廣告前文案、片頭）、⑤ 五字標（不杜撰）。
- 為什麼這樣做：11/06 的 TVBS 法國罷工標題「鐵塔關→封路→物價→鐵塔關」被切成多則，所以 180 秒內標題回來就把中間併成同一則（SAME-8）；名條只當換題線索（SAME-7）；廣告前 20 秒內的短「新聞」多半是廣告文案，併入廣告；少於 25 秒的「廣告」不算（12:00 的 13 秒片頭）。LLM 提示原本附整段語音，模型就借用標題裡沒有的地名與動詞，程式註解記錄當次 49／91 格未過校驗，所以有標題時不給語音。
- 用到的 AI／工具：本機 LLM 提示詞改為只用主標題；SequenceMatcher 相似度（非 AI）。署名未標示。
- 做了什麼：segmenter（SAME-7、SAME-8、廣告前短稿）、ad_state（min_break_sec 25）、labeler（LBL-4 社會新聞要有地點，改以標題為準）、llm 提示、config/rules.1106.fast.yaml（調參用 12:00–12:20、12:20–12:59 只測不調）、tools/seg_score_tvbs.py；tests 三支。標題的「4-slot Ollama」在本 commit 的 llm diff 中看不到對應程式。


## 11/19（週四）

### 6125b08 2026/11/19 12:15 mm113048210-ui — fix(asr): guard MPS hangs with a subprocess deadline, one ASR at a time　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑥ 不確定時不停擺（語音卡死）、⑨ 速度。
- 為什麼這樣做：MPS 上的語音轉錄會卡死；改在子行程跑，超過期限（max(300 秒, 0.5×音檔長度)）就殺掉、改 CPU 重試，再失敗就不用語音繼續；用檔案鎖讓各台輪流用 GPU。
- 用到的 AI／工具：faster-whisper（ASR）；子行程與 fcntl 鎖。署名未標示。
- 做了什麼：asr/__init__ 的 transcribe_guarded、extract 加鎖、音高每段最多抽 25 幀、tools/asr_guard_check.py、seg_score_tvbs 的 --no-asr 對照；tests/test_asr.py。

### cc2c2da 2026/11/19 12:34 mm113048210-ui — docs(requirements): add Q12 on the sheet delivery deadline　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件。
- 為什麼這樣做：把「表格交付時限」列為待客戶回答的問題 Q12。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：requirements.md 新增 1 行。

### 4bfb7c8 2026/11/19 12:58 mm113048210-ui — docs(requirements): NFR-05 status with measured fast-mode timing　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件、⑨ 速度（記錄現況）。
- 為什麼這樣做：把 NFR-05（處理時間）狀態與快速模式實測時間寫入需求書。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：requirements.md 改 1 行；具體數字見該行，diff 未在此重述。

### 89ac640 2026/11/19 14:41 mm113048210-ui — fix(segmenter): bar reads without Chinese are OCR noise; untitled segment before an ad joins the ad　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：① 畫面雜訊、② 廣告包框（舊標題殘留）。
- 為什麼這樣做：11/06 ETTV 12:35 OCR 把 25 秒廣告文案讀成「(0UU6」，表格就寫成「0UU6/廣告」；濾掉噪音後改由語音命名成「娘家一生菌」，再處理成純「廣告」。commit 說明 TVBS 的 F1 不變。
- 用到的 AI／工具：OCR 噪音過濾規則（無中文即噪音，即後來的 TXT-7）。署名未標示。
- 做了什麼：segmenter 兩處修改（無中文讀數視為沒讀到標題；廣告前沒有標題的片段併入廣告）；tests/test_segmenter.py。

### 5674fe7 2026/11/19 14:56 mm113048210-ui — feat(llm): reproducible labels — temperature 0, fixed seed, per-workdir answer cache　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑦ 可複製（同樣輸入得同樣輸出）、⑧ 可驗證。
- 為什麼這樣做：commit 說明：同一份 11/06 資料跑兩次，只靠 temperature 0，240 格中仍有 1 格不同（Ollama 會批次處理平行請求）；加上答案快取後兩次一致。
- 用到的 AI／工具：本機 LLM（Ollama，qwen2.5:7b）：temperature 0、固定 seed、每個工作目錄的答案快取。署名未標示。
- 做了什麼：llm.py 新增快取與參數、cli 傳入；tests/test_llm.py。

### 314b6c5 2026/11/19 15:04 mm113048210-ui — docs: mentor question list　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件（內部問答）。
- 為什麼這樣做：整理要請教導師的問題清單；後來在 6de93fb 因屬內部筆記而刪除。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：新增 docs/mentor-questions.md（32 行）。

### 11555f2 2026/11/19 15:58 mm113048210-ui — feat(demo): results explorer page for the 11/06 run (tools/build_demo.py builds data + crops)　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 示範（成果瀏覽頁）。
- 為什麼這樣做：讓評審不用跑程式也能看 11/06 的輸出與每格截圖。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：新增 demo/index.html（241 行）、tools/build_demo.py（產生資料與裁圖）。

### d1227f0 2026/11/19 16:08 mm113048210-ui — docs(readme): results, demo link, and where the broadcast videos come from　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件。
- 為什麼這樣做：README 補結果、示範連結、廣播影片來源。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：README 改 23 行。

### b57cfa8 2026/11/19 22:53 mm113048210-ui — docs: system status (verified backend vs. unconnected UI prototype); add operator-flow prototype　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 示範（操作流程原型）。
- 為什麼這樣做：誠實區分「已驗證的後端」與「尚未接上的 UI 原型」。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：新增 demo/prototype.html（操作流程原型）、tools/record_run.py、tools/render_demo_video.py；README 補狀態說明。

### d907d19 2026/11/19 23:17 mm113048210-ui — feat(serve): operator web UI wired to the local pipeline　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑦ 一鍵全自動（網頁操作介面）。
- 為什麼這樣做：操作者不必下指令：選影片資料夾、按開始、看每台進度、下載 xlsx、開複查頁。只用標準函式庫、預設只綁 127.0.0.1、拒絕 --video-dir 之外的檔案。
- 用到的 AI／工具：無新 AI；網頁在背景啟動 `newsbar run` 子行程。署名未標示。
- 做了什麼：新增 server.py（227 行）、web/app.html、cli 的 serve 指令；tests/test_server.py。

### e17f1d4 2026/11/19 23:26 mm113048210-ui — feat(serve): per-segment evidence for manual checks　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑥ 不確定時可回查。
- 為什麼這樣做：點任一則新聞或廣告就能看那一秒的整張畫面、OCR 實際讀的標題條裁圖、OCR 文字、語音逐字稿與決策依據。
- 用到的 AI／工具：呈現 OCR 與 ASR 的結果（不新增模型）。署名未標示。
- 做了什麼：server.py 以需要時才從錄影切圖；app.html 的證據面板；tests/test_server.py。


## 11/20（週五）

### 6393a3b 2026/11/20 00:01 mm113048210-ui — fix(evidence): full-frame screenshot for every segment, taken while the title is steady　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑥ 可回查（證據截圖品質）。
- 為什麼這樣做：截圖原本取每段第一秒，常是切換中的空白畫面；改取「讀到的標題連續停 3 秒」的第一秒（廣告則取進廣告數秒後）。
- 用到的 AI／工具：無新 AI。署名未標示。
- 做了什麼：server.py 與 tools/build_demo.py 的選幀邏輯、demo 顯示整張畫面在標題條裁圖上方；tests/test_server.py。

### 47f5fca 2026/11/20 00:13 mm113048210-ui — feat(evidence): four pictures spread across each ad break　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：② 廣告包框、⑥ 可回查。
- 為什麼這樣做：廣告開頭幾秒常落在「預告新聞」畫面，一張圖看起來像新聞；改在廣告段的 5／35／65／95% 處各取一張並標時鐘時間。
- 用到的 AI／工具：無新 AI。署名未標示。
- 做了什麼：server、app.html、demo 與 build_demo 同步；tests/test_server.py。

### 92521d1 2026/11/20 00:26 mm113048210-ui — feat(review): reviewers can correct cells and get a corrected sheet　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑥ 不確定時的人工修正、⑧ 原表不動。
- 為什麼這樣做：複查者在每則證據下標「對／錯並改」（錯誤類型、備註、複查者），每台每分鐘一筆存成 corrections.json；「修正版 Excel」另存新檔，交付的原表從不被編輯。示範頁用 artifact 共用資料庫做同樣的表單。
- 用到的 AI／工具：無新 AI（沿用既有 apply-corrections）。署名未標示。
- 做了什麼：server.py 修正 API、app.html 表單、demo/index.html 的複查與匯出；tests/test_server.py。

### b9a148c 2026/11/20 10:28 mm113048210-ui — fix(labeler): cap five-character labels at six and version the prompt cache　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑤ 五字標品質（字數）。
- 為什麼這樣做：本機 7B 模型常寫出 7 字以上（LBL-5＝五字標長度規則，當時上限 6）；加「先數字數」規則與範例，並把 PROMPT_VERSION 放進快取鍵，避免舊答案被重播。
- 用到的 AI／工具：本機 LLM qwen2.5:7b（Ollama）提示詞。署名未標示。
- 做了什麼：llm.py 5 行加 2 行減；無測試改動。

### d28b42b 2026/11/20 10:43 mm113048210-ui — feat(server): LLM first opinion on weak story changes (FB-1) in the result page　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：③ 換題（只有標題變了的弱訊號）、⑥ 不確定時給人參考。
- 為什麼這樣做：FB-1（換題的佐證只有「標題變了」）仍判成換題但標低信心；現在多請本機 LLM 給「第一意見」，只當複查建議、絕不改表。commit 說明 FB-4 標籤判定仍為實驗、未接上。
- 用到的 AI／工具：本機 LLM（Ollama）；judge.py 檔頭記載在 11/06 TVBS 樣本上 4 個 FB-1 格的判斷與暫定標準答案全部一致，且標示 FB-4 的 7B 判斷幾乎全部標可疑、不採用。署名未標示。
- 做了什麼：新增 judge.py、server 的 POST /api/jobs/<id>/judge（存 <out>.judge.json）、頁面顯示判斷、tools/judge_low.py；tests/test_server.py。

### 565a2a9, bfc0756 · 2026/11/20 10:47–10:48 · mm113048210-ui — README 改寫與清理　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件。
- 為什麼這樣做：把 README 改成「現在的結果、限制、用法」，並以 mermaid 畫流程圖與複查流程；明講暫定標準答案與 46 分鐘執行時間。第二筆移除試算表連結與 artifact 可見性說明。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：README 改寫（565a2a9 約 169 行）、刪 3 行（bfc0756）。

### d7b26fa 2026/11/20 11:10 mm113048210-ui — feat(demo): review queue workspace, five-step prototype flow, LLM first opinion in demo data　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑥ 不確定時的複查工作流、⑩ 示範。
- 為什麼這樣做：兩個示範頁共用 demo/review.js：優先序佇列（FB-3 無標題靠語音、FB-4 規則備援、FB-1、被 LLM 清除的 FB-1、高信心抽樣）、證據並排一個決定（對／改寫／留白）、鍵盤快速鍵、復原、計時、匯出 JSON/CSV；不能寫共用資料庫時退回瀏覽器草稿。
- 用到的 AI／工具：示範中帶入 LLM 第一意見（來自 d28b42b）。署名未標示。
- 做了什麼：demo/review.js（305 行）、review.css、index.html、prototype.html、build_demo.py、serve_demo.py。

### ef3fbf1, 408b99b, 9c0fc15, 6a81a23, 3b85e86, 181cd6d, 247fbbf, 6540638, 2b296b3, f748290, 27c9da0, 63694fb · 2026/11/20 11:53–12:48 · mm113048210-ui — 簡報（deck）一小時內連續修稿　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 簡報（S36 繳交簡報）。
- 為什麼這樣做：簡報由 tools/build_deck.py 生成，放進版本控制；一小時內連續修稿：把低價值區塊換成實作成果、保證與每步示範畫面；去掉行話與自創詞（F1、AI、OCR、GPU 改白話）；示範畫面不再出現 FB 代碼；封面加問題 Q1–Q5 對照。
- 用到的 AI／工具：無 AI 模型參與系統；簡報以腳本生成（commit 說明含原生圖表與形狀圖）。署名未標示。
- 做了什麼：ef3fbf1 新增 build_deck.py（497 行）與 docs/deck/README；408b99b 加已填表、範例、標註畫面、QR、驗證方法；9c0fc15 換成部署架構；6a81a23 換掉低價值區塊；3b85e86 各台成績圖；181cd6d 回答 Q1 並顯示 Q5 進度；247fbbf 改為三項備援保證；6540638 去行話；2b296b3 重排標註表與廣告畫面時間戳；f748290 加標籤檢核、時間對準結果、時間分解與敏感度表；27c9da0 白話標籤；63694fb 問題編號。

### f95d0b4 2026/11/20 13:04 mm113048210-ui — feat: NVIDIA GPU version alongside the Mac version　〔分支：main｜在 main：是｜狀態：已合併 main；已實作未實測（無 NVIDIA 機器）〕
- 痛點：⑨ 擴大到全天、速度與成本（NVIDIA GPU 版）。
- 為什麼這樣做：瓶頸是語音轉錄只能一次一台（Mac MPS 共用會卡死），要在有 NVIDIA 的機器上平行；commit 自述「GPU 版尚未在 NVIDIA 硬體上跑過」。
- 用到的 AI／工具：faster-whisper：有 NVIDIA 就用 CUDA float16（Mac 用 MPS、其他用 CPU int8）；PaddleOCR 偵測 CUDA（固定 2.x 版）；一次一台的鎖改為 N 個名額（Mac 1、其他預設 2）。署名未標示。
- 做了什麼：asr/faster_whisper_backend、extract、ocr/paddle_backend、cli 與 pyproject 的 gpu 組、新增 config/rules.gpu.yaml、README 兩版說明、uv.lock；tests/test_gpu.py。

### 704f71b, b7ee699 · 2026/11/20 13:07–13:11 · mm113048210-ui — 簡報最後一頁連結（兩筆皆有署名）　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 簡報。
- 為什麼這樣做：把 GitHub QR 標為可下載；最後一頁的進度框換成原始碼與示範連結。
- 用到的 AI／工具：無系統 AI。704f71b：Co-Authored-By: Claude Haiku 5.5（commit trailer 所載）；b7ee699：Co-Authored-By: Claude Sonnet 5.5（commit trailer 所載）。
- 做了什麼：tools/build_deck.py 各改約 2、3 行。


## 11/22（週日）

### 8ce6de1 2026/11/22 09:30 mm113048210-ui — docs: bring the implementation status up to date with the code; add the demo data　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件、⑥ 誠實標出未做與未測。
- 為什麼這樣做：把 architecture §12 同步到程式現況：主播鏡頭與換人講話偵測、ROI 校準頁、畫面時鐘校時、搜尋、日報、規則退版、網頁控制台、GPU 版都在程式裡；並寫明還沒測或還沒做的部分；記下 11/06 一次執行中 4 格未過地點規則全是規則擷取（低信心）。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：architecture.md、requirements.md 小改，並追蹤 demo/data.json（截圖仍不入庫）。

### f3e5c41, bdaa84f, c3f7944, ee44378 · 2026/11/22 09:37–11:02 · mm113048210-ui — 簡報依 UNEXT 框架重排（四筆）　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 簡報。
- 為什麼這樣做：對照 UNEXT 提案框架重排十頁：摘要、現況流程、根因與路由、缺口與價值、未來作業、方案與工具、方案邏輯、指標與成果、90 天計畫與風險矩陣；之後恢復截圖、熱度格、畫面標註並補規則層技術細節；最後再改以官方五個問題為骨架（封面把 Q1–Q5 對到頁面，新增時間對準、廣告畫面處理、Excel 保留、全天擴大等頁，4／8／18 小時價值）。f3e5c41 先加入聲音指紋與換題三證據圖。
- 用到的 AI／工具：無系統 AI。熱度格資料取自 demo/data.json（11/06 系統輸出）。署名未標示。
- 做了什麼：tools/build_deck.py 四次大改（各約 250–590 行變動），措辭依 commit 說明對照程式實際行為。

### 33306a7 2026/11/22 11:21 mm113048210-ui — fix: close gaps against the client brief　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑥ 不確定時不停擺、⑤ 五字標（不杜撰、保留語氣）、① 疊字雜訊、⑦ 一鍵、⑧ 原表格式。
- 為什麼這樣做：對照客戶題目補洞：LLM 或單台失敗不再停下整個流程，改走規則標籤並標記；疑／涉／傳等推測詞須保留在標籤中；畫面時鐘讀不到時改用檔名時間並標記該台；LIVE／直播／獨家與角落溫度先濾掉；地名詞表 22 縣市與 368 鄉鎮，規則標籤改為「濃縮」而非截前五字；Excel 檢查擴及條件式格式、資料驗證、凍結窗格與收視欄表頭。
- 用到的 AI／工具：本機 LLM（失敗時備援）；OCR 文字過濾與詞表（非 AI）；openpyxl。署名未標示。
- 做了什麼：新增 lexicon.py、config/lexicon.yaml、textfilter.py、discover.py（`newsbar run --input-dir` 找影片與範本、必要時啟動 Ollama）；改 labeler、llm、pipeline、excel_writer、cli；產生帶黃底與註解的複查版 xlsx；tests：test_lexicon_labels、test_oneclick_review_copy、test_resilience。

### 1ea7f26 2026/11/22 11:31 mm113048210-ui — demo: rework the operating prototype around the speaker's points　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 示範（依講者重點重做原型）、⑦ 一鍵。
- 為什麼這樣做：原型改為：一鍵開始加時段選擇、某台出問題其他台繼續、原版面表格預覽（A30／M30／A4+ 收視欄留白）、交付檔與複查版連結、每格畫面與讀取區框線與逐秒條、決策規則頁、以實測 46 分鐘速率推全天。簡報措辭依修好的程式調整。
- 用到的 AI／工具：無系統 AI。署名未標示。
- 做了什麼：demo/prototype.html（489 行變動）、demo/data.json、兩個示範 xlsx、build_deck.py 22 行。

### 7a7d1ef 2026/11/22 11:38 mm113048210-ui — deck: follow the UNEXT 1.1–5.3 proposal structure　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 簡報。
- 為什麼這樣做：改為跟隨 UNEXT 提案 1.1–5.3 的結構：站點與問題卡、五骨魚骨圖、價值表含計算欄、各站 AI／人分工、影響×就緒象限、RACI、測試計畫、前後對照、30-60-90 天；技術路線保留三頁答 Q1–Q3。
- 用到的 AI／工具：無系統 AI。署名未標示。
- 做了什麼：build_deck.py 約 265 行加 278 行減。

### 9e36877 2026/11/22 11:59 mm113048210-ui — fix(cli): run uses speech transcription by default, like the web app　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑦ 一鍵全自動（預設行為一致）。
- 為什麼這樣做：`newsbar run` 原本沒開語音轉錄，一鍵指令會默默退回只看標題；改為預設開，跟網頁版一致。
- 用到的 AI／工具：faster-whisper（ASR，預設開啟）。署名未標示。
- 做了什麼：cli.py 2 行加 1 行減。

### 5965932 2026/11/22 12:14 mm113048210-ui — feat: optional RAG for 五字標 and learned story-change/ad decisions (default off)　〔分支：feat/rag-ml｜在 main：是｜狀態：已合併 main（經 b7da2a9）；選配、預設關、尚未在真實資料評分〕
- 痛點：⑤ 五字標（範例輔助）、③ 換題與 ② 廣告（學習式判斷），皆為選配。
- 為什麼這樣做：提供可選路徑：以人工確認過的「標題→五字標」知識庫檢索當範例；以人工標準答案訓練模型給機率。預設關閉，所以不改現有輸出（有測試確認規則模式輸出與固定樣本一致）；RAG 的答案仍須通過全部校驗，不會繞過規則。
- 用到的 AI／工具：RAG：bigram TF-IDF 檢索（自建）；學習式：numpy 邏輯迴歸（換題候選、逐秒廣告列）；模型註冊表可退版；LLM 仍為本機 Ollama。署名未標示。
- 做了什麼：新增 kb.py（541 行）、ml.py、mlmodel.py、server 的回饋鉤（複查修正餵回知識庫）、cli 的 kb／ml 指令、rules.example 的設定、README 章節；改 llm、ad_state、segmenter；tests/test_rag_ml.py（約 17 個測試函式）。

### e085876, 2fd59f7, 34d6fa2 · 2026/11/22 12:19–12:47 · mm113048210-ui — 簡報加入 RAG／ML 並更新數字（三筆）　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 簡報。
- 為什麼這樣做：把 RAG 與 ML 寫進簡報（各步驟的 AI 類型、結果、下一步）；改為官方框架順序加摘要頁、每頁約 300 字、以圖代表；摘要頁補五項評估標準，數字改用當前程式重跑的結果（commit 標題：0.868、44 分鐘）。
- 用到的 AI／工具：無系統 AI。署名未標示。
- 做了什麼：tools/build_deck.py 三次修改。

### b7da2a9 2026/11/22 12:51 mm113048210-ui — merge: optional RAG and learned decisions (default off)　〔分支：main（合併 feat/rag-ml）｜在 main：是｜狀態：已合併 main〕
- 痛點：合併 commit（無自己的程式變更）。
- 為什麼這樣做：把 feat/rag-ml 合進主線，保持預設關閉。
- 用到的 AI／工具：見 5965932。署名未標示。
- 做了什麼：父 commit：34d6fa2（主線）與 5965932（feat/rag-ml）；變更內容與 5965932 相同。

### a607eba, 6ec4334 · 2026/11/22 13:01–13:07 · mm113048210-ui — 簡報方法頁與短標題（兩筆）　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 簡報。
- 為什麼這樣做：方法頁說明決策怎麼做、結果只放在摘要與成果頁；加短標題與段落標籤。
- 用到的 AI／工具：無系統 AI。署名未標示。
- 做了什麼：build_deck.py：a607eba 約 371 行加、6ec4334 9 行改。

### b4c5b61 2026/11/22 13:10 mm113048210-ui — fix(labels): narrow social-news and hedge checks, never end a rule label mid-word　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑤ 五字標品質（地點、不寫成定論、不斷在字中間）。
- 為什麼這樣做：社會新聞只指犯罪、事故、災害詞彙；來源有地點就退件沒地點的標籤，來源沒地點則保留並標記；單字推測詞（疑、傳、恐、涉）只標記，多字推測詞仍退件；規則備援先切子句，且不以連接詞或單獨數字收尾。
- 用到的 AI／工具：本機 LLM 的校驗規則與規則備援（LBL-4 地點、LBL-7 不斷詞）。署名未標示。
- 做了什麼：labeler.py（約 210 行變動）、lexicon、兩份詞表；tests/test_labeler.py。

### 6fbd7ec 2026/11/22 13:10 mm113048210-ui — docs(rules): rulebook of every decision rule, newsbar rules command, rule-id comments in code　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件（規則手冊）、⑥ 可回查。
- 為什麼這樣做：把決策規則依客戶痛點分組，做成由程式產生的手冊，列參數與程式位置，測試檢查文件裡每個數值都等於程式預設。
- 用到的 AI／工具：無 AI（rulebook.py 為資料結構）。署名未標示。
- 做了什麼：新增 rulebook.py（513 行）、docs/rules.md（624 行，由程式產生）、cli 的 `newsbar rules`、程式內規則編號註解；tests/test_rulebook.py。

### a4c03f9 2026/11/22 13:11 mm113048210-ui — deck: cost-down table on page 10, short subheadings　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 簡報、⑨ 成本。
- 為什麼這樣做：第 10 頁加降本表與短副標。
- 用到的 AI／工具：無系統 AI。署名未標示。
- 做了什麼：build_deck.py 75 行加 77 行減。

### b448ee8 2026/11/22 13:14 mm113048210-ui — demo: decision-rules tab reads the code's rulebook (collapsible groups)　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 示範（決策規則頁）。
- 為什麼這樣做：示範的決策規則頁改為讀取程式產生的規則手冊，不再手寫一份。
- 用到的 AI／工具：無 AI。署名未標示。
- 做了什麼：新增 demo/rules.json（1045 行）、prototype.html 小改；折疊群組。

### 153ab9f 2026/11/22 13:22 mm113048210-ui — chore(deps): add jieba for Chinese word segmentation　〔分支：main（經 PR #1，分支 chore/add-jieba-dep）｜在 main：是｜狀態：已合併 main〕
- 痛點：⑤ 五字標（中文斷詞）、基礎設施。
- 為什麼這樣做：加入 jieba 套件供中文斷詞（後續 cccfd04 的備援標籤用它判斷詞界）。
- 用到的 AI／工具：jieba：中文斷詞，用來讓規則備援以詞為單位擷取。署名未標示。
- 做了什麼：pyproject.toml 與 uv.lock。

### bb1b0a2 2026/11/22 13:22 JeremyLiu2005 — Merge pull request #1 from JeremyLiu2005/chore/add-jieba-dep　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：合併 commit（基礎設施）。
- 為什麼這樣做：合併 PR #1。
- 用到的 AI／工具：見 153ab9f。
- 做了什麼：由 JeremyLiu2005 合併 chore/add-jieba-dep（含 153ab9f 一筆）。

### 6de93fb 2026/11/22 14:11 mm113048210-ui — docs: sync numbers and status with the 11/22 rerun; remove internal mentor notes　〔分支：main（經 PR #2，分支 docs/sync-with-deck-v3）｜在 main：是｜狀態：已合併 main〕
- 痛點：⑩ 文件（同步數字與狀態）、⑥ 誠實標示。
- 為什麼這樣做：依 11/22 重跑結果同步文件：換題 F1 全小時 0.868、測試段 0.897（非獨立保留集）、廣告 0.952、不開語音 0.758；46 分對 15 分目標標為未達標；TVBS 單人標註的限制；預設全地端（外部 LLM 與 RAG／ML 選配、預設關）；刪除內部導師筆記。
- 用到的 AI／工具：無 AI 工具。署名未標示。
- 做了什麼：README、spec、architecture、requirements、rules.md 共 7 檔；刪 docs/mentor-questions.md。

### 1387d9f 2026/11/22 14:15 JeremyLiu2005 — Merge pull request #2 from JeremyLiu2005/docs/sync-with-deck-v3　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：合併 commit（文件）。
- 為什麼這樣做：合併 PR #2。
- 用到的 AI／工具：見 6de93fb。
- 做了什麼：由 JeremyLiu2005 合併 docs/sync-with-deck-v3（含 6de93fb 一筆）。

### fdf26b2 2026/11/22 14:41 mm113048210-ui — fix: write mid-minute changes from the next minute and cap labels at 5 characters　〔分支：fix/minute-next-label-cap（經 PR #3）｜在 main：是｜狀態：已合併 main〕
- 痛點：④ 秒級切換對準分鐘級表格、⑤ 五字標長度。
- 為什麼這樣做：依三立 11/22 說明，不是整分 00 秒的改變從下一分鐘開始寫（例：12:03:40 改變寫在 1204），一格只放一則；舊的向下取整寫法留作程式預設（floor）。標籤上限改 5，且 pipeline 之前沒傳規則檔的長度（用了 4–6 預設），現在傳入。
- 用到的 AI／工具：本機 LLM 提示改為 4–5 字；規則備援加硬上限。署名未標示。
- 做了什麼：minute_mapper 新增 output.minute_rule（next／floor）；labeler 的 enforce_max；四份規則檔設 next；regenerate docs/rules.md；tests：test_minute_mapper（新增 58 行）、test_lexicon_labels。

### 40346ef 2026/11/22 14:47 mm113048210-ui — test: compare the label cache key with the current PROMPT_VERSION　〔分支：fix/minute-next-label-cap（經 PR #3）｜在 main：是｜狀態：已合併 main〕
- 痛點：⑦ 可複製（測試與提示詞版本對齊）。
- 為什麼這樣做：測試把快取鍵與當前 PROMPT_VERSION 比對，避免提示詞改版後測試寫死舊值。
- 用到的 AI／工具：本機 LLM 提示詞版本機制。署名未標示。
- 做了什麼：tests/test_rag_ml.py 改 1 行。

### cccfd04 2026/11/22 15:11 mm113048210-ui — feat(labels): exactly 5 visible characters, feedback retry, word-aware fallback　〔分支：fix/exact5-feedback（經 PR #3）｜在 main：是｜狀態：已合併 main〕
- 痛點：⑤ 五字標品質（正好 5 個字）。
- 為什麼這樣做：最低與最高字數都設為 5（中日文每字算 1、英數每個字母或數字算 1，例：泰山3100階是 7）；AI 連兩次被退件，再用具體訊息（字數、標題缺哪些字）問一次；規則備援改取長度剛好的標題視窗，以模糊重疊與 jieba 詞界評分。
- 用到的 AI／工具：本機 LLM（提示詞 v6-exact5-fb）；jieba 詞界。署名未標示。
- 做了什麼：labeler.py（86 行變動）、llm.py、rulebook、四份規則檔；tests：test_labeler、test_llm、test_resilience。

### 9a1b891 2026/11/22 15:21 JeremyLiu2005 — Merge pull request #3 from JeremyLiu2005/fix/exact5-feedback　〔分支：main｜在 main：是｜狀態：已合併 main〕
- 痛點：合併 commit。
- 為什麼這樣做：合併 PR #3，標題：下一分鐘放置、正好 5 字標籤、預設不產生黃底複查版。
- 用到的 AI／工具：見 fdf26b2、40346ef、cccfd04。
- 做了什麼：由 JeremyLiu2005 合併 fix/exact5-feedback（含三個 commit，18 個檔、264 行加 51 行減）。標題寫「預設不產生黃底複查版」，但這三個 commit 的訊息未提及；diff 在 cli.py 只有 6 行，內容未確認。

### 25b91cf 2026/11/22 16:33 mm113048210-ui — feat(segmenter): ignore framed-ad bar text, keep complete briefs, emit boundary evidence　〔分支：feat/short-title,feat/story-unit-segmentation｜在 main：否｜狀態：僅在分支（feat/story-unit-segmentation，已推到 origin；本機 feat/short-title 也含）〕
- 痛點：② 廣告包框（外框文字不當標題）、③ 換題（完整短播報不被併入廣告）、⑥ 可回查（邊界證據）。
- 為什麼這樣做：AD-OVL：包框廣告時外框上的標題條文字是節目外框而非新聞，這一秒視為「沒讀到標題」，不開新一則、不命名、也不當廣告前的殘留標題。BRIEF-1：廣告前不到 20 秒的短稿若是完整短播報（穩定新標題、有換題佐證、有新聞語音、不延續到廣告、與前 180 秒無共同詞）就保留並一律送複查。EVD-1／EVD-2：每則有結束時間、邊界機率欄、證據、是否需複查，寫成 <out>.stories.jsonl。commit 說明：在 11/06 快取觀測上四台換題邊界與 main 完全相同，F1 不變；架構文件承認先前的加權融合分數從未實作。
- 用到的 AI／工具：規則；不含新模型。署名未標示。
- 做了什麼：segmenter（95 行）、ad_state、evidence、schemas、rulebook、pipeline、docs（architecture §4.7、rules.md）、demo/rules.json 重新產生；tests：test_segmenter 新增 63 行、test_pipeline。

### 6d7176d 2026/11/22 18:06 mm113048210-ui — feat: Add one-click launchers for NewsBar on Windows and macOS　〔分支：feat/short-title,feat/story-unit-segmentation｜在 main：否｜狀態：僅在分支（feat/story-unit-segmentation 與 feat/short-title）〕
- 痛點：⑦ 一鍵（啟動器）、⑧ 可交付與可驗證（交付檢查、版本紀錄、秒級評估）、⑤ 五字標（不斷詞）、③ 換題（語音佐證）、⑥ 不停擺（失敗分類）。
- 為什麼這樣做：標題只寫啟動器，但 diff 為 29 檔、約 2244 行加：雙擊啟動網頁介面（Windows .bat、macOS .command，預設只綁本機，--lan 才開區網）；Mac 內建 ICU 詞界辨識繁中（jieba 詞典為簡中、會把「違規害婦撞亡」切壞），其他系統用 jieba，兩者都沒有就不檢查；--resume 只在影片、設定、後端都沒變時才沿用擷取（RUN-1）；每次執行存版本紀錄 manifest（RUN-2）；語音失敗不能當成「沒人講話」（RUN-3）；寫出前做交付檢查三態（QA-2）；複查結果成為每格品質狀態（QA-1）；換題加語音佐證 NEW-2e（語音裡聽到新標題的詞、舊標題的詞幾乎沒了）與收尾句併回前一則 SAME-9；TXT-8 把夾在中文字間的 I／l／| 改回驚嘆號；TXT-10 標題 OCR 信心低列入複查；R20-2 斷路器每 30 秒試探恢復；EVAL-2 秒級評估、EVAL-3 難例子集。
- 用到的 AI／工具：Apple 內建 ICU 斷詞（CoreFoundation）、jieba（備援）；本機 LLM 提示詞 v7（要求繁體原字、不得切斷詞）；OCR 信心中位數；ASR 失敗狀態。署名未標示。
- 做了什麼：新增 delivery.py、runinfo.py、words.py、launchers/（NewsBar.bat、NewsBar.command、README）、docs/deck/outline-v10.md（317 行）；改 labeler（211 行）、segmenter、ad_state、cli、evaluate、review 等；README 記載 11/22 重跑結果（全小時換題 F1 0.923、秒級 0.857、廣告 0.952；98 格中 63 格需複查）；tests/test_runinfo_delivery.py（199 行，含 RUN／QA／EVAL／LBL9／LBL10／R20-2 等測試）及 ad_state、segmenter、pipeline 測試。標題未提及上述多數變更，審查時要注意 commit 範圍與訊息不符。

### 142d4b2 2026/11/22 18:16 mm113048210-ui — deck: page 8 states the current minute rule (mid-minute change is written from the next minute, one story per cell)　〔分支：feat/story-unit-segmentation｜在 main：否｜狀態：僅在分支（feat/story-unit-segmentation，尚未推送）〕
- 痛點：④ 秒對分、⑩ 簡報。
- 為什麼這樣做：簡報第 8 頁改為寫明目前的分鐘規則：分鐘中途的改變從下一分鐘開始寫、一格一則（對應 fdf26b2）。
- 用到的 AI／工具：無系統 AI。署名未標示。
- 做了什麼：build_deck.py 13 行加 10 行減。

### c1d6dc8 2026/11/22 18:18 mm113048210-ui — docs(deck): outline v10 gets fix methods for P5/P6/P8/P9, cost assumptions on P10, four-line format and status wording　〔分支：feat/story-unit-segmentation｜在 main：否｜狀態：僅在分支（feat/story-unit-segmentation，尚未推送）〕
- 痛點：⑩ 簡報大綱。
- 為什麼這樣做：把大綱 v10 補上 P5／P6／P8／P9 的修正方法、P10 的成本假設、四行格式與狀態用語。
- 用到的 AI／工具：無系統 AI。署名未標示。
- 做了什麼：docs/deck/outline-v10.md 101 行加 10 行減。

## 未合併或有風險

### 只在分支、尚未進 main

| commit | 分支 | 說明 |
|---|---|---|
| 25b91cf | feat/story-unit-segmentation（origin 已有）；本機 feat/short-title 也含 | AD-OVL、BRIEF-1、證據欄位。commit 說明在 11/06 快取觀測上邊界與 main 相同、F1 不變 |
| 6d7176d | 同上 | 標題只寫啟動器，實際 29 檔、約 2244 行加，含 RUN／QA／EVAL、NEW-2e／SAME-9、斷詞、交付檢查。範圍與訊息不符，合併前建議拆開審查 |
| 142d4b2、c1d6dc8 | feat/story-unit-segmentation，本機領先 origin 2 筆，尚未推送 | 簡報與大綱修稿 |

- feat/story-unit-segmentation 比 main 多 4 筆（上列）。feat/short-title 比 main 多 2 筆（25b91cf、6d7176d）。
- 其他分支（chore/add-jieba-dep、docs/sync-with-deck-v3、fix/exact5-feedback、fix/minute-next-label-cap、fix/story-boundary-evidence、feat/rag-ml）都沒有 main 以外的 commit，內容已併入。

### 已實作未實測、待驗證

- f95d0b4 NVIDIA GPU 版：commit 與 config/rules.gpu.yaml 自承尚未在 NVIDIA 機器上跑過，平行速度未知。
- 069f073 規則自動退版與日報 SMTP 寄送：文件標為模擬、未在真實環境跑過。
- 5965932 RAG／學習式判斷：預設關閉，有單元測試，但 docs/architecture.md 標示尚未在真實資料上評分。
- NFR-14 資料不出內網：requirements.md 標「待驗證」，沒有斷網實測紀錄。

### 已知限制（出自 commit 訊息、README、requirements）

- 處理時間未達標：README 與 requirements 記載每小時四台約 46 分鐘，目標 15 分鐘；瓶頸是語音轉錄一次一台。
- 標準答案只有 TVBS 一台、一位研究者初標；另外三台量不到分數。規則在 11/06 同一小時調過，數字不是獨立驗證集（6de93fb、6d7176d 的 README）。
- 6d7176d 的 README 記載 98 格中 63 格需複查；BRIEF-1 的短播報一律送複查。
- 語者切換用音高跳變，不是聲紋模型（architecture.md）。
- d28b42b：FB-4 標籤判定的 LLM 意見為實驗，未接到介面。
- 9a1b891 的 PR 標題提到「預設不產生黃底複查版」，但該 PR 內三個 commit 的訊息沒提，diff 在 cli.py 僅 6 行，細節未確認。
- 9e8bbde 標題的「4-slot Ollama」在該 commit 的 llm.py diff 看不到對應程式，可能在設定或他處，未確認。
- 49ab665 標題的「4 倍」與「本機 LLM 標籤」是標題說法，diff 內沒有量測碼，cli 對應程式未逐行確認。
