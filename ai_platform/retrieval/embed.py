"""
Embedders. Default is model2vec `potion-base-8M`, a small static embedding
model that runs locally in numpy (256 dims), so the corpus never leaves the
machine and the index is reproducible. `hash` is a deterministic hashed
n-gram embedder with no learned semantics, used by the tests and as the
fallback when the model cannot be downloaded. The index records which one
built it, and recall is reported per embedder.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re

import numpy as np

log = logging.getLogger("ai_platform.retrieval")
MODEL2VEC_NAME = "minishlab/potion-base-8M"


class HashEmbedder:
    name = "hash-ngram-512"
    dim = 512

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            t = t.lower()
            feats = re.findall(r"[a-z0-9]+", t)
            feats += [t[j:j + 3] for j in range(0, min(len(t), 4000) - 2)]
            for f in feats:
                h = int(hashlib.blake2b(f.encode(), digest_size=8).hexdigest(), 16)
                out[i, h % self.dim] += 1.0 if (h >> 63) else -1.0
        return _normalise(out)


class Model2VecEmbedder:
    name = f"model2vec:{MODEL2VEC_NAME}"

    def __init__(self):
        from model2vec import StaticModel
        self.model = StaticModel.from_pretrained(MODEL2VEC_NAME)
        self.dim = int(self.model.dim) if hasattr(self.model, "dim") else int(self.encode(["x"]).shape[1])

    def encode(self, texts: list[str]) -> np.ndarray:
        v = np.asarray(self.model.encode([t[:4000] for t in texts]), dtype=np.float32)
        return _normalise(v)


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def get_embedder(kind: str | None = None):
    kind = (kind or os.environ.get("AI_PLATFORM_EMBEDDER") or "model2vec").lower()
    if kind == "hash":
        return HashEmbedder()
    try:
        return Model2VecEmbedder()
    except Exception as e:  # offline, no cache, etc.
        log.warning("model2vec unavailable (%s); falling back to the hash embedder", e)
        return HashEmbedder()
