from ai_platform.gateway.gateway import BudgetExceeded, ModelGateway, TASK_ROUTES
from ai_platform.gateway.pricing import CHEAP_MODEL, FRONTIER_MODEL, cost_usd
from ai_platform.gateway.providers import OfflineProvider, ProviderError
from ai_platform.gateway.store import Store


def gw(**kw):
    return ModelGateway(provider=kw.pop("provider", OfflineProvider()), store=Store(":memory:"), **kw)


def test_routing_rule_is_two_models_by_task_type():
    assert {TASK_ROUTES[t] for t in ("classify", "extract", "judge")} == {CHEAP_MODEL}
    assert {TASK_ROUTES[t] for t in ("answer", "reason", "agent")} == {FRONTIER_MODEL}
    assert ModelGateway.route("classify") == CHEAP_MODEL
    try:
        ModelGateway.route("nope")
        assert False
    except ValueError:
        pass


def test_every_call_is_logged_with_tokens_latency_cost():
    g = gw()
    r = g.complete("classify", [{"role": "user", "content": "is this a buy?"}], interaction_id="ix1")
    rows = g.store.calls_for("ix1")
    assert len(rows) == 1 and rows[0]["status"] == "ok"
    assert rows[0]["served_model"] == CHEAP_MODEL and rows[0]["input_tokens"] > 0
    assert rows[0]["cost_usd"] == cost_usd(CHEAP_MODEL, rows[0]["input_tokens"], rows[0]["output_tokens"])
    assert r.cost_usd == rows[0]["cost_usd"] and r.latency_ms >= 0


def test_fallback_to_other_model_on_error_and_both_attempts_logged():
    g = gw(provider=OfflineProvider(fail_models={FRONTIER_MODEL}))
    r = g.complete("answer", [{"role": "user", "content": "q"}], interaction_id="ix2")
    assert r.model == CHEAP_MODEL and r.is_fallback
    rows = g.store.calls_for("ix2")
    assert [x["status"] for x in rows] == ["error", "ok"]
    assert [x["is_fallback"] for x in rows] == [0, 1]
    assert rows[0]["requested_model"] == FRONTIER_MODEL and rows[1]["served_model"] == CHEAP_MODEL


def test_no_fallback_when_disabled_and_error_surfaces():
    g = gw(provider=OfflineProvider(fail_models={FRONTIER_MODEL}), enable_fallback=False)
    try:
        g.complete("answer", [{"role": "user", "content": "q"}])
        assert False
    except ProviderError:
        pass


def test_daily_budget_is_enforced_before_the_call():
    g = gw(daily_budget_usd=0.0)
    try:
        g.complete("classify", [{"role": "user", "content": "q"}], interaction_id="ix3")
        assert False
    except BudgetExceeded:
        pass
    rows = g.store.calls_for("ix3")
    assert rows and rows[0]["status"] == "budget" and rows[0]["cost_usd"] == 0


def test_budget_counts_only_this_providers_spend():
    s = Store(":memory:")
    s.log_call(interaction_id="x", task_type="answer", requested_model=FRONTIER_MODEL, served_model=FRONTIER_MODEL,
               provider="anthropic", status="ok", cost_usd=99.0)
    g = ModelGateway(provider=OfflineProvider(), store=s, daily_budget_usd=1.0)
    g.complete("classify", [{"role": "user", "content": "q"}])  # offline spend is separate
    assert s.spend_today(provider="anthropic") == 99.0
