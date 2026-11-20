"""Device selection for the Mac and NVIDIA GPU versions (logic only; no GPU needed)."""

import pytest

from newsbar import extract
from newsbar.ocr.paddle_backend import paddle_has_gpu


def test_asr_device_follows_the_machine():
    fw = pytest.importorskip("newsbar.asr.faster_whisper_backend")
    assert fw.pick_device("auto", platform="darwin") == ("mps", "float16")
    assert fw.pick_device("auto", platform="linux", cuda_count=1) == ("cuda", "float16")
    assert fw.pick_device("auto", platform="linux", cuda_count=0) == ("cpu", "int8")
    assert fw.pick_device("cuda", compute_type="int8_float16") == ("cuda", "int8_float16")


def test_one_transcription_at_a_time_on_a_mac_two_elsewhere():
    assert extract.default_asr_parallel("darwin") == 1
    assert extract.default_asr_parallel("linux") == 2


def test_asr_slots_hand_out_different_locks(tmp_path):
    with extract.asr_slot(tmp_path, 2) as a:
        with extract.asr_slot(tmp_path, 2) as b:
            assert {a, b} == {0, 1}
    with extract.asr_slot(tmp_path, 0) as c:  # 0 = no limit
        assert c is None


def test_ocr_gpu_can_be_forced(monkeypatch):
    monkeypatch.setenv("NEWSBAR_OCR_GPU", "1")
    assert paddle_has_gpu() is True
    monkeypatch.setenv("NEWSBAR_OCR_GPU", "0")
    assert paddle_has_gpu() is False
