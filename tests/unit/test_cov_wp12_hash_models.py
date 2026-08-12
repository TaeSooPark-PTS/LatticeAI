"""wp12 coverage — the deterministic hash embedder.

``lattice_brain.embeddings`` is the one feature-hashing model; 11.5.2 deleted
``latticeai.core.local_embeddings``, an executable-byte-identical second copy
whose only difference from this one was that no parity golden pinned it, so the
two write paths could drift apart without a test noticing. The interesting
paths are the ones a normal sentence never reaches: text with no features, a
vector whose features cancel out to zero, the dimension-mismatch refusal that
10.2.0 chose over a silently truncated comparison, and the float32 codec's
disagreement between the declared dimension and the bytes actually stored.
"""

from __future__ import annotations

import math

import pytest

import lattice_brain.embeddings as brain_embeddings

MODULES = [pytest.param(brain_embeddings, id="lattice-brain")]


@pytest.mark.parametrize("module", MODULES)
def test_text_with_no_features_embeds_to_a_zero_vector(module):
    model = module.LocalEmbeddingModel(dim=6)

    assert model.embed("") == [0.0] * 6
    assert model.embed("!!! ??? ...") == [0.0] * 6


@pytest.mark.parametrize("module", MODULES)
def test_features_that_cancel_out_leave_an_unnormalizable_vector(module):
    # with dim=1 both tokens land on the only slot, and their hash parity gives
    # them opposite signs — the sum is exactly zero, so there is no direction to
    # normalize and the raw vector is returned instead of dividing by zero
    assert module._hash_to_index("tok:aa", 1) == (0, 1.0)
    assert module._hash_to_index("tok:ab", 1) == (0, -1.0)

    assert module.LocalEmbeddingModel(dim=1).embed("aa ab") == [0.0]


@pytest.mark.parametrize("module", MODULES)
def test_an_ordinary_sentence_is_l2_normalized(module):
    vec = module.LocalEmbeddingModel(dim=64).embed("hybrid retrieval over the knowledge graph")

    assert len(vec) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-9)


@pytest.mark.parametrize("module", MODULES)
def test_similarity_refuses_vectors_that_came_from_different_models(module):
    model = module.LocalEmbeddingModel(dim=4)

    with pytest.raises(ValueError, match="embedding dimension mismatch: 4 vs 3"):
        model.similarity([0.5, 0.5, 0.5, 0.5], [0.5, 0.5, 0.5])


@pytest.mark.parametrize("module", MODULES)
def test_similarity_is_the_dot_product_and_is_one_for_a_repeated_text(module):
    model = module.LocalEmbeddingModel(dim=64)
    vec = model.embed("hybrid retrieval over the knowledge graph")

    assert math.isclose(model.similarity(vec, vec), 1.0, rel_tol=1e-9)
    assert math.isclose(model.similarity([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-12)


@pytest.mark.parametrize("module", MODULES)
def test_the_float32_codec_round_trips_a_vector(module):
    model = module.LocalEmbeddingModel(dim=3)
    payload = model.encode([1.0, -0.5, 0.25])

    assert len(payload) == 12
    assert model.decode(payload) == [1.0, -0.5, 0.25]


@pytest.mark.parametrize("module", MODULES)
def test_decoding_an_empty_payload_yields_no_vector(module):
    assert module.LocalEmbeddingModel(dim=3).decode(b"") == []


@pytest.mark.parametrize("module", MODULES)
def test_decoding_trusts_the_payload_length_over_a_disagreeing_dimension(module):
    # a row written before a re-index still decodes to the vector it holds
    model = module.LocalEmbeddingModel(dim=384)
    payload = model.encode([1.0, -0.5, 0.25])

    assert model.decode(payload) == [1.0, -0.5, 0.25]
    assert model.decode(payload, dim=99) == [1.0, -0.5, 0.25]
