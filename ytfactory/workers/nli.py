"""D4 NLI Gate — debaert/v3-small-mnli ONNX entailment pre-filter (S08).

Auto-approve >0.9, auto-fail <0.1, route the middle band to the LLM checker
(architecture.md §8.3: saves 4-5x requests). Returns None when onnxruntime
or the model is unavailable — the caller then routes everything to A6.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

MODEL_ID = "cross-encoder/nli-deberta-v3-small"
HIGH = 0.9
LOW = 0.1


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import CrossEncoder  # lazy heavy dep

    return CrossEncoder(MODEL_ID)


def entail_scores(pairs: list[tuple[str, str]]) -> Optional[list[float]]:
    """[(premise, hypothesis)] -> entailment probabilities, or None if unavailable."""
    try:
        model = _model()
    except Exception as exc:
        print(f"[nli] CrossEncoder unavailable ({exc}); routing all claims to A6")
        return None
    raw = model.predict([(p, h) for p, h in pairs])
    # CrossEncoder NLI output order: [contradiction, entailment, neutral] for this model
    import numpy as np

    probs = []
    for row in raw:
        arr = np.asarray(row, dtype=float)
        exp = np.exp(arr - arr.max())
        softmax = exp / exp.sum()
        probs.append(float(softmax[1]))
    return probs


def route(score: Optional[float]) -> str:
    if score is None:
        return "llm"
    if score > HIGH:
        return "auto_approve"
    if score < LOW:
        return "auto_fail"
    return "llm"
