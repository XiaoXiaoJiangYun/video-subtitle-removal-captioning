"""Source-only tools for adding and removing video subtitles."""

from .caption import CaptionDocument, Cue, render_ass, render_srt
from .removal import DetectionCache, OpenCVInpainter, boxes_to_mask, detect_frames
from .residual import RepairInterval, classify_polygon, splice_reviewed_intervals

__all__ = [
    "CaptionDocument",
    "Cue",
    "DetectionCache",
    "OpenCVInpainter",
    "RepairInterval",
    "boxes_to_mask",
    "classify_polygon",
    "detect_frames",
    "render_ass",
    "render_srt",
    "splice_reviewed_intervals",
]
