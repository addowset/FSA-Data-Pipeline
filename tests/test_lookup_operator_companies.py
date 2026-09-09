import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lookup_operator_companies import is_multi_word_prefix, safe_filename  # noqa: E402


# --- is_multi_word_prefix ---
# The precision gate added 2026-09-10 after a live test found OTIS
# LIMITED (elevators), KAMU LTD (software), and ADDIES LIMITED (legal)
# each coincidentally exact-matching a single-word operator prefix in a
# completely unrelated industry -- see the module docstring.

def test_is_multi_word_prefix_true_for_two_or_more_words():
    assert is_multi_word_prefix("Impact Food Group") is True
    assert is_multi_word_prefix("Busy Bees") is True


def test_is_multi_word_prefix_false_for_single_word():
    assert is_multi_word_prefix("Otis") is False
    assert is_multi_word_prefix("Kamu") is False


def test_is_multi_word_prefix_false_when_only_a_legal_suffix_remains():
    """normalize_company_name strips legal suffixes -- "Addies Ltd"
    would otherwise look like two words while really being one."""
    assert is_multi_word_prefix("Addies Ltd") is False


def test_is_multi_word_prefix_false_for_empty_or_unnormalizable():
    assert is_multi_word_prefix("") is False
    assert is_multi_word_prefix(None) is False


# --- safe_filename ---

def test_safe_filename_is_filesystem_safe():
    result = safe_filename("IFG Group Trading As Dolce Ltd@Barwell Church")
    assert all(c.isalnum() or c == "_" for c in result)


def test_safe_filename_distinguishes_similar_prefixes():
    """Two different prefixes that collapse to the same sanitized slug
    once punctuation/case/truncation are stripped must still not
    collide -- real case: "IFG Group Trading As Dolce" vs "...Ltd"."""
    a = safe_filename("IFG Group Trading As Dolce")
    b = safe_filename("IFG Group Trading As Dolce Ltd")
    assert a != b


def test_safe_filename_deterministic():
    assert safe_filename("Impact Food Group") == safe_filename("Impact Food Group")
