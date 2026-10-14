"""
Exactly three tools, typed in and out with pydantic.

  read_ledger                    rows from the paper trade CSVs (read-only)
  summarise_strategy_performance deterministic statistics over those rows
  propose_parameter_change       append to the proposal queue; never live config

The tool layer validates inputs against the schema before running anything,
validates outputs before returning them, and refuses parameters outside the
allow-list (so DRY_RUN, ALPACA_PAPER, the kill switch and the like cannot be
proposed at all, let alone changed).
"""
from __future__ import annotations

import json
import time
from datetime import date
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import paths
from ..ledger import load_trades, summarise
from . import proposals

Parameter = Literal[
    "STOP_LOSS_PERCENT", "TAKE_PROFIT_PERCENT", "TRAILING_STOP_PERCENT",
    "TRAILING_STOP_ACTIVATION_PERCENT", "RSI_PERIOD", "RSI_OVERSOLD", "RSI_OVERBOUGHT",
    "DONCHIAN_ENTRY_BARS", "DONCHIAN_EXIT_BARS",
]
PARAMETER_BOUNDS: dict[str, tuple[float, float]] = {
    "STOP_LOSS_PERCENT": (0.5, 30.0), "TAKE_PROFIT_PERCENT": (0.0, 50.0),
    "TRAILING_STOP_PERCENT": (0.0, 20.0), "TRAILING_STOP_ACTIVATION_PERCENT": (0.0, 20.0),
    "RSI_PERIOD": (2, 50), "RSI_OVERSOLD": (5, 50), "RSI_OVERBOUGHT": (50, 95),
    "DONCHIAN_ENTRY_BARS": (5, 400), "DONCHIAN_EXIT_BARS": (2, 200),
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- read_ledger ------------------------------------------------------------ #
class ReadLedgerInput(Strict):
    ticker: Optional[str] = Field(description="Symbol such as SPY, or null for every ticker.")
    limit: int = Field(ge=1, le=200, description="Maximum rows to return, most recent last.")
    since: Optional[str] = Field(description="ISO date (YYYY-MM-DD); only trades entered on or after it, or null.")


class TradeRow(Strict):
    ticker: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    exit_reason: str
    net_pct: float
    pnl_usd: float
    holding_hours: float


class ReadLedgerOutput(Strict):
    count: int
    returned: int
    truncated: bool
    source_files: list[str]
    trades: list[TradeRow]


def read_ledger(inp: ReadLedgerInput) -> ReadLedgerOutput:
    since = date.fromisoformat(inp.since) if inp.since else None
    rows = load_trades(paths.REPO_ROOT, scope="all", ticker=inp.ticker, since=since)
    sel = rows[-inp.limit:]
    return ReadLedgerOutput(
        count=len(rows), returned=len(sel), truncated=len(sel) < len(rows),
        source_files=sorted({t.source_file for t in rows}),
        trades=[TradeRow(ticker=t.ticker, entry_time=t.entry_time, exit_time=t.exit_time, entry_price=t.entry_price,
                         exit_price=t.exit_price, exit_reason=t.exit_reason, net_pct=t.net_pct, pnl_usd=t.pnl_usd,
                         holding_hours=t.holding_hours) for t in sel])


# -- summarise_strategy_performance ---------------------------------------- #
class SummariseInput(Strict):
    scope: str = Field(description="'stocks' (all mean-reversion paper bots), 'fcr' (First Candle Rule), or one ticker such as JNJ.")
    since: Optional[str] = Field(description="ISO date; only trades entered on or after it. Use 2026-09-04 for the v4 registration window, or null for all.")


class SummariseOutput(Strict):
    scope: str
    since: Optional[str]
    trades: int
    wins: int
    losses: int
    win_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    avg_net_pct: float
    profit_factor: Optional[float]
    net_pnl_usd: float
    breakeven_win_rate_pct: float
    median_hold_hours: float
    exit_reason_counts: dict[str, int]
    per_ticker: dict[str, dict]
    first_entry: Optional[str]
    last_exit: Optional[str]
    note: str


def summarise_strategy_performance(inp: SummariseInput) -> SummariseOutput:
    since = date.fromisoformat(inp.since) if inp.since else None
    scope = inp.scope.strip()
    if scope.lower() in ("stocks", "fcr", "all"):
        rows = load_trades(paths.REPO_ROOT, scope=scope.lower(), since=since)
    else:
        rows = load_trades(paths.REPO_ROOT, scope="all", ticker=scope.upper(), since=since)
    s = summarise(rows)
    note = ("Pooled counts. 12 stock tickers measure as ~4.9 independent symbols, so divide pooled trade counts by "
            "~2.4 for effective evidence. Wins are small and losses sit at the 5% stop by design (exit invariant).")
    return SummariseOutput(scope=scope, since=inp.since, note=note, **s)


# -- propose_parameter_change ----------------------------------------------- #
class ProposeInput(Strict):
    parameter: Parameter = Field(description="One of the allow-listed strategy parameters.")
    current_value: float
    proposed_value: float
    rationale: str = Field(min_length=20, description="Why, in plain words, with the numbers that support it.")
    evidence: list[str] = Field(description="Specific figures or sources (ledger stats, DECISIONS.md entries) behind the rationale.")
    voids_running_registration: bool = Field(description="True if applying this would void the sealed running registration (it nearly always does).")


class ProposeOutput(Strict):
    proposal_id: str
    status: str
    queue_path: str
    parameter: str
    current_value: float
    proposed_value: float
    note: str


def propose_parameter_change(inp: ProposeInput, interaction_id: str | None = None) -> ProposeOutput:
    lo, hi = PARAMETER_BOUNDS[inp.parameter]
    if not (lo <= inp.proposed_value <= hi):
        raise ValueError(f"{inp.parameter}={inp.proposed_value} outside the allowed range [{lo}, {hi}]")
    if inp.proposed_value == inp.current_value:
        raise ValueError("proposed_value equals current_value; nothing to propose")
    item = proposals.enqueue({
        "parameter": inp.parameter, "current_value": inp.current_value, "proposed_value": inp.proposed_value,
        "rationale": inp.rationale, "evidence": inp.evidence,
        "voids_running_registration": inp.voids_running_registration, "interaction_id": interaction_id,
    })
    return ProposeOutput(
        proposal_id=item["proposal_id"], status=item["status"], queue_path=str(paths.PROPOSAL_QUEUE),
        parameter=inp.parameter, current_value=inp.current_value, proposed_value=inp.proposed_value,
        note="Queued only. No configuration was changed. A person must confirm it, and applying it voids the running registration.")


# -- registry ----------------------------------------------------------------- #
class ToolSpec:
    def __init__(self, name: str, description: str, input_model: type[Strict], output_model: type[Strict], fn: Callable):
        self.name, self.description, self.input_model, self.output_model, self.fn = name, description, input_model, output_model, fn

    def schema(self) -> dict:
        s = self.input_model.model_json_schema()
        s.pop("title", None)
        s["additionalProperties"] = False
        s["required"] = sorted(s.get("properties", {}))      # strict mode: every property required
        for p in s.get("properties", {}).values():
            p.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": s, "strict": True}


TOOLS: dict[str, ToolSpec] = {
    "read_ledger": ToolSpec(
        "read_ledger",
        "Read closed paper trades from the ledger CSVs (read-only). Returns the rows with entry/exit, exit reason, net % and P&L.",
        ReadLedgerInput, ReadLedgerOutput, read_ledger),
    "summarise_strategy_performance": ToolSpec(
        "summarise_strategy_performance",
        "Deterministic performance statistics over the ledger: win rate, average win/loss, profit factor, net P&L, exit reason counts, per-ticker P&L. Computed in code, not estimated.",
        SummariseInput, SummariseOutput, summarise_strategy_performance),
    "propose_parameter_change": ToolSpec(
        "propose_parameter_change",
        "Propose one strategy parameter change. Appends to a proposal queue that a person confirms by hand. It never edits .env or any live configuration, and it cannot touch DRY_RUN, paper/live mode or the kill switch.",
        ProposeInput, ProposeOutput, propose_parameter_change),
}


def tool_definitions() -> list[dict]:
    return [t.schema() for t in TOOLS.values()]


def run_tool(name: str, raw_input: dict, interaction_id: str | None = None) -> tuple[dict | None, str | None, int]:
    """Validate, run, validate. Returns (output, error, duration_ms). Never raises."""
    t0 = time.perf_counter()
    spec = TOOLS.get(name)
    if spec is None:
        return None, f"unknown tool {name!r}", 0
    try:
        inp = spec.input_model.model_validate(raw_input)
    except ValidationError as e:
        return None, f"invalid input: {e.errors()[0].get('msg')} at {e.errors()[0].get('loc')}", int((time.perf_counter() - t0) * 1000)
    try:
        out = spec.fn(inp, interaction_id) if name == "propose_parameter_change" else spec.fn(inp)
        out = spec.output_model.model_validate(out.model_dump())
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", int((time.perf_counter() - t0) * 1000)
    return json.loads(out.model_dump_json()), None, int((time.perf_counter() - t0) * 1000)
