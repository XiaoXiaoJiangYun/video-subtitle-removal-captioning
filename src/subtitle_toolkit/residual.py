"""Audit subtitle-like shapes and splice reviewed correction intervals."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

import cv2
import numpy as np

from .removal import Box, _exclusive_temporary, _probe_media, source_identity


class PolygonDetector(Protocol):
    def predict(self, frame: np.ndarray) -> list[tuple[np.ndarray, float]]: ...


@dataclass(frozen=True)
class RepairInterval:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("repair interval must be a non-negative inclusive range")


def classify_polygon(points: np.ndarray, *, single_glyph: bool = False) -> str | None:
    points = np.asarray(points, dtype=np.float32)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3:
        return None
    width = math.ceil(float(points[:, 0].max())) - math.floor(float(points[:, 0].min())) + 1
    height = math.ceil(float(points[:, 1].max())) - math.floor(float(points[:, 1].min())) + 1
    if width >= 30 and height >= 16 and width >= height * 1.3:
        return "line"
    if (single_glyph and 14 <= width <= 80 and 14 <= height <= 80
            and width <= height * 1.35 and height <= width * 2.2):
        return "single_glyph"
    return None


def detect_candidates(detector: PolygonDetector, frame: np.ndarray, roi: Box,
                      *, scale: float = 2.0, single_glyph: bool = False) -> list[dict[str, Any]]:
    if scale <= 0:
        raise ValueError("audit scale must be positive")
    x1, y1, x2, y2 = roi
    crop = cv2.resize(frame[y1:y2, x1:x2], None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_CUBIC)
    candidates = []
    for polygon, score in detector.predict(crop):
        points = np.asarray(polygon, dtype=np.float32) / scale
        kind = classify_polygon(points, single_glyph=single_glyph)
        if kind is None:
            continue
        candidates.append({
            "rect": [math.floor(float(points[:, 0].min())) + x1,
                     math.floor(float(points[:, 1].min())) + y1,
                     math.ceil(float(points[:, 0].max())) + x1,
                     math.ceil(float(points[:, 1].max())) + y1],
            "score": float(score), "kind": kind,
        })
    return candidates


def boundary_frames(frame_count: int, boundaries: Sequence[int], window: int) -> set[int]:
    if frame_count < 1 or window < 0:
        raise ValueError("frame count must be positive and boundary window non-negative")
    return {frame for boundary in boundaries
            for frame in range(max(0, boundary - window),
                               min(frame_count - 1, boundary + window) + 1)}


def group_frames(frames: Sequence[int], gap: int = 8) -> list[list[int]]:
    if gap < 0:
        raise ValueError("grouping gap must be non-negative")
    groups: list[list[int]] = []
    for frame in sorted(set(frames)):
        if not groups or frame - groups[-1][-1] > gap:
            groups.append([frame])
        else:
            groups[-1].append(frame)
    return groups


def selected_frames(intervals: Sequence[RepairInterval]) -> set[int]:
    return {frame for interval in intervals for frame in range(interval.start, interval.end + 1)}


def load_intervals(path: Path) -> list[RepairInterval]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("interval file must contain a JSON array")
    return [RepairInterval(int(item["start"]), int(item["end"])) for item in data]


def splice_reviewed_intervals(current: Path, corrected: Path, output: Path,
                              intervals: Sequence[RepairInterval], *, crf: int = 18) -> None:
    """Use corrected frames only inside approved intervals and preserve current audio."""
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    current_identity, corrected_identity = source_identity(current), source_identity(corrected)
    keys = ("frame_count", "width", "height", "fps")
    if any(getattr(current_identity, key) != getattr(corrected_identity, key) for key in keys):
        raise ValueError("current and corrected videos have incompatible media geometry")
    chosen = selected_frames(intervals)
    if not chosen or max(chosen) >= current_identity.frame_count:
        raise ValueError("repair intervals must select frames inside the video")

    current_capture, corrected_capture = cv2.VideoCapture(str(current)), cv2.VideoCapture(str(corrected))
    descriptor, staging_name = _exclusive_temporary(output, preserve_suffix=True)
    staging = Path(staging_name)
    os.close(descriptor)
    staging.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([
        "ffmpeg", "-n", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
        "-pix_fmt", "bgr24", "-s", f"{current_identity.width}x{current_identity.height}",
        "-r", f"{current_identity.fps:.12g}", "-i", "pipe:0", "-i", str(current),
        "-map", "0:v:0", "-map", "1:a?", "-c:v", "libx264", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(staging),
    ], stdin=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for index in range(current_identity.frame_count):
            current_ok, current_frame = current_capture.read()
            corrected_ok, corrected_frame = corrected_capture.read()
            if not current_ok or not corrected_ok:
                raise RuntimeError(f"failed to decode frame {index}")
            process.stdin.write((corrected_frame if index in chosen else current_frame).tobytes())
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("FFmpeg interval splice failed")
        probe = _probe_media(staging)
        video = next(item for item in probe["streams"] if item.get("codec_type") == "video")
        if int(video.get("nb_read_frames") or 0) != current_identity.frame_count:
            raise ValueError("spliced output frame count does not match current video")
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(staging),
                        "-map", "0", "-f", "null", "-"], check=True)
        if output.exists():
            raise FileExistsError(f"target appeared during processing: {output}")
        os.replace(staging, output)
    except Exception:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        current_capture.release()
        corrected_capture.release()
        staging.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--corrected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--intervals", type=Path, required=True,
                        help="reviewed zero-based inclusive intervals as a JSON array")
    parser.add_argument("--crf", type=int, default=18)
    args = parser.parse_args(argv)
    splice_reviewed_intervals(args.current, args.corrected, args.output,
                              load_intervals(args.intervals), crf=args.crf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
