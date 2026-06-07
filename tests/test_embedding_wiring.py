from backend.matching.fit_engine import cosine_similarity


def test_cosine_mismatched_dimensions_returns_zero():
    # Gemini 768 vs OpenAI 1536 must never crash — guard returns 0.0
    assert cosine_similarity([0.1, 0.2, 0.3], [0.1, 0.2]) == 0.0


def test_cosine_normal():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
