"""Sentence snapping inside the padded window."""
from hayclips.captions.snap import snap
from hayclips.render import _clip_words


def seg(a, b, t, sp=1):
    return {"start": a, "end": b, "text": t, "speaker": sp}


def test_snaps_to_sentence_edges_and_keeps_reaction_button():
    segs = [seg(0.5, 3.0, "- Նախորդ միտքը։"), seg(5.0, 9.0, "- Ի՞նչ ես կարդում հիմա։"), seg(9.5, 20.0, "Երկար պատմություն։"),
            seg(20.2, 21.0, "- Հա։", 2), seg(22.0, 25.0, "- Հաջորդ թեման")]
    s, e, note = snap(segs, 5.0, 20.0)
    assert s == 4.85 and e == 21.4 and "reaction" in note


def test_no_sentence_punctuation_falls_back_to_segment_edges():
    segs = [seg(0.4, 4.0, "բառեր առանց վերջակետի"), seg(4.5, 9.8, "ու շարունակում ենք"), seg(10.2, 15.0, "դեռ խոսում ենք")]
    s, e, note = snap(segs, 4.6, 10.0)          # old code raised ValueError: min() arg is an empty sequence
    assert (s, e) == (4.35, 10.15) and "no sentence-ending punctuation" in note


def test_empty_transcript_keeps_planned_cut():
    assert snap([], 5.0, 40.0) == (5.0, 40.0, "empty transcript: kept the planned cut")
    assert snap([seg(1, 2, "   ")], 5.0, 40.0)[2].startswith("empty transcript")


def test_word_starting_just_before_a_manual_cut_is_kept():
    raw = {"segments": [seg(0.9, 3.0, "Առաջին բառը այստեղ է։")],
           "words": [{"text": "Առաջին", "start": 0.9, "end": 1.4, "speaker": 1},
                     {"text": "բառը", "start": 1.45, "end": 1.8, "speaker": 1}]}
    words, segs, _ = _clip_words(raw, 1.0, 3.0)  # cut lands 0.1 s into the first word
    assert [w[2] for w in words] == ["Առաջին", "բառը"] and words[0][0] == 0.0
    assert segs and segs[0].start == 0.0
