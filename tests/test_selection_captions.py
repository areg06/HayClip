"""Caption parsing and explainable candidate selection."""
import pytest

from hayclips.errors import ValidationError
from hayclips.models import candidate_id
from hayclips.selection import candidates, explain, parse_srt, pick, sentence_units

ROLLING = """1
00:00:00,160 --> 00:00:05,440
Երեկ մենք գնացինք շուկա, գնեցինք հաց

2
00:00:02,840 --> 00:00:07,759
ու հետո տուն վերադարձանք։

3
00:00:05,440 --> 00:00:09,800
հաջորդ օրը, նորից փորձեցինք նույնը
"""


def test_valid_armenian_captions_and_rolling_cues_are_clamped():
    lines = parse_srt(ROLLING)
    assert [l.text.split()[0] for l in lines] == ["Երեկ", "ու", "հաջորդ"]
    assert lines[0].end == pytest.approx(2.84) and lines[1].end == pytest.approx(5.44)   # no overlap
    assert all(a.end <= b.start for a, b in zip(lines, lines[1:]))


def test_bom_and_crlf_are_accepted():
    assert len(parse_srt("﻿" + ROLLING.replace("\n", "\r\n"))) == 3


@pytest.mark.parametrize("raw", ["", "not an srt at all", "1\n00:00:01,000 --> 00:00:00,500\nback in time\n",
                                 "1\n00:00:xx,000 --> 00:00:02,000\nbad stamp\n"])
def test_malformed_or_missing_captions_raise_a_clear_error(raw):
    with pytest.raises(ValidationError, match="No timed subtitle cues"):
        parse_srt(raw)


def _long_srt():
    blocks, t = [], 0.0
    sentences = ["Ո՞րն է քո ամենասիրած գիրքը։", "Ու բան, ըըը չգիտեմ ինչ ասեմ էլի։",
                 "Իրականում սա շատ կարևոր հարց է մեր համար։", "[ծիծաղ] Լավ, հետո ինչ եղավ այդ մարդու հետ։"]
    for k in range(60):
        s = sentences[k % 4] + " " + " ".join(["բառ"] * 6) + "։"
        blocks.append(f"{k+1}\n00:{int(t // 60):02}:{int(t % 60):02},000 --> 00:{int((t+4) // 60):02}:{int((t+4) % 60):02},000\n{s}\n")
        t += 4.2
    return "\n".join(blocks)


def test_candidates_have_deterministic_ids_and_explanations():
    units = sentence_units(parse_srt(_long_srt()))
    a = candidates(units, 260, 25, 60)
    b = candidates(units, 260, 25, 60)
    assert a and [c.id for c in a] == [c.id for c in b]
    top = a[0]
    assert top.id == candidate_id(top.start, top.end)
    f = top.features
    assert {"ending_sentence", "opening_question", "weak_starter", "fillers_per_min", "laughs", "pace",
            "silence_s", "length_bonus"} <= set(f)
    assert "not a virality prediction" in f["note"]
    total = (f["ending_sentence"] + f["opening_question"] + f["opening_specific"] + f["weak_starter"] + f["pace"]
             + f["laugh_bonus"] + f["filler_penalty"] + f["silence_penalty"] + f["length_bonus"])
    assert total == pytest.approx(top.score, abs=0.01)
    assert "ending" in explain(top)


def test_weak_starter_is_penalised_and_visible():
    units = sentence_units(parse_srt(_long_srt()))
    weak = [c for c in candidates(units, 260, 25, 60) if c.text.startswith("Ու բան")]
    assert weak and weak[0].features["weak_starter_word"] == "ու" and weak[0].features["weak_starter"] == -0.8


def test_pick_keeps_windows_diverse():
    units = sentence_units(parse_srt(_long_srt()))
    chosen = pick(candidates(units, 260, 25, 60), 3)
    for i, x in enumerate(chosen):
        for y in chosen[i + 1:]:
            overlap = max(0, min(x.end, y.end) - max(x.start, y.start))
            assert overlap / min(x.end - x.start, y.end - y.start) < 0.35


def test_skip_start_and_end_are_respected():
    units = sentence_units(parse_srt(_long_srt()))
    assert all(c.start >= 30 and c.end <= 230 for c in candidates(units, 260, 25, 60, 30, 30))
