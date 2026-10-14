"""
Build and load the index: chunks + embeddings on disk, BM25 in memory.

    python -m ai_platform.retrieval.index            # build
    python -m ai_platform.retrieval.index --stats    # show what is indexed
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..paths import INDEX_DIR, ensure_dirs
from .chunker import Chunk, chunk_corpus
from .corpus import load_corpus
from .embed import get_embedder

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.%/-][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    toks = TOKEN_RE.findall(text.lower())
    out = []
    for t in toks:
        out.append(t)
        if any(c in t for c in ".%/-"):               # also index the parts of 80/40, 0.80%, mean-reversion
            out.extend(p for p in re.split(r"[.%/-]", t) if p)
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=np.float32)
        self.avg = float(self.len.mean()) if self.n else 0.0
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def scores(self, query: str) -> np.ndarray:
        q = tokenize(query)
        s = np.zeros(self.n, dtype=np.float32)
        for t in q:
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(t)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)
                s[i] += idf * f * (self.k1 + 1) / denom
        return s


class Index:
    def __init__(self, chunks: list[Chunk], embeddings: np.ndarray, meta: dict, embedder=None):
        self.chunks = chunks
        self.embeddings = embeddings
        self.meta = meta
        self.embedder = embedder
        self.bm25 = BM25([tokenize(c.text) for c in chunks])

    # -- persistence ------------------------------------------------------ #
    def save(self, directory: Path = INDEX_DIR) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "chunks.json").write_text(json.dumps([c.__dict__ for c in self.chunks], ensure_ascii=False))
        np.save(directory / "embeddings.npy", self.embeddings)
        (directory / "meta.json").write_text(json.dumps(self.meta, indent=2))

    @classmethod
    def load(cls, directory: Path = INDEX_DIR, embedder=None) -> "Index":
        meta = json.loads((directory / "meta.json").read_text())
        chunks = [Chunk(**c) for c in json.loads((directory / "chunks.json").read_text())]
        emb = np.load(directory / "embeddings.npy")
        embedder = embedder or get_embedder(meta.get("embedder_kind"))
        if embedder.name != meta["embedder"]:
            raise RuntimeError(f"index built with {meta['embedder']} but {embedder.name} is loaded; rebuild")
        return cls(chunks, emb, meta, embedder)

    @classmethod
    def build(cls, embedder=None, code_root=None, repo_root=None, save_to: Path | None = INDEX_DIR) -> "Index":
        embedder = embedder or get_embedder()
        docs = load_corpus(code_root, repo_root)
        chunks = chunk_corpus(docs)
        emb = embedder.encode([c.text for c in chunks]) if chunks else np.zeros((0, embedder.dim), dtype=np.float32)
        meta = {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "embedder": embedder.name, "embedder_kind": "hash" if embedder.name.startswith("hash") else "model2vec",
            "dim": int(emb.shape[1]) if emb.size else embedder.dim,
            "documents": len(docs), "chunks": len(chunks),
            "sources": sorted({d.source for d in docs}),
            "chunk_chars_avg": int(sum(len(c.text) for c in chunks) / len(chunks)) if chunks else 0,
        }
        idx = cls(chunks, emb, meta, embedder)
        if save_to is not None:
            idx.save(save_to)
        return idx

    @classmethod
    def load_or_build(cls, directory: Path = INDEX_DIR) -> "Index":
        if (directory / "meta.json").is_file():
            try:
                return cls.load(directory)
            except Exception:
                pass
        ensure_dirs()
        return cls.build(save_to=directory)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stats", action="store_true", help="print index metadata and exit")
    ap.add_argument("--embedder", default=None, choices=[None, "model2vec", "hash"])
    a = ap.parse_args(argv)
    ensure_dirs()
    if a.stats and (INDEX_DIR / "meta.json").is_file():
        print(json.dumps(json.loads((INDEX_DIR / "meta.json").read_text()), indent=2))
        return
    idx = Index.build(get_embedder(a.embedder))
    print(json.dumps(idx.meta, indent=2))


if __name__ == "__main__":
    main()
