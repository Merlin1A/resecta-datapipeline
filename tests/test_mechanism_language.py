"""Tests for resecta_data.common.mechanism_language.

These tests lock in the banned-phrase list from `common/mechanism_language.py`.
If a test fails because a new phrase was added, the test must be updated in the
same commit as the list update.
"""

from __future__ import annotations

import pytest

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.mechanism_language import (
    assert_safe,
    scan_text,
)

# -----------------------------------------------------------------------------
# scan_text: positive cases (banned phrases detected)
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "guaranteed",
        "ensures",
        "securely removes",
        "100%",
        "impossible to recover",
        "military-grade",
        "bank-level",
        "certified",
        "structurally impossible",
        "the only provably secure approach",
        "mathematically irreversible",
        "security invariant",
        "provably reliable",
        "your data is safe",
        "no one can recover",
    ],
)
def test_detects_banned_phrase_in_isolation(phrase: str) -> None:
    matches = scan_text(phrase)
    assert len(matches) >= 1
    assert matches[0].phrase.lower() == phrase.lower()


@pytest.mark.parametrize(
    ("phrase", "wrapper"),
    [
        ("guaranteed", "This product is {} to work."),
        ("ensures", "Our app {} your data is safe."),
        ("100%", "We offer {} secure redaction."),
        ("military-grade", "Uses {} encryption."),
        ("your data is safe", "Relax — {}."),
    ],
)
def test_detects_banned_phrase_in_context(phrase: str, wrapper: str) -> None:
    text = wrapper.format(phrase)
    matches = scan_text(text)
    assert len(matches) >= 1


def test_case_insensitive_detection() -> None:
    assert len(scan_text("GUARANTEED")) >= 1
    assert len(scan_text("Guaranteed")) >= 1
    assert len(scan_text("guArANteed")) >= 1


def test_detects_extra_whitespace() -> None:
    # Whitespace-tolerant matching: "bank   level" still hits "bank-level" via \s+.
    # Note: "bank-level" uses a hyphen which the tokenizer splits on whitespace.
    # "bank  level" with two spaces should match "bank level" if tokenized.
    # "structurally  impossible" with double space should match.
    assert len(scan_text("structurally  impossible")) >= 1
    assert len(scan_text("structurally\timpossible")) >= 1
    assert len(scan_text("structurally\nimpossible")) >= 1


def test_detects_at_string_boundaries() -> None:
    # Match at start
    assert len(scan_text("guaranteed to be safe")) >= 1
    # Match at end
    assert len(scan_text("this is guaranteed")) >= 1
    # Match alone
    assert len(scan_text("guaranteed")) >= 1


def test_detects_multiple_phrases() -> None:
    text = "This is guaranteed to be 100% military-grade secure."
    matches = scan_text(text)
    # Expect at least three distinct phrase hits.
    assert len({m.phrase for m in matches}) >= 3


def test_match_includes_excerpt() -> None:
    text = "A" * 100 + " guaranteed " + "B" * 100
    matches = scan_text(text)
    assert len(matches) == 1
    # Excerpt should contain the matched phrase.
    assert "guaranteed" in matches[0].excerpt.lower()


def test_match_reports_correct_offsets() -> None:
    text = "prefix guaranteed suffix"
    matches = scan_text(text)
    assert len(matches) == 1
    assert text[matches[0].start : matches[0].end].lower() == "guaranteed"


# -----------------------------------------------------------------------------
# scan_text: negative cases (safe text produces no hits)
# -----------------------------------------------------------------------------


def test_safe_text_no_matches() -> None:
    text = "The redaction process is designed to remove text from the document structure."
    assert scan_text(text) == []


def test_mechanism_description_no_matches() -> None:
    text = "This Bloom filter is queried against each token after NLTagger returns."
    assert scan_text(text) == []


def test_empty_string() -> None:
    assert scan_text("") == []


def test_whitespace_only() -> None:
    assert scan_text("   \n\t  ") == []


def test_word_boundary_prevents_substring_match() -> None:
    """`\\bword\\b` boundaries mean a banned phrase inside a larger word does not match."""
    # "certified" is banned, but "uncertified" should not match because \b requires
    # a word boundary on the left.
    text = "This is uncertifiedness."
    # "uncertified" embeds "certified" but \b prevents the match.
    assert scan_text(text) == []


def test_similar_but_different_phrase_not_flagged() -> None:
    # "Security invariant" is banned but "security considerations" is fine.
    text = "security considerations for the pipeline"
    assert scan_text(text) == []


# -----------------------------------------------------------------------------
# assert_safe: raises on banned phrases
# -----------------------------------------------------------------------------


def test_assert_safe_passes_on_safe_text() -> None:
    assert_safe("The process is designed to remove text.", context="docstring")


def test_assert_safe_raises_on_banned_phrase() -> None:
    with pytest.raises(PipelineError, match="Banned phrase"):
        assert_safe("This is guaranteed to be secure.", context="test")


def test_assert_safe_error_includes_context() -> None:
    with pytest.raises(PipelineError, match="test_location"):
        assert_safe("100% secure.", context="test_location")


def test_assert_safe_error_lists_all_hits() -> None:
    with pytest.raises(PipelineError) as exc_info:
        assert_safe("100% guaranteed military-grade.", context="test")
    message = str(exc_info.value)
    # All three should appear in the error message.
    assert "100%" in message
    assert "guaranteed" in message
    assert "military-grade" in message


def test_assert_safe_error_references_claude_md() -> None:
    with pytest.raises(PipelineError, match=r"CONTRIBUTING\.md"):
        assert_safe("guaranteed", context="test")


# -----------------------------------------------------------------------------
# Real-world examples of banned mechanism-description phrases
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsafe_example",
    [
        "Your data is safe",
        "No one can recover the original text",
        "100% secure redaction",
        "Military-grade encryption",
        "Bank-level security",
        "Structurally impossible to reverse",
        "Mathematically irreversible process",
    ],
)
def test_rejects_master_legal_reference_unsafe_examples(unsafe_example: str) -> None:
    """Every 🛑 example below must be flagged."""
    assert len(scan_text(unsafe_example)) >= 1


@pytest.mark.parametrize(
    "safe_example",
    [
        "All processing happens on your device",
        "The redaction process removes text from the document structure",
        "Designed to help you prepare documents before sharing with AI services",
        "Built-in verification tools help check redaction completeness",
        "Both modes use the same pixel-destruction process for redacted regions",
        "Automatically falls back to Secure Rasterization when text extraction is unreliable",
    ],
)
def test_accepts_master_legal_reference_safe_examples(safe_example: str) -> None:
    """Every ✅ example below must pass."""
    assert scan_text(safe_example) == []
