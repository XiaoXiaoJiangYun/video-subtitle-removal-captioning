# Third-Party Notices

This file describes external dependencies; none is vendored in this repository.
Version selection and transitive dependencies must be audited before release.

## Prior subtitle-removal project

- Project: YaoFANGUK/video-subtitle-remover
- Source: https://github.com/YaoFANGUK/video-subtitle-remover
- Reviewed baseline: `e109b9ddc1d0e8f153199dfa05c1d767546906d8`
- License: Apache License 2.0
- License text: https://github.com/YaoFANGUK/video-subtitle-remover/blob/e109b9ddc1d0e8f153199dfa05c1d767546906d8/LICENSE

That project informed the original private subtitle-removal workflow. This clean
repository is a source-only implementation and does not vendor its source tree,
Git history, GUI, binaries, model weights, STTN, LaMa, ProPainter, RAFT, or
scene-detection modules. The included OpenCV removal pipeline was rewritten and
substantially modified for cache identity checks, sequential processing, atomic
publication, and output verification. The upstream name is provided for
attribution and does not imply endorsement.

## OpenCV and opencv-python

- Project: https://opencv.org/
- Source: https://github.com/opencv/opencv
- License: Apache License 2.0 for current OpenCV 4.x code
- License text: https://github.com/opencv/opencv/blob/4.x/LICENSE
- Python wheels: https://pypi.org/project/opencv-python/

Wheel contents and bundled native components can differ from source packages.
Audit the selected wheel and its notices rather than assuming the top-level code
license covers every binary component.

## NumPy

- Project/source: https://github.com/numpy/numpy
- License: BSD-3-Clause
- License text: https://github.com/numpy/numpy/blob/main/LICENSE.txt

## PaddleOCR and PaddlePaddle (optional)

- PaddleOCR source: https://github.com/PaddlePaddle/PaddleOCR
- PaddleOCR license: https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE
- PaddlePaddle source: https://github.com/PaddlePaddle/Paddle
- PaddlePaddle license: https://github.com/PaddlePaddle/Paddle/blob/develop/LICENSE

Their source repositories use Apache-2.0, but OCR model weights are distinct
artifacts. Each selected model's model card, download page, dataset provenance,
and license must be reviewed. No OCR model is included or implicitly licensed by
this repository.

## faster-whisper and model weights (optional)

- Source: https://github.com/SYSTRAN/faster-whisper
- License: https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE
- Model information: https://github.com/openai/whisper

Package code, conversion/runtime dependencies, and downloaded ASR weights have
separate provenance and terms. Audit the exact weights and distribution plan.
No model is included.

## FFmpeg (external executable)

- Project: https://ffmpeg.org/
- Legal/licensing guidance: https://ffmpeg.org/legal.html
- Source: https://git.ffmpeg.org/ffmpeg.git

FFmpeg is normally LGPL-2.1-or-later, but a build can become GPL-covered when
GPL components are enabled; `--enable-nonfree` can make it non-redistributable.
Linked codec libraries and static/dynamic linkage affect obligations. This tool
invokes a user-provided executable and does not distribute FFmpeg. Anyone who
bundles it must audit the exact build configuration, provide required notices and
corresponding source, and assess codec patent exposure separately. The caption
render command requests `libx264`, commonly associated with GPL-enabled builds;
that matters if FFmpeg is distributed, even though this repository only invokes
an external executable.

## pytest (development only)

- Source: https://github.com/pytest-dev/pytest
- License: MIT
- License text: https://github.com/pytest-dev/pytest/blob/main/LICENSE

This repository contains no ProPainter, RAFT, scene-detection, STTN, or LaMa code
or weights. Adding any such dependency requires a fresh license and provenance
audit rather than relying on this notice.
