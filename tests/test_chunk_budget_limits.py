"""Regressions for simultaneous character, token, and output budgets."""

import pytest

from summarizer.chunking.splitter import split_document
from summarizer.chunking.strategies import budget_for, chunk_document, chunking_decision
from summarizer.chunking.token import estimate_tokens
from summarizer.config import ChunkingSettings
from summarizer.engine.capabilities import EngineCapabilities
from summarizer.errors import InputError
from summarizer.profiles import Profile


PROFILE = Profile(name="test", text="{{input}}", source="builtin")
CAPABILITIES = EngineCapabilities(context_length=1000, context_source="server")
SETTINGS = ChunkingSettings(max_tokens_per_chunk=100, overlap_tokens=10,
                            reserve_output_tokens=100)


def decide(text, **kwargs):
    return chunking_decision(text, capabilities=CAPABILITIES, settings=SETTINGS,
                             profile=PROFILE, config_context_length=0,
                             explicit_unit="chars", **kwargs)


def test_chars_strict_checks_tokens_not_only_characters():
    text = "a " * 100
    assert len(text) < SETTINGS.max_tokens_per_chunk * 4
    assert estimate_tokens(text) > SETTINGS.max_tokens_per_chunk
    with pytest.raises(InputError, match="strict"):
        decide(text, strict=True)


def test_chars_chunks_enforce_tokens_and_preserve_source():
    text = ("a b c d e f g h i j.\n\n" * 100)
    decision = decide(text)
    chunks = chunk_document(text, decision=decision, settings=SETTINGS,
                            source="short-words.txt")
    assert not decision.single_shot
    assert any(chunk.overlap_chars for chunk in chunks[1:])
    assert "".join(chunk.text[chunk.overlap_chars:] for chunk in chunks) == text
    for chunk in chunks:
        assert chunk.char_count <= decision.max_chunk
        assert chunk.token_estimate <= decision.budget.max_chunk_tokens
        assert chunk.text[chunk.overlap_chars:] == text[chunk.start_char:chunk.end_char]
        assert chunk.source == "short-words.txt"
        assert chunk.boundary == "paragraph"


@pytest.mark.parametrize("output, expected", [(0, 100), (50, 100), (500, 500)])
def test_output_reservation_uses_larger_limit(output, expected):
    budget = budget_for(CAPABILITIES, SETTINGS, PROFILE, max_output_tokens=output)
    assert budget.output_tokens == expected
    assert budget.usable_input_tokens == 1000 - budget.prompt_tokens - expected
    assert decide("small", max_output_tokens=output).budget == budget


def test_output_reservation_can_exhaust_context():
    with pytest.raises(InputError, match="reserved output"):
        decide("small", max_output_tokens=1000)


@pytest.mark.parametrize("text", ["a " * 200, "x" * 500, "a\t b\n c. " * 100])
def test_splitter_combined_limits(text):
    chunks = split_document(text, max_units=80, max_tokens=12, overlap_units=12,
                            unit="chars")
    assert "".join(c.text[c.overlap_chars:] for c in chunks) == text
    for chunk in chunks:
        assert len(chunk.text) <= 80
        assert estimate_tokens(chunk.text) <= 12
        assert chunk.text[chunk.overlap_chars:] == text[chunk.start_char:chunk.end_char]


def test_chars_decision_chunk_ceiling_accounts_for_tokens():
    with pytest.raises(InputError, match="maximum of 1"):
        decide("a " * 100, max_chunks=1)


# --- token safety margin ----------------------------------------------------


def test_token_safety_margin_default_leaves_the_budget_unchanged():
    base = budget_for(CAPABILITIES, SETTINGS, PROFILE)
    explicit = budget_for(CAPABILITIES, ChunkingSettings(
        max_tokens_per_chunk=100, overlap_tokens=10, reserve_output_tokens=100,
        token_safety_margin=1.0), PROFILE)
    assert explicit.usable_input_tokens == base.usable_input_tokens
    assert base.context_tokens == 1000  # the reported window is never scaled


def test_token_safety_margin_shrinks_only_the_document_budget():
    base = budget_for(CAPABILITIES, SETTINGS, PROFILE)
    roomy = ChunkingSettings(max_tokens_per_chunk=100, overlap_tokens=10,
                             reserve_output_tokens=100, token_safety_margin=2.0)
    halved = budget_for(CAPABILITIES, roomy, PROFILE)
    assert halved.context_tokens == base.context_tokens
    assert halved.usable_input_tokens == base.usable_input_tokens // 2
    assert halved.max_chunk_tokens <= halved.usable_input_tokens


def test_context_scale_shrinks_the_budget_for_the_retry_path():
    base = budget_for(CAPABILITIES, SETTINGS, PROFILE)
    scaled = budget_for(CAPABILITIES, SETTINGS, PROFILE, context_scale=0.5)
    assert scaled.usable_input_tokens == base.usable_input_tokens // 2
