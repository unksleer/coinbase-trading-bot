"""
The corpus: documents Lee owns in this repo.

  strategy notes   DECISIONS.md, README.md, trading_profiles.md, OBSERVER.md,
                   backtest_report.md, live_trading_setup.md
  registrations    experiments/*.json (sealed experiment records)
  strategy code    module and class docstrings from strategies/*.py, and the
                   statistical-power note at the top of utils/graduation.py
  ledger           one document per ticker rendered from the paper trade CSVs

Nothing is modified; files are read and rendered to text.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..ledger import load_trades
from .. import paths

DOC_FILES = ["DECISIONS.md", "README.md", "trading_profiles.md", "OBSERVER.md",
             "backtest_report.md", "live_trading_setup.md"]


@dataclass
class Document:
    source: str      # e.g. "DECISIONS.md", "experiments/x.json", "ledger/SPY"
    title: str
    text: str
    kind: str        # markdown | json | code | ledger


def _docstrings(path: Path) -> str:
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return ""
    parts = []
    mod = ast.get_docstring(tree)
    if mod:
        parts.append(mod)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            d = ast.get_docstring(node)
            if d:
                parts.append(f"class {node.name}:\n{d}")
    return "\n\n".join(parts)


def _leading_comment_block(path: Path, start_marker: str) -> str:
    lines = path.read_text().splitlines()
    out, on = [], False
    for ln in lines:
        if start_marker in ln:
            on = True
        if on:
            if ln.startswith("#"):
                out.append(ln.lstrip("# ").rstrip())
            elif out:
                break
    return "\n".join(out)


def _ledger_docs(root: Path) -> list[Document]:
    docs = []
    by_ticker: dict[str, list] = {}
    for t in load_trades(root, scope="all"):
        key = f"{t.ticker}" if t.strategy == "RSI_MEAN_REVERSION" else f"{t.ticker}-{t.strategy}"
        by_ticker.setdefault(key, []).append(t)
    for key, trades in sorted(by_ticker.items()):
        strat = trades[0].strategy
        label = "RSI mean reversion (2026-09-stocks-mean-reversion-v4 registration from 2026-09-04)" \
            if strat == "RSI_MEAN_REVERSION" else strat
        pre = sum(1 for t in trades if t.entry_time[:10] < "2026-09-04")
        lines = [f"Paper trade ledger for {trades[0].ticker}, strategy {label}, venue Alpaca paper. "
                 f"{len(trades)} closed trades, {pre} of them entered before 2026-09-04 and so outside the v4 "
                 f"registration window. Source file {trades[0].source_file}."]
        for i, t in enumerate(trades, 1):
            lines.append(
                f"{t.ticker} trade {i}: entered {t.entry_time[:16].replace('T', ' ')} at {t.entry_price}, "
                f"exited {t.exit_time[:16].replace('T', ' ')} at {t.exit_price}, entry reason {t.entry_reason or 'n/a'}, "
                f"exit reason {t.exit_reason}, net {t.net_pct:+.2f}%, P&L ${t.pnl_usd:+.2f}, held {t.holding_hours:.1f} hours."
            )
        docs.append(Document(source=f"ledger/{key}", title=f"Ledger {key}", text="\n".join(lines), kind="ledger"))
    return docs


def load_corpus(code_root: Path | None = None, repo_root: Path | None = None) -> list[Document]:
    code_root = code_root or paths.CODE_ROOT
    repo_root = repo_root or paths.REPO_ROOT
    docs: list[Document] = []
    for name in DOC_FILES:
        p = code_root / name
        if p.is_file():
            docs.append(Document(source=name, title=name, text=p.read_text(), kind="markdown"))
    for p in sorted((code_root / "experiments").glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        text = f"Experiment registration {p.stem}\n" + json.dumps(data, indent=2, ensure_ascii=False)
        docs.append(Document(source=f"experiments/{p.name}", title=p.stem, text=text, kind="json"))
    for p in sorted((code_root / "strategies").glob("*.py")):
        if p.name.startswith("__"):
            continue
        ds = _docstrings(p)
        if ds.strip():
            docs.append(Document(source=f"strategies/{p.name}", title=p.stem, text=ds, kind="code"))
    g = code_root / "utils" / "graduation.py"
    if g.is_file():
        note = _leading_comment_block(g, "STATISTICAL POWER")
        if note:
            docs.append(Document(source="utils/graduation.py", title="graduation statistical power",
                                 text="Graduation gate statistical power note:\n" + note, kind="code"))
    docs.extend(_ledger_docs(repo_root))
    return docs


def strip_markdown_noise(text: str) -> str:
    text = re.sub(r"\(file:///[^)]*\)", "", text)       # local links
    return text
