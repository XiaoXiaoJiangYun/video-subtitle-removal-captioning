# Security Policy

中文使用说明见 [README.zh-CN.md](README.zh-CN.md)。

## Supported versions

The `main` branch is the supported public source release. No binary, model, media,
or generated-artifact distribution is provided by this repository.

## Reporting

Report suspected vulnerabilities privately through the repository owner's GitHub
security-advisory channel. Do not open a public issue containing exploit details,
credentials, private paths, personal data, or sample media.

## Operational safety

Treat videos, captions, OCR caches, ASR results, and model downloads as untrusted.
Use isolated environments, validate paths, pin dependencies for deployments, and
review FFmpeg/PaddleOCR advisories. The tools refuse to overwrite outputs, but
operators remain responsible for backups and for media/data rights.

Public source status does not grant rights to third-party media, voices, faces,
models, datasets, codecs, or executables.
