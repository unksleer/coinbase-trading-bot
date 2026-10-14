"""
Heading-aware chunking.

Markdown is split at headings; each section carries its heading path as a
prefix (so "DECISIONS.md > 2026-10-01 — Stop-outs" is searchable and shows up
as the source). Sections longer than `max_chars` are split on paragraph
boundaries with `overlap` characters carried forward. Ledger documents are
split per block of trades. JSON and code are treated as one section.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .corpus import Document, strip_markdown_noise

HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.M)


@dataclass
class Chunk:
    chunk_id: str
    source: str
    heading: str
    text: str          # what is embedded and searched (heading prefix + body)
    body: str          # body only, for display
    position: int
    meta: dict = field(default_factory=dict)


def _split_long(body: str, max_chars: int, overlap: int) -> list[str]:
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    pieces, cur = [], ""
    for p in paras:
        if len(p) > max_chars:                       # a single huge block: hard split
            if cur:
                pieces.append(cur); cur = ""
            for i in range(0, len(p), max_chars - overlap):
                pieces.append(p[i:i + max_chars])
            continue
        if cur and len(cur) + len(p) + 2 > max_chars:
            pieces.append(cur)
            cur = cur[-overlap:] + "\n\n" + p if overlap else p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur.strip():
        pieces.append(cur)
    return pieces


def _sections(text: str) -> list[tuple[str, str]]:
    """(heading path, body) pairs for a markdown document."""
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]
    out = []
    stack: list[tuple[int, str]] = []
    pre = text[: matches[0].start()].strip()
    if pre:
        out.append(("", pre))
    for i, m in enumerate(matches):
        level, title = len(m.group(1)), m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        out.append((" > ".join(t for _, t in stack), body))
    return out


def chunk_document(doc: Document, max_chars: int = 1400, overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    if doc.kind == "markdown":
        sections = _sections(strip_markdown_noise(doc.text))
    elif doc.kind == "ledger":
        lines = doc.text.splitlines()
        head, rows = lines[0], lines[1:]
        sections = []
        for i in range(0, max(1, len(rows)), 12):
            sections.append((head, "\n".join(rows[i:i + 12]) or head))
    else:
        sections = [(doc.title, doc.text)]
    pos = 0
    for heading, body in sections:
        if not body.strip():
            continue
        for piece in _split_long(body, max_chars, overlap):
            prefix = f"{doc.source}" + (f" > {heading}" if heading else "")
            chunks.append(Chunk(
                chunk_id=f"{doc.source}#{pos}", source=doc.source, heading=heading,
                text=f"{prefix}\n{piece}", body=piece, position=pos, meta={"kind": doc.kind}))
            pos += 1
    return chunks


def chunk_corpus(docs: list[Document], **kw) -> list[Chunk]:
    out: list[Chunk] = []
    for d in docs:
        out.extend(chunk_document(d, **kw))
    return out
