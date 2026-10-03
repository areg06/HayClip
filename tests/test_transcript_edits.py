"""Transcript corrections: machine result untouched, words re-spread inside the segment's own time span."""
from hayclips.captions.edits import apply_edits, load_edits, save_edit

RAW = {"segments": [{"text": "- Բարև ձեզ։", "start": 0.4, "end": 1.2, "speaker": 1},
                    {"text": "Ինչպե՞ս եք։", "start": 1.4, "end": 2.2, "speaker": 1}],
       "words": [{"text": "- Բարև", "start": 0.40, "end": 0.80, "speaker": 1}, {"text": "ձեզ։", "start": 0.80, "end": 1.20, "speaker": 1},
                 {"text": "Ինչպե՞ս", "start": 1.40, "end": 1.80, "speaker": 1}, {"text": "եք։", "start": 1.80, "end": 2.20, "speaker": 1}]}


def test_edit_replaces_text_keeps_timing_and_original(tmp_path):
    save_edit(tmp_path, RAW, 0, "Բարև   ձեզ, ընկերներ։\n", "op")
    edits = load_edits(tmp_path)
    assert edits["segments"] == {"0": "Բարև ձեզ, ընկերներ։"} and edits["history"][0]["from"] == "Բարև ձեզ։"
    out = apply_edits(RAW, edits)
    assert RAW["segments"][0]["text"] == "- Բարև ձեզ։"                                  # machine result untouched
    assert out["segments"][0]["text"] == "- Բարև ձեզ, ընկերներ։"
    w = [x for x in out["words"] if x["start"] < 1.3]
    assert [x["text"] for x in w] == ["- Բարև", "ձեզ,", "ընկերներ։"]
    assert w[0]["start"] == 0.4 and abs(w[-1]["end"] - 1.2) < 1e-6                      # same span as before
    assert [x["text"] for x in out["words"] if x["start"] >= 1.3] == ["Ինչպե՞ս", "եք։"]


def test_reverting_to_the_original_removes_the_edit(tmp_path):
    save_edit(tmp_path, RAW, 1, "Ինչպե՞ս էք։", "op")
    save_edit(tmp_path, RAW, 1, "Ինչպե՞ս եք։", "op")
    assert load_edits(tmp_path)["segments"] == {} and len(load_edits(tmp_path)["history"]) == 2
