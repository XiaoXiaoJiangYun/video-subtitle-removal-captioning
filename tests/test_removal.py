import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from subtitle_toolkit.removal import (
    DetectionCache, OpenCVInpainter, SourceIdentity, _config_defaults, boxes_to_mask,
    detect_frames, inpaint_video, load_cache, main, roi_pixels, save_cache,
)


class BrightDetector:
    def detect(self, frame: np.ndarray):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        points = cv2.findNonZero((gray > 200).astype(np.uint8))
        if points is None:
            return []
        x, y, width, height = cv2.boundingRect(points)
        return [(x, y, x + width, y + height)]


def synthetic_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 48))
    assert writer.isOpened()
    for index in range(3):
        frame = np.zeros((48, 64, 3), np.uint8)
        if index == 1:
            cv2.rectangle(frame, (20, 36), (43, 42), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def test_roi_and_mask_are_clipped_and_dilated():
    assert roi_pixels((100, 200, 3), (0.5, 1.0, 0.25, 0.75)) == (50, 50, 150, 100)
    mask = boxes_to_mask((20, 30, 3), [(-2, 5, 10, 15)], padding=2, dilation=1)
    assert mask.shape == (20, 30)
    assert mask.dtype == np.uint8
    assert mask[5:15, :10].all()


def test_opencv_inpainting_changes_only_masked_operation():
    image = np.full((30, 30, 3), 90, np.uint8)
    image[10:20, 10:20] = 255
    mask = np.zeros((30, 30), np.uint8)
    mask[10:20, 10:20] = 255
    result = OpenCVInpainter(3, "telea").inpaint(image, mask)
    assert result.shape == image.shape
    assert result[15, 15].mean() < 200
    with pytest.raises(ValueError):
        OpenCVInpainter(method="sttn")


def test_synthetic_detection_and_cache_round_trip(tmp_path: Path):
    video = tmp_path / "synthetic.avi"
    synthetic_video(video)
    cache = detect_frames(video, BrightDetector(), (0.5, 1.0, 0.0, 1.0))
    assert list(cache.frames) == [1]
    x1, y1, x2, y2 = cache.frames[1][0]
    assert x1 <= 20 < x2 and y1 <= 36 < y2
    target = tmp_path / "cache.json"
    save_cache(cache, target)
    loaded = load_cache(target)
    assert loaded == cache
    assert json.loads(target.read_text(encoding="utf-8"))["schema"].endswith("/1")
    with pytest.raises(FileExistsError):
        save_cache(cache, target)


def test_cache_rejects_unknown_schema():
    with pytest.raises(ValueError):
        DetectionCache.from_dict({"schema": "unknown"})


def test_config_loads_example_fields(tmp_path: Path):
    config = tmp_path / "removal.json"
    config.write_text(json.dumps({
        "roi": [0.6, 0.9, 0.1, 0.8], "confidence": 0.7, "padding": 5,
        "dilation": 1, "inpaint_radius": 4, "inpaint_method": "ns",
        "ocr_language": "ch",
    }), encoding="utf-8")
    defaults = _config_defaults(["--config", str(config), "--confidence", "0.9"])
    assert defaults["roi"] == [0.6, 0.9, 0.1, 0.8]
    assert defaults["confidence"] == 0.7  # parser-level explicit override is applied later


def test_config_unknown_field_is_rejected(tmp_path: Path):
    config = tmp_path / "bad.json"
    config.write_text('{"unexpected": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown removal config"):
        _config_defaults(["--config", str(config)])


def test_invalid_source_metadata_and_cli_values_are_rejected(tmp_path: Path):
    bad = DetectionCache(SourceIdentity(1, "hash", 0, 64, 48, 5.0), {}, "test", (0.5, 1, 0, 1))
    cache_path = tmp_path / "bad.json"
    save_cache(bad, cache_path)
    with pytest.raises(SystemExit) as exc:
        main(["--input", str(tmp_path / "missing.mp4"), "--output", str(tmp_path / "out.mp4"),
              "--cache", str(cache_path), "--reuse-cache", "--inpaint-radius", "0"])
    assert exc.value.code == 2


def test_inpaint_video_synthetic_full_pipeline(tmp_path: Path):
    import shutil
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools unavailable")
    source = tmp_path / "source.avi"
    synthetic_video(source)
    cache = detect_frames(source, BrightDetector(), (0.5, 1.0, 0.0, 1.0))
    output = tmp_path / "cleaned.mp4"
    inpaint_video(source, output, cache, padding=1, dilation=0)
    assert output.exists() and output.stat().st_size > 0
    capture = cv2.VideoCapture(str(output))
    frames = 0
    while capture.read()[0]: frames += 1
    capture.release()
    assert frames == cache.source.frame_count
    with pytest.raises(FileExistsError):
        inpaint_video(source, output, cache)
