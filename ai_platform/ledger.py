"""
Read-only access to the paper trade ledgers. Used by the retrieval corpus and
by the agent's read_ledger / summarise tools. Opens CSVs for reading only.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from . import paths

STOCK_GLOB = "completed_trades_stock_*_paper.csv"
FCR_FILE = "completed_trades_spy_fcr_paper.csv"
REGISTRATION_START = date(2026, 9, 4)   # 2026-09-stocks-mean-reversion-v4 `started`


@dataclass
class Trade:
    ticker: str
    strategy: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    entry_reason: str
    exit_reason: str
    gross_pct: float
    net_pct: float
    pnl_usd: float
    holding_hours: float
    source_file: str

    def to_dict(self) -> dict:
        return asdict(self)


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _hours(a: str, b: str) -> float:
    try:
        return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600, 2)
    except Exception:
        return 0.0


def ledger_files(root: Path | None = None) -> list[Path]:
    root = root or paths.REPO_ROOT
    files = sorted(root.glob(STOCK_GLOB))
    fcr = root / FCR_FILE
    if fcr.is_file():
        files.append(fcr)
    return files


def load_trades(root: Path | None = None, *, scope: str = "stocks", ticker: str | None = None,
                since: date | None = None) -> list[Trade]:
    """
    scope: 'stocks' (mean-reversion paper bots), 'fcr' (First Candle Rule), 'all'.
    ticker: filter to one symbol. since: entry date on or after.
    """
    root = root or paths.REPO_ROOT
    out: list[Trade] = []
    for path in ledger_files(root):
        is_fcr = path.name == FCR_FILE
        if scope == "stocks" and is_fcr:
            continue
        if scope == "fcr" and not is_fcr:
            continue
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                t = Trade(
                    ticker=row.get("ticker", "").strip(),
                    strategy=row.get("strategy") or "RSI_MEAN_REVERSION",
                    entry_time=row.get("entry_time", ""),
                    exit_time=row.get("exit_time", ""),
                    entry_price=_f(row.get("entry_price")),
                    exit_price=_f(row.get("exit_price")),
                    entry_reason=row.get("entry_reason", "") or "",
                    exit_reason=row.get("exit_reason", "") or "",
                    gross_pct=_f(row.get("gross_pct")),
                    net_pct=_f(row.get("net_pct")),
                    pnl_usd=_f(row.get("pnl_usd")),
                    holding_hours=_f(row.get("holding_time_hours")) or _hours(row.get("entry_time", ""), row.get("exit_time", "")),
                    source_file=path.name,
                )
                if ticker and t.ticker.upper() != ticker.upper():
                    continue
                if since and t.entry_time and t.entry_time[:10] < since.isoformat():
                    continue
                out.append(t)
    out.sort(key=lambda t: t.entry_time)
    return out


def summarise(trades: list[Trade]) -> dict:
    """Deterministic performance summary. The model never computes these."""
    wins = [t for t in trades if t.net_pct > 0]
    losses = [t for t in trades if t.net_pct <= 0]
    avg_win = sum(t.net_pct for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.net_pct for t in losses) / len(losses) if losses else 0.0
    gross_win = sum(t.net_pct for t in wins)
    gross_loss = -sum(t.net_pct for t in losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    be = (abs(avg_loss) / (abs(avg_loss) + avg_win) * 100) if (avg_win or avg_loss) else 0.0
    reasons: dict[str, int] = {}
    per_ticker: dict[str, dict] = {}
    for t in trades:
        key = t.exit_reason.split(" (")[0]
        reasons[key] = reasons.get(key, 0) + 1
        d = per_ticker.setdefault(t.ticker, {"trades": 0, "net_pnl_usd": 0.0, "wins": 0})
        d["trades"] += 1
        d["net_pnl_usd"] = round(d["net_pnl_usd"] + t.pnl_usd, 4)
        d["wins"] += int(t.net_pct > 0)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "profit_factor": round(pf, 3) if pf != float("inf") else None,
        "net_pnl_usd": round(sum(t.pnl_usd for t in trades), 4),
        "avg_net_pct": round(sum(t.net_pct for t in trades) / len(trades), 3) if trades else 0.0,
        "breakeven_win_rate_pct": round(be, 1),
        "median_hold_hours": sorted(t.holding_hours for t in trades)[len(trades) // 2] if trades else 0.0,
        "exit_reason_counts": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "per_ticker": dict(sorted(per_ticker.items())),
        "first_entry": trades[0].entry_time[:10] if trades else None,
        "last_exit": max((t.exit_time for t in trades), default="")[:10] or None,
    }
