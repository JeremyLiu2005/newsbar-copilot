#!/bin/bash
# NewsBar Copilot — macOS one-click launcher.
# Double-click in Finder: starts the operator web UI on this Mac and opens the browser.
#   ./NewsBar.command          only this Mac can open it (127.0.0.1, default)
#   ./NewsBar.command --lan    also reachable from the office LAN (192.168.x.x)
# Recordings and sheets never leave this machine; no cloud service is involved.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${NEWSBAR_PORT:-8765}"
HOST=127.0.0.1
[[ "${1:-}" == "--lan" ]] && HOST=0.0.0.0

pick() { for f in "$@"; do [[ -f "$f" ]] && { echo "$f"; return; }; done; }
CHANNELS="$(pick config/channels.yaml config/channels.1106.yaml config/channels.example.yaml)"
RULES="$(pick config/rules.yaml config/rules.1106.yaml config/rules.example.yaml)"
FAST="$(pick config/rules.fast.yaml config/rules.1106.fast.yaml)"
VIDEO_DIR="${NEWSBAR_VIDEO_DIR:-data/raw}"
TEMPLATE="${NEWSBAR_TEMPLATE:-$VIDEO_DIR/template.xlsx}"

if ! command -v uv >/dev/null 2>&1; then
  echo "找不到 uv。請先安裝（只需一次）：https://docs.astral.sh/uv/getting-started/installation/"
  read -r -p "按 Enter 關閉…" _; exit 1
fi
mkdir -p "$VIDEO_DIR" out/web
[[ -f "$TEMPLATE" ]] || echo "提醒：找不到空白範本 $TEMPLATE，請把「12監看」範本放進 $VIDEO_DIR/"

echo "NewsBar Copilot 啟動中…（第一次會安裝套件，約需數分鐘）"
echo "  影片資料夾：$VIDEO_DIR　設定：$CHANNELS、$RULES"
echo "  本機網址：http://127.0.0.1:$PORT"
if [[ "$HOST" == 0.0.0.0 ]]; then
  IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '本機 IP')"
  echo "  區網網址：http://$IP:$PORT（同一區網的電腦都能開，請只在公司內網使用）"
fi
echo "  關閉：在這個視窗按 Ctrl+C"

( for _ in $(seq 1 120); do
    curl -fs "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && { open "http://127.0.0.1:$PORT/"; exit 0; }
    sleep 1
  done ) &

ARGS=(--video-dir "$VIDEO_DIR" --template "$TEMPLATE" --channels "$CHANNELS" --rules "$RULES" --host "$HOST" --port "$PORT")
[[ -n "$FAST" ]] && ARGS+=(--rules-fast "$FAST")
exec uv run newsbar serve "${ARGS[@]}"
