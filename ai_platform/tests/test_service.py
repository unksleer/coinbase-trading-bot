"""
The web service, offline, through FastAPI's TestClient. The conftest forces
AI_PLATFORM_OFFLINE=1 and the hash embedder; `ledger_root` gives a tmp repo
root with two small ledgers and a fake .env that must never change.
"""
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from ai_platform import paths
from ai_platform.agent import proposals
from ai_platform.service import ScrubbedJSONResponse, create_app
from ai_platform.tests.test_agent import ledger_root  # noqa: F401  (fixture)

FAKE_KEY = "sk-ant-api03-FAKE-KEY-FOR-THE-TEST-ONLY-0123456789"


@pytest.fixture
def client(ledger_root, monkeypatch):  # noqa: F811
    # A credential is in the environment, as it is on the real box; it must never reach a response.
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    with TestClient(create_app()) as c:
        yield c


def env_hash(root):
    return hashlib.sha256((root / ".env").read_bytes()).hexdigest()


def test_health_and_page(client):
    assert client.get("/healthz").json()["ok"] is True
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "<title>ai_platform</title>" in page.text and "/api/ask" in page.text


def test_ask_agent_returns_answer_and_logs_calls(client):
    r = client.post("/api/ask", json={"question": "What is the win rate and performance of the stock bots?", "mode": "agent"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "66.7" in d["answer"] and d["interaction_id"].startswith("agent_")
    assert [t["tool"] for t in d["tool_calls"]] == ["summarise_strategy_performance"]
    assert d["tool_calls"][0]["ok"] is True and d["tool_calls"][0]["output"]["trades"] == 3
    assert d["models"] == ["claude-opus-5-5"] and d["cost_usd"] > 0 and d["stopped_reason"] == "end_turn"
    # logged: two model steps and one tool call, under the same interaction id
    store = client.app.state.gateway.store
    assert len(store.calls_for(d["interaction_id"])) == 2
    assert len(store.tool_calls_for(d["interaction_id"])) == 1


def test_ask_rag_returns_answer_with_sources_and_one_call(client):
    r = client.post("/api/ask", json={"question": "why was crypto retired", "mode": "rag", "k": 3})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["answer"] and d["tool_calls"] == [] and len(d["sources"]) == 3
    assert d["interaction_id"].startswith("rag_")
    calls = client.app.state.gateway.store.calls_for(d["interaction_id"])
    assert len(calls) == 1 and calls[0]["task_type"] == "answer" and calls[0]["status"] == "ok"


def test_ask_validates_input(client):
    assert client.post("/api/ask", json={"question": "", "mode": "agent"}).status_code == 422
    assert client.post("/api/ask", json={"question": "x", "mode": "oracle"}).status_code == 422
    assert client.post("/api/ask", json={"question": "x", "k": 0}).status_code == 422


def test_receipt_matches_the_ask(client):
    d = client.post("/api/ask", json={"question": "What is the win rate of the stock bots?", "mode": "agent"}).json()
    rc = client.get(f"/api/receipt/{d['interaction_id']}")
    assert rc.status_code == 200
    r = rc.json()
    assert r["interaction_id"] == d["interaction_id"]
    assert r["totals"]["calls"] == 2 and r["totals"]["ok"] == 2
    assert r["totals"]["cost_usd"] == pytest.approx(d["cost_usd"], abs=1e-6)
    assert {c["served_model"] for c in r["calls"]} == set(d["models"])
    assert all(c["task_type"] == "agent" and c["provider"] == "offline" for c in r["calls"])
    assert [t["tool"] for t in r["tool_calls"]] == [t["tool"] for t in d["tool_calls"]]
    assert r["tool_calls"][0]["input"] == d["tool_calls"][0]["input"] and r["tool_calls"][0]["ok"] is True
    assert client.get("/api/receipt/nope_000").status_code == 404


def test_proposals_decide_touches_only_the_queue(client, ledger_root):  # noqa: F811
    before = env_hash(ledger_root)
    d = client.post("/api/ask", json={"question": "Propose tightening the stop loss to 4% for the stock bots.", "mode": "agent"}).json()
    assert "propose_parameter_change" in [t["tool"] for t in d["tool_calls"]]
    lst = client.get("/api/proposals").json()
    assert lst["pending"] == 1 and len(lst["proposals"]) == 1
    pid = lst["proposals"][0]["proposal_id"]
    assert lst["proposals"][0]["status"] == proposals.PENDING

    r = client.post(f"/api/proposals/{pid}/decide", json={"status": "confirmed", "note": "demo"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False and body["proposal"]["status"] == "confirmed" and body["proposal"]["decision_note"] == "demo"
    assert "Edit .env" in body["how_a_person_applies_it"] and "The agent cannot do any of this" in body["how_a_person_applies_it"]
    # the queue file changed, nothing else did
    assert proposals.load()[0]["status"] == "confirmed"
    assert env_hash(ledger_root) == before
    assert (ledger_root / ".env").read_text() == "STOCK_STOP_LOSS_PERCENT=5.0\nDRY_RUN=True\n"
    assert sorted(p.name for p in paths.DATA_DIR.iterdir()) == ["gateway.sqlite", "index", "proposal_queue.jsonl"]
    # a second decision is refused; unknown ids and bad statuses too
    assert client.post(f"/api/proposals/{pid}/decide", json={"status": "rejected"}).status_code == 409
    assert client.post("/api/proposals/prop_nope/decide", json={"status": "rejected"}).status_code == 404
    assert client.post(f"/api/proposals/{pid}/decide", json={"status": "applied"}).status_code == 422
    assert client.get("/api/proposals").json()["pending"] == 0


def test_report_exposes_budget_and_routing_from_the_gateway(client):
    from ai_platform.gateway.gateway import TASK_EFFORT, TASK_ROUTES
    client.post("/api/ask", json={"question": "why was crypto retired", "mode": "rag", "k": 2})
    rep = client.get("/api/report").json()
    gw = client.app.state.gateway
    assert rep["offline"] is True and rep["today"]["provider"] == "offline"
    assert rep["today"]["budget_usd"] == gw.daily_budget_usd
    assert rep["today"]["spend_usd"] == pytest.approx(gw.spend_today(), abs=1e-6) and rep["today"]["spend_usd"] > 0
    assert rep["routing"] == [{"task_type": t, "model": m, "effort": TASK_EFFORT[t]} for t, m in TASK_ROUTES.items()]
    assert rep["summary"]["provider"] == "anthropic" and rep["summary"]["calls"] == 0      # real-provider view, nothing real logged
    assert client.get("/api/report?provider=offline").json()["summary"]["calls"] >= 1
    assert client.get("/api/report?provider=everyone").status_code == 422


def test_readyz_reflects_state(client, monkeypatch):
    r = client.get("/readyz")
    assert r.status_code == 200, r.text
    checks = r.json()["checks"]
    assert checks["sqlite_writable"]["ok"] and checks["index_loadable"]["ok"] and checks["credentials_present"]["ok"]
    assert checks["index_loadable"]["meta"]["chunks"] > 0 and checks["credentials_present"]["offline"] is True
    # an unwritable log location flips it to 503 and names the check
    blocked = paths.DATA_DIR / "blocked"
    blocked.mkdir()
    monkeypatch.setattr(paths, "GATEWAY_DB", blocked)     # a directory is not a database
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["ready"] is False
    assert r.json()["checks"]["sqlite_writable"]["ok"] is False and r.json()["checks"]["index_loadable"]["ok"] is True


def test_eval_runs_in_the_background_and_reports(client):
    r = client.post("/api/eval/run", json={"ids": ["a01", "q02"], "judge": False})
    assert r.status_code == 202, r.text
    assert r.json()["questions"] == 2 and r.json()["run_id"].startswith("eval_")
    for _ in range(100):
        s = client.get("/api/eval/status").json()
        if s["status"] != "running":
            break
        time.sleep(0.1)
    assert s["status"] == "done", s
    assert s["progress"] == {"done": 2, "total": 2}
    rep = s["report"]
    assert rep["questions"] == 2 and 0 <= rep["score_pct"] <= 100 and set(rep["by_kind_pct"]) == {"agent", "rag"}
    assert rep["live_state_untouched"] is True and rep["judge_used"] is False
    assert (paths.DATA_DIR / "eval_report_last.json").is_file() and s["report_source"].endswith("eval_report_last.json")
    assert client.post("/api/eval/run", json={"ids": ["zzz"]}).status_code == 422


def test_eval_refuses_a_second_concurrent_run(client, monkeypatch):
    from ai_platform.eval import run_eval
    gate = {"go": False}

    def slow_run(*a, **k):
        while not gate["go"]:
            time.sleep(0.01)
        return {"run_id": k.get("run_id"), "score_pct": 0.0}

    monkeypatch.setattr(run_eval, "run", slow_run)
    assert client.post("/api/eval/run", json={"ids": ["a01"], "judge": False}).status_code == 202
    assert client.post("/api/eval/run", json={"ids": ["a01"], "judge": False}).status_code == 409
    assert client.get("/api/eval/status").json()["status"] == "running"
    gate["go"] = True
    for _ in range(100):
        if client.get("/api/eval/status").json()["status"] == "done":
            break
        time.sleep(0.02)
    assert client.get("/api/eval/status").json()["status"] == "done"


def test_the_key_is_absent_from_every_response(client, monkeypatch):
    d = client.post("/api/ask", json={"question": "What is the win rate of the stock bots?", "mode": "agent"}).json()
    paths_to_hit = ["/", "/healthz", "/readyz", "/api/report", "/api/report?provider=all", "/api/proposals",
                    f"/api/receipt/{d['interaction_id']}", "/api/eval/status", "/api/receipt/nope", "/api/openapi.json"]
    bodies = [client.get(p).text for p in paths_to_hit]
    bodies.append(json.dumps(d))
    bodies.append(client.post("/api/ask", json={"question": "why was crypto retired", "mode": "rag", "k": 2}).text)
    bodies.append(client.post("/api/ask", json={"question": "", "mode": "rag"}).text)
    for b in bodies:
        assert FAKE_KEY not in b and "sk-ant-api03" not in b
    # and the last line of defence: a body that did contain it is scrubbed
    leaked = ScrubbedJSONResponse({"oops": FAKE_KEY, "nested": [{"k": f"prefix {FAKE_KEY} suffix"}]}).body.decode()
    assert FAKE_KEY not in leaked and leaked.count("[redacted]") == 2
