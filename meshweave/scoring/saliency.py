"""FastSal attention maps for rendered screenshots.

Needs a FastSal ONNX model at ``MESHWEAVE_SALIENCY_MODEL_PATH``. The model is
not downloaded or bundled; point the env var at a local file. Upstream weights
are Apache-2.0 (https://github.com/feiyanhu/FastSal).

The map is only meaningful for the first viewport of a top-of-page screenshot,
which is what :func:`meshweave.crawling.visual.capture_visual_snapshot`
produces. Generic natural-image saliency is a directional signal for UI, not
ground truth.

The assumed export is the FastSal ``saliconC`` ONNX file (Apache-2.0,
https://github.com/feiyanhu/FastSal — sha256 pinned in
``scripts/smoke_visual.py``): batched ``[1, 1, H, W]`` pre-sigmoid logits.
Any other output layout fails loud at predict time.
"""

from __future__ import annotations

import functools
import io
import os
from dataclasses import dataclass
from typing import Any

SALIENCY_MODEL_ENV = "MESHWEAVE_SALIENCY_MODEL_PATH"
INPUT_WIDTH = 256
INPUT_HEIGHT = 192
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)


class SaliencyUnavailable(RuntimeError):
    """The saliency model is unavailable or unusable."""


@dataclass(frozen=True)
class SaliencyMap:
    """A normalised attention map; ``values`` is a 2-D float array."""

    width: int
    height: int
    values: Any

    def share(self, *, x: int, y: int, width: int, height: int) -> float:
        """Share of total predicted attention inside a box, clipped to bounds."""
        top = max(0, min(self.height, y))
        bottom = max(0, min(self.height, y + height))
        left = max(0, min(self.width, x))
        right = max(0, min(self.width, x + width))
        if bottom <= top or right <= left:
            return 0.0
        return float(self.values[top:bottom, left:right].sum())


@functools.lru_cache(maxsize=2)
def load_saliency_model(model_path: str) -> SaliencyModel:
    """Load (and cache) the FastSal ONNX session for *model_path*."""
    return SaliencyModel(model_path)


class SaliencyModel:
    """A loaded FastSal ONNX session plus its preprocessing.

    Construct through :func:`load_saliency_model` — session creation costs
    more than inference and must not repeat per request.
    """

    def __init__(self, model_path: str | None = None) -> None:
        import onnxruntime as ort

        path = model_path or os.environ.get(SALIENCY_MODEL_ENV)
        if not path:
            raise SaliencyUnavailable(
                f"{SALIENCY_MODEL_ENV} is not set; point it at a FastSal "
                "ONNX model to enable attention maps."
            )
        if not os.path.isfile(path):
            raise SaliencyUnavailable(f"Saliency model not found: {path}")
        try:
            self._session = ort.InferenceSession(
                path, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:
            raise SaliencyUnavailable(
                f"Cannot load saliency model {path}: {exc}"
            ) from exc

    def predict(self, png: bytes) -> SaliencyMap:
        """Return the attention map for a PNG screenshot."""
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            small = rgb.resize((INPUT_WIDTH, INPUT_HEIGHT), Image.Resampling.BILINEAR)
            array = np.asarray(small, dtype=np.float32) / 255.0
        mean = np.array(_MEAN, dtype=np.float32)
        std = np.array(_STD, dtype=np.float32)
        array = (array - mean) / std
        array = np.transpose(array, (2, 0, 1))[None]
        name = self._session.get_inputs()[0].name
        output = self._session.run(None, {name: array})[0]
        if output.ndim != 4:
            raise SaliencyUnavailable(
                f"Unexpected saliency output shape {output.shape}; "
                "expected [1, 1, H, W] FastSal logits"
            )
        logits = output[0, 0]
        probability = 1 / (1 + np.exp(-logits))
        resized = np.asarray(
            Image.fromarray(probability.astype(np.float32)).resize(
                (width, height), Image.Resampling.BICUBIC
            ),
            dtype=np.float32,
        )
        resized = np.clip(resized, 0, None)
        total = float(resized.sum())
        values = resized / total if total > 0 else resized
        return SaliencyMap(width=width, height=height, values=values)
