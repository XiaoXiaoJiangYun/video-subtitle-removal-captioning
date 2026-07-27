import json
from pathlib import Path

import pytest

from subtitle_toolkit.caption import (
    Cue, atomic_write, load_reusable_asr_cache, main, natural_key, parse_srt,
    render_ass, render_srt, save_asr_cache, semantic_split_cues,
    strip_terminal_period,
)
from subtitle_toolkit.__main__ import main as dispatcher_main


def test_caption_behavior_matches_contract():
    assert sorted(["10.mp4", "2.mp4", "1.mp4"], key=natural_key) == ["1.mp4", "2.mp4", "10.mp4"]
    cue = parse_srt("1\n00:00:01,250 --> 00:00:02,500\nfirst\nsecond\n")[0]
    assert (cue.start, cue.end, cue.text) == (1.25, 2.5, "first second")
    assert strip_terminal_period("Question?") == "Question?"
    assert strip_terminal_period("Statement.") == "Statement"


def test_ass_is_single_line_and_explicitly_positioned():
    ass = render_ass([Cue(1.25, 2.5, "a{b}\\c\nnext.")], {"width": 720, "height": 1280}, {
        "font": "Arial", "font_size_ratio": 0.05, "baseline_ratio": 0.8,
    })
    assert "{\\an2\\pos(360,1024)}" in ass
    assert "\\N" not in ass
    assert "a\\{b\\}\\\\c next" in ass
    assert "next." not in ass


def test_split_is_sequential_single_line():
    source = Cue(10, 16, "A long clause that needs splitting, and another clause that also needs splitting.")
    split = semantic_split_cues([source], {"width": 500, "height": 800}, {"font_size": 36, "max_width_ratio": 0.45})
    assert len(split) > 1
    assert split[0].start == 10 and split[-1].end == 16
    assert all(split[index].end == split[index + 1].start for index in range(len(split) - 1))
    assert all("\n" not in cue.text and "\\N" not in cue.text for cue in split)
    assert "\\N" not in render_srt(split)


def test_atomic_write_refuses_overwrite(tmp_path: Path):
    target = tmp_path / "out.srt"
    atomic_write(target, "first")
    with pytest.raises(FileExistsError):
        atomic_write(target, "second")
    assert target.read_text(encoding="utf-8") == "first"
    assert not list(tmp_path.glob("*.tmp"))


def test_ass_rejects_structural_style_injection():
    for style in ({"name": "bad,name"}, {"font": "bad\nfont"}, {"font": "bad{font"}):
        with pytest.raises(ValueError):
            render_ass([Cue(0, 1, "text")], {"width": 64, "height": 48}, style)


def test_asr_cache_validates_source_and_configuration(tmp_path: Path):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"synthetic media bytes")
    cache = tmp_path / "asr.json"
    cues = [Cue(0, 1, "hello")]
    save_asr_cache(cache, media, cues, "tiny", "en")
    assert load_reusable_asr_cache(cache, media, "tiny", "en")[0].text == "hello"
    with pytest.raises(ValueError, match="model/language"):
        load_reusable_asr_cache(cache, media, "medium", "en")
    media.write_bytes(b"changed")
    with pytest.raises(ValueError, match="input media"):
        load_reusable_asr_cache(cache, media, "tiny", "en")


def test_legacy_segments_are_loadable_but_not_reusable(tmp_path: Path):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"source")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "old"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="lacks reusable"):
        load_reusable_asr_cache(legacy, media, "tiny", "en")


def test_dispatcher_forwards_subcommand_help(capsys):
    with pytest.raises(SystemExit) as exc:
        dispatcher_main(["caption", "--help"])
    assert exc.value.code == 0
    assert "--asr-model" in capsys.readouterr().out


def test_negative_limit_is_rejected(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        main(["--input", str(tmp_path), "--output", str(tmp_path / "out"), "--limit", "-1"])
    assert exc.value.code == 2
