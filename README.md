# Video Subtitle Removal Captioning

A source-only Python toolkit for two practical workflows:

- build SRT/ASS captions and optionally burn them into video with FFmpeg;
- detect subtitle boxes with externally installed PaddleOCR, cache detections,
  create masks, and remove pixels with OpenCV Telea or Navier-Stokes inpainting.

**Repository status:** private and pre-publication. It has been staged for audit,
not approved for publication. Any public release requires a new source,
dependency, model, data, trademark, privacy, and provenance audit.

The repository intentionally contains no media, generated captions, caches,
logs, models, binaries, private paths, company data, SVN metadata, nested Git
metadata, ProPainter, vendored RAFT, vendored scene-detection code, STTN, or LaMa.
It does not claim to implement STTN or LaMa.

## Requirements

- Python 3.10+
- OpenCV and NumPy (installed by the base package)
- system `ffmpeg` and `ffprobe` for muxing and caption rendering
- optional PaddleOCR/PaddlePaddle plus separately obtained model files for OCR
- optional faster-whisper plus separately obtained model files for ASR

Install for development:

```bash
python -m pip install -e ".[test]"
```

Install OCR support only after reviewing the exact package and model licenses. The
adapter targets the PaddleOCR 2.x `PaddleOCR(...).ocr(..., cls=False)` API, so the
optional dependency is intentionally constrained to `paddleocr>=2.7,<3` and
`paddlepaddle>=2.6,<3`:

```bash
python -m pip install -e ".[ocr]"
```

Install ASR support (model weights are downloaded by faster-whisper unless the
`--asr-model` value is a local model directory):

```bash
python -m pip install -e ".[asr]"
```

ASR runs only with `--run-asr`. `--asr-model` defaults to `medium` and
`--asr-language` defaults to `zh`; pass `--asr-language auto` to request language
detection. A generated ASR run writes a same-directory atomic JSON cache containing
segments, source size/edge SHA-256, and the complete transcription configuration.
`--reuse-cache` accepts that cache only when both source and configuration match.
Legacy plain `segments` JSON remains accepted by import helpers and seed workflows,
but is rejected as a reusable cache because it cannot prove source/config identity.

## Add captions

The caption command processes MP4 files sequentially, uses four CPU threads by
default, refuses to overwrite outputs, emits single-line SRT/ASS, removes only
terminal statement periods/commas while preserving `?`/`!`, and writes explicit
ASS `\\an2\\pos(x,y)` positioning.

Use reviewed SRT or CaptionDocument seeds:

```bash
subtitle-toolkit caption --input ./input --output ./output \
  --seed-evidence ./seeds --style-config ./configs/caption-style.example.json
```

Or explicitly enable optional ASR (it is never automatic):

```bash
subtitle-toolkit caption --input ./input --output ./output --run-asr \
  --asr-model medium --asr-language zh --cpu-threads 4 \
  --style-config ./configs/caption-style.example.json
```

Add `--render` to burn ASS captions through FFmpeg. Existing targets cause a
failure rather than being replaced. Use `--files`, `--start-after`, and `--limit`
for deterministic sequential batches.

## Remove subtitles

Detection and removal are intentionally separate through a JSON cache. First
create and inspect a detection cache, then reuse it without OCR for inpainting:

```bash
subtitle-toolkit remove --config ./configs/removal.example.json \
  --input input.mp4 --cache detections.json --detect-only

subtitle-toolkit remove --config ./configs/removal.example.json \
  --input input.mp4 --output cleaned-review.mp4 \
  --cache detections.json --reuse-cache --inpaint-method ns
```

ROI values are normalized `TOP BOTTOM LEFT RIGHT`. `--config` loads the fields in
`configs/removal.example.json`; an explicitly supplied CLI option overrides its
config value. Review cached boxes before a long run: OCR false positives can remove
legitimate image content. The cache is bound to input size, edge hash, dimensions,
FPS, and frame count. Frames are processed sequentially and the decoded count must
match validated positive source metadata. Output is written to a unique same-folder
staging file with FFmpeg `-n`, fully decoded and checked with ffprobe, then atomically
renamed only if the final target is still absent. Cache and caption text files use
the same exclusive unique-temp/no-overwrite policy.

Only OpenCV Telea and Navier-Stokes removal are included. Advanced backends from the
original/private workflow (including ProPainter, RAFT, STTN, LaMa, and vendored
scene-detection implementations) are deliberately excluded because their source,
license, model, and provenance were not suitable for inclusion. OpenCV inpainting
is local spatial reconstruction, not temporal generative video inpainting;
difficult backgrounds may need manual masks or a different, separately audited
implementation.

## Tests

Tests use generated NumPy images and temporary synthetic videos only:

```bash
python -m pytest -q
```

## Licensing and dependencies

Repository code is offered under Apache-2.0. Dependencies, executables, codecs,
and model weights are separate works with their own terms. Read
`THIRD_PARTY_NOTICES.md` before distribution. Apache-2.0 compatibility of Python
code does not make PaddleOCR/ASR model weights Apache-licensed. FFmpeg licensing
depends on its build configuration, linked libraries, and distribution method;
codec patents are a separate issue.
