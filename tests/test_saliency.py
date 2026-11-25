"""FastSal attention map loader and optional inference.

Real inference (shape guard, normalisation, box clipping) is exercised by
``scripts/smoke_visual.py`` against the pinned model; the unit tests cover
the boundary errors only.
"""

from __future__ import annotations

import pytest

from meshweave.scoring.saliency import (
    SALIENCY_MODEL_ENV,
    SaliencyModel,
    SaliencyUnavailable,
)


def test_requires_model_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SALIENCY_MODEL_ENV, raising=False)
    with pytest.raises(SaliencyUnavailable, match=SALIENCY_MODEL_ENV):
        SaliencyModel()


def test_rejects_missing_model_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv(SALIENCY_MODEL_ENV, str(tmp_path / "missing.onnx"))
    with pytest.raises(SaliencyUnavailable, match="not found"):
        SaliencyModel()
