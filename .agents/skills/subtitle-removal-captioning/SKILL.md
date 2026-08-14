---
name: subtitle-removal-captioning
description: Add, generate, inspect, or burn video captions and detect, cache, mask, or remove hardcoded subtitles with this repository. Use whenever the user mentions subtitle removal, OCR subtitle boxes, inpainting, SRT/ASS generation, caption positioning, or batch caption rendering.
---

# Subtitle Removal and Captioning

Use the repository's existing commands rather than creating ad hoc video scripts.

## Choose the workflow

- For caption creation, SRT/ASS rendering, ASR evidence, or burned captions, use
  `subtitle-toolkit caption`.
- For hardcoded subtitle detection, mask caching, or OpenCV inpainting, use
  `subtitle-toolkit remove`.
- For a reviewed correction video and explicit approved frame intervals, use
  `subtitle-toolkit splice`; it does not generate corrected frames itself.
- Do not claim STTN or LaMa support. Only OpenCV `telea` and `ns` are implemented.

## Prepare safely

1. Read `README.md`, `THIRD_PARTY_NOTICES.md`, and the relevant example config.
2. Confirm the input, output, cache, language, and normalized ROI with the user
   when they cannot be inferred. Prefer a lower-screen ROI for subtitles.
3. Check that every output and cache path is new. Commands refuse overwrite; do
   not bypass that protection.
4. Keep media, captions, caches, model weights, and logs outside the repository.
5. Treat OCR/ASR models as external artifacts requiring their own license review.

## Add captions

Prefer reviewed SRT or CaptionDocument seed evidence. Enable ASR only when the
user explicitly requests it and has installed/reviewed the optional dependency.
Preserve sequential processing, four threads, single-line ASS, explicit position,
and punctuation behavior. Start with `--dry-run`; use `--render` only when the
user wants a new burned-caption video.

Example:

```bash
subtitle-toolkit caption --input INPUT_DIR --output NEW_OUTPUT_DIR \
  --seed-evidence SEED_DIR --style-config configs/caption-style.example.json \
  --dry-run
```

## Remove hardcoded subtitles

Create the detection cache first, inspect its JSON boxes, then reuse it for the
inpaint run. Never copy source media or generated cache data into the repository.

```bash
subtitle-toolkit remove --input INPUT.mp4 --cache NEW_CACHE.json \
  --detect-only --roi 0.55 0.98 0.0 1.0
subtitle-toolkit remove --input INPUT.mp4 --output NEW_OUTPUT.mp4 \
  --cache NEW_CACHE.json --reuse-cache --inpaint-method telea
```

The cache identity check protects
against applying boxes to a different source. Explain that OpenCV inpainting may
blur textured or moving backgrounds and that OCR false positives can erase real
content.

## Splice reviewed residual fixes

Use this only after candidates have been visually compared with source frames and a
separately audited process has produced a full corrected staging video. Store approved
zero-based inclusive intervals in JSON, preserve an immutable backup, and write to a
new output path:

```bash
subtitle-toolkit splice --current ACCEPTED.mp4 --corrected CORRECTED.mp4 \
  --intervals REVIEWED.json --output NEW_OUTPUT.mp4
```

The command copies audio from the accepted video and refuses overwrite. It re-encodes
the selected frame stream, so require visual boundary review and full decode validation
before publishing. Never infer approved intervals from OCR metadata alone.

## Validate

- Review several frames before, during, and after subtitle intervals.
- Check that audio is retained and the output decodes with FFmpeg.
- Run `python -m pytest -q` after code changes.
- Never initialize, publish, push, or authenticate unless the user separately
  authorizes it after the required release audit.
