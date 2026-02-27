"""Tests for preset command builders."""

import pytest
from app.presets import get_preset, PRESETS


def test_all_presets_registered():
    expected = {
        "transcode_h264_mp4", "scale_fit_max", "thumbnail_jpg",
        "burn_subs", "overlay_image", "extract_audio_mp3",
        "concat_videos", "hls_package",
    }
    assert set(PRESETS.keys()) == expected


def test_unknown_preset_raises():
    with pytest.raises(ValueError, match="Unknown preset"):
        get_preset("nonexistent_preset")


def test_transcode_h264_default():
    preset = get_preset("transcode_h264_mp4")
    options = {**preset.defaults}
    cmd = preset.build_cmd(
        input_path="/tmp/input.mov",
        output_path="/tmp/output.mp4",
        options=options,
    )
    assert "-i" in cmd
    assert "/tmp/input.mov" in cmd
    assert "libx264" in cmd
    assert "23" in cmd  # default CRF
    assert "-nostdin" in cmd


def test_transcode_h264_custom_crf():
    preset = get_preset("transcode_h264_mp4")
    options = {**preset.defaults, "crf": 18}
    cmd = preset.build_cmd(
        input_path="/tmp/in.mp4",
        output_path="/tmp/out.mp4",
        options=options,
    )
    assert "18" in cmd


def test_thumbnail_jpg():
    preset = get_preset("thumbnail_jpg")
    options = {**preset.defaults, "at_seconds": 5}
    cmd = preset.build_cmd(
        input_path="/tmp/in.mp4",
        output_path="/tmp/thumb.jpg",
        options=options,
    )
    assert "-ss" in cmd
    assert "5" in cmd
    assert "-frames:v" in cmd


def test_extract_audio_mp3():
    preset = get_preset("extract_audio_mp3")
    options = {**preset.defaults}
    cmd = preset.build_cmd(
        input_path="/tmp/in.mp4",
        output_path="/tmp/audio.mp3",
        options=options,
    )
    assert "-vn" in cmd
    assert "libmp3lame" in cmd


def test_scale_fit_max():
    preset = get_preset("scale_fit_max")
    options = {**preset.defaults, "max_width": 1280, "max_height": 720}
    cmd = preset.build_cmd(
        input_path="/tmp/in.mp4",
        output_path="/tmp/out.mp4",
        options=options,
    )
    assert any("1280" in arg for arg in cmd)
    assert any("720" in arg for arg in cmd)


def test_burn_subs():
    preset = get_preset("burn_subs")
    options = {**preset.defaults}
    cmd = preset.build_cmd(
        input_path="/tmp/in.mp4",
        output_path="/tmp/out.mp4",
        options=options,
        extra_inputs={"input_subs_url": "/tmp/subs.srt"},
    )
    assert any("subtitles" in arg for arg in cmd)


def test_overlay_image():
    preset = get_preset("overlay_image")
    options = {**preset.defaults, "x": 20, "y": 30}
    cmd = preset.build_cmd(
        input_path="/tmp/in.mp4",
        output_path="/tmp/out.mp4",
        options=options,
        extra_inputs={"input_overlay_url": "/tmp/logo.png"},
    )
    assert "/tmp/logo.png" in cmd
    assert any("overlay" in arg for arg in cmd)


def test_concat_videos():
    preset = get_preset("concat_videos")
    options = {}
    cmd = preset.build_cmd(
        input_path="/tmp/concat.txt",
        output_path="/tmp/out.mp4",
        options=options,
        extra_inputs={},
    )
    assert "-f" in cmd
    assert "concat" in cmd


def test_hls_package():
    import os, tempfile
    preset = get_preset("hls_package")
    options = {**preset.defaults}
    with tempfile.TemporaryDirectory() as work_dir:
        cmd = preset.build_cmd(
            input_path="/tmp/in.mp4",
            output_path="/tmp/ignored",
            options=options,
            work_dir=work_dir,
        )
    assert "-f" in cmd
    assert "hls" in cmd
    assert any("index.m3u8" in arg for arg in cmd)
