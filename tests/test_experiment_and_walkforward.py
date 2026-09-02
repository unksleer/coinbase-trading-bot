"""
Pre-registration and walk-forward exist to stop two specific failures.

Registration: deciding what counted as success after seeing results. A grid
searched over thirty parameter pairs with the winner reported on the same data
it was chosen from; a timeframe switched because a number looked better.

Walk-forward: treating one 60/40 split as evidence. One split is one draw.
"""
import json
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from utils import experiment  # noqa: E402
import walkforward  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "EXPERIMENTS", str(tmp_path))
    return tmp_path


def _register(name="t"):
    return experiment.register(
        name=name, hypothesis="h", market="stocks", symbols=["SPY", "QQQ"],
        config={"sl": 5.0}, success_criteria={"trades": 20},
        stopping_rule="stop at 20", started="2026-08-31")


def test_registration_seals_its_terms(sandbox):
    record = _register()
    assert experiment.verify(record)
    assert record["status"] == "running"


def test_symbols_are_order_independent(sandbox):
    """Reordering a list must not read as tampering."""
    a = experiment.register("a", "h", "stocks", ["QQQ", "SPY"], {"sl": 5.0},
                            {"trades": 20}, "r", started="2026-08-31")
    b = experiment.register("b", "h", "stocks", ["SPY", "QQQ"], {"sl": 5.0},
                            {"trades": 20}, "r", started="2026-08-31")
    assert a["seal"] == b["seal"]


def test_changing_the_criteria_breaks_the_seal(sandbox):
    _register()
    path = experiment.path_for("t")
    record = json.load(open(path))
    record["success_criteria"] = {"trades": 3}
    json.dump(record, open(path, "w"))
    assert experiment.verify(experiment.load("t")) is False


def test_notes_may_change_without_breaking_the_seal(sandbox):
    """Only the promise is sealed; a running log must stay editable."""
    _register()
    path = experiment.path_for("t")
    record = json.load(open(path))
    record["notes"] = "added an observation"
    json.dump(record, open(path, "w"))
    assert experiment.verify(experiment.load("t")) is True


def test_registration_will_not_silently_overwrite(sandbox):
    _register()
    with pytest.raises(FileExistsError):
        _register()


def test_conclusion_flags_moved_goalposts(sandbox):
    _register()
    path = experiment.path_for("t")
    record = json.load(open(path))
    record["success_criteria"] = {"trades": 1}
    json.dump(record, open(path, "w"))
    concluded = experiment.conclude("t", {"trades": 1}, "passed")
    assert concluded["terms_intact"] is False
    assert "warning" in concluded


def test_clean_conclusion_has_no_warning(sandbox):
    _register()
    concluded = experiment.conclude("t", {"trades": 20}, "passed")
    assert concluded["terms_intact"] is True
    assert "warning" not in concluded
    assert experiment.active() == []


def test_active_lists_only_running(sandbox):
    _register("one")
    _register("two")
    experiment.conclude("one", {}, "failed")
    assert [r["name"] for r in experiment.active()] == ["two"]


# ── walk-forward ────────────────────────────────────────────────────────────

def test_folds_are_contiguous_and_never_train_on_the_test_window():
    series = list(range(1000))
    got = walkforward.folds(series, 5)
    assert len(got) == 5
    for train, test in got:
        assert train.stop == test.start, "training must end where testing starts"
        assert train.start == 0, "anchored: training uses all prior history"
        assert test.stop > test.start


def test_folds_grow_the_training_window():
    got = walkforward.folds(list(range(1000)), 4)
    ends = [train.stop for train, _ in got]
    assert ends == sorted(ends) and len(set(ends)) == len(ends)


def test_short_series_produce_no_folds():
    assert walkforward.folds(list(range(50)), 5) == []


def test_summary_counts_hits_and_benchmark_separately():
    """
    Profitable and beat-buy-and-hold are different questions, and the stock
    result differs between them: 60% of folds profitable, 45% beat holding.
    """
    results = [
        {"sl": 1.0, "tp": 2.0, "trades": 5, "net": 1.0, "excess": 0.01},
        {"sl": 1.0, "tp": 2.0, "trades": 5, "net": 1.0, "excess": -0.01},
        {"sl": 1.0, "tp": 2.0, "trades": 5, "net": -1.0, "excess": -0.01},
    ]
    s = walkforward.summarise(results)
    assert s["profitable_folds"] == 2
    assert s["beat_hold_folds"] == 1
    assert s["hit_rate"] == pytest.approx(66.7, abs=0.1)


def test_parameter_churn_detects_an_unstable_fit():
    """High churn means the fit tracks noise — the most useful warning here."""
    stable = [{"sl": 2.0, "tp": 3.0, "trades": 1, "net": 1.0, "excess": 0.0}] * 4
    assert walkforward.summarise(stable)["sl_churn"] == pytest.approx(0.0)

    unstable = [{"sl": sl, "tp": 3.0, "trades": 1, "net": 1.0, "excess": 0.0}
                for sl in (1.0, 5.0, 1.0, 5.0)]
    assert walkforward.summarise(unstable)["sl_churn"] > 0.5


def test_empty_results_do_not_crash_the_summary():
    assert walkforward.summarise([]) == {"folds": 0}
