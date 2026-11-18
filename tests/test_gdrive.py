"""Drive push with a fake transport: create once, then update the same file."""

import io
import json

import pytest

from newsbar.gdrive import GSHEET, DriveError, push, token_from


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_create_then_update_same_file(tmp_path):
    x = tmp_path / "12監看_20261106.xlsx"
    x.write_bytes(b"PK-fake-xlsx")
    calls = []

    def opener(req, timeout):
        calls.append((req.get_method(), req.full_url, req.data))
        return Resp(json.dumps({"id": "abc123", "name": "12監看_20261106", "webViewLink": "https://docs.google.com/x"}).encode())

    first = push(x, "tok", folder_id="folder9", opener=opener)
    second = push(x, "tok", opener=opener)
    assert first["id"] == second["id"] == "abc123"
    (m1, u1, b1), (m2, u2, _) = calls
    assert m1 == "POST" and "uploadType=multipart" in u1
    assert GSHEET.encode() in b1 and b"folder9" in b1 and b"PK-fake-xlsx" in b1  # converted to a Sheet
    assert m2 == "PATCH" and "/abc123?" in u2  # same link every run


def test_token_required():
    with pytest.raises(DriveError):
        token_from(None, None)
    assert token_from("t", None) == "t"
    assert token_from(None, "echo hi") == "hi"
