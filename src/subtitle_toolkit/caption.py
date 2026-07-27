"""Generic, conservative caption pipeline.

ASR supplies the complete timeline.  Reference, OCR, novel, and seed evidence only
improve an existing cue; none of them is required for an output document.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

SCHEMA_VERSION = "CaptionDocument/1.0"
ASR_CACHE_SCHEMA = "subtitle-toolkit/asr-cache/1"
SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,\.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,\.](\d{3})")
NATURAL_PARTS = re.compile(r"(\d+)")


@dataclass
class Cue:
    start: float
    end: float
    text: str
    source: str = "asr"
    confidence: float | None = None
    asr_text: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.start, self.end = float(self.start), max(float(self.end), float(self.start) + 0.05)
        self.text = clean_text(self.text)
        if not self.asr_text:
            self.asr_text = self.text


@dataclass
class CaptionDocument:
    media: dict[str, Any]
    cues: list[Cue]
    schema: str = SCHEMA_VERSION
    style: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "media": self.media, "style": self.style,
                "provenance": self.provenance, "cues": [asdict(c) for c in self.cues]}


def natural_key(value: str | Path) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in NATURAL_PARTS.split(str(value))]


def clean_text(value: Any) -> str:
    text = str(value or "").replace("\ufeff", "").replace("\ufffd", "")
    text = text.translate(str.maketrans({"﹑": "、", "﹔": "；", "﹕": "："}))
    return re.sub(r"\s+", " ", text).strip()


def parse_srt_time(value: str) -> float:
    h, m, s, ms = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,\.](\d{3})", value.strip()).groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def format_srt_time(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    h, milliseconds = divmod(milliseconds, 3_600_000)
    m, milliseconds = divmod(milliseconds, 60_000)
    s, milliseconds = divmod(milliseconds, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{milliseconds:03d}"


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.lstrip("\ufeff").strip()):
        lines = [line.strip() for line in block.splitlines()]
        timing_at = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_at is None:
            continue
        match = SRT_TIME.search(lines[timing_at])
        body = clean_text(" ".join(lines[timing_at + 1:]))
        if match and body:
            values = match.groups()
            start = parse_srt_time(":".join(values[:3]) + "," + values[3])
            end = parse_srt_time(":".join(values[4:7]) + "," + values[7])
            cues.append(Cue(start, end, body, source="srt", asr_text=""))
    return cues


def load_srt(path: Path) -> list[Cue]:
    return parse_srt(path.read_text(encoding="utf-8-sig"))


def load_asr(path: Path) -> list[Cue]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = data.get("segments", data) if isinstance(data, dict) else data
    cues = []
    for item in segments:
        if not item.get("text"):
            continue
        evidence = []
        if item.get("words"):
            evidence.append({"source": "asr_words", "words": item["words"]})
        cues.append(Cue(item["start"], item["end"], item.get("text", ""), "asr",
                        item.get("confidence", item.get("avg_logprob")), item.get("text", ""),
                        evidence=evidence))
    return cues


def load_ocr(path: Path) -> list[Cue]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict): data = data.get("cues", data.get("segments", data.get("results", [])))
    return [Cue(x["start"], x["end"], x.get("text", ""), "ocr", x.get("score"), "")
            for x in data if x.get("text")]


def load_caption_document(path: Path) -> CaptionDocument:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"unsupported CaptionDocument schema in {path}")
    return CaptionDocument(data.get("media", {}), [Cue(**cue) for cue in data.get("cues", [])],
                           data["schema"], data.get("style", {}), data.get("provenance", {}))


def overlap(left: Cue, right: Cue) -> float:
    return max(0.0, min(left.end, right.end) - max(left.start, right.start))


def best_overlap(cue: Cue, candidates: Iterable[Cue], minimum: float = .15) -> Cue | None:
    ranked = [(overlap(cue, item), item) for item in candidates]
    return max(ranked, default=(0.0, None), key=lambda item: item[0])[1] if ranked and max(ranked, key=lambda x: x[0])[0] >= minimum else None


def merge_timeline(asr: list[Cue], reference: list[Cue] | None = None, ocr: list[Cue] | None = None,
                   reference_authoritative: bool = False) -> list[Cue]:
    """Keep ASR coverage; references are evidence unless explicitly reviewed/authoritative."""
    if reference_authoritative and reference:
        result = [Cue(**asdict(cue)) for cue in reference]
        for cue in result:
            hit = best_overlap(cue, asr)
            if hit:
                cue.asr_text = hit.asr_text or hit.text
                cue.confidence = 1.0
                cue.source = "reviewed_reference"
                cue.evidence.append({"source": "asr", "start": hit.start, "end": hit.end,
                                     "text": hit.text, "confidence": hit.confidence})
            ocr_hit = best_overlap(cue, ocr or [])
            if ocr_hit:
                cue.evidence.append({"source": "ocr", "start": ocr_hit.start, "end": ocr_hit.end,
                                     "text": ocr_hit.text, "confidence": ocr_hit.confidence})
        return result
    result = [Cue(**asdict(cue)) for cue in asr]
    for cue in result:
        for label, evidence in (("reference", reference or []), ("ocr", ocr or [])):
            hit = best_overlap(cue, evidence)
            if hit:
                cue.evidence.append({"source": label, "start": hit.start, "end": hit.end, "text": hit.text,
                                     "confidence": hit.confidence})
    return result


def docx_chapters(path: Path) -> list[dict[str, str]]:
    """Extract DOCX paragraphs using only zip+xml and retain Heading styles."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    chapters, current = [], {"title": "Document", "text": ""}
    for paragraph in root.findall(".//w:p", ns):
        text = "".join(paragraph.itertext()).strip()
        style = paragraph.find("w:pPr/w:pStyle", ns)
        style_name = style.get("{%s}val" % ns["w"]) if style is not None else ""
        if text and style_name and style_name.lower().startswith("heading"):
            if current["text"] or current["title"] != "Document": chapters.append(current)
            current = {"title": text, "text": ""}
        elif text:
            current["text"] += ("\n" if current["text"] else "") + text
    if current["text"] or not chapters: chapters.append(current)
    return chapters


def novel_terms(path: Path | None) -> list[str]:
    if not path or not path.exists(): return []
    paths = sorted(path.glob("*.docx"), key=natural_key) if path.is_dir() else [path]
    texts = []
    for item in paths:
        if item.suffix.lower() == ".docx":
            texts.extend(chapter["title"] + "\n" + chapter["text"] for chapter in docx_chapters(item))
        elif item.suffix.lower() == ".txt":
            texts.append(item.read_text(encoding="utf-8"))
    words = re.findall(r"[\u4e00-\u9fff]{2,8}", "\n".join(texts))
    return sorted(set(words), key=lambda word: (-len(word), word))


def apply_novel_matches(cues: list[Cue], terms: list[str], threshold: float = .92) -> None:
    """Correct a probable proper-name span only; never replace a full adapted line."""
    for cue in cues:
        raw = cue.text
        for term in terms:
            if term in raw or len(term) < 2: continue
            window = len(term)
            scored = [(difflib.SequenceMatcher(None, raw[i:i + window], term).ratio(), raw[i:i + window])
                      for i in range(max(0, len(raw) - window + 1))]
            if not scored: continue
            score, candidate = max(scored)
            cue.candidates.append({"type": "novel_term", "asr_span": candidate, "candidate": term, "score": round(score, 4)})
            # Never treat an arbitrary one-character difference in a two-character word as
            # a homophone: common Chinese spans would otherwise be rewritten into unrelated names.
            differences = sum(a != b for a, b in zip(candidate, term))
            if score >= threshold and candidate and len(candidate) <= len(term) + 1 and differences <= 2:
                cue.text = raw.replace(candidate, term, 1)
                cue.source = "asr+novel_term"
                break


def ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    h, centiseconds = divmod(centiseconds, 360000)
    m, centiseconds = divmod(centiseconds, 6000)
    s, cs = divmod(centiseconds, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def strip_terminal_period(value: Any) -> str:
    """Remove final statement periods and commas; preserve questions and exclamations."""
    return re.sub(r"[。.．，,]\s*$", "", str(value or ""))


def single_line_text(value: Any) -> str:
    """Normalize all output text to one visual line (ASS must not contain \\N)."""
    text = clean_text(str(value or "").replace("\\N", " ").replace("\\n", " "))
    return strip_terminal_period(text)


def escape_ass(value: str) -> str:
    return single_line_text(value).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _video_size(media: dict[str, Any] | None) -> tuple[int, int]:
    media = media or {}
    width, height = media.get("width"), media.get("height")
    if not width or not height:
        stream = next((s for s in media.get("ffprobe", {}).get("streams", []) if s.get("codec_type") == "video"), {})
        width, height = width or stream.get("width"), height or stream.get("height")
    return max(1, int(width or 1920)), max(1, int(height or 1080))


def _ass_colour(value: Any, default: str) -> str:
    value = str(value or default).strip()
    if value.startswith("#") and len(value) in (7, 9):
        rgb = value[1:7]
        alpha = value[7:9] if len(value) == 9 else "00"
        return f"&H{alpha}{rgb[4:6]}{rgb[2:4]}{rgb[:2]}"
    return value if value.upper().startswith("&H") else default


def render_srt(cues: list[Cue]) -> str:
    return "\n\n".join(f"{i}\n{format_srt_time(c.start)} --> {format_srt_time(c.end)}\n{single_line_text(c.text)}" for i, c in enumerate(cues, 1)) + "\n"


def _ass_identifier(value: Any, field_name: str) -> str:
    value = str(value)
    if not value or any(char in value for char in ",\r\n{}"):
        raise ValueError(f"ASS {field_name} must be non-empty and contain no commas, newlines, or braces")
    return value


def render_ass(cues: list[Cue], media: dict[str, Any] | None = None, style: dict[str, Any] | None = None) -> str:
    # Backward compatibility for render_ass(cues, style), while explicit media is preferred.
    if style is None and media and not any(k in media for k in ("width", "height", "ffprobe")):
        style, media = media, None
    style, media = style or {}, media or {}
    width, height = _video_size(media)
    name = _ass_identifier(style.get("name", "Default"), "style name")
    font = _ass_identifier(style.get("font", "Microsoft YaHei"), "font")
    size = float(style.get("font_size", float(style.get("font_size_ratio", .055)) * height))
    outline = float(style.get("outline", float(style.get("outline_ratio", .035)) * size))
    shadow = float(style.get("shadow", 0))
    alignment = int(style.get("alignment", 2))
    x = round(width / 2 + float(style.get("center_offset_x", 0)))
    y = round(float(style.get("baseline_ratio", .90)) * height)
    primary = _ass_colour(style.get("primary_color"), "&H00FFFFFF")
    outline_colour = _ass_colour(style.get("outline_color"), "&H00000000")
    bold = -1 if bool(style.get("bold", False)) else 0
    header = f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
    line = f"Style: {name},{font},{size:g},{primary},&H000000FF,{outline_colour},&H80000000,{bold},0,0,0,100,100,0,0,1,{outline:g},{shadow:g},{alignment},0,0,0,1\n"
    events = "\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    return header + line + events + "".join(f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.end)},{name},,0,0,0,,{{\\an2\\pos({x},{y})}}{escape_ass(c.text)}\n" for c in cues)


def _text_weight(text: str) -> float:
    return sum(.55 if char.isascii() and (char.isalnum() or char.isspace()) else .5 if char in "，。！？、；：" else 1 for char in text)


def _word_timestamps(cue: Cue) -> list[dict[str, Any]]:
    """Accept common ASR word-timestamp shapes from candidates or evidence."""
    for item in [*cue.candidates, *cue.evidence]:
        words = item.get("words") or item.get("word_timestamps")
        if isinstance(words, list) and words and all("start" in word and "end" in word for word in words if isinstance(word, dict)):
            return [word for word in words if isinstance(word, dict)]
    return []


def semantic_split_cues(cues: list[Cue], media: dict[str, Any] | None = None, style: dict[str, Any] | None = None) -> list[Cue]:
    """Split oversized cues into sequential, single-line semantic captions."""
    style = style or {}
    width, height = _video_size(media)
    font_size = float(style.get("font_size", float(style.get("font_size_ratio", .055)) * height))
    limit = int(style.get("max_line_chars", max(2, float(style.get("max_width_ratio", .82)) * width / max(font_size, 1))))
    protected = [str(x) for x in style.get("non_splittable_terms", style.get("proper_names", []))]
    result: list[Cue] = []
    for cue in cues:
        text = single_line_text(cue.text)
        if _text_weight(text) <= limit:
            cue.text = text
            result.append(cue)
            continue
        parts, current = [], ""
        # Keep punctuation with the preceding clause.  Only fall back to character boundaries
        # when a clause itself is too wide; protected names are never split internally.
        tokens = re.findall(r"[^，。！？、；：]+[，。！？、；：]?|[，。！？、；：]", text)
        for token in tokens:
            if current and _text_weight(current + token) > limit:
                parts.append(current)
                current = token.lstrip("，。！？、；：")
            else:
                current += token
        if current: parts.append(current)
        normalized: list[str] = []
        for part in parts:
            part = part.strip("，,。．.")
            while _text_weight(part) > limit:
                boundary = max(1, min(len(part) - 1, int(limit)))
                # Avoid flashing a one- or two-character orphan after a width split.
                if _text_weight(part[boundary:]) < min(4, limit * .35):
                    boundary = max(1, round(len(part) / 2))
                semantic_boundaries = set()
                for match in re.finditer(r"是不是|有没有|有什么|的|了|着|过", part):
                    semantic_boundaries.add(match.end())
                for match in re.finditer(r"加入|发出|要|会|能|应该|可以|想要|为了|但是|如果|然后", part):
                    semantic_boundaries.add(match.start())
                semantic_boundaries = {point for point in semantic_boundaries
                                       if point >= 4 and len(part) - point >= 4}
                if semantic_boundaries:
                    boundary = min(semantic_boundaries, key=lambda point: abs(point - boundary))
                for term in protected:
                    at = part.find(term)
                    if at < boundary < at + len(term): boundary = at if at else at + len(term)
                while boundary < len(part) and part[boundary] in "，。！？、；：": boundary += 1
                normalized.append(part[:boundary])
                part = part[boundary:].lstrip("，。！？、；：")
            part = part.strip("，,。．.")
            if part: normalized.append(part)
        weights = [_text_weight(part) for part in normalized]
        total = sum(weights) or len(normalized)
        span = cue.end - cue.start
        # A readable floor is preferred, but never extends the original cue's time range.
        minimum = min(float(style.get("min_display_duration", .25)), span / len(normalized))
        durations = [span * weight / total for weight in weights]
        deficit = sum(max(0.0, minimum - value) for value in durations)
        if deficit:
            adjustable = sum(max(0.0, value - minimum) for value in durations)
            if adjustable:
                durations = [max(minimum, value - deficit * max(0.0, value - minimum) / adjustable) for value in durations]
        words = _word_timestamps(cue)
        word_ends, running = [], 0
        for word in words:
            running += len(clean_text(str(word.get("word", word.get("text", "")))).replace(" ", ""))
            word_ends.append((running, float(word["end"])))
        cursor, consumed = cue.start, 0
        for index, (part, duration) in enumerate(zip(normalized, durations)):
            method = "semantic_punctuation"
            chars = len(part.replace(" ", ""))
            if word_ends and index < len(normalized) - 1:
                consumed += chars
                matched = next((end for count, end in word_ends if count >= consumed), None)
                if matched is not None and cursor + minimum <= matched <= cue.end - minimum * (len(normalized) - index - 1):
                    end, method = matched, "semantic_punctuation+word_timestamps"
                else:
                    end = cursor + duration
            else:
                end = cue.end if index == len(normalized) - 1 else cursor + duration
            if index == len(normalized) - 1: end = cue.end
            child = Cue(cursor, end, part, cue.source, cue.confidence, cue.asr_text,
                        list(cue.candidates), list(cue.evidence))
            child.evidence.append({"split_from": {"start": cue.start, "end": cue.end, "text": text}, "split_method": method})
            result.append(child)
            cursor = end
    return result


def validate_cues(cues: list[Cue], duration: float | None = None) -> list[str]:
    errors = []
    previous = -1.0
    for index, cue in enumerate(cues, 1):
        if not cue.text: errors.append(f"cue {index}: empty text")
        if cue.end <= cue.start: errors.append(f"cue {index}: invalid duration")
        if cue.start < previous - .01: errors.append(f"cue {index}: non-monotonic time")
        if duration is not None and cue.end > duration + 1: errors.append(f"cue {index}: exceeds media duration")
        previous = max(previous, cue.end)
    return errors


def _exclusive_temporary(path: Path, *, preserve_suffix: bool = False) -> tuple[int, Path]:
    """Create a unique same-directory file without ever claiming a stale name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix if preserve_suffix else ".tmp"
    return tempfile.mkstemp(prefix=f".{path.stem if preserve_suffix else path.name}.", suffix=suffix, dir=path.parent)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): raise FileExistsError(f"refusing to overwrite {path}")
    descriptor, temporary_name = _exclusive_temporary(path)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists(): raise FileExistsError(f"refusing to overwrite {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as stream:
        digest.update(stream.read(1024 * 1024))
        if size > 1024 * 1024:
            stream.seek(max(0, size - 1024 * 1024))
            digest.update(stream.read(1024 * 1024))
    return {"size": size, "edge_sha256": digest.hexdigest()}


def _asr_config(model_name: str, language: str | None) -> dict[str, Any]:
    return {"model": model_name, "language": language, "word_timestamps": True,
            "vad_filter": False, "condition_on_previous_text": False}


def save_asr_cache(path: Path, media: Path, cues: list[Cue], model_name: str,
                   language: str | None) -> None:
    payload = {"schema": ASR_CACHE_SCHEMA, "source": _source_fingerprint(media),
               "config": _asr_config(model_name, language),
               "segments": [asdict(cue) for cue in cues]}
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_reusable_asr_cache(path: Path, media: Path, model_name: str,
                            language: str | None) -> list[Cue]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # Legacy segment JSON remains loadable, but cannot be trusted as a reusable cache.
    if not isinstance(data, dict) or data.get("schema") != ASR_CACHE_SCHEMA:
        raise ValueError(f"ASR cache lacks reusable source/config fingerprint: {path}")
    if data.get("source") != _source_fingerprint(media):
        raise ValueError("ASR cache does not match the input media")
    if data.get("config") != _asr_config(model_name, language):
        raise ValueError("ASR cache does not match the requested model/language")
    return load_asr(path)


def ffprobe(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels:stream_tags=language,title",
        "-of", "json", str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def media_from_probe(path: Path, probe: dict[str, Any]) -> dict[str, Any]:
    video = next(stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video")
    audio = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"), {})
    return {
        "path": str(path),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": video.get("avg_frame_rate"),
        "duration": float(probe.get("format", {}).get("duration") or 0),
        "audio_codec": audio.get("codec_name"),
        "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        "channels": int(audio["channels"]) if audio.get("channels") else None,
        "ffprobe": probe,
    }


def discover_sidecar(media: Path, roots: list[Path]) -> Path | None:
    for root in roots:
        for suffix in (".srt", ".ass", ".vtt"):
            candidate = root / (media.stem + suffix)
            if candidate.exists(): return candidate
    return None


def extract_embedded_srt(media: Path, probe: dict[str, Any]) -> list[Cue] | None:
    """Best-effort discovery of the first embedded subtitle stream via ffmpeg."""
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "subtitle"]
    if not streams or not shutil.which("ffmpeg"):
        return None
    with tempfile.TemporaryDirectory(prefix="caption-embedded-") as temporary:
        path = Path(temporary) / "subtitle.srt"
        try:
            subprocess.run(["ffmpeg", "-n", "-v", "error", "-i", str(media), "-map", "0:s:0", str(path)],
                           check=True, capture_output=True, text=True)
            return load_srt(path) if path.exists() else None
        except subprocess.CalledProcessError:
            return None


def select_style(stem: str, style_config: Path | None,
                 media: dict[str, Any] | None = None) -> dict[str, Any]:
    if not style_config or not style_config.exists(): return {}
    data = json.loads(style_config.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    base = dict(data.get("series_style", {}))
    if stem in profiles:
        base.update(profiles[stem])
        return base
    width, height = _video_size(media)
    inherited = data.get("resolution_profiles", {}).get(f"{width}x{height}")
    if inherited in profiles:
        inherited_style = profiles[inherited]
        series_terms = list(map(str, base.get("proper_names", [])))
        base.update(inherited_style)
        base["proper_names"] = list(dict.fromkeys(series_terms + list(map(str, inherited_style.get("proper_names", [])))))
        base["inherited_from"] = inherited
        return base
    return base


def seed_document(stem: str, directory: Path | None) -> CaptionDocument | None:
    if not directory: return None
    for path in (directory / f"{stem}.caption.json", directory / f"{stem}.json"):
        if path.exists(): return load_caption_document(path)
    srt = directory / f"{stem}.srt"
    if srt.exists(): return CaptionDocument({}, load_srt(srt), provenance={"seed": str(srt)})
    return None


def run_asr(media: Path, cpu_threads: int, model: Any | None = None,
            model_name: str = "medium", language: str | None = "zh") -> list[Cue]:
    try:
        if model is None:
            from faster_whisper import WhisperModel
            model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
    except ImportError as exc:
        raise RuntimeError("install ASR support with: python -m pip install -e \".[asr]\"") from exc
    segments, _ = model.transcribe(str(media), language=language, word_timestamps=True,
                                   vad_filter=False, condition_on_previous_text=False)
    cues = []
    for segment in segments:
        if not clean_text(segment.text):
            continue
        words = [{"start": word.start, "end": word.end, "word": word.word,
                  "probability": word.probability} for word in segment.words or []]
        cues.append(Cue(segment.start, segment.end, segment.text, "asr", segment.avg_logprob,
                        segment.text, evidence=[{"source": "asr_words", "words": words}]))
    return cues


def process_media(media: Path, args: argparse.Namespace) -> dict[str, Any]:
    stem = media.stem
    probe = ffprobe(media)
    media_info = media_from_probe(media, probe)
    duration = media_info["duration"] or None
    seed = seed_document(stem, args.seed_evidence)
    cache_asr = args.cache / "asr_candidates" / args.asr_model / f"{stem}.json"
    asr_path = cache_asr
    if seed:
        document = seed
        document.media = media_info
        document.provenance["seed_imported"] = True
    elif asr_path.exists() and args.reuse_cache:
        document = CaptionDocument(media_info, load_reusable_asr_cache(
            asr_path, media, args.asr_model, args.asr_language),
            provenance={"asr": str(asr_path), "cache_validated": True})
    elif args.run_asr:
        cues = run_asr(media, args.cpu_threads, model_name=args.asr_model,
                       language=args.asr_language)
        save_asr_cache(asr_path, media, cues, args.asr_model, args.asr_language)
        document = CaptionDocument(media_info, cues,
                                   provenance={"asr": "generated", "asr_cache": str(asr_path)})
    else:
        raise RuntimeError("no ASR cache; pass --reuse-cache with a cache or explicitly --run-asr")
    reference = load_srt(args.reference / f"{stem}.srt") if args.reference and (args.reference / f"{stem}.srt").exists() else None
    sidecar = discover_sidecar(media, [args.input, args.reference] if args.reference else [args.input])
    if reference is None and sidecar and sidecar.suffix == ".srt": reference = load_srt(sidecar)
    if reference is None:
        reference = extract_embedded_srt(media, probe)
    ocr_path = args.cache / "original_subtitle_ocr" / f"{stem}.json"
    ocr = load_ocr(ocr_path) if ocr_path.exists() else None
    document.cues = merge_timeline(document.cues, reference, ocr)
    apply_novel_matches(document.cues, novel_terms(args.novel))
    document.style = select_style(stem, args.style_config, document.media)
    document.cues = semantic_split_cues(document.cues, document.media, document.style)
    errors = validate_cues(document.cues, duration)
    if errors: raise ValueError("; ".join(errors))
    return {"document": document, "errors": errors, "probe": probe}


def _ffmpeg_filter_path(path: Path) -> str:
    """Escape a Windows path for FFmpeg filtergraph option parsing."""
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_media(media: Path, ass: Path, target: Path) -> None:
    if target.exists(): raise FileExistsError(f"refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = _exclusive_temporary(target, preserve_suffix=True)
    staging = Path(staging_name)
    os.close(descriptor)
    staging.unlink()  # reserve a unique name; FFmpeg -n creates the actual media file.
    command = ["ffmpeg", "-n", "-hide_banner", "-loglevel", "error", "-i", str(media),
               "-vf", f"ass=filename='{_ffmpeg_filter_path(ass)}'", "-c:v", "libx264",
               "-preset", "fast", "-crf", "18", "-threads", "4", "-c:a", "copy",
               "-movflags", "+faststart", str(staging)]
    try:
        subprocess.run(command, check=True)
        # Decode every stream, rather than validating only container metadata.
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(staging), "-map", "0", "-f", "null", "-"], check=True)
        probe = ffprobe(staging)
        if not probe.get("format", {}).get("duration") or not any(s.get("codec_type") == "video" for s in probe.get("streams", [])):
            raise ValueError("rendered media is missing duration or video stream")
        if target.exists():
            raise FileExistsError(f"target appeared during render: {target}")
        os.replace(staging, target)
    finally:
        if staging.exists(): staging.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--novel", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("cache"))
    parser.add_argument("--seed-evidence", type=Path, help="generic directory of complete CaptionDocument JSON or SRT")
    parser.add_argument("--style-config", type=Path)
    parser.add_argument("--files", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-after")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reuse-cache", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--run-asr", action="store_true", help="explicitly enable low-load CPU ASR; never default")
    parser.add_argument("--asr-model", default="medium", help="faster-whisper model name or local model path")
    parser.add_argument("--asr-language", default="zh", help="language code; use 'auto' for detection")
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args(argv)
    if args.asr_language.casefold() == "auto": args.asr_language = None
    if args.cpu_threads < 1: parser.error("--cpu-threads must be positive")
    if args.limit is not None and args.limit < 0: parser.error("--limit must not be negative")
    media = sorted(args.input.glob("*.mp4"), key=lambda path: natural_key(path.name))
    if args.files:
        wanted = {Path(value).name for value in args.files} | {Path(value).stem for value in args.files}
        media = [p for p in media if p.name in wanted or p.stem in wanted]
    if args.start_after:
        media = [p for p in media if natural_key(p.name) > natural_key(args.start_after)]
    if args.limit is not None: media = media[:args.limit]
    if not media:
        print("No matching MP4 files.", file=sys.stderr); return 1
    failures = []
    for item in media:  # Intentional sequential processing, preserving cache/media locality.
        try:
            print(f"Processing {item.name}")
            result = process_media(item, args)
            document: CaptionDocument = result["document"]
            base = args.output / item.stem
            if args.dry_run:
                print(f"  dry-run: {len(document.cues)} cues")
                continue
            atomic_write(base.with_suffix(".caption.json"), json.dumps(document.to_dict(), ensure_ascii=False, indent=2) + "\n")
            atomic_write(base.with_suffix(".srt"), render_srt(document.cues))
            ass_path = base.with_suffix(".ass")
            atomic_write(ass_path, render_ass(document.cues, document.media, document.style))
            report = {"media": str(item), "cue_count": len(document.cues), "embedded_subtitle_streams": [s for s in result["probe"].get("streams", []) if s.get("codec_type") == "subtitle"], "issues": []}
            atomic_write(base.with_suffix(".review.json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            if args.render: render_media(item, ass_path, base.with_suffix(".captioned.mp4"))
        except Exception as exc:  # Keep batch failures isolated.
            failures.append({"media": str(item), "error": str(exc)})
            print(f"FAILED {item.name}: {exc}", file=sys.stderr)
    if failures:
        print(json.dumps({"failures": failures}, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
