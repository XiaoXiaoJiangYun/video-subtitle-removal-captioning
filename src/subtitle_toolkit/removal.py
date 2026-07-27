"""Detect subtitle regions, cache boxes, build masks, and inpaint with OpenCV.

PaddleOCR is loaded only when detection is requested. The only inpainting modes
implemented here are OpenCV Telea and Navier-Stokes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

import cv2
import numpy as np

CACHE_SCHEMA = "subtitle-toolkit/detection-cache/1"
Box = tuple[int, int, int, int]  # x_min, y_min, x_max, y_max; max coordinates exclusive


class Detector(Protocol):
    def detect(self, frame: np.ndarray) -> list[Box]: ...


@dataclass(frozen=True)
class SourceIdentity:
    size: int
    edge_sha256: str
    frame_count: int
    width: int
    height: int
    fps: float


@dataclass
class DetectionCache:
    source: SourceIdentity
    frames: dict[int, list[Box]]
    detector: str
    roi: tuple[float, float, float, float]
    schema: str = CACHE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source": asdict(self.source),
            "detector": self.detector,
            "roi": list(self.roi),
            "frames": {str(key): [list(box) for box in boxes] for key, boxes in sorted(self.frames.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DetectionCache":
        if data.get("schema") != CACHE_SCHEMA:
            raise ValueError("unsupported detection cache schema")
        return cls(
            SourceIdentity(**data["source"]),
            {int(key): [tuple(map(int, box)) for box in boxes] for key, boxes in data["frames"].items()},
            str(data["detector"]),
            tuple(map(float, data["roi"])),
        )


class PaddleOCRDetector:
    """Thin adapter around the externally installed PaddleOCR package and models."""

    def __init__(self, language: str = "en", confidence: float = 0.8) -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("install the optional 'ocr' dependencies before detection") from exc
        self.confidence = confidence
        self.engine = PaddleOCR(use_angle_cls=False, lang=language, show_log=False)

    def detect(self, frame: np.ndarray) -> list[Box]:
        result = self.engine.ocr(frame, cls=False)
        lines = result[0] if result and isinstance(result[0], list) else result
        boxes: list[Box] = []
        for item in lines or []:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            polygon, recognition = item[0], item[1]
            score = float(recognition[1]) if isinstance(recognition, (list, tuple)) and len(recognition) > 1 else 1.0
            if score < self.confidence or not polygon:
                continue
            xs, ys = zip(*polygon)
            boxes.append((int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1))
        return boxes


class OpenCVInpainter:
    def __init__(self, radius: float = 3, method: str = "telea") -> None:
        if radius <= 0:
            raise ValueError("inpaint radius must be positive")
        methods = {"telea": cv2.INPAINT_TELEA, "ns": cv2.INPAINT_NS}
        if method not in methods:
            raise ValueError("OpenCV inpaint method must be 'telea' or 'ns'")
        self.radius, self.flag = radius, methods[method]

    def inpaint(self, frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        return cv2.inpaint(frame, mask, self.radius, self.flag)


def edge_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as stream:
        digest.update(stream.read(1024 * 1024))
        if size > 1024 * 1024:
            stream.seek(max(0, size - 1024 * 1024))
            digest.update(stream.read(1024 * 1024))
    return digest.hexdigest()


def _validate_identity(identity: SourceIdentity, *, processed_count: int | None = None) -> None:
    if identity.frame_count <= 0 or identity.width <= 0 or identity.height <= 0:
        raise ValueError("video frame count and dimensions must be positive")
    if not math.isfinite(identity.fps) or identity.fps <= 0:
        raise ValueError("video FPS must be positive and finite")
    if processed_count is not None and processed_count != identity.frame_count:
        raise ValueError(f"decoded {processed_count} frames, expected {identity.frame_count}")


def source_identity(path: Path, capture: cv2.VideoCapture | None = None) -> SourceIdentity:
    owned = capture is None
    capture = capture or cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    identity = SourceIdentity(
        path.stat().st_size,
        edge_fingerprint(path),
        round(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
        round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        float(capture.get(cv2.CAP_PROP_FPS)),
    )
    if owned:
        capture.release()
    _validate_identity(identity)
    return identity


def roi_pixels(shape: Sequence[int], roi: Sequence[float]) -> Box:
    if len(roi) != 4 or any(value < 0 or value > 1 for value in roi):
        raise ValueError("ROI must contain four normalized values in [0, 1]")
    top, bottom, left, right = roi
    if top >= bottom or left >= right:
        raise ValueError("ROI must have positive width and height")
    height, width = shape[:2]
    return round(left * width), round(top * height), round(right * width), round(bottom * height)


def detect_frames(path: Path, detector: Detector, roi: Sequence[float] = (0.55, 0.98, 0.0, 1.0)) -> DetectionCache:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    identity = source_identity(path, capture)
    frames: dict[int, list[Box]] = {}
    index = 0
    while True:  # Intentional sequential processing for bounded memory and deterministic caches.
        ok, frame = capture.read()
        if not ok:
            break
        x1, y1, x2, y2 = roi_pixels(frame.shape, roi)
        local_boxes = detector.detect(frame[y1:y2, x1:x2])
        boxes = [(a + x1, b + y1, c + x1, d + y1) for a, b, c, d in local_boxes]
        if boxes:
            frames[index] = boxes
        index += 1
    capture.release()
    _validate_identity(identity, processed_count=index)
    return DetectionCache(identity, frames, detector.__class__.__name__, tuple(map(float, roi)))


def boxes_to_mask(shape: Sequence[int], boxes: Sequence[Box], padding: int = 3, dilation: int = 2) -> np.ndarray:
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    for x1, y1, x2, y2 in boxes:
        x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
        x2, y2 = min(width, x2 + padding), min(height, y2 + padding)
        if x1 < x2 and y1 < y2:
            mask[y1:y2, x1:x2] = 255
    if dilation > 0 and mask.any():
        kernel = np.ones((dilation * 2 + 1, dilation * 2 + 1), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def _exclusive_temporary(path: Path, *, preserve_suffix: bool = False) -> tuple[int, Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix if preserve_suffix else ".tmp"
    return tempfile.mkstemp(prefix=f".{path.stem if preserve_suffix else path.name}.", suffix=suffix,
                            dir=path.parent)


def save_cache(cache: DetectionCache, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    descriptor, temporary_name = _exclusive_temporary(path)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            json.dump(cache.to_dict(), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists(): raise FileExistsError(f"refusing to overwrite {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_cache(path: Path) -> DetectionCache:
    return DetectionCache.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _check_cache(path: Path, cache: DetectionCache) -> None:
    actual = source_identity(path)
    if actual != cache.source:
        raise ValueError("detection cache does not match the input video")


def _probe_media(path: Path) -> dict[str, Any]:
    completed = subprocess.run([
        "ffprobe", "-v", "error", "-count_frames", "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate,nb_read_frames",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _verify_media(path: Path, source: SourceIdentity, processed_count: int) -> None:
    _validate_identity(source, processed_count=processed_count)
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
                    "-map", "0", "-f", "null", "-"], check=True)
    probe = _probe_media(path)
    video = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), None)
    duration = float(probe.get("format", {}).get("duration") or 0)
    if not video or duration <= 0:
        raise ValueError("staged output is missing a video stream or positive duration")
    if int(video.get("width") or 0) != source.width or int(video.get("height") or 0) != source.height:
        raise ValueError("staged output dimensions do not match the source")
    decoded = int(video.get("nb_read_frames") or 0)
    if decoded != processed_count:
        raise ValueError(f"staged output has {decoded} frames, expected {processed_count}")


def inpaint_video(input_path: Path, output_path: Path, cache: DetectionCache, *, padding: int = 3,
                  dilation: int = 2, radius: float = 3, method: str = "telea") -> None:
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite {output_path}")
    _check_cache(input_path, cache)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened(): raise RuntimeError(f"cannot open video: {input_path}")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    inpainter = OpenCVInpainter(radius, method)
    descriptor, staging_name = _exclusive_temporary(output_path, preserve_suffix=True)
    staging = Path(staging_name)
    os.close(descriptor)
    staging.unlink()
    with tempfile.TemporaryDirectory(prefix="subtitle-toolkit-") as directory:
        video_only = Path(directory) / "video.mp4"
        writer = cv2.VideoWriter(str(video_only), fourcc, cache.source.fps, (cache.source.width, cache.source.height))
        if not writer.isOpened():
            capture.release()
            raise RuntimeError("cannot open temporary video writer")
        index = 0
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame.shape[1] != cache.source.width or frame.shape[0] != cache.source.height:
                    raise ValueError(f"frame {index} dimensions do not match source metadata")
                mask = boxes_to_mask(frame.shape, cache.frames.get(index, []), padding, dilation)
                writer.write(inpainter.inpaint(frame, mask) if mask.any() else frame)
                index += 1
        finally:
            capture.release()
            writer.release()
        _validate_identity(cache.source, processed_count=index)
        command = ["ffmpeg", "-n", "-hide_banner", "-loglevel", "error", "-threads", "4",
                   "-i", str(video_only), "-i", str(input_path), "-map", "0:v:0", "-map", "1:a?",
                   "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", str(staging)]
        try:
            subprocess.run(command, check=True)
            _verify_media(staging, cache.source, index)
            if output_path.exists():
                raise FileExistsError(f"target appeared during processing: {output_path}")
            os.replace(staging, output_path)
        finally:
            staging.unlink(missing_ok=True)


CONFIG_FIELDS = {"roi", "confidence", "padding", "dilation", "inpaint_radius",
                 "inpaint_method", "ocr_language"}


def _config_defaults(argv: list[str] | None) -> dict[str, Any]:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path)
    preliminary, _ = pre_parser.parse_known_args(argv)
    if not preliminary.config:
        return {}
    data = json.loads(preliminary.config.read_text(encoding="utf-8"))
    if not isinstance(data, dict): raise ValueError("removal config must be a JSON object")
    unknown = set(data) - CONFIG_FIELDS
    if unknown: raise ValueError(f"unknown removal config fields: {', '.join(sorted(unknown))}")
    return data


def main(argv: list[str] | None = None) -> int:
    try:
        defaults = _config_defaults(argv)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid --config: {exc}") from exc
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON defaults; explicit CLI options take precedence")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--reuse-cache", action="store_true")
    parser.add_argument("--detect-only", action="store_true", help="write a new detection cache without inpainting")
    parser.add_argument("--roi", nargs=4, type=float, default=defaults.get("roi", (0.55, 0.98, 0.0, 1.0)), metavar=("TOP", "BOTTOM", "LEFT", "RIGHT"))
    parser.add_argument("--ocr-language", default=defaults.get("ocr_language", "en"))
    parser.add_argument("--confidence", type=float, default=defaults.get("confidence", 0.8))
    parser.add_argument("--padding", type=int, default=defaults.get("padding", 3))
    parser.add_argument("--dilation", type=int, default=defaults.get("dilation", 2))
    parser.add_argument("--inpaint-radius", type=float, default=defaults.get("inpaint_radius", 3))
    parser.add_argument("--inpaint-method", choices=("telea", "ns"), default=defaults.get("inpaint_method", "telea"))
    args = parser.parse_args(argv)
    if args.detect_only and args.reuse_cache:
        parser.error("--detect-only and --reuse-cache cannot be combined")
    if not args.detect_only and args.output is None:
        parser.error("--output is required unless --detect-only is used")
    if not 0 <= args.confidence <= 1: parser.error("--confidence must be in [0, 1]")
    if args.padding < 0 or args.dilation < 0: parser.error("--padding and --dilation must not be negative")
    if args.inpaint_radius <= 0: parser.error("--inpaint-radius must be positive")
    if args.inpaint_method not in ("telea", "ns"): parser.error("--inpaint-method must be 'telea' or 'ns'")
    try:
        roi_pixels((1, 1), args.roi)
    except ValueError as exc:
        parser.error(str(exc))
    if args.reuse_cache:
        cache = load_cache(args.cache)
    else:
        detector = PaddleOCRDetector(args.ocr_language, args.confidence)
        cache = detect_frames(args.input, detector, args.roi)
        save_cache(cache, args.cache)
    if not args.detect_only:
        inpaint_video(args.input, args.output, cache, padding=args.padding, dilation=args.dilation,
                      radius=args.inpaint_radius, method=args.inpaint_method)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
