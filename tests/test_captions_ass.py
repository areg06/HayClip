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
