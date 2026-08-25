# Caption timing guidance

ASR segment timestamps are recognition containers, not automatically safe subtitle display intervals. A segment can span multiple utterances, pauses, music, or silence, especially when the decoder carries context across windows. Rendering one caption for the full segment can make it appear early, remain on screen after the words finish, or disappear too late.

## Safe workflow

1. Transcribe the exact media that will be captioned. Do not reuse a reviewed timeline from another edit unless the source fingerprint and cut are identical.
2. Keep word timestamps when the ASR backend supports them.
3. Use overlapping short windows or reliable voice-activity segmentation to locate each utterance. Merge duplicate window results instead of treating every window as a separate cue.
4. Start a cue at the first spoken word and end it at the last spoken word, allowing only a small, consistent display margin. Keep pauses between utterances as gaps unless the text is intentionally held for readability.
5. Reject credit-watermark hallucinations, zero-duration words, punctuation-only output, and long low-confidence spans.
6. Treat reference text as wording evidence only. It can correct names and terminology, but it cannot determine when a line is spoken.
7. Review the first cue, every cue transition, and the final cue against the source audio before publishing. Successful video decoding does not prove synchronization.

## Configuration guidance

Use reviewed or exact replacement mappings for wording corrections, not fuzzy whole-line rewriting. Keep timing evidence and text evidence separate in the output provenance so a later review can tell whether a cue boundary came from audio, word timestamps, or human review.

The caption command remains sequential and refuses to overwrite existing outputs. Keep an immutable backup before replacing a reviewed result.
