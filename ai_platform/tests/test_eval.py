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
    ok, detail = run_check({"type": "contains_computed", "metric": "exit_dates", "scope": "SPY", "since": None},
                           "exited 2026-09-03 and 2026-09-23", ctx)
    assert ok, detail
    assert not run_check({"type": "contains_computed", "metric": "exit_dates", "scope": "SPY", "since": None}, "exited 2026-09-03", ctx)[0]
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


def test_offline_eval_writes_its_index_under_the_test_data_dir(ledger_root, data_dir):  # noqa: F811
    # Regression: the fixture redirects INDEX_DIR, and the index must resolve it at call time,
    # otherwise a test run overwrites the real index with a hash-embedder, two-ledger one.
    import time
    from ai_platform.retrieval.index import Index
    t0 = time.time()
    idx = Index.load_or_build()
    assert (data_dir / "index" / "meta.json").is_file() and idx.meta["embedder"].startswith("hash")
    real = paths.PACKAGE_DIR / "data" / "index" / "meta.json"
    assert not real.exists() or real.stat().st_mtime < t0


def test_markdown_emphasis_is_stripped_before_phrase_checks():
    from ai_platform.eval.scorer import strip_markdown
    ctx = {}
    assert strip_markdown("appends only **closed** candles") == "appends only closed candles"
    assert strip_markdown("it `never saves` a _forming_ candle") == "it never saves a forming candle"
    assert strip_markdown("use `restart_bots.py`, not pm2") == "use restart_bots.py, not pm2"   # snake_case survives
    assert run_check({"type": "contains_any", "values": ["only closed"]}, "appends only **closed** candles", ctx)[0]
    assert run_check({"type": "contains_all", "values": ["never saves", "forming"]}, "It *never saves* a __forming__ candle.", ctx)[0]
    assert run_check({"type": "contains_any", "values": ["restart_bots.py"]}, "run `restart_bots.py`", ctx)[0]
    assert not run_check({"type": "not_contains", "values": ["closed"]}, "only **closed** candles", ctx)[0]
    # the q19 answer that scored 0 on eval_20261014T201342Z_524b now passes its check
    q19 = "The store never saves a candle that is still forming; it appends only **closed** candles."
    assert run_check({"type": "contains_any", "values": ["never stores", "never saves", "only closed"]}, q19, ctx)[0]
