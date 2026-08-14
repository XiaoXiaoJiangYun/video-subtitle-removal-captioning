import json
from pathlib import Path

import numpy as np
import pytest

from subtitle_toolkit.residual import (
    RepairInterval, boundary_frames, classify_polygon, detect_candidates,
    group_frames, load_intervals, selected_frames,
)


class FakeDetector:
    def __init__(self, polygons):
        self.polygons = polygons

    def predict(self, frame: np.ndarray):
        return [(np.asarray(points, np.float32), 0.9) for points in self.polygons]


def test_square_single_glyph_is_optional_and_size_limited():
    square = np.array([[10, 10], [49, 10], [49, 49], [10, 49]], np.float32)
    large = np.array([[5, 5], [89, 5], [89, 89], [5, 89]], np.float32)
    assert classify_polygon(square) is None
    assert classify_polygon(square, single_glyph=True) == "single_glyph"
    assert classify_polygon(large, single_glyph=True) is None


def test_line_candidate_remains_available():
    line = np.array([[2, 3], [81, 3], [81, 22], [2, 22]], np.float32)
    assert classify_polygon(line, single_glyph=True) == "line"


def test_candidate_coordinates_are_mapped_back_to_source_roi():
    detector = FakeDetector([[[20, 20], [98, 20], [98, 98], [20, 98]]])
    frame = np.zeros((120, 160, 3), np.uint8)
    boxes = detect_candidates(detector, frame, (30, 40, 130, 110), scale=2,
                              single_glyph=True)
    assert boxes == [{"rect": [40, 50, 79, 89], "score": 0.9, "kind": "single_glyph"}]


def test_boundaries_and_candidate_groups_cover_edge_windows():
    assert boundary_frames(12, [0, 6, 11], 2) == {0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11}
    assert group_frames([1, 2, 10, 18, 19], 8) == [[1, 2, 10, 18, 19]]
    assert group_frames([1, 2, 11], 8) == [[1, 2], [11]]


def test_reviewed_intervals_are_inclusive_and_validated(tmp_path: Path):
    path = tmp_path / "intervals.json"
    path.write_text(json.dumps([{"start": 2, "end": 4}, {"start": 7, "end": 7}]),
                    encoding="utf-8")
    intervals = load_intervals(path)
    assert selected_frames(intervals) == {2, 3, 4, 7}
    with pytest.raises(ValueError):
        RepairInterval(4, 3)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        load_intervals(path)
