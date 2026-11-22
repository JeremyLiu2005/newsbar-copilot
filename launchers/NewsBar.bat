@echo off
rem NewsBar Copilot - Windows one-click launcher.
rem Double-click: starts the operator web UI on this PC and opens the browser.
rem   NewsBar.bat          only this PC can open it (127.0.0.1, default)
rem   NewsBar.bat --lan    also reachable from the office LAN (192.168.x.x)
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

if "%NEWSBAR_PORT%"=="" (set PORT=8765) else (set PORT=%NEWSBAR_PORT%)
set HOST=127.0.0.1
if /i "%~1"=="--lan" set HOST=0.0.0.0

set CHANNELS=
for %%f in (config\channels.yaml config\channels.1106.yaml config\channels.example.yaml) do if not defined CHANNELS if exist %%f set CHANNELS=%%f
set RULES=
for %%f in (config\rules.yaml config\rules.1106.yaml config\rules.example.yaml) do if not defined RULES if exist %%f set RULES=%%f
set FAST=
for %%f in (config\rules.fast.yaml config\rules.1106.fast.yaml) do if not defined FAST if exist %%f set FAST=--rules-fast %%f
if "%NEWSBAR_VIDEO_DIR%"=="" (set VIDEO_DIR=data\raw) else (set VIDEO_DIR=%NEWSBAR_VIDEO_DIR%)
if "%NEWSBAR_TEMPLATE%"=="" (set TEMPLATE=%VIDEO_DIR%\template.xlsx) else (set TEMPLATE=%NEWSBAR_TEMPLATE%)

where uv >nul 2>nul
if errorlevel 1 (
  echo 找不到 uv。請先安裝（只需一次）：https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)
if not exist "%VIDEO_DIR%" mkdir "%VIDEO_DIR%"
if not exist out\web mkdir out\web
if not exist "%TEMPLATE%" echo 提醒：找不到空白範本 %TEMPLATE%，請把「12監看」範本放進 %VIDEO_DIR%\

echo NewsBar Copilot 啟動中…（第一次會安裝套件，約需數分鐘）
echo   本機網址：http://127.0.0.1:%PORT%
if "%HOST%"=="0.0.0.0" echo   區網網址：http://（這台電腦的 IP）:%PORT%　用 ipconfig 查 IPv4；請只在公司內網使用
echo   關閉：在這個視窗按 Ctrl+C

start "" /b powershell -NoProfile -Command "for($i=0;$i -lt 120;$i++){try{Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%PORT%/ -TimeoutSec 1 ^| Out-Null; Start-Process http://127.0.0.1:%PORT%/; break}catch{Start-Sleep 1}}"
uv run newsbar serve --video-dir "%VIDEO_DIR%" --template "%TEMPLATE%" --channels %CHANNELS% --rules %RULES% %FAST% --host %HOST% --port %PORT%
pause
