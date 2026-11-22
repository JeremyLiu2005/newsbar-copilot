# 一鍵啟動（免雲端，地端試用）

雙擊即在本機開監看台網頁（`newsbar serve`），瀏覽器自動打開。影片與表格不離開這台電腦。

| 系統 | 檔案 | 說明 |
|---|---|---|
| macOS | `NewsBar.command` | Finder 雙擊；第一次若被擋，右鍵 → 打開 |
| Windows | `NewsBar.bat` | 檔案總管雙擊 |

- 預設只開在 `http://127.0.0.1:8765`，只有這台電腦能開。
- 加 `--lan` 參數（終端機執行 `launchers/NewsBar.command --lan`）會綁 `0.0.0.0`，同一區網的電腦可用 `http://192.168.x.x:8765` 開啟。網頁沒有登入機制，只在公司內網使用。
- 需要先裝一次 [uv](https://docs.astral.sh/uv/getting-started/installation/)；第一次啟動會自動安裝 Python 套件。
- 影片放 `data/raw/`，空白「12監看」範本放 `data/raw/template.xlsx`。可用環境變數 `NEWSBAR_VIDEO_DIR`、`NEWSBAR_TEMPLATE`、`NEWSBAR_PORT` 改位置。
- 設定檔依序找 `config/channels.yaml` → `channels.1106.yaml` → `channels.example.yaml`（rules 同理）。

正式排程仍用無介面 CLI（`newsbar run`），不必常駐網頁。這個啟動器只給新聞部人員看結果、看截圖用。

之後若要連 uv 都不裝，可用 PyInstaller 把整個程式包成單一 `.app`／`.exe`（尚未做；語音模型體積大，包出來約數 GB）。
