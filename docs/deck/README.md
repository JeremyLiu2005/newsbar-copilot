# 交件簡報（S36）

`tools/build_deck.py` 在底稿 .pptx 上產生 10 頁簡報：保留底稿第 1 頁（封面）與第 2 頁（問題），其餘頁面用同一套紅橘配色重畫，圖表是 PowerPoint 原生圖表。

```bash
pip install python-pptx pillow
python3 tools/build_deck.py 底稿.pptx 輸出.pptx            # 素材預設在 data/deck/assets
python3 tools/build_deck.py 底稿.pptx 輸出.pptx 素材資料夾
```

轉 PDF：在 PowerPoint 開啟輸出檔，另存為 PDF（本機沒有 LibreOffice）。

## 素材（不放進 repo）

底稿 .pptx（`data/deck/base.pptx`）和下列素材都不在 repo 裡，所以無法從乾淨的 clone 重建簡報。

截圖含電視台畫面，版權屬各台，放在 `data/deck/assets/`（`data/` 不進版本控制）。需要的檔案：

| 檔名 | 來源 |
|---|---|
| `upload.png`、`progress.png`、`result.png` | 操作原型 `demo/prototype.html` 的選影片、分析、結果畫面，淺色主題，2 倍解析度，裁到卡片邊界 |
| `rv_queue.png`、`rv_card.png` | 同一頁的人工複查步驟（佇列與卡片兩個區塊）；佇列已示範複查 6 格 |
| `ov_timeline.png` | 監看台 `demo/index.html` 總覽的四台時間軸 |
| `excel_annot.png` | `out/12監看_20261106.xlsx` 的 12:00–12:05 與 12:34–12:37 節錄，用 openpyxl 讀值、HTML 表格排版（標出換題、「/」、廣告）後截圖 |
| `{tvbs,ettv,ftv,cts}_1200_full.jpg` | `demo/img/` 四台 12:00 的完整畫面 |
| `qr_proto.png`、`qr_monitor.png`、`qr_github.png` | 操作原型、監看台、GitHub 網址的 QR code（`qrcode` 套件） |
| `tvbs_1204_full.jpg`、`tvbs_1204_bar.jpg` | `demo/img/` 的 TVBS 12:04 完整畫面與主 bar 裁切 |
| `ettv_1234_0_ad05.jpg`、`_ad35`、`_ad65`、`_ad95` | `demo/img/` 的東森 12:34 廣告破口 4 格 |

擷取方式：用 `uv run python tools/serve_demo.py` 開本機展示，再用 headless Chrome（`--force-device-scale-factor=2`，頁面設 `data-theme="light"`）截圖後裁切。
