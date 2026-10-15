"""
The web service: one process, one JSON API, one static page.

    python -m ai_platform.service                 # http://127.0.0.1:8765
    python -m ai_platform.service --host 0.0.0.0  # in a container

Everything a request does goes through the same code the CLI uses: the
ModelGateway for every model call (routing, log, fallback, budget), run_agent
and the RAG answer path, the proposal queue, run_eval.run. The service adds
nothing of its own except an eval worker thread. The routing table and the
budget live in the gateway; this module only exposes them.

The API key never appears in a response: the response class scrubs it from
every JSON body as a last line of defence, and no endpoint returns
environment or configuration values.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel, Field
except ImportError as e:  # pragma: no cover
    raise SystemExit("FastAPI is not installed: .venv/bin/pip install fastapi uvicorn") from e

from . import paths
from .agent import proposals
from .agent.agent import run_agent
from .eval import run_eval
from .gateway import ModelGateway
from .gateway.gateway import TASK_EFFORT, TASK_ROUTES, BudgetExceeded
from .gateway.providers import ProviderError
from .gateway.report import summarise
from .gateway.store import Store

log = logging.getLogger("ai_platform.service")

UI_DIR = Path(__file__).with_name("ui")
SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8765


# --------------------------------------------------------------------------- #
# Secret scrubbing
# --------------------------------------------------------------------------- #
def _secrets() -> list[bytes]:
    return [v.encode() for k in SECRET_ENV_VARS if (v := os.environ.get(k)) and len(v) >= 8]


class ScrubbedJSONResponse(JSONResponse):
    """JSON response that replaces any credential value with '[redacted]'."""

    def render(self, content: Any) -> bytes:
        body = super().render(content)
        for s in _secrets():
            if s in body:
                log.error("a credential value reached a response body; redacted")
                body = body.replace(s, b"[redacted]")
        return body


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #
class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal["agent", "rag"] = "agent"
    k: int = Field(5, ge=1, le=20)


class DecideIn(BaseModel):
    status: Literal["confirmed", "rejected"]
    note: Optional[str] = Field(None, max_length=2000)


class EvalRunIn(BaseModel):
    ids: Optional[list[str]] = None
    kind: Optional[Literal["rag", "agent"]] = None
    judge: bool = True
    k: int = Field(5, ge=1, le=20)


# --------------------------------------------------------------------------- #
# Eval worker
# --------------------------------------------------------------------------- #
class EvalJob:
    """One eval run at a time, in a background thread. Status is polled."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.status = "idle"            # idle | running | done | error
        self.run_id: str | None = None
        self.total = 0
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self.report: dict | None = None
        self.report_source: str | None = None

    def start(self, questions: list[dict], *, gateway: ModelGateway, k: int, use_judge: bool) -> str:
        with self.lock:
            if self.status == "running":
                raise RuntimeError("an eval run is already in progress")
            run_id = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:4]}"
            self.status, self.run_id, self.total = "running", run_id, len(questions)
            self.started_at, self.finished_at, self.error = _now(), None, None
            self.thread = threading.Thread(target=self._work, name="eval-run", daemon=True,
                                           args=(questions, gateway, k, use_judge, run_id))
            self.thread.start()
            return run_id

    def _work(self, questions, gateway, k, use_judge, run_id):
        try:
            rep = run_eval.run(questions, gateway=gateway, k=k, use_judge=use_judge, run_id=run_id, verbose=False)
            out = paths.DATA_DIR / "eval_report_last.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(rep, indent=2, ensure_ascii=False))
            with self.lock:
                self.report, self.report_source, self.status = rep, str(out), "done"
        except Exception as e:  # noqa: BLE001 - surfaced through /api/eval/status
            log.exception("eval run failed")
            with self.lock:
                self.status, self.error = "error", f"{type(e).__name__}: {e}"
        finally:
            with self.lock:
                self.finished_at = _now()

    def progress(self, gateway: ModelGateway) -> dict:
        """Questions started so far: distinct interaction ids logged under this run."""
        if self.status != "running" or not self.run_id:
            return {"done": self.total if self.status == "done" else 0, "total": self.total}
        rows = gateway.store.query("SELECT COUNT(DISTINCT interaction_id) n FROM calls WHERE interaction_id LIKE ?",
                                   (self.run_id + ":%",))
        return {"done": int(rows[0]["n"]) if rows else 0, "total": self.total}

    def snapshot(self, gateway: ModelGateway) -> dict:
        with self.lock:
            return {"status": self.status, "run_id": self.run_id, "started_at": self.started_at,
                    "finished_at": self.finished_at, "error": self.error, "progress": self.progress(gateway),
                    "report": self.report, "report_source": self.report_source}

    def load_last_report(self) -> None:
        """The last UI run if there is one, else the committed measured run."""
        for p in (paths.DATA_DIR / "eval_report_last.json", paths.REPORTS_DIR / "eval_report.json"):
            if p.is_file():
                try:
                    self.report, self.report_source = json.loads(p.read_text()), str(p)
                    return
                except Exception:
                    continue


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sqlite_writable(db: Path) -> tuple[bool, str | None]:
    """Take and release a write lock without changing anything."""
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db), timeout=2)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        finally:
            conn.close()
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


_index_cache: dict[str, dict] = {}


def _index_loadable() -> tuple[bool, dict | None, str | None]:
    """Full load once per index directory; after that, the probe only checks the files are still there."""
    key = str(paths.INDEX_DIR)
    if key in _index_cache and (paths.INDEX_DIR / "meta.json").is_file():
        return True, _index_cache[key], None
    try:
        from .retrieval.index import Index
        idx = Index.load_or_build()
        meta = {k: idx.meta.get(k) for k in ("embedder", "documents", "chunks", "built_at")}
        _index_cache[key] = meta
        return True, meta, None
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def _models_for(store, interaction_id: str) -> list[str]:
    rows = store.query("SELECT DISTINCT served_model FROM calls WHERE interaction_id = ? AND status = 'ok' ORDER BY id",
                       (interaction_id,))
    return [r["served_model"] for r in rows]


def _loads(s: str | None) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def create_app(gateway: ModelGateway | None = None) -> FastAPI:
    """Build the app. Paths are read from `paths` at call time so tests can redirect them."""
    paths.ensure_dirs()
    app = FastAPI(title="ai_platform", version="0.1", default_response_class=ScrubbedJSONResponse,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    gw = gateway or ModelGateway(store=Store(paths.GATEWAY_DB))   # resolved now, not at import, so tests can redirect it
    job = EvalJob()
    job.load_last_report()
    app.state.gateway, app.state.eval = gw, job
    offline = gw.provider.name == "offline"

    # -- the page --------------------------------------------------------- #
    @app.get("/", include_in_schema=False)
    def index_page():
        return FileResponse(UI_DIR / "index.html", media_type="text/html")

    # -- probes ----------------------------------------------------------- #
    @app.get("/healthz")
    def healthz():
        return {"ok": True, "time": _now()}

    @app.get("/readyz")
    def readyz():
        db_ok, db_err = _sqlite_writable(paths.GATEWAY_DB)
        ix_ok, ix_meta, ix_err = _index_loadable()
        creds_ok = gw.provider.name == "anthropic" or offline
        checks = {
            "sqlite_writable": {"ok": db_ok, "error": db_err},
            "index_loadable": {"ok": ix_ok, "meta": ix_meta, "error": ix_err},
            "credentials_present": {"ok": creds_ok, "provider": gw.provider.name, "offline": offline},
        }
        ready = db_ok and ix_ok and creds_ok
        body = {"ready": ready, "checks": checks, "time": _now()}
        return ScrubbedJSONResponse(body, status_code=200 if ready else 503)

    # -- ask -------------------------------------------------------------- #
    @app.post("/api/ask")
    def ask(inp: AskIn):
        t0 = time.perf_counter()
        if inp.mode == "agent":
            r = run_agent(inp.question, gateway=gw, k=inp.k)
            ix = r.interaction_id
            answer, sources, cost = r.answer, r.sources, r.cost_usd
            tool_calls = [{"step": tc.step, "tool": tc.tool, "input": tc.input, "output": tc.output,
                           "ok": tc.error is None, "error": tc.error, "duration_ms": tc.duration_ms}
                          for tc in r.tool_calls]
            stopped = r.stopped_reason
        else:
            ix = f"rag_{uuid.uuid4().hex[:10]}"
            try:
                answer, sources, cost = run_eval.answer_rag(gw, inp.question, inp.k, ix)
            except BudgetExceeded as e:
                raise HTTPException(429, str(e)) from e
            except ProviderError as e:
                raise HTTPException(502, str(e)) from e
            tool_calls, stopped = [], "end_turn"
        return {"interaction_id": ix, "mode": inp.mode, "question": inp.question, "answer": answer,
                "sources": sources, "tool_calls": tool_calls, "cost_usd": round(cost, 6),
                "latency_ms": int((time.perf_counter() - t0) * 1000), "models": _models_for(gw.store, ix),
                "stopped_reason": stopped}

    # -- receipt ---------------------------------------------------------- #
    @app.get("/api/receipt/{interaction_id}")
    def receipt(interaction_id: str):
        calls = gw.store.calls_for(interaction_id)
        tools = gw.store.tool_calls_for(interaction_id)
        if not calls and not tools:
            raise HTTPException(404, f"no calls logged for {interaction_id}")
        for t in tools:
            t["input"] = _loads(t.pop("input_json", None))
            t["output"] = _loads(t.pop("output_json", None))
            t["ok"] = bool(t["ok"])
        for c in calls:
            c["is_fallback"] = bool(c["is_fallback"])
        totals = {"calls": len(calls), "ok": sum(1 for c in calls if c["status"] == "ok"),
                  "input_tokens": sum(c["input_tokens"] for c in calls),
                  "output_tokens": sum(c["output_tokens"] for c in calls),
                  "latency_ms": sum(c["latency_ms"] for c in calls),
                  "cost_usd": round(sum(c["cost_usd"] for c in calls), 6)}
        return {"interaction_id": interaction_id, "calls": calls, "tool_calls": tools, "totals": totals}

    # -- report ----------------------------------------------------------- #
    @app.get("/api/report")
    def report(provider: str = Query("anthropic", pattern="^(anthropic|offline|all)$"),
               day: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
        spent = gw.spend_today()
        budget = gw.daily_budget_usd
        return {
            "summary": summarise(gw.store, provider=provider, day=day),
            "today": {"day": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "provider": gw.provider.name,
                      "spend_usd": round(spent, 6), "budget_usd": budget,
                      "remaining_usd": round(max(0.0, budget - spent), 6),
                      "pct_used": round(min(100.0, spent / budget * 100), 1) if budget else 100.0},
            "routing": [{"task_type": t, "model": m, "effort": TASK_EFFORT.get(t)} for t, m in TASK_ROUTES.items()],
            "fallback_enabled": gw.enable_fallback,
            "offline": offline,
        }

    # -- proposals -------------------------------------------------------- #
    @app.get("/api/proposals")
    def list_proposals():
        items = proposals.load()
        return {"proposals": items, "pending": sum(1 for p in items if p["status"] == proposals.PENDING)}

    @app.post("/api/proposals/{proposal_id}/decide")
    def decide_proposal(proposal_id: str, inp: DecideIn):
        status = proposals.CONFIRMED if inp.status == "confirmed" else proposals.REJECTED
        try:
            item = proposals.decide(proposal_id, status, inp.note)
        except KeyError:
            raise HTTPException(404, f"unknown proposal {proposal_id}") from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return {"proposal": item, "applied": False,
                "how_a_person_applies_it": item.get("how_a_person_applies_it") if status == proposals.CONFIRMED else None}

    # -- eval ------------------------------------------------------------- #
    @app.post("/api/eval/run", status_code=202)
    def eval_run(inp: EvalRunIn):
        qs = run_eval.load_eval_set()
        if inp.ids:
            want = set(inp.ids)
            qs = [q for q in qs if q["id"] in want]
        if inp.kind:
            qs = [q for q in qs if q["kind"] == inp.kind]
        if not qs:
            raise HTTPException(422, "no questions match ids/kind")
        try:
            run_id = job.start(qs, gateway=gw, k=inp.k, use_judge=inp.judge)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from None
        return {"run_id": run_id, "status": "running", "questions": len(qs), "judge": inp.judge,
                "provider": gw.provider.name}

    @app.get("/api/eval/status")
    def eval_status():
        return job.snapshot(gw)

    return app


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST, help="bind address (default localhost; 0.0.0.0 in a container)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--log-level", default="info")
    a = ap.parse_args(argv)
    try:
        import uvicorn
    except ImportError:  # pragma: no cover
        raise SystemExit("uvicorn is not installed: .venv/bin/pip install fastapi uvicorn")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = create_app()
    print(f"ai_platform service: provider={app.state.gateway.provider.name}  budget=${app.state.gateway.daily_budget_usd:.2f}/day"
          f"  log={paths.GATEWAY_DB}\nopen http://{a.host}:{a.port}/", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level=a.log_level)


if __name__ == "__main__":
    main()
