import json

from ai_platform import paths
from ai_platform.eval import run_eval
from ai_platform.eval.scorer import deterministic_score, fill_template, run_check
from ai_platform.gateway.gateway import ModelGateway
from ai_platform.gateway.providers import OfflineProvider
from ai_platform.gateway.store import Store
from ai_platform.tests.test_agent import ledger_root  # noqa: F401  (fixture)


def test_deterministic_checks_and_three_point_scoring(ledger_root):  # noqa: F811
    ctx = {"tools_called": ["read_ledger"], "new_proposals": [], "live_state_before": "a", "live_state_after": "a"}
    assert run_check({"type": "contains_any", "values": ["Alpaca", "x"]}, "we use alpaca paper", ctx)[0]
    assert not run_check({"type": "contains_all", "values": ["35", "65"]}, "oversold 35", ctx)[0]
    ok, detail = run_check({"type": "number_close", "metric": "win_rate_pct", "scope": "stocks", "since": None, "tol": 0.6},
                           "win rate is about 66.7% on 3 trades", ctx)
    assert ok, detail
    assert run_check({"type": "tool_called", "values": ["read_ledger"]}, "", ctx)[0]
    assert not run_check({"type": "tool_not_called", "values": ["read_ledger"]}, "", ctx)[0]
    assert run_check({"type": "no_live_write"}, "", ctx)[0]
    assert run_check({"type": "contains_computed", "metric": "worst_ticker", "scope": "stocks", "since": None}, "JNJ lost most", ctx)[0]
    score, res = deterministic_score([{"type": "contains_any", "values": ["yes"]}, {"type": "contains_any", "values": ["no"]}], "yes", ctx)
    assert score == 1 and [r["ok"] for r in res] == [True, False]
    assert deterministic_score([], "x", ctx) == (None, [])


def test_template_fills_ledger_numbers(ledger_root):  # noqa: F811
    s = fill_template("rate {win_rate_pct} over {trades} is {above_or_below_70}", since=None)
    assert s == "rate 66.7 over 3 is below"


def test_eval_set_is_well_formed_and_in_range():
    qs = run_eval.load_eval_set()
    assert 30 <= len(qs) <= 50 and len({q["id"] for q in qs}) == len(qs)
    for q in qs:
        assert q["kind"] in ("rag", "agent") and q["question"]
        assert q.get("checks") or q.get("judge_expected")
        for c in q.get("checks", []):
            assert c["type"] in {"contains_any", "contains_all", "not_contains", "number_close", "contains_computed",
                                 "tool_called", "tool_not_called", "proposal_queued", "proposal_queue_unchanged", "no_live_write"}


def test_whole_eval_runs_offline_and_reports_a_score(ledger_root, monkeypatch):  # noqa: F811
    # retrieval over the real docs with the hash embedder; ledger from the fixture
    g = ModelGateway(provider=OfflineProvider(), store=Store(":memory:"))
    qs = [q for q in run_eval.load_eval_set() if q["id"] in ("q02", "q05", "a01", "a06", "a07")]
    rep = run_eval.run(qs, gateway=g, k=3, use_judge=True, run_id="t", verbose=False)
    assert rep["questions"] == 5 and 0 <= rep["score_pct"] <= 100
    assert rep["live_state_untouched"] is True
    by_id = {r["id"]: r for r in rep["results"]}
    assert by_id["a06"]["score"] == 2, by_id["a06"]["checks"]      # proposal queued, nothing live touched
    assert by_id["a07"]["score"] == 2, by_id["a07"]["checks"]      # refused, no proposal
    assert by_id["a01"]["score"] == 2, by_id["a01"]["checks"]      # numbers match the ledger
    assert "judge" in rep["cost_by_task"] and rep["cost_by_task"]["judge"]["calls"] >= 1
    json.dumps(rep)  # serialisable
