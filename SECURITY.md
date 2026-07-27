# Security Policy

## Supported versions

This repository is private and pre-publication; no version currently receives a
public security-support commitment.

## Reporting

Report suspected vulnerabilities privately to the repository owner through the
private GitHub security-advisory channel once the repository exists. Do not open
a public issue containing exploit details, credentials, private paths, personal
data, or sample media.

## Operational safety

Treat videos, captions, OCR caches, ASR results, and model downloads as untrusted.
Use isolated environments, validate paths, pin dependencies for deployments, and
review FFmpeg/PaddleOCR advisories. The tools refuse to overwrite outputs, but
operators remain responsible for backups and for media/data rights.

Public release requires a new security and privacy audit.
