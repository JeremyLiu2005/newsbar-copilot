"""Push the finished sheet to Google Drive (NFR-25/26).

The course template on Drive belongs to someone else (view-only), so results
go to the operator's own Drive: first run creates a Google Sheet (converted
from the xlsx, formatting kept); later runs update that same file so the link
never changes. The file id is remembered in `<out>.gdrive.json`.

Auth: an OAuth access token with the drive.file scope, from
NEWSBAR_GDRIVE_TOKEN or `--token-cmd` (e.g. `gcloud auth print-access-token`).
Plain urllib — no extra dependency.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
import uuid
from pathlib import Path

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GSHEET = "application/vnd.google-apps.spreadsheet"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


class DriveError(RuntimeError):
    pass


def token_from(env_token: str | None, token_cmd: str | None) -> str:
    if env_token:
        return env_token
    if token_cmd:
        return subprocess.run(token_cmd, shell=True, capture_output=True, text=True, check=True).stdout.strip()
    raise DriveError("沒有 Google Drive 權杖：設定 NEWSBAR_GDRIVE_TOKEN 或 --token-cmd")


def _multipart(meta: dict, data: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{b}\r\nContent-Type: {XLSX}\r\n\r\n").encode() + data + f"\r\n--{b}--".encode()
    return body, f"multipart/related; boundary={b}"


def upload(xlsx: Path, token: str, title: str | None = None, folder_id: str | None = None,
           file_id: str | None = None, convert: bool = True, opener=urllib.request.urlopen) -> dict:
    meta: dict = {}
    if file_id is None:
        meta["name"] = title or xlsx.stem
        if convert:
            meta["mimeType"] = GSHEET
        if folder_id:
            meta["parents"] = [folder_id]
    body, ctype = _multipart(meta, xlsx.read_bytes())
    url = f"{UPLOAD}/{file_id}?uploadType=multipart&fields=id,name,webViewLink" if file_id \
        else f"{UPLOAD}?uploadType=multipart&fields=id,name,webViewLink"
    req = urllib.request.Request(url, data=body, method="PATCH" if file_id else "POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})
    try:
        with opener(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        raise DriveError(f"Drive 上傳失敗 {e.code}：{e.read().decode()[:300]}") from e


def push(xlsx: Path, token: str, folder_id: str | None = None, opener=urllib.request.urlopen) -> dict:
    """Create once, then keep updating the same Drive file (id cached next to the xlsx)."""
    state = xlsx.with_suffix(".gdrive.json")
    fid = json.loads(state.read_text())["id"] if state.exists() else None
    res = upload(xlsx, token, xlsx.stem, folder_id, fid, opener=opener)
    state.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res
