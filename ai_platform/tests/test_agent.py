import hashlib
import json
import textwrap

import pytest

from ai_platform import paths
from ai_platform.agent import proposals
from ai_platform.agent.agent import run_agent
from ai_platform.agent.tools import TOOLS, run_tool, tool_definitions
from ai_platform.gateway.gateway import ModelGateway
from ai_platform.gateway.providers import OfflineProvider, ProviderResponse, Usage
from ai_platform.gateway.store import Store
from ai_platform.ledger import load_trades, summarise

LEDGER = textwrap.dedent("""\
    ticker,entry_time,exit_time,entry_price,exit_price,entry_reason,exit_reason,gross_pct,net_pct,pnl_usd,shares,mode
    SPY,2026-09-01T14:02:10+00:00,2026-09-03T14:01:52+00:00,762.54,768.78,SMA Bullish Cross,RSI fell back through 65,0.8183,0.8183,0.0818,0.013,PAPER
    SPY,2026-09-09T13:31:48+00:00,2026-09-23T13:31:45+00:00,763.88,772.58,SMA Bullish Cross,RSI fell back through 65,1.1389,1.1389,1.1389,0.13,PAPER
    """)
LEDGER2 = textwrap.dedent("""\
    ticker,entry_time,exit_time,entry_price,exit_price,entry_reason,exit_reason,gross_pct,net_pct,pnl_usd,shares,mode
    JNJ,2026-09-08T16:24:08+00:00,2026-10-02T17:19:46+00:00,269.0,255.495,SMA Bullish Cross,Stop Loss Triggered,-5.0204,-5.0204,-5.0204,0.37,PAPER
    """)


@pytest.fixture
def ledger_root(tmp_path, monkeypatch, data_dir):
    (tmp_path / "completed_trades_stock_spy_paper.csv").write_text(LEDGER)
    (tmp_path / "completed_trades_stock_jnj_paper.csv").write_text(LEDGER2)
    (tmp_path / ".env").write_text("STOCK_STOP_LOSS_PERCENT=5.0\nDRY_RUN=True\n")
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    return tmp_path


def gw():
    return ModelGateway(provider=OfflineProvider(), store=Store(":memory:"))


def test_exactly_three_tools_with_strict_schemas():
    defs = tool_definitions()
    assert [d["name"] for d in defs] == ["read_ledger", "summarise_strategy_performance", "propose_parameter_change"]
    for d in defs:
        s = d["input_schema"]
        assert d["strict"] is True and s["additionalProperties"] is False
        assert sorted(s["required"]) == sorted(s["properties"])


def test_read_ledger_tool_is_typed_and_read_only(ledger_root):
    out, err, _ = run_tool("read_ledger", {"ticker": "SPY", "limit": 1, "since": None})
    assert err is None and out["count"] == 2 and out["returned"] == 1 and out["truncated"]
    assert out["trades"][0]["net_pct"] == 1.1389
    out, err, _ = run_tool("read_ledger", {"ticker": "SPY", "limit": 1})
    assert out is None and "invalid input" in err


def test_summarise_matches_ledger_module(ledger_root):
    out, err, _ = run_tool("summarise_strategy_performance", {"scope": "stocks", "since": None})
    assert err is None
    truth = summarise(load_trades(ledger_root, scope="stocks"))
    assert out["trades"] == 3 and out["wins"] == 2 and out["win_rate_pct"] == truth["win_rate_pct"] == 66.7
    assert out["exit_reason_counts"] == {"RSI fell back through 65": 2, "Stop Loss Triggered": 1}


def test_propose_writes_queue_only_and_refuses_bad_parameters(ledger_root):
    env = ledger_root / ".env"
    before = hashlib.sha256(env.read_bytes()).hexdigest()
    out, err, _ = run_tool("propose_parameter_change", {
        "parameter": "STOP_LOSS_PERCENT", "current_value": 5.0, "proposed_value": 4.0,
        "rationale": "Three winners dipped within a point of the stop; this is a test proposal.",
        "evidence": ["DECISIONS.md 2026-10-01"], "voids_running_registration": True})
    assert err is None and out["status"] == "pending_confirmation"
    q = proposals.load()
    assert len(q) == 1 and q[0]["proposal_id"] == out["proposal_id"] and q[0]["parameter"] == "STOP_LOSS_PERCENT"
    assert hashlib.sha256(env.read_bytes()).hexdigest() == before
    # disallowed parameter: schema rejects it, nothing written
    out2, err2, _ = run_tool("propose_parameter_change", {
        "parameter": "DRY_RUN", "current_value": 1, "proposed_value": 0, "rationale": "go live please now thanks",
        "evidence": [], "voids_running_registration": False})
    assert out2 is None and "invalid input" in err2 and len(proposals.load()) == 1
    # out of range
    out3, err3, _ = run_tool("propose_parameter_change", {
        "parameter": "STOP_LOSS_PERCENT", "current_value": 5.0, "proposed_value": 99.0,
        "rationale": "way outside the allowed range for a stop", "evidence": [], "voids_running_registration": True})
    assert out3 is None and "outside the allowed range" in err3 and len(proposals.load()) == 1
    # confirm / reject only touch the queue
    proposals.decide(out["proposal_id"], proposals.CONFIRMED, "ok")
    assert proposals.load()[0]["status"] == "confirmed"
    assert hashlib.sha256(env.read_bytes()).hexdigest() == before


def test_agent_answers_with_tool_and_logs_every_call(ledger_root):
    g = gw()
    r = run_agent("What is the win rate and performance of the stock bots?", gateway=g, with_retrieval=False)
    assert r.stopped_reason == "end_turn" and "66.7" in r.answer
    assert [tc.tool for tc in r.tool_calls] == ["summarise_strategy_performance"]
    rows = g.store.tool_calls_for(r.interaction_id)
    assert len(rows) == 1 and rows[0]["ok"] == 1 and json.loads(rows[0]["output_json"])["trades"] == 3
    assert len(g.store.calls_for(r.interaction_id)) == 2  # two model steps, both logged


def test_agent_proposal_goes_to_queue(ledger_root):
    g = gw()
    r = run_agent("Propose tightening the stop loss to 4% for the stock bots.", gateway=g, with_retrieval=False)
    assert [tc.tool for tc in r.tool_calls] == ["summarise_strategy_performance", "propose_parameter_change"]
    q = proposals.load()
    assert len(q) == 1 and q[0]["proposed_value"] == 4.0 and q[0]["status"] == "pending_confirmation"
    assert q[0]["proposal_id"] in r.answer


def test_agent_refuses_live_switch_without_tools(ledger_root):
    r = run_agent("Switch the bots to live trading now.", gateway=gw(), with_retrieval=False)
    assert r.tool_calls == [] and "will not" in r.answer.lower()


def test_step_budget_stops_runaway_loop(ledger_root):
    class Loopy(OfflineProvider):
        def complete(self, *, model, system, messages, tools, max_tokens, effort):
            self.calls += 1
            return ProviderResponse(content=[{"type": "tool_use", "id": f"t{self.calls}", "name": "read_ledger",
                                              "input": {"ticker": None, "limit": 1, "since": None}}],
                                    stop_reason="tool_use", usage=Usage(10, 5), model=model)
    g = ModelGateway(provider=Loopy(), store=Store(":memory:"))
    r = run_agent("loop forever", gateway=g, with_retrieval=False)
    assert r.steps == 10 and r.stopped_reason == "step_budget" and len(r.tool_calls) == 10
    assert len(g.store.tool_calls_for(r.interaction_id)) == 10
    assert "step budget" in r.answer.lower()


def test_strict_schemas_carry_no_value_constraints_but_validation_still_enforces_them(ledger_root):
    import json as _json
    blob = _json.dumps(tool_definitions())
    for kw in ("minimum", "maximum", "minLength", "title"):
        assert f'"{kw}"' not in blob
    out, err, _ = run_tool("read_ledger", {"ticker": None, "limit": 0, "since": None})
    assert out is None and "invalid input" in err
