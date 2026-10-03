"""ASS generation: injection safety, hook rules, overflow, Armenian casing."""
import pytest

from hayclips.captions import ass as A
from hayclips.errors import ValidationError


def W(*items):
    return [(a, b, t, False) for a, b, t in items]


def dialogues(doc):
    return [l for l in doc.splitlines() if l.startswith("Dialogue:")]


def test_transcript_text_cannot_inject_tags_or_lines():
    evil = "{\\pos(0,0)\\c&H0000FF&}բարև\nDialogue: 0,0:00:00.00,0:00:09.00,A,,0,0,0,,owned"
    doc, ev = A.build_ass("A", W((0.0, 1.0, evil)), 2.0)
    lines = dialogues(doc)
    assert len(lines) == 1 and "owned" in lines[0]          # still one event, text kept as text
    assert "{\\pos" not in doc and "(/pos(0,0)/c&H0000FF&)" in doc


def test_newline_hook_is_collapsed_into_one_event():
    doc, _ = A.build_ass("A", W((0.0, 1.0, "բարև")), 3.0, hook="Տող\nDialogue: 1,0:0:0.00,0:0:9.00,A,,0,0,0,,x")
    hooks = [l for l in dialogues(doc) if ",Hook," in l]
    assert len(dialogues(doc)) == 2 and len(hooks) == 1 and "Տող Dialogue:" in hooks[0]
    assert len(A.validate_hook("ա\r\nբ\tգ")) == 5


def test_long_hook_is_refused_not_truncated():
    with pytest.raises(ValidationError, match="limit is 45"):
        A.build_ass("A", W((0.0, 1.0, "x")), 2.0, hook="ա" * 46)
    assert A.validate_hook("ա" * 45) == "ա" * 45


def test_armenian_text_is_never_uppercased():
    doc, _ = A.build_ass("C", W((0.0, 1.0, "և"), (1.0, 2.0, "ուսումնասիրություն")), 3.0)
    assert "և" in doc and "ԵՒ" not in doc and "ԵՎ" not in doc


def test_overlong_single_word_gets_its_own_shrunk_screen():
    long_word = "ուսումնասիրություններովհանդերձ"      # 30 characters, longer than style A's 22
    doc, ev = A.build_ass("A", W((0.0, 0.5, "բառ"), (0.5, 2.0, long_word), (2.0, 2.5, "վերջ։")), 3.0)
    screens = [e for e in ev if long_word in e[3]]
    assert screens and all(e[3].startswith("{\\fs42}") for e in screens)   # 58 * 22 / 30
    assert all(long_word not in e[3] or "բառ" not in e[3].replace(long_word, "") for e in screens)


def test_caption_bottom_is_a_parameter():
    doc, ev = A.build_ass("A", W((0.0, 1.0, "բառ")), 2.0, caption_bottom=830)
    assert ",60,60,450,1" in doc
    doc, ev = A.build_ass("B", W((0.0, 1.0, "բառ")), 2.0, caption_bottom=830)
    assert "\\pos(360,785)" in doc
    with pytest.raises(ValidationError):
        A.build_ass("A", W((0.0, 1.0, "բառ")), 2.0, caption_bottom=2000)


def test_font_family_is_configurable():
    doc, _ = A.build_ass("C", W((0.0, 1.0, "բառ")), 2.0, family="Test Sans")
    assert "Style: C,Test Sans SemiBold," in doc and "Style: Hook,Test Sans Black," in doc


# ----- Phase 1d looks ----------------------------------------------------------------------------

def _words():
    return [(0.1, 0.5, "Երևան", True), (0.5, 0.9, "և", False), (0.9, 1.4, "Գյումրի։", False)]


def test_no_look_is_the_legacy_document():
    from hayclips.captions.ass import build_ass
    assert build_ass("A", _words(), 2.0)[0] == build_ass("A", _words(), 2.0, look=None, hook_look=None)[0]


def test_look_sets_position_size_colours_and_keeps_armenian():
    from hayclips.captions.ass import build_ass
    from hayclips.look import normalise_look
    look = normalise_look({"preset": "active", "x": 0.4, "y": 0.6, "size": 1.25, "color": "#FFEE00", "highlight": "#00FF00"})
    doc, ev = build_ass("A", _words(), 2.0, look=look)
    style = [l for l in doc.splitlines() if l.startswith("Style: A,")][0].split(",")
    assert style[2] == str(round(58 * 1.25)) and style[3] == "&H0000EEFF"
    assert all("\\an2\\pos(288,768)" in e[3] for e in ev)
    assert "\\c&H00FF00&" in doc and "և" in doc and "ԵՒ" not in doc


def test_clean_preset_and_background_and_words_per_line():
    from hayclips.captions.ass import build_ass
    from hayclips.look import normalise_look
    look = normalise_look({"preset": "clean", "background": True, "words_per_line": 1})
    doc, ev = build_ass("L", _words(), 2.0, look=look)
    assert len(ev) == 3 and not any("\\c&" in e[3] for e in ev)          # one word per screen, no highlight
    style = [l for l in doc.splitlines() if l.startswith("Style: L,")][0].split(",")
    assert style[15] == "3"                                                # boxed background


def test_hook_look_hides_or_moves_the_hook():
    from hayclips.captions.ass import build_ass
    from hayclips.look import normalise_hook
    hidden, _ = build_ass("A", _words(), 4.0, hook="Ի՞նչ ես կարծում", hook_look=normalise_hook({"show": False}))
    assert "Hook,," not in hidden.split("[Events]")[1]
    moved, ev = build_ass("A", _words(), 6.0, hook="Ի՞նչ ես կարծում", hook_look=normalise_hook({"y": 0.3, "duration": 5}))
    hook = [e for e in ev if e[2] == "Hook"][0]
    assert hook[1] == 5.0 and "\\an8\\pos(360,384)" in hook[3]
