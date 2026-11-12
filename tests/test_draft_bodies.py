"""The small-number grammar in scripts/draft_bodies.py: the places where a
count of nought or one has produced a wrong sentence in a real draft."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import draft_bodies as d  # noqa: E402


def test_subject_singular():
    assert d.subject_for(1, 'Somerset').startswith('1 new food & drink venue in ')


def test_subject_four_week_period():
    assert d.subject_for(11, 'Liverpool', 'in four weeks') == (
        '11 new food & drink venues in Liverpool in four weeks %s any use?' % d.DASH)


def test_long_subject_puts_the_question_first():
    s = d.subject_for(4, 'Bristol, Bath, North Somerset and South Gloucestershire')
    assert s.startswith('Any use? ')


def test_lead_in():
    assert [d.lead_in(n) for n in (1, 3, 5, 6)] == [
        'Here it is', 'All of them', 'All of them', 'Five of them']


def test_four_week_split():
    assert d.four_week_split(1, 1) == 'a brand-new opening'
    assert d.four_week_split(0, 1) == 'a change of hands'
    assert d.four_week_split(0, 4) == 'all changes of hand'
    assert d.four_week_split(4, 4) == 'all brand-new openings'
    assert d.four_week_split(1, 5) == '1 brand-new opening, the rest changes of hand'
    assert d.four_week_split(6, 11) == '6 brand-new openings, the rest changes of hand'


def test_split_sentence_never_says_nought():
    assert '0 are' not in d.split_sentence(0, 4)
